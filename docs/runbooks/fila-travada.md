# Runbook — Fila travada

**Severidade:** P1 (`realtime`/`dispatch`) · P2 (`production`/`maintenance`)
**Alertas:** `FilaTravada`, `FilaRealtimeAtrasada`, `TasksExpirando`, `WorkerMorto`, `ContaSilenciosa`

> A fila travada é o incidente mais comum e o mais fácil de diagnosticar errado. A sequência abaixo elimina as causas em ordem de probabilidade. **Não pule etapas** — reiniciar o worker antes de saber por que ele parou destrói a evidência e o problema volta em duas horas.

---

## 1. Sintoma

- `olimpo_oldest_pending_task_age_seconds` crescendo sem parar.
- `olimpo_queue_depth{queue="dispatch"}` subindo monotonicamente.
- Contas `active` sem publicar nada há horas; `ContaSilenciosa` disparando.
- Tasks presas em `dispatched` sem nunca chegar a `verified`.
- Interações (comentários/DM) envelhecendo sem resposta.
- `olimpo_task_expired_total` subindo — em fofoca, furos morrendo na fila.

---

## 2. Diagnóstico

### 2.0 Triagem em 30 segundos

```bash
cd /caminho/do/olimpo

docker compose ps
curl -fsS localhost:8000/health | jq .
docker compose exec worker celery -A olimpo.workers.app inspect ping
for q in realtime dispatch production maintenance; do
  printf '%-12s %s\n' "$q" "$(docker compose exec -T redis redis-cli LLEN "$q")"
done
docker compose exec -T postgres psql -U olimpo -d olimpo -c \
  "SELECT status, count(*) FROM tasks GROUP BY status ORDER BY 2 DESC;"
```

Leitura do resultado:

| Observação | Vá para |
|---|---|
| `inspect ping` sem resposta / worker `Exited` | §3.1 |
| Filas com profundidade alta e worker vivo | §2.2, §3.2 |
| Filas **vazias** mas tasks `pending` acumulando | §2.3 — o problema é o dispatcher, não a fila |
| Muitas tasks em `dispatched`/`running` | §2.4 |
| `GLOBAL_KILL_SWITCH=true` | Não é incidente. [kill-switch.md](kill-switch.md) |
| `DISPATCH_ENABLED=false` | Não é incidente. É o default do projeto |

**Sempre confira os dois interruptores antes de investigar qualquer outra coisa:**

```bash
grep -E '^(GLOBAL_KILL_SWITCH|DISPATCH_ENABLED|OLIMPO_ENV)=' .env
```

Muita hora de plantão já foi gasta depurando uma "fila travada" que era `DISPATCH_ENABLED=false`.

### 2.1 Quão travado, e desde quando

```sql
-- Idade da task mais antiga que já deveria ter saído
SELECT
    min(scheduled_for)                                        AS mais_antiga,
    extract(epoch FROM (now() - min(scheduled_for)))/60       AS atraso_min,
    count(*)                                                  AS total_vencidas
FROM tasks
WHERE status IN ('pending','scheduled')
  AND scheduled_for <= now();

-- Distribuição por prioridade (menor = mais urgente)
SELECT
    CASE WHEN priority <  50 THEN 'p0'
         WHEN priority < 100 THEN 'p1'
         WHEN priority < 200 THEN 'p2'
         ELSE 'p3' END                                        AS banda,
    count(*),
    round(max(extract(epoch FROM (now() - scheduled_for)))/60) AS pior_atraso_min
FROM tasks
WHERE status IN ('pending','scheduled') AND scheduled_for <= now()
GROUP BY banda ORDER BY banda;

-- Está travado para todo mundo, ou só para alguns tipos/contas?
SELECT t.type, count(*),
       round(avg(extract(epoch FROM (now() - t.scheduled_for)))/60) AS atraso_medio_min
FROM tasks t
WHERE t.status IN ('pending','scheduled') AND t.scheduled_for <= now()
GROUP BY t.type ORDER BY 2 DESC;

SELECT a.handle, a.status, count(*) AS travadas
FROM tasks t JOIN accounts a ON a.id = t.account_id
WHERE t.status IN ('pending','scheduled') AND t.scheduled_for <= now()
GROUP BY a.handle, a.status ORDER BY 3 DESC LIMIT 20;
```

> Se todas as tasks travadas são de **uma conta**, o problema não é a fila — é o lock daquela conta (§3.5) ou o status dela.

### 2.2 O worker está vivo e trabalhando?

Vivo ≠ trabalhando. Um worker pode responder `ping` e estar com todos os slots ocupados por uma task pendurada.

```bash
# Responde?
docker compose exec worker celery -A olimpo.workers.app inspect ping

# O que está executando AGORA (e há quanto tempo)
docker compose exec worker celery -A olimpo.workers.app inspect active | head -60

# O que já foi puxado da fila mas ainda não começou
docker compose exec worker celery -A olimpo.workers.app inspect reserved | head -40

# Concorrência e filas consumidas — confirma que as 4 filas estão sendo lidas
docker compose exec worker celery -A olimpo.workers.app inspect stats \
  | grep -E 'concurrency|pool|max-concurrency'
docker compose exec worker celery -A olimpo.workers.app inspect active_queues \
  | grep -E 'name'

# Erros recentes
docker compose logs --since 30m --no-log-prefix worker \
  | jq -r 'select(.level=="error") | "\(.timestamp) \(.event) \(.phase // "-")"' \
  | tail -40
```

**Sinal de esgotamento de pool:** `inspect active` mostra `WORKER_CONCURRENCY` (default 8) tasks, todas iniciadas há muito tempo. O worker está vivo e completamente bloqueado.

Causa mais comum: **chamadas de LLM lentas ocupando todos os slots**. Confirme:

```sql
SELECT backend, count(*),
       round(avg(latency_ms))               AS media_ms,
       round(max(latency_ms))               AS pior_ms,
       count(*) FILTER (WHERE NOT success)  AS falhas
FROM llm_calls
WHERE created_at > now() - interval '30 minutes'
GROUP BY backend;
```

Média acima de 30 000 ms → o problema real é [llm-saturada.md](llm-saturada.md), não a fila.

### 2.3 O beat está acordando o dispatcher?

Se as filas Redis estão **vazias** e as tasks continuam `pending`, ninguém está produzindo trabalho. O `beat` é o marcapasso.

```bash
docker compose ps beat
docker compose logs --since 15m --no-log-prefix beat | tail -30
```

```sql
-- O dispatcher rodou nos últimos minutos?
SELECT max(created_at) AS ultimo_tick,
       extract(epoch FROM (now() - max(created_at))) AS segundos_atras
FROM events
WHERE type LIKE 'dispatcher%' OR type = 'kill_switch.blocked';

-- Ticks por minuto na última hora (esperado: ~2, com beat a cada 30 s)
SELECT date_trunc('minute', created_at) AS minuto, count(*)
FROM events
WHERE type LIKE 'dispatcher%' AND created_at > now() - interval '1 hour'
GROUP BY minuto ORDER BY minuto DESC LIMIT 20;
```

Sem tick há mais de 2 minutos → §3.3.

### 2.4 Tasks presas em `dispatched` sem verificação

O caso mais insidioso: o Olimpo despachou, o orchestrator não confirmou, e a task ocupa o slot da conta indefinidamente.

```sql
SELECT t.id, a.handle, t.type, t.status,
       t.dispatched_at,
       round(extract(epoch FROM (now() - t.dispatched_at))/60) AS min_em_voo,
       t.external_task_id, t.external_status, t.attempts
FROM tasks t JOIN accounts a ON a.id = t.account_id
WHERE t.status IN ('dispatched','running')
  AND t.dispatched_at < now() - interval '15 minutes'
ORDER BY t.dispatched_at;

-- Quantas, e desde quando
SELECT count(*) AS presas,
       min(dispatched_at) AS mais_antiga,
       count(DISTINCT account_id) AS contas_afetadas
FROM tasks
WHERE status IN ('dispatched','running')
  AND dispatched_at < now() - interval '15 minutes';
```

Três causas possíveis, distinguíveis:

```bash
# (a) O orchestrator está fora do ar?
curl -fsS -m 5 -H "Authorization: Bearer ${ORCHESTRATOR_TOKEN}" \
  "${ORCHESTRATOR_URL}/v1/health" | jq . || echo "ORCHESTRATOR INACESSÍVEL"

# (b) Ele conhece a task e já a concluiu? (webhook perdido)
curl -fsS -H "Authorization: Bearer ${ORCHESTRATOR_TOKEN}" \
  "${ORCHESTRATOR_URL}/v1/tasks/<external_task_id>" | jq .

# (c) O webhook chegou e foi rejeitado?
docker compose logs --since 2h --no-log-prefix api \
  | jq -r 'select(.event | test("webhook")) | "\(.timestamp) \(.event) \(.result // "")"' \
  | tail -30
```

| Resultado | Diagnóstico | Ação |
|---|---|---|
| (a) falha | Orchestrator caiu | [orchestrator-fora-do-ar.md](orchestrator-fora-do-ar.md) |
| (b) diz `completed` | **Webhook perdido** | §3.4 — reconciliação |
| (b) diz `running` | Execução lenta de verdade | Aguardar; só intervir após 60 min |
| (b) `404` | Task sumiu do orchestrator | §3.4 — marcar `failed` e reagendar |
| (c) `invalid_hmac` | Segredo dessincronizado | [12-seguranca.md §1.4](../12-seguranca.md#14-rotação) |

### 2.5 Locks órfãos no Redis

O dispatcher usa lock por conta para não despachar duas ações simultâneas no mesmo device. Um worker morto no meio de uma task deixa o lock para trás — e a conta congela.

```bash
docker compose exec redis redis-cli --scan --pattern 'olimpo:lock:*'

# TTL de cada um. -1 significa "sem expiração" = lock órfão, o pior caso.
for k in $(docker compose exec -T redis redis-cli --scan --pattern 'olimpo:lock:*'); do
  printf '%-60s ttl=%s\n' "$k" "$(docker compose exec -T redis redis-cli TTL "$k")"
done
```

Lock com `ttl=-1`, ou conta travada com lock presente e nenhuma task ativa correspondente → §3.5.

### 2.6 Saúde do Redis e do Postgres

```bash
docker compose exec redis redis-cli INFO clients | grep -E 'connected_clients|blocked'
docker compose exec redis redis-cli INFO memory  | grep -E 'used_memory_human|maxmemory'
docker compose exec redis redis-cli INFO stats   | grep -E 'rejected|evicted'
```

```sql
-- Conexões e queries longas no Postgres
SELECT count(*) FROM pg_stat_activity;
SELECT pid, state, wait_event_type, wait_event,
       now() - query_start AS duracao, left(query, 90) AS query
FROM pg_stat_activity
WHERE state <> 'idle' AND now() - query_start > interval '30 seconds'
ORDER BY duracao DESC;

-- Locks bloqueando
SELECT bl.pid AS bloqueado, ka.query AS bloqueador_query,
       now() - bl.query_start AS ha_quanto_tempo
FROM pg_stat_activity bl
JOIN pg_locks l1 ON l1.pid = bl.pid AND NOT l1.granted
JOIN pg_locks l2 ON l2.relation = l1.relation AND l2.granted
JOIN pg_stat_activity ka ON ka.pid = l2.pid;
```

---

## 3. Correção

### 3.1 Worker morto ou sem consumir

```bash
docker compose ps worker
docker compose logs --tail=200 worker | tail -60

# Se saiu por OOM, o exit code costuma ser 137
docker inspect --format='{{.State.ExitCode}} {{.State.OOMKilled}}' \
  "$(docker compose ps -q worker)"
```

```bash
# Reinício simples
docker compose restart worker

# Confirmar que voltou consumindo as 4 filas
sleep 10
docker compose exec worker celery -A olimpo.workers.app inspect active_queues \
  | grep -E 'realtime|dispatch|production|maintenance'
```

Se foi OOM: reduza `WORKER_CONCURRENCY` no `.env` (8 → 4) e suba de novo. Concorrência alta com prompts grandes de LLM estoura memória rapidamente.

Se o worker morre repetidamente, isole por fila antes de reiniciar às cegas:

```bash
# Sobe um worker só para as filas críticas, enquanto se investiga production
docker compose run --rm -d worker \
  celery -A olimpo.workers.app worker --loglevel=INFO \
  --queues=realtime,dispatch --concurrency=4
```

### 3.2 Fila com backlog e worker saudável

Se o worker está bem mas não dá conta, o problema é capacidade — e a prioridade é preservar `realtime` e `dispatch`.

```bash
# Worker adicional dedicado às filas urgentes
docker compose run --rm -d worker \
  celery -A olimpo.workers.app worker --loglevel=INFO \
  --queues=realtime,dispatch --concurrency=6 --hostname=urgente@%h

# E, se necessário, drenar production separadamente
docker compose run --rm -d worker \
  celery -A olimpo.workers.app worker --loglevel=INFO \
  --queues=production --concurrency=2 --hostname=producao@%h
```

Se a causa raiz é LLM lenta, aumentar concorrência **piora** — mais requisições concorrendo pelo mesmo Mac mini. Vá para [llm-saturada.md](llm-saturada.md).

### 3.3 Beat parado

```bash
docker compose restart beat
sleep 45
docker compose logs --since 1m --no-log-prefix beat | tail -20
```

```sql
-- Confirmar que o tick voltou
SELECT max(created_at), extract(epoch FROM (now() - max(created_at))) AS seg
FROM events WHERE type LIKE 'dispatcher%';
```

> **Nunca rode duas instâncias de `beat`.** Duplicidade gera despacho duplicado — a mesma ação executada duas vezes na mesma conta, que é exatamente o comportamento não-humano que a plataforma detecta.
> `docker compose ps beat` deve mostrar **um** container.

### 3.4 Reconciliar tasks presas em `dispatched`

Reconciliação é **consulta ao orchestrator**, nunca suposição.

```bash
PSQL="docker compose exec -T postgres psql -U olimpo -d olimpo -tA"

$PSQL -c "SELECT id || ' ' || external_task_id
          FROM tasks
          WHERE status IN ('dispatched','running')
            AND dispatched_at < now() - interval '15 minutes'
            AND external_task_id IS NOT NULL;" \
| while read -r task_id ext_id; do
    resp=$(curl -fsS -m 10 -H "Authorization: Bearer ${ORCHESTRATOR_TOKEN}" \
             "${ORCHESTRATOR_URL}/v1/tasks/${ext_id}" 2>/dev/null)
    st=$(echo "$resp" | jq -r '.status // "unreachable"')
    echo "${task_id} ${ext_id} -> ${st}"
  done | tee /tmp/reconciliacao.txt
```

Aplique o desfecho conforme a resposta:

```sql
-- (a) Orchestrator confirma conclusão → o webhook se perdeu.
-- Reprocessar o callback é preferível a marcar 'verified' na mão,
-- porque a criação de `posts` e a memória episódica dependem dele.
-- Só use este UPDATE se o reprocessamento não for possível:
UPDATE tasks
SET status = 'verified', verified_at = now(),
    external_status = 'completed',
    result = result || jsonb_build_object('reconciled_by','runbook fila-travada')
WHERE id = '<task_id>' AND status IN ('dispatched','running');

-- (b) Orchestrator não conhece a task (404) → falhou sem rastro
UPDATE tasks
SET status = 'failed',
    last_error = 'orchestrator nao conhece a task (reconciliacao)',
    attempts = attempts + 1
WHERE id = '<task_id>';

-- (c) Presa há mais de 2 h e o orchestrator está inacessível
UPDATE tasks
SET status = 'failed',
    last_error = 'timeout de verificacao (> 2h, orchestrator inacessivel)',
    attempts = attempts + 1
WHERE status IN ('dispatched','running')
  AND dispatched_at < now() - interval '2 hours';
```

> **A ação pode ter acontecido de verdade.** Marcar `failed` e reagendar arrisca **publicar duas vezes**. Para `publish_*`, verifique o perfil real antes de reagendar. Detalhe em [orchestrator-fora-do-ar.md §5](orchestrator-fora-do-ar.md#5-reconciliação).

### 3.5 Liberar locks órfãos

```bash
# Sempre confirme antes que não há task ativa para aquela conta
docker compose exec worker celery -A olimpo.workers.app inspect active \
  | grep -i '<account_id>'

# Só então libere o lock específico
docker compose exec redis redis-cli DEL "olimpo:lock:account:<account_id>"
```

Limpeza em massa apenas com o worker parado — caso contrário você remove locks de tasks que estão executando agora:

```bash
docker compose stop worker
docker compose exec redis redis-cli --scan --pattern 'olimpo:lock:*' \
  | xargs -r -n 50 docker compose exec -T redis redis-cli DEL
docker compose start worker
```

### 3.6 Limpar tasks expiradas com segurança

> **Regra de ouro:** task expirada é **marcada**, nunca deletada. `tasks` é registro histórico e alimenta o post-mortem. `DELETE FROM tasks` também derruba a referência de `posts.task_id`.

**Passo 1 — inspecionar antes de tocar:**

```sql
SELECT t.type, count(*),
       min(t.expires_at) AS mais_antiga,
       count(DISTINCT t.account_id) AS contas
FROM tasks t
WHERE t.status IN ('pending','scheduled')
  AND t.expires_at IS NOT NULL
  AND t.expires_at < now()
GROUP BY t.type ORDER BY 2 DESC;
```

**Passo 2 — expirar o que tem validade vencida:**

```sql
BEGIN;

UPDATE tasks
SET status = 'expired',
    last_error = coalesce(last_error || ' | ', '') ||
                 'expirada em ' || to_char(now(),'YYYY-MM-DD HH24:MI')
WHERE status IN ('pending','scheduled')
  AND expires_at IS NOT NULL
  AND expires_at < now();

-- Confira o número antes de confirmar
SELECT count(*) FROM tasks
WHERE status = 'expired' AND updated_at > now() - interval '1 minute';

COMMIT;   -- ou ROLLBACK se o número não fizer sentido
```

**Passo 3 — obsoletas sem `expires_at`.** Publicar hoje um post planejado para anteontem quebra a narrativa da conta:

```sql
BEGIN;

UPDATE tasks
SET status = 'cancelled',
    last_error = 'obsoleta: agendada ha mais de 24h (limpeza de fila)'
WHERE status IN ('pending','scheduled')
  AND scheduled_for < now() - interval '24 hours'
  AND type IN ('publish_post','publish_story','publish_reels',
               'comment','reply_comment','send_dm','like','share');

COMMIT;
```

**O que NÃO cancelar por idade:**

| Tipo | Motivo |
|---|---|
| `collect_metrics` | Coletar métrica atrasada continua útil |
| `collect_interactions` | Idem — a interação existe e precisa de resposta |
| `reflect` | Reflexão atrasada ainda consolida a memória do dia |
| `plan_day` de hoje | Ainda é o plano válido |
| `monitor_check` | Reagendar, não cancelar (o monitor precisa continuar vigiando) |

**Passo 4 — registrar a limpeza:**

```sql
INSERT INTO events (type, detail, trace_id)
VALUES ('maintenance.queue_cleanup',
        jsonb_build_object('actor','<seu.nome>',
          'expired', <n>, 'cancelled', <n>,
          'runbook','fila-travada.md',
          'human','Limpeza de fila após travamento de <duração>.'),
        'incident-' || to_char(now(),'YYYYMMDDHH24MI'));
```

### 3.7 Espalhar o backlog restante

Após destravar, o que sobrou vai tentar sair **tudo de uma vez**. Isso é o padrão de farm.

```sql
WITH ordenadas AS (
  SELECT id, row_number() OVER (PARTITION BY account_id
                                ORDER BY priority, scheduled_for) AS n
  FROM tasks
  WHERE status IN ('pending','scheduled') AND scheduled_for <= now()
)
UPDATE tasks t
SET scheduled_for = now() + (o.n * interval '15 minutes')
                          + (random() * interval '20 minutes')
FROM ordenadas o
WHERE t.id = o.id;

-- Verificação: nenhuma conta acima do teto em nenhuma hora
SELECT a.handle, date_trunc('hour', t.scheduled_for) AS hora, count(*)
FROM tasks t JOIN accounts a ON a.id = t.account_id
WHERE t.status IN ('pending','scheduled')
  AND t.scheduled_for BETWEEN now() AND now() + interval '12 hours'
GROUP BY a.handle, hora
HAVING count(*) > 12
ORDER BY 3 DESC;
```

A verificação deve voltar **vazia**.

### 3.8 Confirmar que destravou

```sql
-- A idade da task mais antiga deve estar caindo. Rode duas vezes, com 2 min de intervalo.
SELECT round(extract(epoch FROM (now() - min(scheduled_for)))/60) AS atraso_min,
       count(*) AS vencidas
FROM tasks WHERE status IN ('pending','scheduled') AND scheduled_for <= now();

-- Tasks verificadas nos últimos 10 minutos: deve ser > 0
SELECT count(*) FROM tasks WHERE verified_at > now() - interval '10 minutes';
```

```bash
for q in realtime dispatch production maintenance; do
  printf '%-12s %s\n' "$q" "$(docker compose exec -T redis redis-cli LLEN "$q")"
done
```

---

## 4. Prevenção

| Medida | Detalhe |
|---|---|
| **`expires_at` em toda task com validade** | Fofoca e clipagem: obrigatório. Sem ele, todo backlog vira decisão manual |
| **Varredor de reconciliação** | Job em `maintenance` a cada 10 min: tasks em `dispatched` há > 15 min consultam o orchestrator automaticamente. É a dívida nº 1 da Fase 0 |
| **TTL em todo lock Redis** | Nenhum lock sem expiração. `ttl=-1` é bug, não configuração |
| **Timeout em chamada de LLM** | Chamada sem timeout ocupa slot do worker para sempre |
| **`task_time_limit` no Celery** | Soft/hard limit por task — um travamento não deve derrubar o pool |
| **Worker dedicado a `realtime`** | Interação nunca compete com ideação pelo mesmo pool |
| **Teto de despacho no dispatcher** | Recusar despacho acima de `ACCOUNT_MAX_ACTIONS_PER_HOUR`, tornando a §3.7 rede de segurança |
| **Alerta antes da dor** | `olimpo_oldest_pending_task_age_seconds > 900` é P1 — bem antes de a conta ficar silenciosa |
| **Monitorar `beat` como serviço crítico** | Beat parado é silencioso: nada quebra, nada acontece |
| **Rever `WORKER_CONCURRENCY`** | Calibrar contra a latência real dos Mac minis, não pelo default |

---

## 5. Escalação

| Quando | Para quem | Prazo |
|---|---|---|
| Travamento < 30 min, causa identificada e resolvida | Registrar; sem escalação | — |
| Travamento > 1 h | Admin do Olimpo | 1 h |
| Travamento > 1 h em conta de fofoca (furos morrendo) | Admin + responsável editorial | Imediato |
| Reincidência (3ª vez na mesma semana) | Responsável técnico — é problema de arquitetura, não de plantão | 24 h |
| Causa raiz no orchestrator | Responsável pelo android-orchestrator | Imediato |
| Causa raiz nos Mac minis | [llm-saturada.md](llm-saturada.md) + responsável de infra | 1 h |
| Suspeita de despacho duplicado (dois `beat`) | **Kill switch** + admin — risco de ban | Imediato |
| Backlog > 500 tasks | Admin decide o que descartar antes de destravar | Antes de destravar |

---

**Ver também:** [orchestrator-fora-do-ar.md](orchestrator-fora-do-ar.md) · [llm-saturada.md](llm-saturada.md) · [kill-switch.md](kill-switch.md) · [11-observabilidade.md](../11-observabilidade.md)
