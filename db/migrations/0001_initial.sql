-- =============================================================================
-- Olimpo — Migração 0001: schema inicial
-- =============================================================================
-- Convenção: identificadores em inglês (alinhados ao código), documentação em
-- português. Ver docs/glossario.md para o mapeamento PT ↔ EN.
--
-- Campos marcados como [DORMENTE] não são usados na Fase 1 (influencers), mas
-- nascem aqui de propósito: adicioná-los depois, com dezenas de contas em
-- produção, seria uma migração dolorosa. Custo hoje: zero.
-- =============================================================================

CREATE EXTENSION IF NOT EXISTS "uuid-ossp";
CREATE EXTENSION IF NOT EXISTS "vector";
CREATE EXTENSION IF NOT EXISTS "pg_trgm";
-- hmac(): pseudonimiza handles de terceiros na varredura de retenção, e
-- gera body_sha256 para provar qual texto foi avaliado sem reter o texto.
-- Ver docs/12-seguranca.md §4.3.
CREATE EXTENSION IF NOT EXISTS "pgcrypto";

-- =============================================================================
-- TIPOS
-- =============================================================================

-- Arquétipo de conta. Define qual ContentSource o agente usa (ver docs/05).
CREATE TYPE archetype AS ENUM ('influencer', 'gossip', 'clipper');

-- Nível de autonomia. Ver docs/08-guardrails-e-autonomia.md.
--   L0 estagiário  — só drafts, humano aprova tudo
--   L1 júnior      — auto-posta, interação vai para revisão
--   L2 pleno       — auto-posta e auto-responde, revisão por amostragem
--   L3 sênior      — full auto, humano só vê alertas
CREATE TYPE autonomy_level AS ENUM ('L0', 'L1', 'L2', 'L3');

CREATE TYPE account_status AS ENUM (
    'draft',       -- criada no painel, ainda sem device/login
    'warming',     -- em aquecimento, só consome conteúdo
    'active',      -- operando normalmente
    'paused',      -- pausada por humano
    'suspended',   -- kill switch acionado (guardrail ou incidente)
    'lost'         -- conta banida/perdida
);

CREATE TYPE platform AS ENUM ('instagram', 'tiktok', 'whatsapp');

CREATE TYPE task_type AS ENUM (
    -- produção (rodam no core, não tocam device)
    'plan_day', 'produce_content', 'reflect',
    -- execução (despachadas ao android-orchestrator)
    'publish_post', 'publish_story', 'publish_reels',
    'like', 'comment', 'follow', 'unfollow', 'save', 'share',
    'watch_reels', 'view_stories', 'send_dm', 'reply_comment',
    -- coleta
    'extract_content',   -- recon: abrir um conteúdo e extrair o que é
    'collect_metrics', 'collect_interactions', 'monitor_check'
);

CREATE TYPE task_status AS ENUM (
    'pending',      -- criada, aguardando janela de agendamento
    'scheduled',    -- dentro da janela, aguardando dispatch
    'dispatched',   -- enviada ao android-orchestrator
    'running',      -- orchestrator confirmou início
    'verified',     -- orchestrator confirmou conclusão COM verificação
    'failed',
    'cancelled',
    'expired'       -- perdeu a janela de validade (ex.: furo de notícia)
);

CREATE TYPE content_status AS ENUM (
    'draft', 'qa_pending', 'qa_failed', 'approval_pending',
    'approved', 'rejected', 'scheduled', 'published', 'archived'
);

-- Status epistêmico de uma afirmação. OBRIGATÓRIO em páginas de fofoca:
-- o texto publicado precisa refletir isto. Ver docs/08.
CREATE TYPE claim_status AS ENUM (
    'fact',          -- confirmado por fonte primária
    'reported',      -- veículo confiável afirma
    'rumor',         -- circulando, não confirmado
    'opinion',       -- opinião editorial explícita
    'unverified'     -- default; bloqueia publicação em L≥1 sem revisão
);

-- Direitos sobre a mídia. Crítico para clipagem.
CREATE TYPE rights_status AS ENUM (
    'own',           -- gerado por nós (máquina de influencers)
    'licensed',      -- criador autorizou explicitamente
    'fair_use',      -- transformação suficiente, com crédito
    'unknown'        -- default; bloqueia publicação
);

CREATE TYPE campaign_status AS ENUM (
    'draft', 'recon_pending', 'recon_done', 'planning',
    'approval_pending', 'approved', 'running', 'completed',
    'cancelled', 'aborted'
);

CREATE TYPE campaign_kind AS ENUM (
    'content',      -- todas postam sobre um tema/áudio/brand
    'engagement',   -- todas interagem com um alvo externo (like/comment/save)
    'mixed'
);

CREATE TYPE monitor_status AS ENUM ('active', 'paused', 'error', 'disabled');

CREATE TYPE memory_kind AS ENUM (
    'episodic',      -- diário: "hoje postei X, fulano comentou Y"
    'semantic',      -- fatos consolidados sobre si e sobre seguidores
    'narrative'      -- estado do arco: "estou na semana da praia"
);

CREATE TYPE source_kind AS ENUM (
    'ig_account', 'tiktok_account', 'hashtag', 'keyword',
    'rss', 'youtube_channel', 'live_stream', 'http_api'
);

-- =============================================================================
-- CONTROLE DE MIGRAÇÃO
-- =============================================================================

CREATE TABLE schema_migrations (
    version     text PRIMARY KEY,
    applied_at  timestamptz NOT NULL DEFAULT now(),
    checksum    text NOT NULL
);

-- =============================================================================
-- TENANTS / CLIENTES  [DORMENTE até a Fase 5 — clipagem como serviço]
-- =============================================================================

CREATE TABLE clients (
    id          uuid PRIMARY KEY DEFAULT uuid_generate_v4(),
    name        text NOT NULL,
    contract    jsonb NOT NULL DEFAULT '{}',
    active      boolean NOT NULL DEFAULT true,
    created_at  timestamptz NOT NULL DEFAULT now(),
    updated_at  timestamptz NOT NULL DEFAULT now()
);

-- =============================================================================
-- CONTAS  (a unidade central: 1 conta = 1 agente)
-- =============================================================================

CREATE TABLE accounts (
    id                uuid PRIMARY KEY DEFAULT uuid_generate_v4(),
    handle            text NOT NULL,
    platform          platform NOT NULL,
    archetype         archetype NOT NULL DEFAULT 'influencer',
    status            account_status NOT NULL DEFAULT 'draft',
    autonomy          autonomy_level NOT NULL DEFAULT 'L0',
    client_id         uuid REFERENCES clients(id) ON DELETE SET NULL,  -- [DORMENTE]

    -- Persona Card completo (voz, tabus, estética, linha editorial).
    -- Schema em docs/04-persona-card-spec.md.
    persona_card      jsonb NOT NULL DEFAULT '{}',
    persona_version   integer NOT NULL DEFAULT 1,

    -- Ligação com os serviços existentes
    persona_engine_id text,        -- id na máquina de influencers
    device_binding    jsonb NOT NULL DEFAULT '{}',  -- container/device no android-manager

    -- Ritmo natural da conta. O motor de campanhas NÃO pode ultrapassar isto.
    cadence           jsonb NOT NULL DEFAULT '{"posts_per_day": 1, "stories_per_day": 3}',
    timezone          text NOT NULL DEFAULT 'America/Sao_Paulo',
    -- Janela "acordada": fora dela o agente não age (parecer humano)
    active_hours      int4range NOT NULL DEFAULT '[8,23)',

    -- Estado dinâmico: humor/energia afetam tom e frequência
    mood              jsonb NOT NULL DEFAULT '{}',

    followers         integer NOT NULL DEFAULT 0,
    warmup_started_at timestamptz,
    last_action_at    timestamptz,
    notes             text,
    created_at        timestamptz NOT NULL DEFAULT now(),
    updated_at        timestamptz NOT NULL DEFAULT now(),

    UNIQUE (platform, handle)
);

CREATE INDEX idx_accounts_status     ON accounts(status) WHERE status = 'active';
CREATE INDEX idx_accounts_archetype  ON accounts(archetype);
CREATE INDEX idx_accounts_client     ON accounts(client_id);

-- Histórico de versões do Persona Card (auditoria: "por que ela mudou de voz?")
CREATE TABLE persona_versions (
    id           uuid PRIMARY KEY DEFAULT uuid_generate_v4(),
    account_id   uuid NOT NULL REFERENCES accounts(id) ON DELETE CASCADE,
    version      integer NOT NULL,
    persona_card jsonb NOT NULL,
    changed_by   text,
    change_note  text,
    created_at   timestamptz NOT NULL DEFAULT now(),
    UNIQUE (account_id, version)
);

-- =============================================================================
-- MEMÓRIA E NARRATIVA  (o "vida própria")
-- =============================================================================

CREATE TABLE story_arcs (
    id          uuid PRIMARY KEY DEFAULT uuid_generate_v4(),
    account_id  uuid NOT NULL REFERENCES accounts(id) ON DELETE CASCADE,
    title       text NOT NULL,
    summary     text NOT NULL,
    state       jsonb NOT NULL DEFAULT '{}',   -- onde está no arco
    starts_at   date NOT NULL,
    ends_at     date,
    active      boolean NOT NULL DEFAULT true,
    created_at  timestamptz NOT NULL DEFAULT now(),
    updated_at  timestamptz NOT NULL DEFAULT now()
);

CREATE INDEX idx_arcs_account_active ON story_arcs(account_id) WHERE active;

CREATE TABLE memories (
    id          uuid PRIMARY KEY DEFAULT uuid_generate_v4(),
    account_id  uuid NOT NULL REFERENCES accounts(id) ON DELETE CASCADE,
    kind        memory_kind NOT NULL,
    content     text NOT NULL,
    embedding   vector(1024),
    -- 0..10, atribuída pela LLM na escrita. Recuperação pondera
    -- relevância × recência × importância (ver generative_agents).
    importance  real NOT NULL DEFAULT 5.0,
    metadata    jsonb NOT NULL DEFAULT '{}',
    arc_id      uuid REFERENCES story_arcs(id) ON DELETE SET NULL,
    occurred_at timestamptz NOT NULL DEFAULT now(),
    last_used_at timestamptz,
    created_at  timestamptz NOT NULL DEFAULT now()
);

CREATE INDEX idx_memories_account_kind ON memories(account_id, kind, occurred_at DESC);
CREATE INDEX idx_memories_embedding    ON memories
    USING hnsw (embedding vector_cosine_ops);

-- Plano do dia: o output do passo PLAN do loop
CREATE TABLE day_plans (
    id           uuid PRIMARY KEY DEFAULT uuid_generate_v4(),
    account_id   uuid NOT NULL REFERENCES accounts(id) ON DELETE CASCADE,
    plan_date    date NOT NULL,
    narrative    text NOT NULL,          -- "hoje ela está na praia, humor alto"
    intents      jsonb NOT NULL,         -- lista de intenções → viram tasks
    reasoning    text,                   -- por que este plano (auditoria)
    llm_call_id  uuid,
    created_at   timestamptz NOT NULL DEFAULT now(),
    UNIQUE (account_id, plan_date)
);

-- =============================================================================
-- FONTES E INGESTÃO  [DORMENTE até a Fase 4 — fofoca]
-- =============================================================================

CREATE TABLE sources (
    id           uuid PRIMARY KEY DEFAULT uuid_generate_v4(),
    kind         source_kind NOT NULL,
    endpoint     text NOT NULL,        -- @handle, #tag, url...
    niche        text,
    reliability  real NOT NULL DEFAULT 0.5,   -- 0..1, alimenta o claim_status
    active       boolean NOT NULL DEFAULT true,
    metadata     jsonb NOT NULL DEFAULT '{}',
    created_at   timestamptz NOT NULL DEFAULT now(),
    UNIQUE (kind, endpoint)
);

CREATE TABLE ingested_items (
    id           uuid PRIMARY KEY DEFAULT uuid_generate_v4(),
    source_id    uuid REFERENCES sources(id) ON DELETE SET NULL,
    monitor_id   uuid,                          -- FK adicionada abaixo
    dedup_hash   text NOT NULL,                 -- evita reprocessar o mesmo furo
    payload      jsonb NOT NULL,
    media_refs   jsonb NOT NULL DEFAULT '[]',
    relevance    real,
    novelty      real,                          -- quão novo vs. o que já vimos
    consumed_by  uuid REFERENCES accounts(id) ON DELETE SET NULL,
    ingested_at  timestamptz NOT NULL DEFAULT now(),
    UNIQUE (dedup_hash)
);

CREATE INDEX idx_ingested_unconsumed ON ingested_items(ingested_at DESC)
    WHERE consumed_by IS NULL;

-- =============================================================================
-- MONITORES 24/7  (Fase 4 — mas o schema nasce agora)
-- =============================================================================
-- Um monitor é um vigia persistente: checa um alvo em intervalo curto,
-- detecta novidade e dispara produção de conteúdo. Diferente de `sources`
-- (catálogo) — o monitor é o processo vivo que observa.

CREATE TABLE monitors (
    id               uuid PRIMARY KEY DEFAULT uuid_generate_v4(),
    name             text NOT NULL,
    owner_account_id uuid REFERENCES accounts(id) ON DELETE CASCADE,
    source_id        uuid REFERENCES sources(id) ON DELETE SET NULL,
    status           monitor_status NOT NULL DEFAULT 'active',

    interval_seconds integer NOT NULL DEFAULT 300,
    -- Qual conta Android faz a coleta (rotaciona para não queimar uma só)
    collector_pool   jsonb NOT NULL DEFAULT '[]',
    -- Regras de disparo: o que conta como "novidade que merece post"
    trigger_rules    jsonb NOT NULL DEFAULT '{}',

    last_check_at    timestamptz,
    next_check_at    timestamptz,
    consecutive_errors integer NOT NULL DEFAULT 0,
    created_at       timestamptz NOT NULL DEFAULT now(),
    updated_at       timestamptz NOT NULL DEFAULT now()
);

CREATE INDEX idx_monitors_due ON monitors(next_check_at)
    WHERE status = 'active';

ALTER TABLE ingested_items
    ADD CONSTRAINT fk_ingested_monitor
    FOREIGN KEY (monitor_id) REFERENCES monitors(id) ON DELETE SET NULL;

CREATE TABLE monitor_hits (
    id            uuid PRIMARY KEY DEFAULT uuid_generate_v4(),
    monitor_id    uuid NOT NULL REFERENCES monitors(id) ON DELETE CASCADE,
    ingested_id   uuid REFERENCES ingested_items(id) ON DELETE SET NULL,
    triggered     boolean NOT NULL DEFAULT false,
    trigger_reason text,
    content_item_id uuid,                 -- FK adicionada abaixo
    detected_at   timestamptz NOT NULL DEFAULT now()
);

CREATE INDEX idx_monitor_hits_monitor ON monitor_hits(monitor_id, detected_at DESC);

-- =============================================================================
-- TENDÊNCIAS  (compartilhado pelos três arquétipos)
-- =============================================================================

CREATE TABLE trends (
    id          uuid PRIMARY KEY DEFAULT uuid_generate_v4(),
    platform    platform NOT NULL,
    kind        text NOT NULL,            -- audio | hashtag | format | topic
    ref         text NOT NULL,            -- id do áudio, texto da hashtag...
    label       text,
    score       real NOT NULL DEFAULT 0,
    metadata    jsonb NOT NULL DEFAULT '{}',
    window_start timestamptz NOT NULL DEFAULT now(),
    window_end   timestamptz,
    created_at  timestamptz NOT NULL DEFAULT now()
);

CREATE INDEX idx_trends_live ON trends(platform, kind, score DESC)
    WHERE window_end IS NULL;

-- =============================================================================
-- CONTEÚDO
-- =============================================================================

CREATE TABLE content_items (
    id            uuid PRIMARY KEY DEFAULT uuid_generate_v4(),
    account_id    uuid NOT NULL REFERENCES accounts(id) ON DELETE CASCADE,
    day_plan_id   uuid REFERENCES day_plans(id) ON DELETE SET NULL,
    campaign_id   uuid,                          -- FK adicionada abaixo
    kind          task_type NOT NULL,            -- publish_post|story|reels
    status        content_status NOT NULL DEFAULT 'draft',

    caption       text,
    hashtags      text[],
    audio_ref     text,
    media_refs    jsonb NOT NULL DEFAULT '[]',   -- refs no storage/máquina de influencers

    -- Proveniência: qual ContentSource gerou (generated|curated|derived)
    source_strategy text NOT NULL DEFAULT 'generated',
    source_refs   jsonb NOT NULL DEFAULT '[]',   -- [DORMENTE] de onde veio
    attribution   text,                          -- [DORMENTE] crédito obrigatório
    rights_status rights_status NOT NULL DEFAULT 'own',
    claim_status  claim_status NOT NULL DEFAULT 'unverified',  -- [DORMENTE]

    qa_report     jsonb NOT NULL DEFAULT '{}',
    reasoning     text,                          -- por que este conteúdo
    scheduled_for timestamptz,
    created_at    timestamptz NOT NULL DEFAULT now(),
    updated_at    timestamptz NOT NULL DEFAULT now()
);

CREATE INDEX idx_content_account_status ON content_items(account_id, status);
CREATE INDEX idx_content_pending_approval ON content_items(created_at)
    WHERE status = 'approval_pending';

ALTER TABLE monitor_hits
    ADD CONSTRAINT fk_hit_content
    FOREIGN KEY (content_item_id) REFERENCES content_items(id) ON DELETE SET NULL;

-- =============================================================================
-- CAMPANHAS  (incl. recon: olhar o conteúdo ANTES de planejar)
-- =============================================================================

CREATE TABLE campaigns (
    id             uuid PRIMARY KEY DEFAULT uuid_generate_v4(),
    name           text NOT NULL,
    kind           campaign_kind NOT NULL DEFAULT 'content',
    status         campaign_status NOT NULL DEFAULT 'draft',
    client_id      uuid REFERENCES clients(id) ON DELETE SET NULL,

    brief          text NOT NULL,               -- o que o humano pediu
    -- Alvo externo (campanhas de engajamento): post/perfil a ser inspecionado
    target_url     text,
    target_handle  text,

    brand_refs     jsonb NOT NULL DEFAULT '[]', -- assets de marca
    audio_ref      text,
    -- Filtro de elegibilidade de contas: nicho, arquétipo, followers mín...
    account_filter jsonb NOT NULL DEFAULT '{}',
    -- Espalhamento anti-detecção. OBRIGATÓRIO — ver docs/06.
    spread_policy  jsonb NOT NULL,

    window_start   timestamptz NOT NULL,
    window_end     timestamptz NOT NULL,
    created_by     text,
    created_at     timestamptz NOT NULL DEFAULT now(),
    updated_at     timestamptz NOT NULL DEFAULT now(),

    CONSTRAINT campaign_window_valid CHECK (window_end > window_start)
);

CREATE INDEX idx_campaigns_status ON campaigns(status);

ALTER TABLE content_items
    ADD CONSTRAINT fk_content_campaign
    FOREIGN KEY (campaign_id) REFERENCES campaigns(id) ON DELETE SET NULL;

-- RECON: o resultado de "mandar uma conta Android abrir o conteúdo e olhar".
-- Uma campanha de engajamento não pode ser planejada sem isto.
CREATE TABLE campaign_recon (
    id             uuid PRIMARY KEY DEFAULT uuid_generate_v4(),
    campaign_id    uuid NOT NULL REFERENCES campaigns(id) ON DELETE CASCADE,
    task_id        uuid,                        -- task extract_content que coletou
    collector_account_id uuid REFERENCES accounts(id) ON DELETE SET NULL,

    -- Bruto: o que a conta Android extraiu
    raw_capture    jsonb NOT NULL DEFAULT '{}', -- caption, autor, contadores...
    media_refs     jsonb NOT NULL DEFAULT '[]', -- screenshots/vídeo baixado
    top_comments   jsonb NOT NULL DEFAULT '[]',

    -- Analisado: o que a LLM (multimodal) entendeu
    analysis       jsonb NOT NULL DEFAULT '{}', -- tema, tom, público, gancho
    summary        text,
    sentiment      text,
    safety_verdict text,                        -- safe | risky | blocked
    safety_reasons text[],
    -- Ângulos de comentário sugeridos, para as personas adaptarem
    suggested_angles jsonb NOT NULL DEFAULT '[]',

    llm_call_id    uuid,
    captured_at    timestamptz,
    analyzed_at    timestamptz,
    created_at     timestamptz NOT NULL DEFAULT now()
);

CREATE INDEX idx_recon_campaign ON campaign_recon(campaign_id);

-- Quais contas participam e o que cada uma faz (adaptado à sua persona)
CREATE TABLE campaign_participants (
    id             uuid PRIMARY KEY DEFAULT uuid_generate_v4(),
    campaign_id    uuid NOT NULL REFERENCES campaigns(id) ON DELETE CASCADE,
    account_id     uuid NOT NULL REFERENCES accounts(id) ON DELETE CASCADE,
    -- Como esta persona traduziu o brief para a própria realidade
    local_adaptation text,
    -- Ações atribuídas: [{type: 'comment', text: '...', at: '...'}]
    actions        jsonb NOT NULL DEFAULT '[]',
    -- Slot sorteado dentro da janela (jitter anti-detecção)
    slot_at        timestamptz NOT NULL,
    skipped        boolean NOT NULL DEFAULT false,
    skip_reason    text,
    created_at     timestamptz NOT NULL DEFAULT now(),
    UNIQUE (campaign_id, account_id)
);

CREATE INDEX idx_participants_slot ON campaign_participants(slot_at)
    WHERE NOT skipped;

-- =============================================================================
-- TASKS  (a ponte com o android-orchestrator)
-- =============================================================================

CREATE TABLE tasks (
    id              uuid PRIMARY KEY DEFAULT uuid_generate_v4(),
    account_id      uuid REFERENCES accounts(id) ON DELETE CASCADE,
    type            task_type NOT NULL,
    status          task_status NOT NULL DEFAULT 'pending',
    priority        integer NOT NULL DEFAULT 100,   -- menor = mais urgente
    origin          text NOT NULL DEFAULT 'self',   -- manager|self|human|campaign|monitor

    payload         jsonb NOT NULL DEFAULT '{}',
    content_item_id uuid REFERENCES content_items(id) ON DELETE SET NULL,
    campaign_id     uuid REFERENCES campaigns(id) ON DELETE SET NULL,
    monitor_id      uuid REFERENCES monitors(id) ON DELETE SET NULL,

    scheduled_for   timestamptz NOT NULL DEFAULT now(),
    expires_at      timestamptz,                    -- furo que perdeu validade
    dispatched_at   timestamptz,
    verified_at     timestamptz,

    -- Ligação com o android-orchestrator
    external_task_id text,
    external_status  text,
    result          jsonb NOT NULL DEFAULT '{}',

    attempts        integer NOT NULL DEFAULT 0,
    max_attempts    integer NOT NULL DEFAULT 3,
    last_error      text,
    created_at      timestamptz NOT NULL DEFAULT now(),
    updated_at      timestamptz NOT NULL DEFAULT now()
);

-- O índice que o dispatcher usa a cada tick
CREATE INDEX idx_tasks_due ON tasks(scheduled_for, priority)
    WHERE status IN ('pending', 'scheduled');
CREATE INDEX idx_tasks_inflight ON tasks(external_task_id)
    WHERE status IN ('dispatched', 'running');
CREATE INDEX idx_tasks_account ON tasks(account_id, created_at DESC);

ALTER TABLE campaign_recon
    ADD CONSTRAINT fk_recon_task
    FOREIGN KEY (task_id) REFERENCES tasks(id) ON DELETE SET NULL;

-- =============================================================================
-- PUBLICAÇÕES E INTERAÇÕES
-- =============================================================================

CREATE TABLE posts (
    id              uuid PRIMARY KEY DEFAULT uuid_generate_v4(),
    account_id      uuid NOT NULL REFERENCES accounts(id) ON DELETE CASCADE,
    content_item_id uuid REFERENCES content_items(id) ON DELETE SET NULL,
    task_id         uuid REFERENCES tasks(id) ON DELETE SET NULL,
    platform        platform NOT NULL,
    external_id     text,
    permalink       text,
    published_at    timestamptz NOT NULL DEFAULT now(),
    created_at      timestamptz NOT NULL DEFAULT now()
);

CREATE INDEX idx_posts_account ON posts(account_id, published_at DESC);

CREATE TABLE post_metrics (
    id           uuid PRIMARY KEY DEFAULT uuid_generate_v4(),
    post_id      uuid NOT NULL REFERENCES posts(id) ON DELETE CASCADE,
    likes        integer NOT NULL DEFAULT 0,
    comments     integer NOT NULL DEFAULT 0,
    shares       integer NOT NULL DEFAULT 0,
    saves        integer NOT NULL DEFAULT 0,
    reach        integer NOT NULL DEFAULT 0,
    views        integer NOT NULL DEFAULT 0,
    raw          jsonb NOT NULL DEFAULT '{}',
    collected_at timestamptz NOT NULL DEFAULT now()
);

CREATE INDEX idx_metrics_post ON post_metrics(post_id, collected_at DESC);

CREATE TABLE interactions (
    id            uuid PRIMARY KEY DEFAULT uuid_generate_v4(),
    account_id    uuid NOT NULL REFERENCES accounts(id) ON DELETE CASCADE,
    post_id       uuid REFERENCES posts(id) ON DELETE SET NULL,
    kind          text NOT NULL,             -- comment | dm | mention
    external_id   text,
    author_handle text,
    body          text NOT NULL,
    -- fan | hater | spam | opportunity | sensitive
    classification text,
    -- Sensível (saúde, dinheiro, romance, assédio) NUNCA é auto-respondido
    sensitive     boolean NOT NULL DEFAULT false,
    reply_body    text,
    replied_by    text,                      -- agent | human | none
    replied_at    timestamptz,
    received_at   timestamptz NOT NULL DEFAULT now(),
    created_at    timestamptz NOT NULL DEFAULT now()
);

CREATE INDEX idx_interactions_pending ON interactions(account_id, received_at DESC)
    WHERE replied_at IS NULL;

-- =============================================================================
-- GOVERNANÇA: aprovações, guardrails, auditoria, custo de LLM
-- =============================================================================

CREATE TABLE approvals (
    id            uuid PRIMARY KEY DEFAULT uuid_generate_v4(),
    subject_type  text NOT NULL,            -- content_item | campaign | interaction
    subject_id    uuid NOT NULL,
    account_id    uuid REFERENCES accounts(id) ON DELETE CASCADE,
    decision      text,                     -- approved | rejected | edited
    decided_by    text,
    note          text,
    edited_payload jsonb,
    -- true quando a revisão veio de amostragem (L2), não de exigência (L0/L1)
    sampled       boolean NOT NULL DEFAULT false,
    requested_at  timestamptz NOT NULL DEFAULT now(),
    decided_at    timestamptz
);

CREATE INDEX idx_approvals_open ON approvals(requested_at)
    WHERE decided_at IS NULL;

CREATE TABLE guardrail_violations (
    id           uuid PRIMARY KEY DEFAULT uuid_generate_v4(),
    account_id   uuid REFERENCES accounts(id) ON DELETE CASCADE,
    subject_type text,
    subject_id   uuid,
    rule         text NOT NULL,            -- id da regra violada
    severity     text NOT NULL,            -- info | warn | block | kill
    detail       jsonb NOT NULL DEFAULT '{}',
    action_taken text,                     -- blocked | flagged | suspended
    created_at   timestamptz NOT NULL DEFAULT now()
);

CREATE INDEX idx_violations_recent ON guardrail_violations(created_at DESC);

-- Auditoria total: toda decisão do sistema vira uma linha aqui.
-- "Por que a conta X postou isso às 19h?" precisa ser respondível.
CREATE TABLE events (
    id          bigserial PRIMARY KEY,
    account_id  uuid REFERENCES accounts(id) ON DELETE SET NULL,
    campaign_id uuid REFERENCES campaigns(id) ON DELETE SET NULL,
    task_id     uuid REFERENCES tasks(id) ON DELETE SET NULL,
    type        text NOT NULL,
    detail      jsonb NOT NULL DEFAULT '{}',
    trace_id    text,
    created_at  timestamptz NOT NULL DEFAULT now()
);

CREATE INDEX idx_events_account ON events(account_id, created_at DESC);
CREATE INDEX idx_events_type    ON events(type, created_at DESC);
CREATE INDEX idx_events_trace   ON events(trace_id);

-- Custo e latência de cada chamada de LLM (Mac mini vs API externa)
CREATE TABLE llm_calls (
    id             uuid PRIMARY KEY DEFAULT uuid_generate_v4(),
    account_id     uuid REFERENCES accounts(id) ON DELETE SET NULL,
    purpose        text NOT NULL,          -- plan_day | caption | recon_analysis...
    model          text NOT NULL,
    backend        text NOT NULL,          -- mac_mini_1 | mac_mini_2 | anthropic...
    prompt_tokens  integer,
    output_tokens  integer,
    latency_ms     integer,
    cost_usd       numeric(10,6) NOT NULL DEFAULT 0,
    success        boolean NOT NULL DEFAULT true,
    error          text,
    trace_id       text,
    created_at     timestamptz NOT NULL DEFAULT now()
);

CREATE INDEX idx_llm_calls_time ON llm_calls(created_at DESC);

ALTER TABLE day_plans
    ADD CONSTRAINT fk_plan_llm FOREIGN KEY (llm_call_id)
    REFERENCES llm_calls(id) ON DELETE SET NULL;
ALTER TABLE campaign_recon
    ADD CONSTRAINT fk_recon_llm FOREIGN KEY (llm_call_id)
    REFERENCES llm_calls(id) ON DELETE SET NULL;

-- =============================================================================
-- TRIGGERS: updated_at automático
-- =============================================================================

CREATE OR REPLACE FUNCTION touch_updated_at() RETURNS trigger AS $$
BEGIN
    NEW.updated_at = now();
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

DO $$
DECLARE t text;
BEGIN
    FOREACH t IN ARRAY ARRAY[
        'clients','accounts','story_arcs','monitors','content_items',
        'campaigns','tasks'
    ] LOOP
        EXECUTE format(
            'CREATE TRIGGER trg_%1$s_touch BEFORE UPDATE ON %1$s
             FOR EACH ROW EXECUTE FUNCTION touch_updated_at()', t);
    END LOOP;
END $$;
