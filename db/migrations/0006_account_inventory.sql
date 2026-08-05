-- =============================================================================
-- Olimpo — Migração 0006: inventário real das contas
-- =============================================================================
-- A sincronização buscava as contas do orchestrator e jogava fora quase tudo:
-- só o handle sobrevivia. O serviço devolve, em cada varredura, o id da conta
-- na plataforma, seguidores, seguindo, número de publicações, qual conta está
-- em primeiro plano no device e o JID no WhatsApp.
--
-- Sem esses campos a lista de contas mostra "0 seguidores" para todo mundo —
-- tecnicamente sincronizada, praticamente inútil.
-- =============================================================================

ALTER TABLE accounts
    -- Id da conta NA PLATAFORMA (o "32822973107" do Instagram). É o único
    -- identificador estável: handle muda, id não.
    ADD COLUMN external_id text,
    ADD COLUMN following integer NOT NULL DEFAULT 0,
    ADD COLUMN media_count integer NOT NULL DEFAULT 0,
    -- Telefone/JID no WhatsApp. Nunca preenchido em conta de rede social.
    ADD COLUMN jid text,
    ADD COLUMN last_synced_at timestamptz,
    -- Origem do registro: importado do inventário de um nó ou criado à mão.
    ADD COLUMN discovered_from uuid REFERENCES servers(id) ON DELETE SET NULL;

-- Um id externo é único por plataforma. Duas linhas com o mesmo id seriam a
-- mesma conta duplicada — o que acontece quando o handle muda entre varreduras.
CREATE UNIQUE INDEX idx_accounts_external
    ON accounts(platform, external_id)
    WHERE external_id IS NOT NULL;

ALTER TABLE account_placements
    ADD COLUMN external_id text,
    -- Métricas por LUGAR, não por conta: a mesma conta em dois devices pode
    -- reportar números diferentes, e a divergência é sinal de problema.
    ADD COLUMN followers integer,
    ADD COLUMN following integer,
    ADD COLUMN media_count integer;

-- Histórico de sincronização, para responder "desde quando esta conta sumiu
-- do inventário?" — pergunta que aparece toda vez que uma conta cai.
CREATE TABLE inventory_syncs (
    id             uuid PRIMARY KEY DEFAULT uuid_generate_v4(),
    server_id      uuid REFERENCES servers(id) ON DELETE CASCADE,
    service_id     uuid REFERENCES server_services(id) ON DELETE SET NULL,
    kind           text NOT NULL,          -- accounts | devices | actions | full
    ok             boolean NOT NULL,
    accounts_found integer NOT NULL DEFAULT 0,
    accounts_new   integer NOT NULL DEFAULT 0,
    accounts_gone  integer NOT NULL DEFAULT 0,
    devices_found  integer NOT NULL DEFAULT 0,
    duration_ms    integer,
    forced_refresh boolean NOT NULL DEFAULT false,
    error          text,
    detail         jsonb NOT NULL DEFAULT '{}',
    created_at     timestamptz NOT NULL DEFAULT now()
);

CREATE INDEX idx_syncs_server ON inventory_syncs(server_id, created_at DESC);
