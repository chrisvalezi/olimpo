-- =============================================================================
-- Olimpo — Migração 0005: servidores, serviços e devices
-- =============================================================================
-- Até aqui um "nó" era sinônimo de "uma instância do android-orchestrator".
-- Isso não sobrevive ao contato com a realidade: o MESMO host roda o
-- orchestrator (8078) E o android-manager (443/4000) E o gateway de tela
-- (9000). São endereços, credenciais, TLS e saúde diferentes — de um único
-- servidor físico.
--
-- O modelo passa a ser:
--
--   servers            um host. Identidade, endereços, rótulos.
--     └─ server_services   cada API HTTP que roda nele
--          └─ orchestrator_nodes   dados específicos do orchestrator
--     └─ devices            containers redroid daquele host
--
-- Ver ADR-0012.
-- =============================================================================

CREATE TYPE service_kind AS ENUM (
    'orchestrator',    -- android-orchestrator worker (executa ações)
    'android_manager', -- painel da frota redroid (leitura para nós)
    'screen_gateway',  -- transmissão de tela dos devices
    'persona_engine',  -- máquina de influencers
    'media_forge',     -- cortes de vídeo (Fase 5)
    'custom'
);

CREATE TYPE auth_scheme AS ENUM (
    'none',
    'basic',    -- Authorization: Basic — orchestrator worker, nginx
    'bearer',   -- token estático no header
    'login'     -- POST de login devolve token com validade — android-manager
);

CREATE TYPE service_status AS ENUM (
    'unknown', 'online', 'degraded', 'offline', 'unauthorized', 'disabled'
);

CREATE TYPE server_status AS ENUM ('active', 'degraded', 'unreachable', 'disabled');

-- =============================================================================
-- SERVIDORES
-- =============================================================================

CREATE TABLE servers (
    id           uuid PRIMARY KEY DEFAULT uuid_generate_v4(),
    name         text NOT NULL UNIQUE,     -- "i9-14900k", "media-dev"
    description  text,

    -- Vários caminhos para o mesmo host. O painel escolhe o melhor para
    -- montar link clicável; o core usa `preferred_host` para falar.
    hostname     text,                     -- i9-14900k.taila41397.ts.net
    tailscale_ip text,
    lan_ip       text,
    preferred_host text,                   -- qual usar por padrão

    status       server_status NOT NULL DEFAULT 'active',
    labels       text[] NOT NULL DEFAULT '{}',
    notes        text,
    created_at   timestamptz NOT NULL DEFAULT now(),
    updated_at   timestamptz NOT NULL DEFAULT now()
);

CREATE TRIGGER trg_servers_touch BEFORE UPDATE ON servers
    FOR EACH ROW EXECUTE FUNCTION touch_updated_at();

-- =============================================================================
-- SERVIÇOS
-- =============================================================================

CREATE TABLE server_services (
    id           uuid PRIMARY KEY DEFAULT uuid_generate_v4(),
    server_id    uuid NOT NULL REFERENCES servers(id) ON DELETE CASCADE,
    kind         service_kind NOT NULL,
    label        text,                     -- distingue dois do mesmo tipo

    base_url     text NOT NULL,            -- onde a API responde
    -- Onde ABRIR NO NAVEGADOR. Nem sempre igual à API: o android-manager
    -- atende a API em /api mas a interface na raiz, atrás de HTTPS.
    web_url      text,
    health_path  text NOT NULL DEFAULT '/healthz',

    auth_scheme  auth_scheme NOT NULL DEFAULT 'none',
    auth_user    text,
    auth_secret  text,
    -- Certificado autoassinado é a regra em rede interna. Desligar a
    -- verificação é uma decisão POR SERVIÇO, nunca global.
    verify_tls   boolean NOT NULL DEFAULT true,

    enabled      boolean NOT NULL DEFAULT true,
    status       service_status NOT NULL DEFAULT 'unknown',
    last_seen_at timestamptz,
    last_error   text,
    consecutive_errors integer NOT NULL DEFAULT 0,
    latency_ms   integer,
    version      text,

    -- O que este serviço sabe fazer, descoberto na sondagem. Guardado em
    -- vez de deduzido para que a interface saiba o que oferecer sem
    -- precisar conhecer cada serviço.
    capabilities jsonb NOT NULL DEFAULT '{}',
    metadata     jsonb NOT NULL DEFAULT '{}',

    created_at   timestamptz NOT NULL DEFAULT now(),
    updated_at   timestamptz NOT NULL DEFAULT now(),

    UNIQUE (server_id, kind, label)
);

CREATE INDEX idx_services_server ON server_services(server_id);
CREATE INDEX idx_services_kind   ON server_services(kind) WHERE enabled;

CREATE TRIGGER trg_services_touch BEFORE UPDATE ON server_services
    FOR EACH ROW EXECUTE FUNCTION touch_updated_at();

-- =============================================================================
-- DEVICES
-- =============================================================================
-- Antes os containers viviam apenas em `orchestrator_nodes.devices_cache`,
-- que é bom para exibir e ruim para tudo o mais: não dá para referenciar,
-- não guarda histórico e não acomoda os campos que a transmissão de tela
-- exige.

CREATE TABLE devices (
    id            uuid PRIMARY KEY DEFAULT uuid_generate_v4(),
    server_id     uuid NOT NULL REFERENCES servers(id) ON DELETE CASCADE,
    container     text NOT NULL,           -- "samsung-galaxy-s24-ultra-2"

    alive         boolean NOT NULL DEFAULT false,
    android_version text,
    -- Endereço ADB (ip:5555). O gateway de tela precisa dele como alvo.
    adb_target    text,
    -- /dev/videoN do v4l2loopback. Sem isto não há transmissão contínua —
    -- o modo alternativo é monoviewer e derruba quem já estava assistindo.
    video_device  text,
    screencast_capable boolean NOT NULL DEFAULT false,

    -- Outro sistema mexendo neste device (watchdog do android-manager, por
    -- exemplo) é motivo para NÃO despachar ação nossa aqui.
    under_external_management boolean NOT NULL DEFAULT false,

    labels        text[] NOT NULL DEFAULT '{}',
    last_seen_at  timestamptz NOT NULL DEFAULT now(),
    metadata      jsonb NOT NULL DEFAULT '{}',
    created_at    timestamptz NOT NULL DEFAULT now(),
    updated_at    timestamptz NOT NULL DEFAULT now(),

    UNIQUE (server_id, container)
);

CREATE INDEX idx_devices_server ON devices(server_id);
CREATE INDEX idx_devices_alive  ON devices(server_id) WHERE alive;

CREATE TRIGGER trg_devices_touch BEFORE UPDATE ON devices
    FOR EACH ROW EXECUTE FUNCTION touch_updated_at();

-- =============================================================================
-- LIGAÇÕES
-- =============================================================================

ALTER TABLE orchestrator_nodes
    ADD COLUMN server_id  uuid REFERENCES servers(id) ON DELETE CASCADE,
    ADD COLUMN service_id uuid REFERENCES server_services(id) ON DELETE SET NULL;

ALTER TABLE account_placements
    ADD COLUMN device_id uuid REFERENCES devices(id) ON DELETE SET NULL,
    -- Usuário Android em execução. ⚠️ NÃO é "está na tela": o Android
    -- mantém vários usuários rodando ao mesmo tempo, e o orchestrator
    -- reporta todos como ativos. Qual está em primeiro plano ele não diz.
    ADD COLUMN foreground boolean NOT NULL DEFAULT false;

-- =============================================================================
-- MIGRAÇÃO DOS DADOS EXISTENTES
-- =============================================================================
-- Cada nó vira um servidor com um serviço do tipo orchestrator.

INSERT INTO servers (name, preferred_host, notes)
SELECT n.name,
       regexp_replace(n.base_url, '^https?://([^:/]+).*$', '\1'),
       'criado automaticamente na migração 0005'
  FROM orchestrator_nodes n
 ON CONFLICT (name) DO NOTHING;

INSERT INTO server_services
    (server_id, kind, base_url, web_url, health_path,
     auth_scheme, auth_user, auth_secret, status, last_seen_at,
     last_error, consecutive_errors, capabilities)
SELECT s.id, 'orchestrator', n.base_url, n.base_url, '/healthz',
       CASE WHEN n.basic_user IS NOT NULL THEN 'basic'::auth_scheme
            ELSE 'none'::auth_scheme END,
       n.basic_user, n.basic_password,
       CASE n.status WHEN 'active' THEN 'online'::service_status
                     WHEN 'degraded' THEN 'degraded'::service_status
                     WHEN 'unreachable' THEN 'offline'::service_status
                     ELSE 'disabled'::service_status END,
       n.last_seen_at, n.last_error, n.consecutive_errors,
       jsonb_build_object('platforms', to_jsonb(n.platforms))
  FROM orchestrator_nodes n
  JOIN servers s ON s.name = n.name;

UPDATE orchestrator_nodes n
   SET server_id  = s.id,
       service_id = sv.id
  FROM servers s
  JOIN server_services sv ON sv.server_id = s.id AND sv.kind = 'orchestrator'
 WHERE s.name = n.name;

-- Devices que já estavam no cache viram linhas de verdade.
INSERT INTO devices (server_id, container, alive, last_seen_at)
SELECT n.server_id,
       d->>'container',
       COALESCE((d->>'alive')::boolean, false),
       COALESCE(n.inventory_refreshed_at, now())
  FROM orchestrator_nodes n, jsonb_array_elements(n.devices_cache) d
 WHERE n.server_id IS NOT NULL AND d->>'container' IS NOT NULL
 ON CONFLICT (server_id, container) DO NOTHING;

UPDATE account_placements p
   SET device_id = dv.id
  FROM orchestrator_nodes n
  JOIN devices dv ON dv.server_id = n.server_id
 WHERE p.node_id = n.id AND dv.container = p.container;
