# Runbook — android-orchestrator fora do ar

**Severidade:** P1 · **Alertas:** `OrchestratorForaDoAr`, `TasksPresasEmDispatched`

> O android-orchestrator é a **única** camada que toca as plataformas. Se ele cai, o Olimpo não executa nada — e isso é um comportamento **projetado**, não uma falha. O core continua planejando, produzindo e acumulando; nada se perde.
>
> O perigo não está na queda. Está na **volta**: religar com backlog acumulado produz uma rajada de ações simultâneas em toda a frota, que é exatamente o padrão de detecção de farm. A §4 deste runbook é a parte que importa.

---

## 1. Sintoma

- `curl "${ORCHESTRATOR_URL}/v1/health"` falha, dá timeout ou devolve 5xx.
- `olimpo_circuit_breaker_state{service="orchestrator"} == 2` (aberto).
- Tasks acumulando em `dispatched` sem nunca chegar a `verified`.
- Nenhum webhook chegando em `/webhooks/orchestrator`.
- `tasks.last_error` repetindo `connection refused`, `timeout` ou `orchestrator_5xx`.
- Contas ativas silenciosas, mas as filas Celery **não** estão travadas (a produção continua).

---

## 2. Comportamento esperado do Olimpo

O que **deve** acontecer sozinho, sem intervenção. Se algo aqui não estiver acontecendo, isso é um bug e vai para a §7.

| Componente | Comportamento durante a queda |
|---|---|
| **Circuit breaker** | Após N falhas consecutivas, abre. Para de tentar, evita esgotar o pool do worker com chamadas que vão falhar. Meio-abre periodicamente para sondar |
| **Dispatcher** | Vê o circuito aberto e **não despacha**. Emite evento; deixa as tasks em `pending`/`scheduled` |
| **Tasks novas** | Continuam sendo criadas normalmente. Acumulam em `scheduled` |
| **Loop do agente** | `PLAN`, `SOURCE`, `PRODUCE`, `QA` continuam. `content_items` acumula em `approved`/`scheduled` |
| **Fila `production`** | Continua consumindo — o sistema continua "pensando" |
| **Fila `dispatch`** | Esvazia ou fica ociosa (nada a despachar com o circuito aberto) |
| **Tasks já em voo** | Ficam em `dispatched`/`running`. **Estado desconhecido** — o núcleo do problema (§5) |
| **`expires_at`** | Tasks com validade vencem e são marcadas `expired`. Perda controlada e desejada |
| **Nada é perdido** | O Postgres é a fonte da verdade do agendamento (ADR-0004). Nenhuma task some porque o orchestrator caiu |

**Consequência prática:** uma queda de até algumas horas é absorvível sem intervenção. O trabalho do plantão é (a) confirmar que o comportamento acima está ocorrendo, (b) evitar a tempestade na volta, (c) reconciliar as tasks em voo.

---

## 3. Diagnóstico

### 3.1 Confirmar que é o orchestrator

```bash
# Alcançável?
curl -fsS -m 5 "${ORCHESTRATOR_URL}/v1/health" | jq . || echo "INACESSÍVEL"

# Autenticação ainda válida? (401 é problema diferente de queda)
curl -s -o /dev/null -w '%{http_code}\n' -m 5 \
  -H "Authorization: Bearer ${ORCHESTRATOR_TOKEN}" \
  "${ORCHESTRATOR_URL}/v1/tasks?limit=1"

# É rede ou é o serviço?
docker compose exec api sh -c 'getent hosts host.docker.internal || echo "DNS falhou"'
docker compose exec api sh -c "curl -sS -m 5 -o /dev/null -w '%{http_code} %{time_total}s\n' ${ORCHESTRATOR_URL}/v1/health"
```

| Resultado | Diagnóstico |
|---|---|
| `connection refused` | Serviço parado. Reinício do lado do orchestrator |
| Timeout sem resposta | Serviço travado ou rede bloqueada |
| `502`/`503` | Proxy de pé, backend caído |
| `500` | Orchestrator vivo mas quebrado — **pior caso**, pode estar executando parcialmente |
| `401`/`403` | Não é queda: token expirado ou rotacionado ([12-seguranca.md §1.4](../12-seguranca.md#14-rotação)) |
| Responde do host mas não do container | Problema de rede Docker / `extra_hosts` |

### 3.2 Estado do circuit breaker

```sql
SELECT created_at, type, detail->>'service' AS servico,
       detail->>'failures' AS falhas,
       coalesce(detail->>'human', detail::text) AS o_que
FROM events
WHERE type IN ('orchestrator.circuit.open','orchestrator.circuit.closed')
  AND created_at > now() - interval '6 hours'
ORDER BY created_at DESC;
```

```bash
curl -fsS localhost:8000/metrics | grep -E 'olimpo_circuit_breaker_state|olimpo_integration_requests_total.*orchestrator'
```

Circuito **fechado** com o orchestrator inacessível significa que o breaker não está funcionando — o worker vai gastar todos os slots em chamadas condenadas. Isso agrava para uma [fila travada](fila-travada.md).

### 3.3 Dimensionar o acúmulo

```sql
-- O que está esperando
SELECT status, count(*), min(scheduled_for) AS mais_antiga
FROM tasks
WHERE status IN ('pending','scheduled','dispatched','running')
GROUP BY status ORDER BY 2 DESC;

-- O que está em voo — o problema real
SELECT t.id, a.handle, t.type, t.status, t.dispatched_at,
       round(extract(epoch FROM (now() - t.dispatched_at))/60) AS min_em_voo,
       t.external_task_id
FROM tasks t JOIN accounts a ON a.id = t.account_id
WHERE t.status IN ('dispatched','running')
ORDER BY t.dispatched_at;

-- O que vai expirar durante a queda
SELECT t.type, count(*), min(t.expires_at) AS primeira_a_expirar
FROM tasks t
WHERE t.status IN ('pending','scheduled')
  AND t.expires_at IS NOT NULL AND t.expires_at > now()
GROUP BY t.type;

-- Confirmar que o núcleo continua trabalhando (deve haver produção recente)
SELECT count(*) FILTER (WHERE created_at > now() - interval '1 hour') AS conteudo_1h,
       count(*) FILTER (WHERE status = 'approved')                    AS aprovado_esperando
FROM content_items;
```

### 3.4 Quando o orchestrator voltar, ele lembra?

Pergunta decisiva para a reconciliação. Depende de o orchestrator ter estado durável.

```bash
# Após a volta: ele ainda conhece as tasks que despachamos?
curl -fsS -H "Authorization: Bearer ${ORCHESTRATOR_TOKEN}" \
  "${ORCHESTRATOR_URL}/v1/tasks/<external_task_id>" | jq '{status, updated_at, result}'
```

| Resposta | Significado |
|---|---|
| Task existe, `completed` | Executou; **o webhook se perdeu**. Reconciliar como sucesso |
| Task existe, `failed` | Não executou. Seguro reagendar |
| Task existe, `running` | Ainda executando. **Aguardar**, não intervir |
| `404` | Perdeu o estado. **Verificação manual obrigatória** para ações de publicação (§5.3) |

---

## 4. Correção

### 4.1 Durante a queda — não faça nada precipitado

O sistema está se comportando como projetado. A tentação de "consertar" é o maior risco.

**NÃO faça:**

- ❌ Reiniciar o worker em loop — não é o worker que está com problema
- ❌ Baixar o limiar do circuit breaker para "tentar mais" — martelar um serviço caído não o levanta
- ❌ Cancelar tasks em massa — elas são o trabalho acumulado, e é isso que preserva a operação
- ❌ Marcar tasks `dispatched` como `failed` sem consultar (§5) — arrisca publicação duplicada
- ❌ Desligar o circuit breaker

**FAÇA:**

```bash
# 1) Confirmar que o núcleo continua saudável
curl -fsS localhost:8000/health | jq .
docker compose exec worker celery -A olimpo.workers.app inspect ping

# 2) Registrar o início do incidente
docker compose exec -T postgres psql -U olimpo -d olimpo <<'SQL'
INSERT INTO events (type, detail, trace_id)
VALUES ('incident.opened',
        jsonb_build_object('service','android-orchestrator',
          'runbook','orchestrator-fora-do-ar.md','actor','<seu.nome>',
          'human','Orchestrator inacessível; tasks acumulando em scheduled.'),
        'incident-orch-' || to_char(now(),'YYYYMMDDHH24MI'));
SQL

# 3) Acionar quem cuida do orchestrator (§8)
```

### 4.2 Quando a queda passa de 2 horas

O acúmulo começa a virar um problema por si só.

```sql
-- Congelar a produção de conteúdo perecível, preservando o resto.
-- Não adianta gerar mais posts que não podem sair.
UPDATE accounts SET status = 'paused'
WHERE status = 'active' AND archetype = 'gossip';   -- fofoca é a mais perecível

-- Expirar proativamente o que já não faz sentido
UPDATE tasks
SET status = 'expired',
    last_error = 'expirou durante indisponibilidade do orchestrator'
WHERE status IN ('pending','scheduled')
  AND expires_at IS NOT NULL AND expires_at < now();
```

Acima de 6 horas, considere `GLOBAL_KILL_SWITCH=true` ([kill-switch.md](kill-switch.md)) — isso força a retomada a passar pelo checklist de espalhamento, em vez de acontecer sozinha no meio da noite.

### 4.3 Evitar a tempestade de retry na volta

**A parte mais importante deste runbook.** Faça isto **antes** de o orchestrator voltar, ou imediatamente após confirmar que voltou — mas sempre com o dispatcher ainda parado.

```bash
# Passo 1 — segurar o dispatcher ANTES que ele veja o circuito fechar
docker compose stop worker beat
```

```sql
-- Passo 2 — dimensionar o estrago potencial
SELECT count(*) AS vencidas,
       count(DISTINCT account_id) AS contas,
       round(count(*)::numeric / greatest(count(DISTINCT account_id),1), 1) AS por_conta
FROM tasks
WHERE status IN ('pending','scheduled') AND scheduled_for <= now();
```

> Se `por_conta > 12`, ligar sem espalhar viola `ACCOUNT_MAX_ACTIONS_PER_HOUR` na primeira hora. É a receita do ban coletivo.

```sql
-- Passo 3 — descartar o obsoleto
UPDATE tasks SET status = 'expired',
                 last_error = 'expirada durante queda do orchestrator'
WHERE status IN ('pending','scheduled')
  AND expires_at IS NOT NULL AND expires_at < now();

UPDATE tasks SET status = 'cancelled',
                 last_error = 'obsoleta apos queda do orchestrator (> 24h)'
WHERE status IN ('pending','scheduled')
  AND scheduled_for < now() - interval '24 hours'
  AND type IN ('publish_post','publish_story','publish_reels',
               'comment','reply_comment','send_dm','like','share');
```

```sql
-- Passo 4 — ESPALHAR o que sobrou.
-- Ações urgentes primeiro, e nunca mais de uma por conta a cada ~18 min.
WITH ordenadas AS (
  SELECT id, row_number() OVER (PARTITION BY account_id
                                ORDER BY priority, scheduled_for) AS n
  FROM tasks
  WHERE status IN ('pending','scheduled') AND scheduled_for <= now()
)
UPDATE tasks t
SET scheduled_for = now() + interval '10 minutes'          -- carência inicial
                          + (o.n * interval '18 minutes')
                          + (random() * interval '22 minutes')
FROM ordenadas o
WHERE t.id = o.id;

-- Passo 5 — VERIFICAR. Deve voltar vazio.
SELECT a.handle, date_trunc('hour', t.scheduled_for) AS hora, count(*)
FROM tasks t JOIN accounts a ON a.id = t.account_id
WHERE t.status IN ('pending','scheduled')
  AND t.scheduled_for BETWEEN now() AND now() + interval '12 hours'
GROUP BY a.handle, hora
HAVING count(*) > 12
ORDER BY 3 DESC;
```

```bash
# Passo 6 — religar em degraus
docker compose start beat
sleep 30
docker compose start worker

# Observar os primeiros 10 minutos
docker compose logs -f --tail=50 worker | grep -iE 'dispatch|circuit|orchestrator'
```

### 4.4 Confirmar a recuperação

```sql
-- O circuito fechou?
SELECT created_at, type, detail
FROM events WHERE type LIKE 'orchestrator.circuit%'
ORDER BY created_at DESC LIMIT 5;

-- Tasks voltaram a ser verificadas?
SELECT count(*) FROM tasks WHERE verified_at > now() - interval '15 minutes';

-- A fila está drenando? Rode duas vezes com 5 min de intervalo.
SELECT count(*) FROM tasks
WHERE status IN ('pending','scheduled') AND scheduled_for <= now();
```

---

## 5. Reconciliação

> **O problema central:** uma task em `dispatched` pode ter executado no device sem que a confirmação tenha voltado. Marcá-la `failed` e reagendar arrisca **publicar o mesmo conteúdo duas vezes** — que além de estragar o feed, é comportamento não-humano evidente.
>
> **Regra:** nunca reagende uma ação de escrita sem verificação positiva.

### 5.1 Classificar o risco de cada task em voo

```sql
SELECT t.id, a.handle, t.type, t.external_task_id, t.dispatched_at,
       CASE
         WHEN t.type IN ('publish_post','publish_story','publish_reels')
              THEN 'ALTO — duplicação visível no feed'
         WHEN t.type IN ('comment','reply_comment','send_dm')
              THEN 'ALTO — duplicação visível a terceiros'
         WHEN t.type IN ('follow','unfollow','like','save','share')
              THEN 'MEDIO — idempotente na prática'
         WHEN t.type IN ('watch_reels','view_stories','collect_metrics',
                         'collect_interactions','extract_content','monitor_check')
              THEN 'BAIXO — seguro reexecutar'
         ELSE 'REVISAR'
       END AS risco_de_duplicacao
FROM tasks t JOIN accounts a ON a.id = t.account_id
WHERE t.status IN ('dispatched','running')
ORDER BY 7, t.dispatched_at;
```

### 5.2 Reconciliação automática — consultar o orchestrator

```bash
PSQL="docker compose exec -T postgres psql -U olimpo -d olimpo"

$PSQL -tA -c "SELECT id || ' ' || external_task_id FROM tasks
              WHERE status IN ('dispatched','running')
                AND external_task_id IS NOT NULL;" \
| while read -r tid ext; do
    resp=$(curl -fsS -m 10 -H "Authorization: Bearer ${ORCHESTRATOR_TOKEN}" \
             "${ORCHESTRATOR_URL}/v1/tasks/${ext}" 2>/dev/null)
    if [ -z "$resp" ]; then
      echo "${tid} ${ext} DESCONHECIDA"
    else
      echo "${tid} ${ext} $(echo "$resp" | jq -r '.status')"
    fi
  done | tee /tmp/reconc-orch.txt

sort -k3 /tmp/reconc-orch.txt | uniq -c -f2 | sort -rn
```

Aplicar por categoria:

```sql
-- (a) completed → o webhook se perdeu.
-- PREFIRA reprocessar o callback real: ele cria `posts`, grava métricas
-- e escreve a memória episódica. Este UPDATE é o plano B.
UPDATE tasks
SET status = 'verified', verified_at = now(), external_status = 'completed',
    result = result || jsonb_build_object(
      'reconciled', true, 'reconciled_at', now(),
      'source', 'runbook orchestrator-fora-do-ar')
WHERE id = ANY(ARRAY['<uuid>','<uuid>']::uuid[]);

-- (b) failed no orchestrator → seguro reagendar, com espalhamento
UPDATE tasks
SET status = 'scheduled',
    scheduled_for = now() + interval '15 minutes' + (random() * interval '90 minutes'),
    attempts = attempts + 1,
    external_task_id = NULL, external_status = NULL,
    last_error = 'reagendada apos falha confirmada no orchestrator'
WHERE id = ANY(ARRAY['<uuid>']::uuid[])
  AND attempts < max_attempts;

-- (c) running → NÃO TOCAR. Reavaliar em 30 minutos.
```

### 5.3 Tasks desconhecidas — verificação manual

Para as que o orchestrator não conhece (`404`) **e** cujo tipo tem risco alto de duplicação, verificação no perfil real é obrigatória:

```sql
-- Gera a lista de conferência manual
SELECT a.handle, t.type, t.dispatched_at,
       left(ci.caption, 90) AS caption_esperada,
       'https://instagram.com/' || a.handle AS conferir_em
FROM tasks t
JOIN accounts a ON a.id = t.account_id
LEFT JOIN content_items ci ON ci.id = t.content_item_id
WHERE t.status IN ('dispatched','running')
  AND t.type IN ('publish_post','publish_story','publish_reels',
                 'comment','reply_comment','send_dm')
ORDER BY t.dispatched_at;
```

Para cada linha, um humano confere o perfil e aplica:

```sql
-- Publicou → registrar o post e fechar a task
UPDATE tasks SET status = 'verified', verified_at = now(),
       result = result || '{"reconciled":true,"verified_by":"manual"}'::jsonb
WHERE id = '<uuid>';

INSERT INTO posts (account_id, content_item_id, task_id, platform, external_id, permalink, published_at)
VALUES ('<account_id>','<content_item_id>','<task_id>','instagram',
        '<external_id>','<permalink>','<published_at>');

UPDATE content_items SET status = 'published' WHERE id = '<content_item_id>';

-- Não publicou → devolver ao ciclo
UPDATE tasks SET status = 'scheduled',
       scheduled_for = now() + interval '30 minutes' + (random() * interval '2 hours'),
       attempts = attempts + 1, external_task_id = NULL,
       last_error = 'nao executada; confirmado manualmente'
WHERE id = '<uuid>';
```

### 5.4 Rede de segurança

```sql
-- Nada pode ficar em voo por mais de 4 h. Depois disso, é falha.
UPDATE tasks
SET status = 'failed',
    last_error = 'nao reconciliada em 4h apos queda do orchestrator',
    attempts = attempts + 1
WHERE status IN ('dispatched','running')
  AND dispatched_at < now() - interval '4 hours';
```

Task `failed` desse jeito **não** é reagendada automaticamente para tipos de escrita. Ela vai para revisão humana.

### 5.5 Fechar o incidente

```sql
INSERT INTO events (type, detail, trace_id)
VALUES ('incident.closed',
        jsonb_build_object('service','android-orchestrator',
          'runbook','orchestrator-fora-do-ar.md',
          'duration_min', <n>,
          'tasks_reconciled_ok', <n>,
          'tasks_rescheduled', <n>,
          'tasks_manual_check', <n>,
          'tasks_expired', <n>,
          'root_cause','<causa>',
          'actor','<seu.nome>'),
        'incident-orch-...');
```

---

## 6. Prevenção

| Medida | Detalhe |
|---|---|
| **Varredor de reconciliação automático** | Job em `maintenance` a cada 10 min: consulta o orchestrator sobre toda task em `dispatched` há > 15 min. Elimina 90% deste runbook. **Dívida nº 1 da Fase 0** |
| **Polling como fallback do webhook** | Já previsto no risco da Fase 0: suportar os dois modos no `orchestrator.py` |
| **Circuit breaker com meio-aberto lento** | Sondar a cada 60 s, não a cada 5 s. Sondagem agressiva vira DDoS no serviço que está tentando subir |
| **Espalhamento na retomada por código** | O dispatcher deve recusar despacho acima de `ACCOUNT_MAX_ACTIONS_PER_HOUR`, tornando a §4.3 uma rede de segurança |
| **`expires_at` em toda task perecível** | Autolimpeza do backlog sem decisão humana |
| **Idempotência ponta a ponta** | Chave de idempotência por `tasks.id` enviada ao orchestrator, para que a execução duplicada seja recusada **na origem** |
| **Health check do orchestrator no `/health` do Olimpo** | Detectar antes de o usuário perceber |
| **Alerta em `dispatched` presas** | `TasksPresasEmDispatched > 5` por 10 min, sem esperar o circuito abrir |
| **Retenção de estado no orchestrator** | Negociar com o serviço: task não pode sumir do histórico após restart, ou a §5.3 vira rotina |

---

## 7. Quando o comportamento esperado não acontece

Se durante a queda você observar qualquer coisa da coluna direita, há um bug — registre e escale:

| Deveria acontecer | Se em vez disso acontecer |
|---|---|
| Circuito abre após N falhas | Worker esgota o pool tentando indefinidamente → falta circuit breaker → vira [fila travada](fila-travada.md) |
| Tasks ficam em `scheduled` | Tasks viram `failed` imediatamente → retry sem backoff, `attempts` estourando à toa |
| Produção continua | Loop inteiro para → acoplamento indevido entre `PRODUCE` e `DISPATCH` |
| Nada se perde | Tasks somem → o agendamento não está no Postgres como manda o ADR-0004 |
| Rajada evitada na volta | Rajada acontece → dispatcher sem teto de ações/hora — **risco de ban, é P1** |

---

## 8. Escalação

| Quando | Para quem | Prazo |
|---|---|---|
| Confirmada indisponibilidade | Responsável pelo android-orchestrator | **Imediato** |
| Queda > 1 h | Admin do Olimpo — decidir sobre pausar arquétipos perecíveis | 1 h |
| Queda > 6 h | Admin — avaliar `GLOBAL_KILL_SWITCH` e descarte de backlog | 6 h |
| Queda > 24 h | Admin + comercial (clientes com `client_id` afetados) | 24 h |
| Orchestrator responde `500` (vivo e quebrado) | **Imediato** — pode estar executando ações parciais nos devices | Imediato |
| Tasks em voo não reconciliáveis (§5.3) | Revisor humano para conferência nos perfis | 2 h |
| Duplicação confirmada em conta real | [conta-banida.md](conta-banida.md) — risco de ban elevado | Imediato |
| Comportamento da §7 observado | Responsável técnico do Olimpo — é bug de arquitetura | 24 h |

---

**Ver também:** [fila-travada.md](fila-travada.md) · [kill-switch.md](kill-switch.md) · [conta-banida.md](conta-banida.md) · [ADR-0001](../adr/0001-core-proprio.md) · [contratos/](../contratos/)
