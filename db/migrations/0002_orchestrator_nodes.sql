-- =============================================================================
-- Olimpo — Migração 0002: registro de nós do android-orchestrator
-- =============================================================================
-- Motivo: uma instância do android-orchestrator controla APENAS os containers
-- redroid do próprio host — ela descobre por `docker ps` local e executa por
-- `docker exec` local. Não existe controle remoto nem índice de frota: cada
-- nó só conhece a si mesmo, com SQLite próprio, fila própria e auth própria.
--
-- Consequência: **o dono da lista de nós é o Olimpo**. A visão de frota se
-- monta por fan-out (bater /accounts, /devices e /actions em cada nó).
--
-- Consequência 2: os ids de task do orchestrator são inteiros autoincrement
-- POR NÓ — a task 42 do nó A não tem relação com a 42 do nó B. A chave real
-- é o par (node_id, external_task_id).
--
-- Ver docs/contratos/android-orchestrator.md §9.
-- =============================================================================

CREATE TYPE node_status AS ENUM ('active', 'degraded', 'unreachable', 'disabled');

CREATE TABLE orchestrator_nodes (
    id            uuid PRIMARY KEY DEFAULT uuid_generate_v4(),
    name          text NOT NULL UNIQUE,      -- "i9-14900k", "fd-server-1"
    -- SEMPRE a porta do worker (8078). O executor (8077) monta o docker.sock
    -- e escuta só em loopback: quem fala com ele tem root no host.
    base_url      text NOT NULL,
    -- Auth é opt-in no serviço e pode diferir por nó: um nó pode estar aberto
    -- e outro exigir Basic. Por isso ambos são anuláveis.
    basic_user    text,
    basic_password text,
    status        node_status NOT NULL DEFAULT 'active',

    -- Peso para roteamento quando a mesma conta existe em mais de um nó.
    priority      integer NOT NULL DEFAULT 100,
    -- Teto de tasks simultâneas neste nó. Um device = uma raia: o nó
    -- serializa por container, então capacidade se mede em devices.
    max_concurrent integer NOT NULL DEFAULT 4,

    -- Inventário obtido por fan-out. Cache: /accounts?refresh=1 custa ~90s.
    devices_cache jsonb NOT NULL DEFAULT '[]',
    accounts_cache jsonb NOT NULL DEFAULT '[]',
    -- O catálogo de ações pode divergir entre nós (versões diferentes do
    -- código). Sempre validar params contra o /actions DO NÓ, nunca contra
    -- uma lista fixa no código.
    actions_cache jsonb NOT NULL DEFAULT '{}',
    inventory_refreshed_at timestamptz,

    last_seen_at  timestamptz,
    last_error    text,
    consecutive_errors integer NOT NULL DEFAULT 0,
    version       text,
    notes         text,
    created_at    timestamptz NOT NULL DEFAULT now(),
    updated_at    timestamptz NOT NULL DEFAULT now()
);

CREATE INDEX idx_nodes_active ON orchestrator_nodes(priority)
    WHERE status = 'active';

CREATE TRIGGER trg_orchestrator_nodes_touch BEFORE UPDATE ON orchestrator_nodes
    FOR EACH ROW EXECUTE FUNCTION touch_updated_at();

-- =============================================================================
-- ONDE CADA CONTA VIVE
-- =============================================================================
-- A mesma conta pode estar logada em containers de nós diferentes — o que dá
-- paralelismo, mas também o risco de a mesma conta operar em duas telas ao
-- mesmo tempo. O nó serializa por device; serializar por CONTA é obrigação
-- do Olimpo (lock no Redis antes do dispatch).

CREATE TABLE account_placements (
    id           uuid PRIMARY KEY DEFAULT uuid_generate_v4(),
    account_id   uuid NOT NULL REFERENCES accounts(id) ON DELETE CASCADE,
    node_id      uuid NOT NULL REFERENCES orchestrator_nodes(id) ON DELETE CASCADE,
    container    text NOT NULL,            -- "google-pixel-9-pro-1"
    android_user integer,                  -- user Android dentro do container
    logged_in    boolean NOT NULL DEFAULT false,
    last_seen_at timestamptz NOT NULL DEFAULT now(),
    created_at   timestamptz NOT NULL DEFAULT now(),
    UNIQUE (account_id, node_id, container)
);

CREATE INDEX idx_placements_account ON account_placements(account_id)
    WHERE logged_in;
CREATE INDEX idx_placements_node ON account_placements(node_id);

-- =============================================================================
-- TASKS: ligação com o nó
-- =============================================================================

ALTER TABLE tasks
    ADD COLUMN node_id uuid REFERENCES orchestrator_nodes(id) ON DELETE SET NULL,
    -- id do job no executor; é onde vive o resultado real (ok, data, verified)
    ADD COLUMN external_job_id text,
    -- ação qualificada como o orchestrator exige: "instagram.post-feed"
    ADD COLUMN external_action text,
    -- Polling: não existe webhook no orchestrator. Ver ADR-0011.
    ADD COLUMN next_poll_at timestamptz,
    ADD COLUMN poll_count integer NOT NULL DEFAULT 0;

-- A chave real de uma task externa é o par (nó, id) — ids colidem entre nós.
CREATE UNIQUE INDEX idx_tasks_external_key
    ON tasks(node_id, external_task_id)
    WHERE external_task_id IS NOT NULL;

-- O índice que o poller usa a cada tick.
CREATE INDEX idx_tasks_awaiting_poll ON tasks(next_poll_at)
    WHERE status IN ('dispatched', 'running');

-- =============================================================================
-- EVIDÊNCIA DE EXECUÇÃO
-- =============================================================================
-- `ok: true` significa que o comando saiu com exit 0 — não que a ação teve
-- efeito. Só algumas ações populam `data.verified` / `published` / `sent`.
-- `like` e `comment` devolvem data vazio: não há recibo.
--
-- Guardamos os dois separados para que "publicou mesmo?" seja respondível
-- sem reinterpretar JSON solto.

ALTER TABLE tasks
    ADD COLUMN evidence jsonb NOT NULL DEFAULT '{}',
    -- verified  → o nó confirmou pelo efeito na tela (data.verified/published)
    -- reported  → exit 0, mas a ação não produz prova
    -- unknown   → timeout/restart no meio: PODE ter acontecido. Nunca retentar
    --             automaticamente quando a ação publica.
    ADD COLUMN evidence_level text NOT NULL DEFAULT 'none';

COMMENT ON COLUMN tasks.evidence_level IS
    'none|reported|verified|unknown — unknown exige verificação humana';
