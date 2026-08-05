# Fase 0 — Fundação

**Duração estimada:** 1–2 semanas · **Depende de:** nada · **Entrega para:** [Fase 1](fase-1-primeira-influencer.md)

---

## 1. Resumo

Levanta o esqueleto executável do Olimpo: stack Docker completa, schema `0001_initial.sql` aplicado, LLM Gateway roteando para os Mac minis, e os clientes das três APIs externas (android-orchestrator, máquina de influencers, android-manager) atrás de uma interface única com **modo dry-run**. Ao final, uma task `publish_post` percorre `PLAN → … → RECORD` inteira sem tocar em nenhum device real.

Nenhuma inteligência de persona nesta fase. O que se prova aqui é **encanamento**: o dado entra, atravessa o sistema, vira evento auditável e sai gravado em disco.

---

## 2. Pré-requisitos

| Item | Detalhe | Bloqueante |
|---|---|---|
| Docker + Compose v2 | `docker compose version` ≥ 2.20 | Sim |
| Ollama nos 2 Mac minis | Modelos puxados e servindo em `:11434` / `:11435`, alcançáveis da máquina do Docker | Sim |
| Contratos das 3 APIs | Endpoints, payloads, formato do callback de verificação do orchestrator — documentar em `docs/contratos/` | Sim |
| Tokens de serviço | `ORCHESTRATOR_TOKEN`, `PERSONA_ENGINE_TOKEN`, `ANDROID_MANAGER_TOKEN` | Não (dry-run funciona sem) |
| `ANTHROPIC_API_KEY` / `OPENAI_API_KEY` | Fallback do gateway | Não |
| Benchmark dos Mac minis | tokens/s de Qwen 2.5 7B, Llama 3.1 8B e um 14B — define o `model_list` do LiteLLM | Sim (item 3 dos próximos passos do PLANO) |

> Sem contrato de API mapeado não se escreve cliente. Mapear é a primeira tarefa da fase, não a última.

---

## 3. Escopo

1. **`olimpo.core.config`** — `Settings` (pydantic-settings) lendo todo o `.env.example`. `DISPATCH_ENABLED` e `GLOBAL_KILL_SWITCH` como propriedades de primeira classe, não strings soltas.
2. **`olimpo.core.db`** — engine assíncrona (`asyncpg`), `session_scope()`, registro do tipo `vector` do pgvector.
3. **`olimpo.core.models`** — modelos SQLAlchemy 2.0 espelhando **todas** as tabelas de `db/migrations/0001_initial.sql`, incluindo as dormentes (`clients`, `sources`, `ingested_items`, `monitors`, `monitor_hits`, `trends`, `campaign_recon`). Enums Python espelhando os `CREATE TYPE`.
4. **`olimpo.db.migrate`** — runner de migração próprio (`up`, `status`), gravando `version` + `checksum` em `schema_migrations`. É o `command` do serviço `migrate` no compose. Falha se o checksum de uma migração já aplicada mudou.
5. **`olimpo.db.seed`** — cria 1 conta `archetype='influencer'`, `status='draft'`, `autonomy='L0'` com `persona_card` mínimo. Alvo do `make seed`.
6. **`olimpo.core.events`** — `emit(type, *, account_id=None, task_id=None, campaign_id=None, detail={}, trace_id)` gravando em `events`. **Toda** decisão do sistema passa por aqui desde o primeiro commit. `trace_id` propagado via `contextvars`.
7. **`olimpo.llm.gateway`** — cliente do LiteLLM (`LLM_GATEWAY_URL`) com `complete()` e `embed()`. Grava uma linha em `llm_calls` por chamada: `purpose`, `model`, `backend`, tokens, `latency_ms`, `cost_usd`. Corta com erro claro quando o gasto do dia excede `LLM_DAILY_BUDGET_USD`.
8. **`ops/litellm.config.yaml`** — `model_list` com os dois Mac minis, aliases lógicos (`olimpo-fast`, `olimpo-smart`, `olimpo-embed`, `olimpo-vision`), fallback para Anthropic/OpenAI e `router_settings` com prioridade de fila.
9. **`olimpo.integrations.base`** — `ServiceClient` sobre `httpx.AsyncClient` + `tenacity`: timeout, retry com backoff, propagação de `trace_id`, e o **shim de dry-run**. Quando `DISPATCH_ENABLED=false`, toda chamada de escrita serializa `{método, url, payload, timestamp, trace_id}` em `media/dryrun/<data>/<serviço>/<uuid>.json` e devolve uma resposta sintética plausível.
10. **`olimpo.integrations.orchestrator`** — `dispatch_task()`, `get_task()`, `cancel_task()`. Traduz `tasks.type` do Olimpo para o vocabulário do orchestrator e devolve o `external_task_id`.
11. **`olimpo.integrations.persona_engine`** — `get_persona()`, `generate_image()`, `generate_video()`, `list_brands()`.
12. **`olimpo.integrations.android_manager`** — **somente leitura**: `fleet_health()`, `device_status()`. O core nunca escreve na frota.
13. **`olimpo.api.main`** — FastAPI com `/health` (checa Postgres, Redis, LiteLLM), `/ready`, `/metrics` (prometheus-client) e autenticação por `API_TOKEN` em tudo que não seja health.
14. **`olimpo.api.webhooks.orchestrator`** — `POST /webhooks/orchestrator`: valida HMAC com `ORCHESTRATOR_WEBHOOK_SECRET`, é idempotente por `external_task_id`, move a task para `verified`/`failed` e grava `result`. Nesta fase só recebe callbacks simulados.
15. **`olimpo.workers.app`** — Celery com as 4 filas (`realtime`, `dispatch`, `production`, `maintenance`) e roteamento explícito por task. `beat` com um único job: `dispatcher_tick` a cada 30 s.
16. **`olimpo.workers.dispatcher`** — varre `tasks` usando `idx_tasks_due` (`status IN ('pending','scheduled') AND scheduled_for <= now()`), respeita `GLOBAL_KILL_SWITCH` e `ACCOUNT_MAX_ACTIONS_PER_HOUR`, aplica jitter e chama o cliente do orchestrator. Lock por conta em Redis para não despachar duas ações simultâneas do mesmo device.
17. **`olimpo.agents.loop`** — esqueleto das 8 etapas com implementações triviais (o PLAN devolve uma intenção fixa, o SOURCE devolve um `ContentDraft` com mídia placeholder). Serve de andaime para a Fase 1 preencher.
18. **`olimpo.agents.sources.base`** — o `Protocol` `ContentSource.propose(account, day_plan) -> list[ContentDraft]` e o registro por `archetype`.
19. **Painel Next.js mínimo** — página única listando contas, tasks e os últimos 100 `events`. Sem ações.
20. **Testes** — `tests/` com pytest + `respx` mockando as 3 APIs; um teste end-to-end que roda o loop em dry-run e afirma o encadeamento de eventos.
21. **`docs/contratos/`** — um arquivo por serviço com endpoints, payloads e exemplos reais capturados.
22. **`docs/adr/0001-core-proprio.md`** e **`0004-agendamento-no-postgres.md`** — as duas decisões já referenciadas no README e no compose.

---

## 4. Fora de escopo

- Qualquer despacho real ao android-orchestrator (`DISPATCH_ENABLED` permanece `false` a fase inteira).
- Persona Card real, memória, embeddings de memória, arcos narrativos — Fase 1.
- Campanhas, `spread_policy`, recon — Fase 3.
- Monitores, ingestão, `CuratedSource` — Fase 4.
- `media-forge`, `DerivedSource` — Fase 5.
- Painel com aprovação, edição ou kill switch por conta — Fase 1.
- Autenticação multiusuário no painel. Um `API_TOKEN` compartilhado basta.
- Prometheus/Grafana provisionados de verdade. O endpoint `/metrics` existe; os dashboards ficam para a Fase 2.
- Otimização de custo/latência de LLM. Mede-se (`llm_calls`), não se otimiza.

---

## 5. Critérios de saída

- [ ] `cp .env.example .env && make up` sobe 7 containers saudáveis; `make ps` mostra todos `healthy` ou `running`.
- [ ] `curl -fsS localhost:8000/health` devolve `200` com `{"postgres":"ok","redis":"ok","litellm":"ok"}`.
- [ ] `make migrate` roda duas vezes seguidas: a segunda não aplica nada e sai `0`.
- [ ] `make psql -c "\dt"` lista as 22 tabelas do `0001_initial.sql`; `SELECT count(*) FROM pg_type WHERE typname='claim_status'` devolve `1`.
- [ ] `SELECT * FROM pg_extension WHERE extname IN ('vector','pg_trgm','uuid-ossp')` devolve 3 linhas.
- [ ] `python -m olimpo.llm.smoke` chama `olimpo-fast` nos dois Mac minis e `olimpo-embed`, e grava ≥ 3 linhas em `llm_calls` com `backend` distinto e `latency_ms > 0`.
- [ ] O embedding devolvido tem **1024 dimensões** (bate com `memories.embedding vector(1024)`).
- [ ] `make seed` cria 1 conta; `SELECT handle, archetype, autonomy FROM accounts` devolve exatamente 1 linha `influencer/L0`.
- [ ] `python -m olimpo.agents.loop --account <id> --dry-run` completa as 8 etapas e:
  - cria 1 linha em `day_plans`, 1 em `content_items` (`status='draft'`) e 1 em `tasks` (`type='publish_post'`);
  - gera ≥ 8 linhas em `events` compartilhando o mesmo `trace_id`, uma por etapa do loop;
  - grava ≥ 1 arquivo JSON em `media/dryrun/`.
- [ ] Com `DISPATCH_ENABLED=false`, `SELECT count(*) FROM tasks WHERE external_task_id IS NOT NULL AND external_task_id NOT LIKE 'dryrun-%'` devolve `0`.
- [ ] `GLOBAL_KILL_SWITCH=true` + `make restart`: o `dispatcher_tick` roda, emite `event.type='kill_switch.blocked'` e despacha `0` tasks.
- [ ] Webhook simulado (`scripts/fake_orchestrator_callback.sh <external_task_id>`) move a task para `verified` e preenche `verified_at`. Reenviar o **mesmo** callback não cria linha duplicada em `posts`.
- [ ] Callback com assinatura HMAC inválida devolve `401` e não altera a task.
- [ ] `make test` passa com cobertura ≥ 60% em `olimpo/integrations` e `olimpo/core`.
- [ ] `make lint` sai `0` (ruff + mypy `disallow_untyped_defs`).
- [ ] `docs/contratos/` tem um arquivo por serviço com pelo menos um request e um response reais.

---

## 6. Riscos da fase

| Risco | Impacto | Mitigação |
|---|---|---|
| Contrato do orchestrator diferente do imaginado (ex.: verificação por polling, não webhook) | Reescrita do `dispatcher` | Mapear o contrato **antes** de escrever o cliente; suportar os dois modos no `orchestrator.py` desde já — polling em `maintenance` como fallback do webhook |
| Mac minis inalcançáveis do container (`host.docker.internal` no Linux) | LLM Gateway não sobe | `extra_hosts: host-gateway` já no compose; documentar o fallback por IP de LAN no `.env` e validar no `/health` |
| Embedding do modelo local com dimensão ≠ 1024 | Migração de `memories.embedding` com dados dentro | Travar o modelo de embedding **nesta fase** e validar a dimensão no smoke test; se não for 1024, alterar o `0001_initial.sql` agora, enquanto o banco está vazio |
| Dry-run devolvendo resposta irreal | Fase 1 quebra no primeiro despacho real | Fixtures do dry-run derivadas dos exemplos reais em `docs/contratos/`, não inventadas |
| LiteLLM `main-stable` mudando comportamento de config | Gateway quebra em rebuild | Fixar a digest da imagem no compose assim que a config estabilizar |
| Schema tratado como negociável | Migração dolorosa com contas em produção | O `0001_initial.sql` é a fonte da verdade. Mudança nele nesta fase é livre; a partir da Fase 1, só por migração nova |
| Over-engineering do loop vazio | Atraso da Fase 1 | O loop da Fase 0 é andaime: cada etapa pode ser 5 linhas e um `emit()` |

---

## 7. O que fica dormente

| Artefato | Criado nesta fase | Acorda em |
|---|---|---|
| `clients`, `accounts.client_id` | Tabela + FK | Fase 5 (multi-tenant) |
| `sources`, `ingested_items` | Tabelas + índice `idx_ingested_unconsumed` | Fase 4 |
| `monitors`, `monitor_hits` | Tabelas + índice `idx_monitors_due` | Fase 4 |
| `campaigns`, `campaign_recon`, `campaign_participants` | Tabelas + `spread_policy NOT NULL` | Fase 3 |
| `content_items.source_refs`, `.attribution` | Colunas com default | Fases 4 e 5 |
| `content_items.claim_status` | Coluna, default `'unverified'` | Fase 4 (torna-se obrigatória e bloqueante) |
| `content_items.rights_status` | Coluna, default `'own'` (verdadeiro para influencer) | Fase 5 |
| `trends` | Tabela + `idx_trends_live` | Fase 3 (trend engine v1) |
| `memories.embedding`, índice HNSW | Coluna + índice | Fase 1 (episódica) e Fase 2 (semântica) |
| `story_arcs` | Tabela | Fase 1 (um arco manual), Fase 3 (motor completo) |
| `interactions` | Tabela + `idx_interactions_pending` | Fase 2 |
| `approvals` | Tabela | Fase 1 (L0) |
| `guardrail_violations` | Tabela | Fase 1 (QA básico), Fase 4 (jurídico) |
| `post_metrics` | Tabela | Fase 2 (realimenta o PLAN) |
| `task_type` `extract_content` | Valor do enum | Fase 3 (recon) |
| `task_type` `monitor_check` | Valor do enum | Fase 4 |
| `MEDIA_FORGE_URL` no `.env` | Variável vazia | Fase 5 |
| `olimpo.integrations.media_forge` | **Não criar** stub vazio | Fase 5 |
| Filas `realtime` e `maintenance` | Declaradas e consumidas pelo worker, sem produtores | `realtime` na Fase 2 (interações), `maintenance` na Fase 2 (coleta de métricas) |

---

## 8. Handoff para a Fase 1

**Estado do sistema entregue**

- Stack sobe com um comando; banco migrado com o schema completo; 1 conta semente em `L0`/`draft`.
- Loop de 8 etapas executável ponta a ponta em dry-run, com rastro completo em `events` por `trace_id`.
- Os 3 clientes externos funcionam e mentem de forma realista quando `DISPATCH_ENABLED=false`.
- Gateway de LLM com aliases lógicos e contabilidade de custo por chamada.

**Decisões tomadas (não reabrir sem ADR)**

| Decisão | Registro |
|---|---|
| Core próprio, sem fork de framework de agente | `docs/adr/0001-core-proprio.md` |
| Agendamento é o Postgres (`tasks.scheduled_for`); o Celery beat só acorda o dispatcher | `docs/adr/0004-agendamento-no-postgres.md` |
| Migrações em SQL puro com runner próprio, não Alembic | `docs/adr/0002-migracoes-sql-puro.md` |
| Dimensão de embedding = 1024, modelo travado | `docs/adr/0003-embeddings.md` |
| Verificação de ação é do orchestrator; o Olimpo só confia em `status='verified'` | ADR-0001 |

**Dívidas técnicas aceitas conscientemente**

1. O `dispatcher` reconcilia tasks presas em `dispatched` só por webhook. O varredor de reconciliação por polling (`maintenance`) fica para a Fase 1, quando existir despacho real.
2. `POST /webhooks/orchestrator` valida HMAC mas não tem replay window por timestamp — aceitável enquanto o webhook não é público.
3. Sem RLS nem separação por `client_id` nas queries. Entra na Fase 5.
4. Painel é read-only e sem paginação. A Fase 1 o reescreve para aprovação.
5. `mood` e `cadence` em `accounts` têm defaults e ninguém os lê. Fases 1 e 3.

**A primeira coisa que a Fase 1 faz:** rodar o spike do Letta/MemGPT (1–2 dias) **antes** de escrever a camada de memória, porque a decisão define se `memories` é a fonte da verdade ou um espelho.
