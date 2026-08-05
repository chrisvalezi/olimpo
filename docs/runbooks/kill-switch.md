# Runbook — Kill switch

**Severidade:** P1 · **Alerta:** nenhum (é a *resposta* a outros alertas)

> Parar a frota custa horas de conteúdo. Não parar pode custar contas, dinheiro ou um processo judicial. **Na dúvida, pare.** Acionar é permissivo — qualquer plantonista pode. Liberar é restrito — só admin, e só com o checklist da §6 completo.

---

## 1. Quando acionar

### Parada global — imediata, sem pedir autorização

| Situação | Por quê |
|---|---|
| Conteúdo impróprio, ofensivo ou ilegal publicado | Cada minuto no ar multiplica o alcance |
| ≥ 2 contas suspensas ou banidas em 72 h | Detecção de padrão coordenado; continuar rodando queima o resto |
| Agente fora da persona de forma sistemática | Não se sabe o que ele vai publicar em seguida |
| Guardrail com `severity='kill'` disparado | O sistema já decidiu; o humano só confirma |
| Vazamento de segredo suspeito ou confirmado | Terceiro pode estar despachando tasks |
| Comportamento anômalo não explicado | "Não entendo o que está acontecendo" é motivo suficiente |
| Notificação jurídica (difamação, DMCA, direito de imagem) | Contenção antes de negociação |
| Migração ou restauração de banco | Estado inconsistente + frota ativa = ações duplicadas |

### Parada por conta

| Situação | Por quê |
|---|---|
| Uma conta com falhas de login | Insistir converte challenge em ban |
| Suspeita de shadowban | Reduzir pegada enquanto se observa |
| Conteúdo específico de uma conta sob revisão | Contenção cirúrgica |
| Conta atingindo o teto de ações/hora repetidamente | Risco de ban acumulando |
| Deriva de persona em uma conta só | Rebaixar autonomia pode bastar (§4.3) |

---

## 2. Sintoma

Este runbook é acionado **por decisão**, não por sintoma. Chega-se aqui vindo de:

- [conta-banida.md](conta-banida.md) — múltiplas contas caindo
- [conteudo-improprio-publicado.md](conteudo-improprio-publicado.md) — algo ruim no ar
- [restaurar-backup.md](restaurar-backup.md) — antes de restaurar
- [12-seguranca.md §5.4](../12-seguranca.md#54-resposta-a-incidente) — incidente de segurança

---

## 3. Correção — parada global

### 3.1 Acionar (< 60 segundos)

```bash
cd /caminho/do/olimpo

# 1) Vira o interruptor
sed -i.bak 's/^GLOBAL_KILL_SWITCH=.*/GLOBAL_KILL_SWITCH=true/' .env
grep -E '^(GLOBAL_KILL_SWITCH|DISPATCH_ENABLED)=' .env

# 2) Recarrega os processos que leem a variável
docker compose restart api worker beat      # ou: make restart

# 3) Confirma que pegou
docker compose logs --since 2m --no-log-prefix worker \
  | grep -i 'kill_switch' | tail -20
```

Confirmação pelo banco — o dispatcher passa a emitir `kill_switch.blocked` a cada tick:

```sql
SELECT count(*) AS bloqueios_ultimos_5min
FROM events
WHERE type = 'kill_switch.blocked'
  AND created_at > now() - interval '5 minutes';
```

> Se esse contador ficar em `0` depois de 2 minutos, **o kill switch não está funcionando**. Vá direto para a §3.5 (parada por força bruta).

### 3.2 Registrar quem, quando e por quê

Obrigatório. Um kill switch sem autor é auditoria quebrada.

```sql
INSERT INTO events (type, detail, trace_id)
VALUES ('kill_switch.engaged',
        jsonb_build_object(
          'scope','global',
          'actor','<seu.nome>',
          'reason','<uma frase objetiva>',
          'runbook','kill-switch.md',
          'incident_id','INC-2026-03-12-01',
          'human','Parada global acionada: <motivo>.'),
        'incident-' || to_char(now(),'YYYYMMDDHH24MI'));
```

### 3.3 O que acontece com as tasks em voo

Esta é a parte que gera dúvida no plantão. O comportamento por status:

| Status no momento da parada | O que acontece | Perdida? |
|---|---|---|
| `pending` | Fica parada. O dispatcher a vê e emite `kill_switch.blocked` em vez de despachar | **Não** |
| `scheduled` | Idem. Continua elegível quando liberar — sujeita a `expires_at` | **Não**, salvo expiração |
| `dispatched` | **Já está com o orchestrator.** O kill switch do Olimpo não a alcança | Ver §3.4 |
| `running` | **Executando no device agora.** Vai terminar | Ver §3.4 |
| `verified` / `failed` | Terminal. Nada muda | — |
| Task Celery na fila `production` | Continua sendo consumida — produz conteúdo, mas **não despacha** | Não |
| Task Celery na fila `realtime` | Idem: classifica interação, não responde | Não |

**A regra:** o `GLOBAL_KILL_SWITCH` bloqueia o **despacho**, não a **produção**. Conteúdo continua sendo gerado e fica em `content_items`. É intencional — parar de pensar não é necessário; parar de agir é.

Ver o que ficou em voo:

```sql
SELECT t.id, a.handle, t.type, t.status, t.dispatched_at,
       t.external_task_id, extract(epoch FROM (now() - t.dispatched_at))/60 AS min_em_voo
FROM tasks t JOIN accounts a ON a.id = t.account_id
WHERE t.status IN ('dispatched','running')
ORDER BY t.dispatched_at;
```

### 3.4 Cancelar o que já está com o orchestrator

Se o motivo da parada é **conteúdo impróprio** ou **risco jurídico**, não basta parar o Olimpo — as tasks em voo precisam ser abortadas no orchestrator:

```bash
PSQL="docker compose exec -T postgres psql -U olimpo -d olimpo -tA"

$PSQL -c "SELECT external_task_id FROM tasks
          WHERE status IN ('dispatched','running')
            AND external_task_id IS NOT NULL;" \
| while read -r ext; do
    echo "cancelando ${ext}"
    curl -fsS -X POST -H "Authorization: Bearer ${ORCHESTRATOR_TOKEN}" \
      "${ORCHESTRATOR_URL}/v1/tasks/${ext}/cancel" || echo "  ✗ falhou: ${ext}"
  done
```

Se o motivo for operacional (fila travada, LLM saturada), **deixe as tasks em voo terminarem**. Cancelar ação de publicação no meio pode deixar um post parcialmente criado no device.

### 3.5 Se o kill switch não funcionar

Ordem crescente de brutalidade. Cada passo é mais rápido e mais destrutivo que o anterior.

```bash
# (a) Parar só quem despacha — a fila e o banco sobrevivem intactos
docker compose stop worker beat

# (b) Esvaziar a fila de dispatch (o Postgres continua sendo a fonte da verdade;
#     as tasks serão reenfileiradas quando o worker voltar)
docker compose exec redis redis-cli DEL dispatch

# (c) Cortar no banco: nada elegível para despacho
docker compose exec -T postgres psql -U olimpo -d olimpo <<'SQL'
UPDATE accounts SET status = 'paused' WHERE status = 'active';
UPDATE tasks SET status = 'cancelled',
                 last_error = 'kill switch de emergência'
 WHERE status IN ('pending','scheduled');
SQL

# (d) Último recurso: derruba a stack (mantém volumes)
docker compose down
```

> **(c) é destrutivo para o agendamento.** As tasks canceladas não voltam sozinhas — a retomada precisará recriá-las. Use apenas se (a) e (b) não bastarem.

---

## 4. Correção — parada por conta

### 4.1 Suspender uma conta

```sql
\set acc 'a3f9b21c-0000-4000-8000-000000000001'

UPDATE accounts SET status = 'suspended' WHERE id = :'acc';

UPDATE tasks
SET status = 'cancelled',
    last_error = 'cancelado: kill switch por conta'
WHERE account_id = :'acc'
  AND status IN ('pending','scheduled');

INSERT INTO events (account_id, type, detail, trace_id)
VALUES (:'acc', 'kill_switch.engaged',
        jsonb_build_object('scope','account','actor','<seu.nome>',
          'reason','<motivo>','runbook','kill-switch.md',
          'human','Conta suspensa individualmente: <motivo>.'),
        'incident-' || to_char(now(),'YYYYMMDDHH24MI'));
```

### 4.2 `paused` ou `suspended`?

| Status | Significado | Retomada |
|---|---|---|
| `paused` | Decisão operacional reversível — férias da conta, observação, ajuste | Simples: voltar para `active` |
| `suspended` | **Incidente.** Guardrail disparou, ban suspeito, conteúdo sob revisão | Exige o checklist da §6 |

Ambos param o dispatcher. A diferença é semântica e importa para a auditoria e para os dashboards.

### 4.3 Alternativa: rebaixar autonomia

Nem todo problema exige parada. Se o problema é **qualidade**, e não **risco imediato**, o rebaixamento mantém a conta viva com humano no loop:

```sql
UPDATE accounts SET autonomy = 'L0' WHERE id = :'acc';

INSERT INTO events (account_id, type, detail, trace_id)
VALUES (:'acc','account.autonomy.changed',
        jsonb_build_object('from','L2','to','L0','actor','<seu.nome>',
          'reason','deriva_de_persona',
          'human','Rebaixada a L0: todo conteúdo volta a exigir aprovação.'),
        'incident-...');
```

Em L0 a conta continua produzindo, mas nada sai sem aprovação humana. Para deriva de persona, é a resposta correta — parar a conta não conserta o prompt.

### 4.4 Parada por grupo

```sql
-- Todas as contas de um arquétipo (ex.: risco jurídico de fofoca)
UPDATE accounts SET status = 'paused'
WHERE archetype = 'gossip' AND status = 'active';

-- Todas as contas de um device
UPDATE accounts SET status = 'paused'
WHERE device_binding->>'device_id' = '<device>' AND status = 'active';

-- Todas as participantes de uma campanha
UPDATE accounts SET status = 'paused'
WHERE id IN (SELECT account_id FROM campaign_participants
             WHERE campaign_id = '<campaign_id>')
  AND status = 'active';

-- E abortar a campanha junto
UPDATE campaigns SET status = 'aborted' WHERE id = '<campaign_id>';
```

---

## 5. Diagnóstico — enquanto está parado

A parada é o momento de descobrir o que aconteceu, não de esperar.

```sql
-- O que estava em voo quando parou
SELECT status, count(*) FROM tasks
WHERE status IN ('pending','scheduled','dispatched','running')
GROUP BY status;

-- Tasks que vão expirar durante a parada (fofoca: cada uma é um furo perdido)
SELECT t.id, a.handle, t.type, t.expires_at,
       extract(epoch FROM (t.expires_at - now()))/60 AS min_restantes
FROM tasks t JOIN accounts a ON a.id = t.account_id
WHERE t.status IN ('pending','scheduled')
  AND t.expires_at IS NOT NULL
  AND t.expires_at > now()
ORDER BY t.expires_at
LIMIT 30;

-- Últimos 200 eventos antes da parada — a cena do crime
SELECT created_at, type, account_id,
       coalesce(detail->>'human', left(detail::text, 100)) AS o_que
FROM events
WHERE created_at > (SELECT max(created_at) FROM events
                    WHERE type = 'kill_switch.engaged') - interval '2 hours'
ORDER BY created_at DESC
LIMIT 200;

-- Guardrails nas 6 h anteriores
SELECT created_at, account_id, rule, severity, action_taken
FROM guardrail_violations
WHERE created_at > now() - interval '6 hours'
ORDER BY created_at DESC;
```

---

## 6. Retomada

> A retomada é o momento perigoso. Ligar tudo de volta sem limpar o acúmulo produz uma **tempestade de ações simultâneas** — precisamente o padrão que causa banimento. Nunca ligue e vá dormir.

### 6.1 Checklist — nenhum item é opcional

**Causa**
- [ ] Causa raiz identificada e escrita, não apenas suspeitada
- [ ] Correção aplicada (código, config, guardrail ou `spread_policy`) — não só "vamos observar"
- [ ] Se foi conteúdo impróprio: removido da plataforma e o guardrail corrigido ([runbook](conteudo-improprio-publicado.md))
- [ ] Se foi ban: contas de risco tratadas ([runbook](conta-banida.md))
- [ ] Se foi segurança: segredos rotacionados ([12-seguranca.md §1.4](../12-seguranca.md#14-rotação))

**Estado do sistema**
- [ ] `curl -fsS localhost:8000/health` → `postgres`, `redis`, `litellm` todos `ok`
- [ ] `docker compose exec worker celery -A olimpo.workers.app inspect ping` responde
- [ ] `curl -fsS "${ORCHESTRATOR_URL}/v1/health"` responde
- [ ] Circuit breakers fechados (`olimpo_circuit_breaker_state == 0`)
- [ ] Nenhuma task presa em `dispatched` há mais de 30 min (§6.2)

**Acúmulo — o passo que as pessoas pulam**
- [ ] Tasks expiradas marcadas como `expired`, não reenfileiradas
- [ ] Backlog dimensionado e **espalhado** (§6.3)
- [ ] Nenhuma janela de 1 h com mais de `ACCOUNT_MAX_ACTIONS_PER_HOUR` por conta
- [ ] Campanhas abortadas continuam abortadas (não ressuscitam sozinhas)

**Contas**
- [ ] Cada conta `paused`/`suspended` avaliada individualmente — nunca um `UPDATE` em massa de volta para `active`
- [ ] Contas envolvidas no incidente voltam com autonomia rebaixada
- [ ] Contas em `warming` continuam em `warming`

**Autorização**
- [ ] Admin aprovou a retomada (operador não libera kill switch global)
- [ ] Evento `kill_switch.released` registrado com autor e justificativa

### 6.2 Limpar o acúmulo antes de ligar

```sql
-- (1) Expirar o que perdeu a validade. NÃO reenfileirar furo velho.
UPDATE tasks
SET status = 'expired',
    last_error = 'expirou durante parada do kill switch'
WHERE status IN ('pending','scheduled')
  AND expires_at IS NOT NULL
  AND expires_at < now();

-- (2) Cancelar o que envelheceu demais mesmo sem expires_at.
-- Um post planejado para anteontem não faz sentido publicar hoje.
UPDATE tasks
SET status = 'cancelled',
    last_error = 'obsoleta após parada (> 24h)'
WHERE status IN ('pending','scheduled')
  AND scheduled_for < now() - interval '24 hours'
  AND type IN ('publish_post','publish_story','publish_reels',
               'comment','reply_comment','send_dm');

-- (3) Resolver o que ficou em voo (ver orchestrator-fora-do-ar.md §5)
SELECT id, account_id, type, external_task_id, dispatched_at
FROM tasks WHERE status IN ('dispatched','running');

-- (4) Dimensionar o que sobrou
SELECT type, count(*), min(scheduled_for), max(scheduled_for)
FROM tasks WHERE status IN ('pending','scheduled')
GROUP BY type ORDER BY 2 DESC;
```

### 6.3 Espalhar o backlog — obrigatório

Se sobrou backlog relevante, **redistribua no tempo antes de ligar**. Ligar com 40 tasks vencidas é entregar à plataforma o padrão que ela procura.

```sql
-- Redistribui as tasks vencidas ao longo das próximas 8 horas,
-- com jitter aleatório e espaçamento por conta.
WITH ordenadas AS (
  SELECT id,
         row_number() OVER (PARTITION BY account_id
                            ORDER BY priority, scheduled_for) AS n
  FROM tasks
  WHERE status IN ('pending','scheduled')
    AND scheduled_for <= now()
)
UPDATE tasks t
SET scheduled_for = now()
      + (o.n * interval '18 minutes')                    -- espaça por conta
      + (random() * interval '25 minutes')               -- jitter individual
FROM ordenadas o
WHERE t.id = o.id;

-- Conferir: nenhuma conta deve ter mais de 12 ações em qualquer hora
SELECT a.handle, date_trunc('hour', t.scheduled_for) AS hora, count(*)
FROM tasks t JOIN accounts a ON a.id = t.account_id
WHERE t.status IN ('pending','scheduled')
  AND t.scheduled_for BETWEEN now() AND now() + interval '12 hours'
GROUP BY a.handle, hora
HAVING count(*) > 12
ORDER BY 3 DESC;
```

A última query deve voltar **vazia**. Se não voltar, aumente o espaçamento e rode de novo.

### 6.4 Religar

Sempre em degraus, nunca de uma vez.

```bash
# Degrau 1 — liberar o interruptor, mas com o worker parado
sed -i 's/^GLOBAL_KILL_SWITCH=.*/GLOBAL_KILL_SWITCH=false/' .env
docker compose stop worker
docker compose restart api beat

# Degrau 2 — uma conta canária. Escolha a de menor valor.
docker compose exec -T postgres psql -U olimpo -d olimpo -c \
  "UPDATE accounts SET status='active' WHERE handle='<conta_canaria>';"

docker compose start worker
```

Observar por **30 minutos**:

```sql
-- A canária despachou e verificou normalmente?
SELECT t.type, t.status, t.dispatched_at, t.verified_at, t.last_error
FROM tasks t JOIN accounts a ON a.id = t.account_id
WHERE a.handle = '<conta_canaria>'
  AND t.updated_at > now() - interval '30 minutes'
ORDER BY t.updated_at DESC;

-- Nenhum guardrail novo?
SELECT count(*) FROM guardrail_violations
WHERE created_at > now() - interval '30 minutes';
```

```bash
# Degrau 3 — 25% da frota, aguardar 1 h
# Degrau 4 — 50%, aguardar 1 h
# Degrau 5 — restante
```

```sql
-- Degrau 3: reativa 25% das contas que estavam ativas antes do incidente
UPDATE accounts SET status = 'active'
WHERE id IN (
  SELECT id FROM accounts
  WHERE status = 'paused'
  ORDER BY followers ASC          -- as de menor valor primeiro
  LIMIT (SELECT greatest(1, count(*)/4) FROM accounts WHERE status = 'paused')
);
```

Campanhas voltam **por último**, e nunca no mesmo dia da retomada.

### 6.5 Registrar a liberação

```sql
INSERT INTO events (type, detail, trace_id)
VALUES ('kill_switch.released',
        jsonb_build_object(
          'scope','global',
          'actor','<admin>',
          'duration_min', <minutos_parado>,
          'root_cause','<causa raiz>',
          'fix_applied','<o que mudou>',
          'tasks_expired', <n>,
          'tasks_cancelled', <n>,
          'accounts_reactivated', <n>,
          'runbook','kill-switch.md',
          'human','Frota retomada em degraus após <causa>.'),
        'incident-...');
```

---

## 7. Prevenção

| Medida | Detalhe |
|---|---|
| **Exercitar trimestralmente** | Acionar e liberar em staging, cronometrando. Um kill switch nunca testado não é um kill switch |
| **Botão no painel** | Admin aciona o global e operador aciona por conta sem editar `.env` e sem `make restart` (Fase 1) |
| **Kill switch a quente** | Ler `GLOBAL_KILL_SWITCH` do Redis, não só do `.env`, para eliminar o restart do caminho crítico |
| **Alerta de estado** | `olimpo_kill_switch_active` no dashboard NOC — ninguém deve descobrir por acaso que a frota está parada |
| **`expires_at` bem definido** | Task com validade explícita se autolimpa; sem ele, todo backlog vira decisão manual |
| **Espalhamento na retomada por código** | O dispatcher deveria recusar-se a despachar mais de `ACCOUNT_MAX_ACTIONS_PER_HOUR`, tornando a §6.3 uma rede de segurança em vez de um passo manual |
| **Autonomia rebaixada por padrão** | Após qualquer parada por incidente, as contas envolvidas voltam um nível abaixo |

---

## 8. Escalação

| Quando | Para quem | Prazo |
|---|---|---|
| Acionamento global | Admin + canal de operação, **no momento do acionamento** | Imediato |
| Kill switch não funcionou (§3.5) | Admin + responsável técnico — é bug crítico | Imediato |
| Parada > 4 h | Admin decide sobre comunicação a clientes com `client_id` | 4 h |
| Parada > 24 h | Revisão de arquitetura: por que não foi possível retomar? | 24 h |
| Retomada com incidente reincidente | **Não retomar.** Parar de novo e escalar para admin | Imediato |
| Kill switch acionado por não-admin | Admin revisa em até 1 h — mas **a parada permanece válida** até a revisão | 1 h |

---

**Ver também:** [conta-banida.md](conta-banida.md) · [conteudo-improprio-publicado.md](conteudo-improprio-publicado.md) · [fila-travada.md](fila-travada.md) · [08-guardrails-e-autonomia.md](../08-guardrails-e-autonomia.md)
