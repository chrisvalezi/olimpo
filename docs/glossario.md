# Glossário

> Vocabulário do Olimpo. A documentação é em **português**; os identificadores de código e de banco são em **inglês** — a tabela de mapeamento está na §3.
> Fonte da verdade do schema: [`db/migrations/0001_initial.sql`](../db/migrations/0001_initial.sql).

---

## 1. Termos

### Agente
Uma conta social operada autonomamente pelo Olimpo. **Uma conta = um agente.** Tem persona, memória, arco narrativo, agenda e nível de autonomia próprios; roda o loop de oito etapas sozinho.
→ Tabela `accounts` (a linha *é* o agente). Código: `olimpo.agents.loop`.

### Arquétipo
O tipo de conta, que determina **de onde vem o conteúdo** — e só isso. Três valores: `influencer`, `gossip` (fofoca), `clipper` (clipagem). Todo o resto do sistema é idêntico entre eles.
→ `accounts.archetype`, tipo `archetype`. Ver [00 §5](00-visao-geral.md).

### ContentSource
A interface que implementa a única peça variável do loop: o passo SOURCE. Três implementações — `GeneratedSource` (influencer, gera da própria história), `CuratedSource` (fofoca, cura de ingestão) e `DerivedSource` (clipagem, deriva de conteúdo longo).
→ `olimpo.agents.sources.base` (Protocol `propose(account, day_plan) -> list[ContentDraft]`); registrado por arquétipo. Reflete em `content_items.source_strategy` (`generated|curated|derived`).

### Persona card
O documento que define quem a conta é: identidade, voz, tom, tabus, estética, linha editorial, exemplos de post. Segue o formato de *character file* do ElizaOS, estendido. Entra em **todo** prompt do agente. Toda alteração gera versão.
→ `accounts.persona_card jsonb`, `accounts.persona_version`, histórico em `persona_versions`. Spec em [04](04-persona-card-spec.md).

### Arco narrativo
O enredo em que a conta está no momento — "treinando para a maratona", "semana da praia". Tem título, resumo, estado, início e fim. É o que dá continuidade entre os dias e alimenta o PLAN.
→ `story_arcs` (`title`, `summary`, `state jsonb`, `starts_at`, `ends_at`, `active`); memórias podem apontar para o arco via `memories.arc_id`.

### Memória episódica
O diário: o que aconteceu, datado. "Hoje postei o treino, a @fulana comentou de novo."
→ `memories` com `kind='episodic'`. Escrita no passo RECORD.

### Memória semântica
Fato consolidado sobre si mesma ou sobre terceiros, sem data específica. "@fulana é seguidora antiga e sempre comenta sobre corrida." Nasce da síntese de memórias episódicas.
→ `memories` com `kind='semantic'`. Escrita no passo REFLECT.

### Memória narrativa
O estado atual da história, do ponto de vista da própria conta. "Estou na terceira semana de treino e cansada."
→ `memories` com `kind='narrative'`, geralmente ligada a um `arc_id`.

> As três compartilham a mesma tabela e o mesmo mecanismo de recuperação: **relevância × recência × importância** — similaridade de cosseno em `memories.embedding vector(1024)` (índice HNSW), decaimento sobre `occurred_at`/`last_used_at`, e `memories.importance real` (0–10) atribuída pela LLM na escrita. Blueprint: paper *generative_agents* ([ADR-0001](adr/0001-core-proprio.md)).

### Plano do dia
O resultado do passo PLAN: a narrativa do dia, a lista de intenções que viram tasks, e o **motivo** de cada decisão. Um por conta por dia.
→ `day_plans` (`plan_date`, `narrative`, `intents jsonb`, `reasoning`, `llm_call_id`), `UNIQUE (account_id, plan_date)`.

### Recon
O reconhecimento que precede uma campanha de engajamento: uma conta Android **abre o conteúdo alvo** e extrai o que ele é (task `extract_content`); depois uma LLM multimodal interpreta. Sem recon não se planeja campanha de engajamento — decidir o que comentar sem ter visto o post é como se comenta besteira.
→ `campaign_recon`: bruto em `raw_capture`/`media_refs`/`top_comments`; interpretado em `analysis`/`summary`/`sentiment`/`safety_verdict`/`suggested_angles`. Ver [06](06-campanhas.md).

### Spread policy (política de espalhamento)
A política anti-detecção obrigatória de toda campanha: janela mínima entre contas, texto sempre único, participação parcial, limite de interação entre contas da casa e proibição de dobrar a cadência natural. **Campo `NOT NULL`** — não existe campanha sem ela.
→ `campaigns.spread_policy jsonb`. Defaults em `.env`: `CAMPAIGN_JITTER_MIN_MINUTES`, `CAMPAIGN_JITTER_MAX_MINUTES`, `CAMPAIGN_MAX_PARTICIPATION`. Editor: `SpreadPolicyEditor` ([10 §5](10-painel.md)).

### Jitter
O deslocamento aleatório aplicado ao horário de uma ação, para que N contas nunca ajam simultaneamente e para que a mesma conta não aja em horário previsível. Aplicado tanto no sorteio do slot de campanha quanto no despacho individual.
→ `campaign_participants.slot_at` (sorteado dentro de `campaigns.window_start`–`window_end`); no dispatcher, sobre `tasks.scheduled_for`.

### Nível de autonomia
Quanto o agente faz sem humano. Quatro níveis: **L0** estagiário (só drafts, humano aprova tudo) · **L1** júnior (auto-posta, interação vai para revisão) · **L2** pleno (auto-posta e auto-responde, revisão por amostragem) · **L3** sênior (full auto, humano só vê alertas). Autonomia muda **quem revisa**, nunca se as regras valem.
→ `accounts.autonomy`, tipo `autonomy_level`. Ver [08](08-guardrails-e-autonomia.md).

### Guardrail
Regra de segurança que vale em **todos** os níveis de autonomia: brand safety, temas proibidos, rate limit, blacklist de alvos, exigência de `claim_status`/`rights_status`, proibição de auto-resposta a interação sensível. Violação é registrada com severidade `info|warn|block|kill`.
→ `guardrail_violations` (`rule`, `severity`, `action_taken`). Ver [08](08-guardrails-e-autonomia.md).

### Kill switch
Parada imediata. **Por conta:** move para `status='suspended'` e cancela tasks não executadas. **Global:** `GLOBAL_KILL_SWITCH=true` — o dispatcher para tudo e emite `kill_switch.blocked`. Exige confirmação dupla no painel.
→ `accounts.status='suspended'` (tipo `account_status`); env `GLOBAL_KILL_SWITCH`.

### Dry-run
Modo padrão do sistema: nenhuma chamada de escrita chega aos serviços externos. Cada uma é serializada em `media/dryrun/<data>/<serviço>/<uuid>.json` e o cliente devolve uma resposta sintética plausível. É como se valida o loop sem arriscar uma conta.
→ Env `DISPATCH_ENABLED=false` (default); shim em `olimpo.integrations.base`. No painel, faixa âmbar permanente.

### Task verificada
Uma task só conta como feita quando o android-orchestrator **confirma a execução por leitura de tela** e o webhook chega. Nenhum outro estado significa "publicado". Progresso de campanha e métrica de sucesso contam apenas `verified`.
→ `tasks.status='verified'`, `tasks.verified_at`, `tasks.external_task_id`. Webhook: `POST /webhooks/orchestrator` (HMAC, idempotente por `external_task_id`).

### Monitor
Vigia persistente: checa um alvo em intervalo curto, detecta novidade e dispara produção. Diferente de uma **fonte** (`sources`, que é catálogo) — o monitor é o processo vivo. Roda a coleta rotacionando entre contas Android para não queimar uma só.
→ `monitors` (`interval_seconds`, `collector_pool`, `trigger_rules`, `next_check_at`, `consecutive_errors`); task `monitor_check`. Fase 4, ver [07](07-monitores.md).

### Hit
Uma detecção de um monitor. Nem todo hit vira conteúdo: `triggered` marca os que bateram as regras de disparo, com o motivo registrado.
→ `monitor_hits` (`triggered`, `trigger_reason`, `ingested_id`, `content_item_id`).

### Furo
Notícia com janela de valor curta — vale muito nos primeiros minutos e nada depois. Domina a operação de fofoca e é a razão de existirem fila `realtime` e `tasks.expires_at`.
→ `tasks.expires_at`, `task_status='expired'`; `ingested_items.novelty`.

### Claim status
O status epistêmico de uma afirmação publicada: `fact` (fonte primária) · `reported` (veículo confiável afirma) · `rumor` (circulando, não confirmado) · `opinion` (opinião editorial explícita) · `unverified` (default, **bloqueia publicação**). Obrigatório em fofoca e o texto publicado precisa refleti-lo.
→ `content_items.claim_status`, tipo `claim_status`. Dormente até a Fase 4.

### Rights status
Os direitos sobre a mídia: `own` (gerado por nós) · `licensed` (criador autorizou) · `fair_use` (transformação suficiente, com crédito) · `unknown` (default, **bloqueia publicação**). Crítico para clipagem.
→ `content_items.rights_status` + `content_items.attribution` (crédito obrigatório). Dormente até a Fase 5.

### Aquecimento de conta
Período inicial em que a conta só consome (assiste reels, vê stories, segue), sem publicar, para construir histórico de comportamento antes de agir. Conta nova que já sai postando é conta banida.
→ `accounts.status='warming'`, `accounts.warmup_started_at`; tasks `watch_reels`, `view_stories`, `follow`.

### Crossover
Interação deliberada entre contas da própria casa (comentário, menção, colaboração). Poderoso para engajamento e **é rastro** — o grafo de quem interage com quem é exatamente o que denuncia um farm. Por isso é limitado pela `spread_policy`.
→ Regra na `campaigns.spread_policy`; execução via `tasks` de `comment`/`like` entre `account_id` da casa.

### Trend engine
Componente que acompanha o que está em alta por plataforma (áudio, hashtag, formato, tópico) e alimenta o PLAN e as campanhas. Compartilhado pelos três arquétipos.
→ `trends` (`platform`, `kind`, `ref`, `score`, `window_start`/`window_end`), índice `idx_trends_live`. Fase 3.

### media-forge
Microserviço a construir na Fase 5: ingere vídeo longo, roda ASR (Whisper), detecta o momento que vira clipe, corta, reenquadra para vertical, queima legenda e aplica trilha. É o motor do `DerivedSource`.
→ Env `MEDIA_FORGE_URL`; cliente `olimpo.integrations.media_forge` (**não criar stub antes da Fase 5**).

### Dispatcher
O componente que converte task agendada em ação real: varre `tasks` devidas, respeita kill switch, teto horário e `active_hours`, aplica jitter, toma lock por conta no Redis e chama o android-orchestrator.
→ `olimpo.workers.dispatcher`, índice `idx_tasks_due`. Ver [ADR-0004](adr/0004-agendamento-no-banco.md).

### Tick
O batimento do sistema: o `dispatcher_tick` do Celery beat, a cada 30 s. Ele **não** é a fonte da verdade do agendamento — quem agenda é o Postgres (`tasks.scheduled_for`); o tick só acorda o dispatcher.
→ `olimpo.workers.app` (beat). Ver [ADR-0004](adr/0004-agendamento-no-banco.md).

---

## 2. Termos de apoio

| Termo | Definição curta | Onde aparece |
|---|---|---|
| **Manager** | O "CEO": emite campanhas, aplica políticas globais, aprova, mantém o kill switch | `campaigns`, `tasks.origin='manager'` |
| **Panteão** | O conjunto de contas operadas pelo Olimpo | conceito; `accounts` |
| **Cadência** | Ritmo natural da conta (posts/dia, stories/dia). Campanha não pode ultrapassá-lo | `accounts.cadence jsonb` |
| **Janela ativa** | Horas em que a conta "está acordada". Fora dela o agente não age | `accounts.active_hours int4range` |
| **Humor** | Estado dinâmico que afeta tom e frequência | `accounts.mood jsonb` |
| **Trace id** | Identificador que amarra todos os eventos e chamadas de uma execução do loop | `events.trace_id`, `llm_calls.trace_id`, `contextvars` |
| **Amostragem** | Revisão humana de uma fração do que o agente faz em L2 | `approvals.sampled`, env `L2_SAMPLING_RATE` |
| **Dedup hash** | Impede reprocessar o mesmo item ingerido | `ingested_items.dedup_hash` (UNIQUE) |
| **Coletor** | Conta Android usada para observar/extrair, não para publicar | `monitors.collector_pool`, `campaign_recon.collector_account_id` |
| **Adaptação local** | Como cada persona traduziu um brief de campanha para a própria realidade | `campaign_participants.local_adaptation` |
| **Backend de LLM** | Qual worker atendeu a chamada (`mac_mini_1`, `mac_mini_2`, `anthropic`, `openai`) | `llm_calls.backend` |
| **Dormente** | Campo/tabela criada na v1 sem uso, para evitar migração dolorosa depois | marcações `[DORMENTE]` em `0001_initial.sql` |

---

## 3. Mapeamento PT ↔ EN

A documentação fala português; o código e o banco falam inglês. Esta tabela é o dicionário oficial — ao escrever código, use a coluna da direita.

### 3.1 Entidades

| Português | Identificador | Tipo |
|---|---|---|
| conta / agente | `accounts` | tabela |
| cliente (multi-tenant) | `clients` | tabela |
| persona card | `accounts.persona_card` | coluna |
| versão de persona | `persona_versions` | tabela |
| memória | `memories` | tabela |
| arco narrativo | `story_arcs` | tabela |
| plano do dia | `day_plans` | tabela |
| fonte | `sources` | tabela |
| item ingerido | `ingested_items` | tabela |
| monitor / vigia | `monitors` | tabela |
| hit de monitor | `monitor_hits` | tabela |
| tendência | `trends` | tabela |
| item de conteúdo | `content_items` | tabela |
| campanha | `campaigns` | tabela |
| reconhecimento (recon) | `campaign_recon` | tabela |
| participante de campanha | `campaign_participants` | tabela |
| task / tarefa | `tasks` | tabela |
| publicação | `posts` | tabela |
| métrica de post | `post_metrics` | tabela |
| interação | `interactions` | tabela |
| aprovação | `approvals` | tabela |
| violação de guardrail | `guardrail_violations` | tabela |
| evento (auditoria) | `events` | tabela |
| chamada de LLM | `llm_calls` | tabela |

### 3.2 Enums e valores

| Português | Identificador | Valores |
|---|---|---|
| arquétipo | `archetype` | `influencer` · `gossip` (fofoca) · `clipper` (clipagem) |
| nível de autonomia | `autonomy_level` | `L0` · `L1` · `L2` · `L3` |
| status de conta | `account_status` | `draft` (rascunho) · `warming` (aquecendo) · `active` (ativa) · `paused` (pausada) · `suspended` (suspensa) · `lost` (perdida) |
| plataforma | `platform` | `instagram` · `tiktok` · `whatsapp` |
| tipo de task | `task_type` | `plan_day` · `produce_content` · `reflect` · `publish_post` · `publish_story` · `publish_reels` · `like` · `comment` · `follow` · `unfollow` · `save` · `share` · `watch_reels` · `view_stories` · `send_dm` · `reply_comment` · `extract_content` · `collect_metrics` · `collect_interactions` · `monitor_check` |
| status de task | `task_status` | `pending` (pendente) · `scheduled` (agendada) · `dispatched` (despachada) · `running` (executando) · `verified` (verificada) · `failed` (falhou) · `cancelled` (cancelada) · `expired` (expirada) |
| status de conteúdo | `content_status` | `draft` · `qa_pending` · `qa_failed` · `approval_pending` · `approved` · `rejected` · `scheduled` · `published` · `archived` |
| status de afirmação | `claim_status` | `fact` (fato) · `reported` (noticiado) · `rumor` (rumor) · `opinion` (opinião) · `unverified` (não verificado) |
| status de direitos | `rights_status` | `own` (próprio) · `licensed` (licenciado) · `fair_use` (uso justo) · `unknown` (desconhecido) |
| status de campanha | `campaign_status` | `draft` · `recon_pending` · `recon_done` · `planning` · `approval_pending` · `approved` · `running` · `completed` · `cancelled` · `aborted` |
| tipo de campanha | `campaign_kind` | `content` (conteúdo) · `engagement` (engajamento) · `mixed` (misto) |
| status de monitor | `monitor_status` | `active` · `paused` · `error` · `disabled` |
| tipo de memória | `memory_kind` | `episodic` (episódica) · `semantic` (semântica) · `narrative` (narrativa) |
| tipo de fonte | `source_kind` | `ig_account` · `tiktok_account` · `hashtag` · `keyword` · `rss` · `youtube_channel` · `live_stream` · `http_api` |

### 3.3 Conceitos e campos

| Português | Identificador |
|---|---|
| espalhamento / política de espalhamento | `spread_policy` |
| janela da campanha | `window_start` / `window_end` |
| slot sorteado | `slot_at` |
| filtro de contas | `account_filter` |
| adaptação local | `local_adaptation` |
| cadência | `cadence` |
| janela ativa | `active_hours` |
| humor | `mood` |
| importância (memória) | `importance` |
| relevância / novidade | `relevance` / `novelty` |
| confiabilidade da fonte | `reliability` |
| regras de disparo | `trigger_rules` |
| pool de coletores | `collector_pool` |
| erros consecutivos | `consecutive_errors` |
| raciocínio / por quê | `reasoning` |
| relatório de QA | `qa_report` |
| atribuição / crédito | `attribution` |
| proveniência | `source_refs` / `source_strategy` |
| id externo da task | `external_task_id` |
| origem da task | `origin` (`manager` · `self` · `human` · `campaign` · `monitor`) |
| severidade | `severity` (`info` · `warn` · `block` · `kill`) |
| interação sensível | `sensitive` |
| classificação de interação | `classification` (`fan` · `hater` · `spam` · `opportunity` · `sensitive`) |
| revisão por amostragem | `sampled` |
| id de rastreamento | `trace_id` |
| propósito da chamada de LLM | `purpose` |
| aquecimento | `warmup_started_at` |

### 3.4 Etapas do loop

| Português | Identificador |
|---|---|
| planejar | `PLAN` |
| buscar material | `SOURCE` |
| produzir | `PRODUCE` |
| controle de qualidade | `QA` |
| despachar | `DISPATCH` |
| verificar | `VERIFY` |
| registrar | `RECORD` |
| refletir | `REFLECT` |

> **Regra:** nomes de tabela, coluna, enum, classe, função e variável são sempre em inglês. Comentários, docstrings, mensagens de commit, documentação e rótulos do painel são em português. Rótulo de UI traduz o enum — nunca renomeia o dado.
