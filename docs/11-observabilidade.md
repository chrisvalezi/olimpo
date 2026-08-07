# 11 — Observabilidade

> **A pergunta que este documento existe para responder:**
> *"Por que a conta @luna.corre postou **isso**, às **19h04**, no dia **12/03**?"*
>
> Se o sistema não consegue responder isso em menos de um minuto, com SQL, sem adivinhação — a observabilidade está quebrada, independentemente de quantos dashboards existam.

O Olimpo decide sozinho. Um sistema que decide sozinho e não registra o **porquê** de cada decisão não é autônomo: é irresponsável. Toda linha deste documento serve a uma coisa — tornar cada ação da frota **reconstruível depois do fato**.

---

## 1. Filosofia

### 1.1 Três camadas, uma verdade

| Camada | Onde vive | Retenção | Serve para |
|---|---|---|---|
| **`events`** (Postgres) | tabela `events`, append-only | **Permanente** | Auditoria, forense, "por que postou isso" |
| **Logs estruturados** (structlog → stdout → `docker compose logs`) | container | 7–14 dias | Depuração de execução, stack traces |
| **Métricas** (Prometheus) | `/metrics` da API e do worker | 15–90 dias | Tendência, alerta, capacidade |

A regra de precedência é rígida: **quando os três discordam, `events` está certo.** Métrica é amostra agregada, log é volátil, `events` é o registro contábil da operação.

### 1.2 A regra do `trace_id`

Um `trace_id` nasce em **uma** de três origens e não muda até o fim:

| Origem | Quem cria | Formato sugerido |
|---|---|---|
| Tick do agente (`plan_day`) | `olimpo.agents.loop` | `loop-<account_id_curto>-<YYYYMMDD>` |
| Campanha | `olimpo.campaigns` | `camp-<campaign_id_curto>` |
| Ação humana no painel | `olimpo.api` | `human-<uuid4_curto>` |
| Monitor (Fase 4) | `olimpo.monitors` | `mon-<monitor_id_curto>-<epoch>` |

O `trace_id` é propagado por `contextvars`, atravessa a fila Celery no header da mensagem, vai no header HTTP `X-Trace-Id` para o android-orchestrator e para a máquina de influencers, e volta no callback do webhook. **Três tabelas o carregam como coluna de primeira classe:** `events.trace_id`, `llm_calls.trace_id` e — por herança, via `tasks.id` — todo o resto.

> **Invariante:** nenhum `INSERT` em `events` ou `llm_calls` sem `trace_id`. Se o código não tem um, ele está rodando fora do loop e isso é um bug, não um caso especial.

### 1.3 O que obrigatoriamente vira evento

Cada uma das 8 etapas do loop emite **pelo menos um** evento. O `type` segue `<domínio>.<fato>` em minúsculas:

```
loop.plan.started          loop.plan.completed
loop.source.proposed       loop.produce.completed
loop.qa.passed             loop.qa.failed
loop.dispatch.skipped      loop.dispatch.sent
loop.verify.confirmed      loop.verify.timeout
loop.record.written        loop.reflect.completed

task.created               task.dispatched         task.verified
task.failed                task.expired            task.cancelled
task.retry.scheduled

guardrail.blocked          guardrail.flagged       guardrail.kill
approval.requested         approval.decided
kill_switch.blocked        kill_switch.engaged     kill_switch.released

campaign.recon.captured    campaign.recon.analyzed
campaign.participant.selected   campaign.participant.skipped

account.status.changed     account.autonomy.changed
persona.version.bumped
llm.budget.exceeded        llm.fallback.opened
orchestrator.circuit.open  orchestrator.circuit.closed
monitor.hit                monitor.triggered
```

O `detail jsonb` carrega o **motivo legível por humano**, não só ids. Um `loop.dispatch.skipped` com `detail = {"reason": "rate_limit"}` é inútil; o correto é:

```json
{
  "reason": "rate_limit",
  "limit": "ACCOUNT_MAX_ACTIONS_PER_HOUR",
  "limit_value": 12,
  "actions_last_hour": 12,
  "rescheduled_for": "2026-03-12T20:15:00-03:00",
  "human": "Luna já fez 12 ações na última hora; publicação adiada 71 min."
}
```

---

> ⚠️ **O que esta página observa são as NOSSAS tabelas.** Gasto que acontece fora delas é invisível por construção — foi assim que uma sonda de saúde do gateway queimou US$ 36 em 43 horas sem produzir um único job, asset ou registro em `llm_calls`. Ver [INC-0001](incidentes/0001-sonda-de-saude-gerando-imagem.md).
>
> Teto de gasto e alerta **no provider** são a única defesa que não depende de o custo passar por código nosso.

## 2. Logs estruturados (structlog)

### 2.1 Campos obrigatórios em **todo** log

Um processador do structlog injeta estes campos a partir dos `contextvars`. Log emitido sem eles é bug de instrumentação:

| Campo | Tipo | Sempre? | Origem |
|---|---|---|---|
| `timestamp` | ISO-8601 UTC | Sim | processador |
| `level` | `debug\|info\|warning\|error\|critical` | Sim | structlog |
| `event` | string curta e estável (não interpolada) | Sim | chamador |
| `trace_id` | string | Sim | `contextvars` |
| `service` | `api\|worker\|beat\|dispatcher` | Sim | env |
| `olimpo_env` | `development\|staging\|production` | Sim | `OLIMPO_ENV` |
| `account_id` | uuid | Sempre que houver conta no contexto | `contextvars` |
| `task_id` | uuid | Sempre que houver task | `contextvars` |
| `phase` | `plan\|source\|produce\|qa\|dispatch\|verify\|record\|reflect` | Dentro do loop | `contextvars` |
| `campaign_id` | uuid | Em contexto de campanha | `contextvars` |
| `queue` | `realtime\|dispatch\|production\|maintenance` | No worker | Celery |
| `duration_ms` | int | Em qualquer log de conclusão | chamador |

O `event` é **chave de agrupamento**, não frase. Certo: `event="dispatch.rate_limited"`. Errado: `event=f"Luna atingiu o limite às {hora}"`.

### 2.2 Formato

- `OLIMPO_ENV=development` → renderer colorido, legível no `make logs`.
- `OLIMPO_ENV=staging|production` → **JSON puro**, uma linha por evento, para ingestão.

### 2.3 Consulta rápida

```bash
# Tudo de um trace específico, em todos os serviços
docker compose logs --no-log-prefix --since 6h api worker beat \
  | grep '"trace_id":"loop-a3f9b21c-20260312"' | jq .

# Só os erros da última hora, agrupados por evento
docker compose logs --no-log-prefix --since 1h worker \
  | jq -r 'select(.level=="error") | .event' | sort | uniq -c | sort -rn

# Rastro de uma conta específica dentro do loop
docker compose logs --no-log-prefix --since 24h worker \
  | jq -c 'select(.account_id=="a3f9b21c-0000-4000-8000-000000000001")
           | {timestamp, phase, event, duration_ms}'
```

### 2.4 Nunca vai para o log

Ver [12-seguranca.md](12-seguranca.md#13-o-que-nunca-vai-para-o-log). Resumo: tokens, `LITELLM_MASTER_KEY`, `ORCHESTRATOR_WEBHOOK_SECRET`, credenciais de conta, prompt completo de LLM (vai truncado, com hash) e o corpo íntegro de DMs de terceiros.

---

## 3. Métricas Prometheus

Prefixo `olimpo_`. Expostas em `GET /metrics` da API e em um endpoint próprio do worker (porta interna 9100, não publicada no host). Scrape a cada 15 s via `ops/prometheus/prometheus.yml`.

Métricas derivadas do banco (`accounts`, `tasks`, `approvals`) são recalculadas por um job `metrics_refresh` na fila `maintenance` a cada 30 s e servidas como gauges — **não** consultadas no handler do `/metrics`.

### 3.1 Tasks e pipeline

| Métrica | Tipo | Labels | Nota |
|---|---|---|---|
| `olimpo_tasks_total` | counter | `type`, `status`, `origin` | Incrementa a cada **transição** de `tasks.status`. `type` = `tasks.type`, `origin` = `tasks.origin` (`manager\|self\|human\|campaign\|monitor`) |
| `olimpo_tasks_current` | gauge | `status` | Snapshot de `SELECT status, count(*) FROM tasks GROUP BY status` |
| `olimpo_task_dispatch_to_verified_seconds` | histogram | `type` | `verified_at - dispatched_at`. Buckets: `10, 30, 60, 120, 300, 600, 1800, 3600` |
| `olimpo_task_schedule_lag_seconds` | histogram | `type`, `priority_band` | `dispatched_at - scheduled_for`. Mede atraso do dispatcher. Buckets: `5, 15, 30, 60, 300, 900` |
| `olimpo_task_failures_total` | counter | `type`, `reason` | `reason` derivado de `tasks.last_error` normalizado (`timeout`, `device_offline`, `login_required`, `action_blocked`, `orchestrator_5xx`, `unknown`) |
| `olimpo_task_attempts` | histogram | `type` | `tasks.attempts` no momento do desfecho. Detecta tipos que só passam na 3ª tentativa |
| `olimpo_task_expired_total` | counter | `type` | Task que passou de `expires_at` — em fofoca, é furo perdido |
| `olimpo_oldest_pending_task_age_seconds` | gauge | `priority_band` | **A métrica mais importante do pipeline.** `now() - min(scheduled_for)` entre `status IN ('pending','scheduled')` e `scheduled_for <= now()` |
| `olimpo_tasks_stuck_dispatched` | gauge | — | Tasks em `dispatched`/`running` há mais de 15 min sem `verified_at` |

`priority_band` agrupa `tasks.priority` (menor = mais urgente) em `p0` (< 50), `p1` (50–99), `p2` (100–199), `p3` (≥ 200) — cardinalidade controlada.

### 3.2 Filas Celery / Redis

| Métrica | Tipo | Labels | Nota |
|---|---|---|---|
| `olimpo_queue_depth` | gauge | `queue` | `LLEN` da lista Redis de cada fila (`realtime`, `dispatch`, `production`, `maintenance`) |
| `olimpo_queue_oldest_message_age_seconds` | gauge | `queue` | Idade da mensagem no topo da fila |
| `olimpo_celery_workers_online` | gauge | — | De `celery inspect ping` |
| `olimpo_celery_tasks_active` | gauge | `queue` | Tasks em execução no momento |
| `olimpo_celery_task_runtime_seconds` | histogram | `name`, `queue` | Tempo de execução da task Celery (≠ task do domínio) |

### 3.3 LLM (espelha a tabela `llm_calls`)

| Métrica | Tipo | Labels | Nota |
|---|---|---|---|
| `olimpo_llm_calls_total` | counter | `backend`, `model`, `purpose`, `success` | `backend` = `mac_mini_1\|mac_mini_2\|anthropic\|openai` |
| `olimpo_llm_latency_seconds` | histogram | `backend`, `model`, `purpose` | De `llm_calls.latency_ms`. Buckets: `0.5, 1, 2, 5, 10, 20, 45, 90, 180` |
| `olimpo_llm_tokens_total` | counter | `backend`, `model`, `direction` | `direction` = `prompt\|output` |
| `olimpo_llm_cost_usd_total` | counter | `backend`, `model` | De `llm_calls.cost_usd` |
| `olimpo_llm_daily_budget_ratio` | gauge | — | Gasto do dia ÷ `LLM_DAILY_BUDGET_USD`. Alerta em 0.8 |
| `olimpo_llm_fallback_total` | counter | `from_backend`, `to_backend`, `reason` | Quando o LiteLLM sai do Mac mini para API externa |
| `olimpo_llm_errors_total` | counter | `backend`, `error_class` | `timeout\|context_length\|rate_limit\|connection\|invalid_response` |
| `olimpo_llm_queue_wait_seconds` | histogram | `backend` | Espera antes do primeiro token — sinal de saturação do Mac mini |

### 3.4 Guardrails e governança

| Métrica | Tipo | Labels | Nota |
|---|---|---|---|
| `olimpo_guardrail_violations_total` | counter | `rule`, `severity`, `action_taken` | Espelha `guardrail_violations`. `severity` = `info\|warn\|block\|kill` |
| `olimpo_qa_results_total` | counter | `check`, `result` | `check` = `visual\|textual\|brand_safety\|legal\|persona_drift\|claim_status\|rights` |
| `olimpo_approvals_open` | gauge | `subject_type` | `approvals` com `decided_at IS NULL` |
| `olimpo_approval_wait_seconds` | histogram | `subject_type` | `decided_at - requested_at`. Em fofoca, o gargalo é humano |
| `olimpo_approval_decisions_total` | counter | `decision`, `sampled` | `approved\|rejected\|edited`; `sampled=true` é a amostragem de L2 |
| `olimpo_approval_rejection_ratio` | gauge | `account_id` | Rejeições ÷ decisões, janela 7 d. **Proxy direto de deriva de persona** |
| `olimpo_kill_switch_active` | gauge | `scope` | `scope=global` (de `GLOBAL_KILL_SWITCH`) e `scope=account` (nº de contas `suspended`) |
| `olimpo_dispatch_enabled` | gauge | — | 0/1 de `DISPATCH_ENABLED`. Estar em 0 sem querer já custou uma semana |

### 3.5 Frota de contas

| Métrica | Tipo | Labels | Nota |
|---|---|---|---|
| `olimpo_accounts_total` | gauge | `status`, `archetype`, `autonomy` | `status` do enum `account_status` |
| `olimpo_account_actions_last_hour` | gauge | `account_id` | Contra `ACCOUNT_MAX_ACTIONS_PER_HOUR`. **Só para frotas < 100 contas** — acima disso vira histograma |
| `olimpo_account_actions_ratio` | histogram | `archetype` | Ações/hora ÷ teto. Substitui a métrica acima em escala |
| `olimpo_account_silence_seconds` | gauge | `account_id` | `now() - last_action_at`. Conta viva e muda por 48 h é sintoma |
| `olimpo_followers` | gauge | `account_id` | De `accounts.followers`. Queda abrupta = shadowban ou purge |
| `olimpo_posts_published_total` | counter | `archetype`, `kind` | De `posts` |
| `olimpo_account_bans_total` | counter | `platform`, `archetype` | Transição para `status='lost'`. Contador que deve ficar em zero |

### 3.6 Integrações externas

| Métrica | Tipo | Labels | Nota |
|---|---|---|---|
| `olimpo_integration_requests_total` | counter | `service`, `endpoint`, `status_class` | `service` = `orchestrator\|persona_engine\|android_manager\|media_forge` |
| `olimpo_integration_latency_seconds` | histogram | `service`, `endpoint` | |
| `olimpo_circuit_breaker_state` | gauge | `service` | `0=fechado, 1=meio-aberto, 2=aberto` |
| `olimpo_webhook_received_total` | counter | `result` | `ok\|invalid_hmac\|duplicate\|unknown_task` |
| `olimpo_webhook_lag_seconds` | histogram | — | Entre `dispatched_at` e a chegada do callback |

### 3.7 Monitores (Fase 4 — dormente)

| Métrica | Tipo | Labels | Nota |
|---|---|---|---|
| `olimpo_monitor_checks_total` | counter | `monitor_id`, `result` | `ok\|error\|skipped` |
| `olimpo_monitor_hits_total` | counter | `monitor_id`, `triggered` | Espelha `monitor_hits.triggered` |
| `olimpo_monitor_trigger_ratio` | gauge | `monitor_id` | Hits que dispararam ÷ hits. Ratio ~1.0 = regra frouxa demais |
| `olimpo_monitor_check_lag_seconds` | gauge | `monitor_id` | `now() - next_check_at` quando vencido |
| `olimpo_monitor_consecutive_errors` | gauge | `monitor_id` | De `monitors.consecutive_errors` |
| `olimpo_time_to_publish_seconds` | histogram | `monitor_id` | **Métrica-chave da fofoca:** de `monitor_hits.detected_at` até `posts.published_at` |

---

## 4. Dashboards Grafana

Provisionados em `ops/grafana/provisioning/`. Sobem com `make up-obs` (perfil `observability`), Grafana em `http://localhost:3001`.

### 4.1 `Olimpo / NOC` — a tela do plantão

A única aberta em um monitor o dia inteiro. Se algo está errado, aparece aqui.

- **Faixa de estado** (stat, grande): `GLOBAL_KILL_SWITCH`, `DISPATCH_ENABLED`, contas `active` / `suspended` / `lost`, workers online.
- **Idade da task mais antiga pendente** (gauge por `priority_band`, com limiares em 5/15 min).
- **Profundidade das 4 filas** (timeseries empilhado).
- **Taxa de falha de task** (%, janela 15 min) e **tasks presas em `dispatched`**.
- **Estado dos circuit breakers** (state timeline, uma linha por serviço).
- **Violações de guardrail `severity IN ('block','kill')`** (últimas 24 h, tabela clicável).
- **Aprovações abertas** e a mais antiga.
- **Gasto de LLM do dia vs. `LLM_DAILY_BUDGET_USD`** (barra).

### 4.2 `Olimpo / Pipeline de Tasks`

- Funil por status: `pending → scheduled → dispatched → running → verified` (últimas 24 h).
- `olimpo_task_schedule_lag_seconds` p50/p95/p99 por `priority_band`.
- `olimpo_task_dispatch_to_verified_seconds` p50/p95 por `type`.
- Falhas por `reason` (barras empilhadas) e por `type`.
- Distribuição de `attempts` até o sucesso.
- Tasks expiradas por `type` — em fofoca, cada barra é um furo perdido.
- Latência de webhook e `olimpo_webhook_received_total{result!="ok"}`.

### 4.3 `Olimpo / Frota de Contas`

- Heatmap **hora do dia × conta** de ações executadas. **É o painel anti-detecção:** listras verticais alinhadas entre contas = padrão coordenado visível a olho nu, e o que o Instagram enxerga.
- Ações/hora por conta contra `ACCOUNT_MAX_ACTIONS_PER_HOUR`.
- Seguidores por conta (7 d/30 d) com anotação de eventos `account.status.changed`.
- Silêncio por conta (`olimpo_account_silence_seconds`), ordenado desc.
- Contas por `status` e por `autonomy` ao longo do tempo.
- Tabela de contas em `warming` com dias desde `warmup_started_at`.
- **Grafo de interação interno**: quantas ações de contas da casa miram contas da casa (rastro de farm).

### 4.4 `Olimpo / LLM e Custo`

- Chamadas/min por `backend` (os 2 Mac minis vs. APIs) — área empilhada.
- Latência p50/p95/p99 por `backend` e por `purpose`. **A divergência entre `mac_mini_1` e `mac_mini_2` é o alerta precoce de saturação.**
- `olimpo_llm_queue_wait_seconds` p95 por backend.
- Custo acumulado do dia/mês, e custo por `purpose` (onde o dinheiro vai: `plan_day`? `recon_analysis`?).
- Taxa de fallback para API externa, com `reason`.
- Erros por `error_class` e por backend.
- Tokens/s efetivo por modelo (para comparar com o benchmark da Fase 0).

### 4.5 `Olimpo / Governança e Guardrails`

- Violações por `rule` (top 10) e por `severity`, 7 d.
- `action_taken`: `blocked` vs. `flagged` vs. `suspended`.
- QA: taxa de aprovação por `check`, por `archetype`.
- Taxa de aprovação humana e tempo de espera (p50/p95) por `subject_type`.
- **Taxa de rejeição por conta** — sobe = persona derivando ou prompt degradado.
- Amostragem de L2: nº revisado ÷ nº publicado, contra `L2_SAMPLING_RATE`.
- Distribuição de `content_items.claim_status` e `rights_status` do que foi publicado (Fases 4/5).

### 4.6 `Olimpo / Campanhas`

- Timeline de `campaign_participants.slot_at` por campanha — **inspeção visual do jitter**. Slots agrupados = `spread_policy` mal aplicada.
- Participação: contas elegíveis vs. selecionadas vs. `skipped` (com `skip_reason`).
- Tempo de recon: `captured_at → analyzed_at`.
- `safety_verdict` do recon (`safe\|risky\|blocked`).
- Conclusão da campanha: participantes com task `verified` ÷ total.
- Delta de métricas dos posts da campanha vs. baseline da conta.

---

## 5. Alertas

Regras em `ops/prometheus/rules/olimpo.yml`. **Todo alerta tem dono e ação** — alerta sem ação definida é ruído e será removido.

Severidades: **P1** acorda alguém · **P2** olhar no mesmo dia útil · **P3** revisar na semana.

| Alerta | Condição | Sev | Ação esperada |
|---|---|---|---|
| `ContaPerdida` | `increase(olimpo_account_bans_total[1h]) > 0` | **P1** | [runbooks/conta-banida.md](runbooks/conta-banida.md) — contenção imediata das contas irmãs |
| `ConteudoImproprioPublicado` | `increase(olimpo_guardrail_violations_total{severity="kill"}[5m]) > 0` | **P1** | [runbooks/conteudo-improprio-publicado.md](runbooks/conteudo-improprio-publicado.md) — remoção em minutos |
| `MultiplasContasSuspensas` | `olimpo_accounts_total{status="suspended"} > 2` | **P1** | Suspeita de detecção de padrão coordenado. Acionar kill switch global e revisar `spread_policy` |
| `OrchestratorForaDoAr` | `olimpo_circuit_breaker_state{service="orchestrator"} == 2` por 5 min | **P1** | [runbooks/orchestrator-fora-do-ar.md](runbooks/orchestrator-fora-do-ar.md) |
| `WorkerMorto` | `olimpo_celery_workers_online == 0` por 2 min | **P1** | `docker compose ps worker` → `restart`. Nada é executado enquanto durar |
| `PostgresIndisponivel` | `up{job="olimpo-api"} == 0` ou `/health` com `postgres != ok` por 2 min | **P1** | Verificar `pgdata`, espaço em disco, [runbooks/restaurar-backup.md](runbooks/restaurar-backup.md) se corrompido |
| `FilaTravada` | `olimpo_oldest_pending_task_age_seconds{priority_band=~"p0\|p1"} > 900` | **P1** | [runbooks/fila-travada.md](runbooks/fila-travada.md) |
| `TasksPresasEmDispatched` | `olimpo_tasks_stuck_dispatched > 5` por 10 min | **P2** | Reconciliação — [runbooks/orchestrator-fora-do-ar.md](runbooks/orchestrator-fora-do-ar.md#5-reconciliação) |
| `TaxaDeFalhaAlta` | `sum(rate(olimpo_task_failures_total[15m])) / sum(rate(olimpo_tasks_total{status="verified"}[15m])) > 0.20` | **P2** | Identificar `reason` dominante no dashboard Pipeline; se `device_offline`, é frota, não Olimpo |
| `LLMSaturada` | `histogram_quantile(0.95, olimpo_llm_latency_seconds{backend=~"mac_mini.*"}) > 45` por 10 min | **P2** | [runbooks/llm-saturada.md](runbooks/llm-saturada.md) — degradar produção, preservar `realtime` |
| `MacMiniMudo` | `rate(olimpo_llm_calls_total{backend="mac_mini_2"}[10m]) == 0` enquanto `mac_mini_1 > 0` | **P2** | Um dos dois caiu; LiteLLM está mascarando. Checar Ollama no Mac |
| `OrcamentoLLMEstourando` | `olimpo_llm_daily_budget_ratio > 0.8` | **P2** | Verificar `purpose` que puxa custo; considerar cortar fallback externo |
| `OrcamentoLLMEstourado` | `olimpo_llm_daily_budget_ratio >= 1.0` | **P2** | O gateway já cortou. Decidir: elevar teto ou operar só local pelo resto do dia |
| `GuardrailBloqueandoDemais` | `rate(olimpo_guardrail_violations_total{severity="block"}[1h]) > 5` | **P2** | Guardrail calibrado errado **ou** LLM fora da persona. Ver `guardrail_violations.rule` dominante |
| `PersonaDerivando` | `olimpo_approval_rejection_ratio > 0.3` por 24 h, por conta | **P2** | Revisar Persona Card e prompts; rebaixar autonomia da conta até normalizar |
| `AprovacoesEmpilhando` | `olimpo_approvals_open > 20` ou `olimpo_approval_wait_seconds{quantile="0.5"} > 14400` | **P2** | Fila humana é gargalo. Em fofoca, isso mata a latência do furo |
| `ContaSilenciosa` | `olimpo_account_silence_seconds > 172800` para conta `status="active"` | **P2** | Conta ativa muda por 48 h: loop quebrado, device sem login, ou shadowban |
| `QuedaDeSeguidores` | `delta(olimpo_followers[24h]) < -0.05 * olimpo_followers` | **P2** | Purge da plataforma ou shadowban. Pausar a conta e observar 48 h |
| `TasksExpirando` | `increase(olimpo_task_expired_total[1h]) > 3` | **P2** | Pipeline lento demais para a janela. Em fofoca é perda de receita direta |
| `WebhookHMACInvalido` | `increase(olimpo_webhook_received_total{result="invalid_hmac"}[10m]) > 3` | **P2** | Segredo dessincronizado **ou** tentativa de forjar callback. Ver [12-seguranca.md](12-seguranca.md) |
| `DispatchDesligadoEmProducao` | `olimpo_dispatch_enabled == 0 and olimpo_env == "production"` por 30 min | **P2** | Alguém esqueceu `DISPATCH_ENABLED=false`. A frota está parada sem ninguém saber |
| `FilaRealtimeAtrasada` | `olimpo_queue_oldest_message_age_seconds{queue="realtime"} > 120` | **P2** | Interação envelhecendo. Realocar concorrência; ver [runbooks/llm-saturada.md](runbooks/llm-saturada.md) |
| `MonitorComErro` | `olimpo_monitor_consecutive_errors > 5` | **P3** | Coletor queimado ou fonte mudou de layout. Rotacionar `collector_pool` |
| `MonitorRuidoso` | `olimpo_monitor_trigger_ratio > 0.9` por 6 h | **P3** | `trigger_rules` frouxa — tudo virando post |
| `CadenciaAcimaDoNatural` | `olimpo_account_actions_ratio > 0.9` por 3 h | **P3** | Campanha esticando a conta perto do teto. Revisar `CAMPAIGN_MAX_PARTICIPATION` |
| `BackupNaoVerificado` | ausência de métrica `olimpo_backup_last_success_timestamp` recente (> 26 h) | **P2** | [runbooks/restaurar-backup.md](runbooks/restaurar-backup.md) — backup que não roda é backup que não existe |

> **Sobre P1 e o kill switch:** `ContaPerdida`, `ConteudoImproprioPublicado` e `MultiplasContasSuspensas` autorizam **qualquer plantonista** a acionar `GLOBAL_KILL_SWITCH=true` sem aprovação prévia. Parar a frota custa horas de conteúdo; não parar pode custar a operação.

---

## 6. Rastreabilidade: reconstruindo a história de um post

### 6.1 O caminho

```
posts.id
  └─ posts.task_id ──────────────▶ tasks
  └─ posts.content_item_id ──────▶ content_items
                                    └─ day_plan_id ──▶ day_plans (narrative, reasoning)
                                    └─ campaign_id ──▶ campaigns + campaign_recon
  tasks.id ─────────────────────▶ events (task_id)  ──▶ trace_id
                                                        │
       ┌────────────────────────────────────────────────┘
       ▼
  events   WHERE trace_id = ?   ← a linha do tempo completa da decisão
  llm_calls WHERE trace_id = ?  ← todo raciocínio de LLM, com custo e backend
  guardrail_violations          ← o que o QA barrou no caminho
  approvals                     ← quem aprovou, quando, se editou
```

### 6.2 Passo 1 — do post ao `trace_id`

```sql
-- Entrada: o permalink ou o external_id que você viu no Instagram.
SELECT
    p.id              AS post_id,
    p.published_at,
    a.handle,
    a.autonomy,
    ci.id             AS content_item_id,
    ci.caption,
    ci.source_strategy,
    ci.claim_status,
    ci.rights_status,
    ci.reasoning      AS por_que_este_conteudo,
    t.id              AS task_id,
    t.origin          AS quem_pediu,
    t.scheduled_for,
    t.dispatched_at,
    t.verified_at,
    t.external_task_id,
    (SELECT e.trace_id FROM events e
      WHERE e.task_id = t.id AND e.trace_id IS NOT NULL
      ORDER BY e.created_at LIMIT 1) AS trace_id
FROM posts p
JOIN accounts       a  ON a.id  = p.account_id
LEFT JOIN content_items ci ON ci.id = p.content_item_id
LEFT JOIN tasks        t  ON t.id  = p.task_id
WHERE p.permalink = 'https://www.instagram.com/p/DXxxxxxxxxx/';
```

### 6.3 Passo 2 — a linha do tempo completa

```sql
-- Substitua pelo trace_id obtido acima.
\set trace 'loop-a3f9b21c-20260312'

SELECT
    to_char(e.created_at AT TIME ZONE 'America/Sao_Paulo', 'DD/MM HH24:MI:SS') AS quando,
    e.type,
    coalesce(e.detail->>'human', e.detail->>'reason', left(e.detail::text, 120)) AS o_que,
    e.account_id,
    e.task_id
FROM events e
WHERE e.trace_id = :'trace'
ORDER BY e.created_at, e.id;
```

Saída típica de um post bem-comportado — as 8 etapas, em ordem:

```
12/03 06:02:11  loop.plan.started        conta acordou; humor=animada, energia=0.8
12/03 06:02:47  loop.plan.completed      arco "treinando maratona", semana 3 — 1 post + 2 stories
12/03 06:03:02  loop.source.proposed     GeneratedSource: 3 drafts a partir do arco
12/03 06:04:19  loop.produce.completed   caption escolhida (draft 2), 187 caracteres
12/03 06:04:25  loop.qa.passed           visual ok (face_sim=0.94), textual ok, brand_safety ok
12/03 06:04:26  approval.requested       autonomia L1: post auto, sem aprovação
12/03 06:04:30  task.created             publish_post agendada 19:04 (jitter +34min sobre 18:30)
12/03 19:04:03  task.dispatched          external_task_id=orc_7f3a12
12/03 19:05:41  task.verified            orchestrator confirmou; permalink capturado
12/03 19:05:42  loop.record.written      post + memória episódica gravados
12/03 23:50:08  loop.reflect.completed   arco avançado: "primeiro 15k concluído"
```

### 6.4 Passo 3 — o raciocínio da LLM, com custo

```sql
SELECT
    to_char(l.created_at AT TIME ZONE 'America/Sao_Paulo', 'DD/MM HH24:MI:SS') AS quando,
    l.purpose,
    l.model,
    l.backend,
    l.prompt_tokens,
    l.output_tokens,
    l.latency_ms,
    l.cost_usd,
    l.success,
    l.error
FROM llm_calls l
WHERE l.trace_id = :'trace'
ORDER BY l.created_at;
```

Se `backend='anthropic'` aparece onde deveria haver `mac_mini_*`, houve fallback — cruze com o evento `llm.fallback.opened` no mesmo trace.

### 6.5 Passo 4 — o que o QA viu e quem aprovou

```sql
-- Guardrails disparados no caminho deste conteúdo
SELECT gv.created_at, gv.rule, gv.severity, gv.action_taken, gv.detail
FROM guardrail_violations gv
WHERE gv.subject_type = 'content_item'
  AND gv.subject_id = '<content_item_id>'
ORDER BY gv.created_at;

-- Decisão humana (existe em L0/L1, e por amostragem em L2)
SELECT ap.requested_at, ap.decided_at, ap.decision, ap.decided_by,
       ap.sampled, ap.note,
       ap.edited_payload IS NOT NULL AS foi_editado
FROM approvals ap
WHERE ap.subject_type = 'content_item'
  AND ap.subject_id = '<content_item_id>';

-- O relatório de QA íntegro
SELECT jsonb_pretty(qa_report) FROM content_items WHERE id = '<content_item_id>';
```

### 6.6 Passo 5 — o contexto narrativo (o "por quê" de verdade)

```sql
-- O plano do dia que originou o conteúdo
SELECT dp.plan_date, dp.narrative, dp.reasoning, jsonb_pretty(dp.intents)
FROM content_items ci
JOIN day_plans dp ON dp.id = ci.day_plan_id
WHERE ci.id = '<content_item_id>';

-- As memórias que provavelmente entraram no prompt do PLAN
SELECT m.kind, m.importance, m.occurred_at, left(m.content, 160) AS memoria
FROM memories m
WHERE m.account_id = '<account_id>'
  AND m.last_used_at::date = '2026-03-12'
ORDER BY m.importance DESC, m.occurred_at DESC
LIMIT 20;

-- O arco ativo naquele dia
SELECT sa.title, sa.summary, jsonb_pretty(sa.state), sa.starts_at, sa.ends_at
FROM story_arcs sa
WHERE sa.account_id = '<account_id>'
  AND sa.starts_at <= '2026-03-12'
  AND (sa.ends_at IS NULL OR sa.ends_at >= '2026-03-12');
```

### 6.7 Consulta reversa: da campanha a tudo que ela causou

```sql
SELECT
    c.name,
    a.handle,
    cp.slot_at,
    cp.skipped,
    cp.skip_reason,
    left(cp.local_adaptation, 90) AS adaptacao,
    t.status  AS task_status,
    t.verified_at,
    p.permalink
FROM campaigns c
JOIN campaign_participants cp ON cp.campaign_id = c.id
JOIN accounts a  ON a.id = cp.account_id
LEFT JOIN tasks t ON t.campaign_id = c.id AND t.account_id = cp.account_id
LEFT JOIN posts p ON p.task_id = t.id
WHERE c.id = '<campaign_id>'
ORDER BY cp.slot_at;
```

> **Teste de espalhamento:** se os `slot_at` desta consulta cabem numa janela menor que `CAMPAIGN_JITTER_MIN_MINUTES × (nº de participantes − 1)`, a `spread_policy` não foi respeitada. Isso é um bug **P1** — é exatamente o padrão que a plataforma detecta.

---

## 7. Higiene e retenção

| Tabela | Crescimento | Política |
|---|---|---|
| `events` | ~200–800 linhas/conta/dia | **Nunca deletar.** Particionar por mês a partir de 50 M de linhas; mover partições > 12 meses para armazenamento frio |
| `llm_calls` | ~30–100/conta/dia | Manter 180 d íntegro; agregar diário por (`backend`, `model`, `purpose`) e podar o detalhe |
| `post_metrics` | 1 linha/post/coleta | Manter granular 30 d; depois 1 snapshot/dia por post |
| `ingested_items` | alto na Fase 4 | Podar `consumed_by IS NULL` com > 30 d |
| `monitor_hits` | muito alto na Fase 4 | Manter `triggered = true` permanente; `false` por 30 d |
| `interactions` | médio | Ver política de dados pessoais em [12-seguranca.md](12-seguranca.md#4-dados-de-terceiros) |
| `tasks` | médio | Manter; `result` de tasks `verified` > 90 d pode ser truncado |

Job `retention_sweep` na fila `maintenance`, diário, fora do horário de pico da frota. **Ele nunca toca em `events`, `memories`, `story_arcs` nem `persona_versions`.**

---

## 8. Verificação de sanidade

Antes de declarar a observabilidade pronta em qualquer fase:

- [ ] Todo evento em `events` das últimas 24 h tem `trace_id` não nulo.
      `SELECT count(*) FROM events WHERE trace_id IS NULL AND created_at > now() - interval '24 hours'` → **0**
- [ ] Todo `llm_calls` das últimas 24 h tem `trace_id` que existe em `events`.
- [ ] Um post escolhido ao acaso é reconstruível pela seção 6 em < 2 min, sem ler código.
- [ ] `/metrics` da API responde < 200 ms (métricas de banco vêm do cache do `metrics_refresh`).
- [ ] Cada alerta P1 foi disparado ao menos uma vez em staging e levou a um runbook que resolveu.
- [ ] Nenhum log em `production` contém `ORCHESTRATOR_TOKEN`, `LITELLM_MASTER_KEY` ou `POSTGRES_PASSWORD`.
      `docker compose logs --since 24h | grep -cE 'sk-|Bearer [A-Za-z0-9]{12}'` → **0**
- [ ] Dashboard `Olimpo / NOC` carrega em < 5 s com a frota inteira.

---

**Ver também:** [08 — Guardrails e autonomia](08-guardrails-e-autonomia.md) · [12 — Segurança](12-seguranca.md) · [Runbooks](runbooks/)
