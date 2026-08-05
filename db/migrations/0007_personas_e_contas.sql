-- =============================================================================
-- Olimpo — Migração 0007: separar a PERSONA da CONTA
-- =============================================================================
-- O modelo até aqui tratava "conta" como sendo tudo ao mesmo tempo: a
-- identidade fictícia, o login numa plataforma e o lugar onde ela roda. Três
-- coisas diferentes, com ciclos de vida diferentes:
--
--   • A Luna Prado (a personagem) existe mesmo que nenhum login exista.
--   • O @luna.prado do Instagram é um login — pode ser banido e recriado.
--   • O WhatsApp da Luna é OUTRO login, da MESMA personagem.
--
-- Duas consequências práticas do modelo antigo, ambas ruins:
--
-- 1. Uma conta só existia se um device a revelasse. Não dava para cadastrar a
--    persona primeiro e vincular o login depois — que é a ordem natural.
-- 2. A mesma personagem em Instagram e WhatsApp virava duas entidades sem
--    relação, cada uma com sua memória. A personagem tem UMA vida.
--
-- O modelo passa a ser:
--
--   personas      a identidade: voz, história, estética, memória, arcos
--     └─ accounts    os logins dela — um por plataforma, ou vários
--          └─ account_placements   onde cada login está rodando agora
--
-- Sair de todos os devices NUNCA apaga nada: some o placement, o login
-- continua, a persona continua. Ver ADR-0013.
-- =============================================================================

-- =============================================================================
-- PERSONAS
-- =============================================================================

CREATE TABLE personas (
    id           uuid PRIMARY KEY DEFAULT uuid_generate_v4(),
    name         text NOT NULL,             -- "Luna Prado"
    slug         text NOT NULL UNIQUE,      -- "luna-prado"
    description  text,

    -- Voz, lore, estética, tabus. Ver docs/04-persona-card-spec.md.
    persona_card jsonb NOT NULL DEFAULT '{}',
    persona_version integer NOT NULL DEFAULT 1,
    -- Vínculo com a máquina de influencers: a referência facial vive lá, e
    -- é ela que impede a personagem de mudar de rosto entre posts.
    persona_engine_id text,

    client_id    uuid REFERENCES clients(id) ON DELETE SET NULL,
    labels       text[] NOT NULL DEFAULT '{}',
    notes        text,
    active       boolean NOT NULL DEFAULT true,
    created_at   timestamptz NOT NULL DEFAULT now(),
    updated_at   timestamptz NOT NULL DEFAULT now()
);

CREATE INDEX idx_personas_active ON personas(name) WHERE active;
CREATE INDEX idx_personas_labels ON personas USING gin (labels);

CREATE TRIGGER trg_personas_touch BEFORE UPDATE ON personas
    FOR EACH ROW EXECUTE FUNCTION touch_updated_at();

-- =============================================================================
-- CONTAS: o que muda
-- =============================================================================

ALTER TABLE accounts
    -- Nulo é legítimo: uma conta coletora não representa personagem nenhuma.
    ADD COLUMN persona_id uuid REFERENCES personas(id) ON DELETE SET NULL,

    -- Papel operacional, diferente do arquétipo. Uma conta pode existir só
    -- para observar (recon, monitores) e nunca publicar.
    ADD COLUMN role text NOT NULL DEFAULT 'agent',

    -- Dados operacionais do login: e-mail, telefone, data de criação,
    -- origem, proxy, o que o operador precisar. Sem schema fixo de
    -- propósito — cada operação tem seus campos.
    -- ⚠️ NÃO guarde senha aqui: a coluna não é cifrada. Use `secret_ref`
    -- para apontar onde a credencial realmente vive.
    ADD COLUMN profile jsonb NOT NULL DEFAULT '{}',
    ADD COLUMN secret_ref text,

    ADD COLUMN labels text[] NOT NULL DEFAULT '{}',
    ADD COLUMN metadata jsonb NOT NULL DEFAULT '{}',

    -- Presença: quando o inventário viu este login pela última vez.
    -- Distinto de `status`, que é intenção. Uma conta pode estar `active`
    -- (queremos que opere) e há três dias fora do inventário (não dá).
    ADD COLUMN last_seen_in_inventory timestamptz,

    -- Handle muda; id da plataforma não. Guardar o anterior evita concluir
    -- que a conta sumiu quando ela só se renomeou.
    ADD COLUMN previous_handles text[] NOT NULL DEFAULT '{}';

CREATE INDEX idx_accounts_persona ON accounts(persona_id);
CREATE INDEX idx_accounts_labels  ON accounts USING gin (labels);
-- Busca por handle em lista grande: trigram serve para "contém", que é o
-- que o operador digita.
CREATE INDEX idx_accounts_handle_trgm ON accounts USING gin (handle gin_trgm_ops);

ALTER TABLE accounts
    ADD CONSTRAINT accounts_role_valido
    CHECK (role IN ('agent', 'collector', 'utility'));

-- 'draft' não dizia nada a quem opera. O estado é: cadastrada, ainda não
-- liberada para agir.
ALTER TYPE account_status RENAME VALUE 'draft' TO 'registered';

-- =============================================================================
-- OBSERVAÇÕES
-- =============================================================================
-- `accounts.notes` é um campo de texto que só a última pessoa a escrever
-- sobrevive. Operação real precisa de histórico: quem anotou o quê e quando.

CREATE TABLE entity_notes (
    id           uuid PRIMARY KEY DEFAULT uuid_generate_v4(),
    subject_type text NOT NULL,            -- account | persona | device | server
    subject_id   uuid NOT NULL,
    body         text NOT NULL,
    author       text,
    -- Anotação fixada aparece sempre no topo da ficha: "esta conta já caiu
    -- uma vez", "não usar para publi".
    pinned       boolean NOT NULL DEFAULT false,
    created_at   timestamptz NOT NULL DEFAULT now()
);

CREATE INDEX idx_notes_subject ON entity_notes(subject_type, subject_id, created_at DESC);
CREATE INDEX idx_notes_pinned  ON entity_notes(subject_type, subject_id) WHERE pinned;

-- =============================================================================
-- MEMÓRIA E ARCOS PERTENCEM À PERSONA
-- =============================================================================
-- A personagem tem uma vida só. Se a memória ficasse presa ao login, perder
-- uma conta apagaria a história — e a mesma personagem no Instagram e no
-- WhatsApp lembraria de coisas diferentes.

ALTER TABLE memories
    ADD COLUMN persona_id uuid REFERENCES personas(id) ON DELETE CASCADE;
ALTER TABLE story_arcs
    ADD COLUMN persona_id uuid REFERENCES personas(id) ON DELETE CASCADE;
ALTER TABLE day_plans
    ADD COLUMN persona_id uuid REFERENCES personas(id) ON DELETE CASCADE;

CREATE INDEX idx_memories_persona ON memories(persona_id, kind, occurred_at DESC);
CREATE INDEX idx_arcs_persona     ON story_arcs(persona_id) WHERE active;

-- =============================================================================
-- MIGRAÇÃO DOS DADOS
-- =============================================================================
-- Toda conta que já tem persona_card preenchido vira uma persona; as demais
-- (importadas do inventário, sem persona escrita) ficam sem vínculo, para o
-- operador decidir a quem pertencem.

INSERT INTO personas (name, slug, persona_card, persona_version, persona_engine_id, notes)
SELECT COALESCE(a.persona_card -> 'identity' ->> 'name', a.handle),
       regexp_replace(lower(trim(leading '@' from a.handle)), '[^a-z0-9]+', '-', 'g'),
       a.persona_card, a.persona_version, a.persona_engine_id,
       'criada na migração 0007 a partir da conta ' || a.handle
  FROM accounts a
 WHERE a.persona_card <> '{}'::jsonb
 ON CONFLICT (slug) DO NOTHING;

UPDATE accounts a
   SET persona_id = p.id
  FROM personas p
 WHERE p.slug = regexp_replace(lower(trim(leading '@' from a.handle)), '[^a-z0-9]+', '-', 'g')
   AND a.persona_card <> '{}'::jsonb;

UPDATE memories m SET persona_id = a.persona_id
  FROM accounts a WHERE a.id = m.account_id AND a.persona_id IS NOT NULL;
UPDATE story_arcs s SET persona_id = a.persona_id
  FROM accounts a WHERE a.id = s.account_id AND a.persona_id IS NOT NULL;
UPDATE day_plans d SET persona_id = a.persona_id
  FROM accounts a WHERE a.id = d.account_id AND a.persona_id IS NOT NULL;

-- Contas de mensageria e coletoras não são agentes de conteúdo.
UPDATE accounts SET role = 'utility' WHERE archetype = 'messaging';
UPDATE accounts SET role = 'collector' WHERE archetype = 'collector';

-- Presença inicial a partir do que o inventário já sabia.
UPDATE accounts a
   SET last_seen_in_inventory = sub.visto
  FROM (SELECT account_id, max(last_seen_at) AS visto
          FROM account_placements GROUP BY account_id) sub
 WHERE sub.account_id = a.id;

-- `accounts.persona_card` fica como herança: a fonte da verdade passa a ser
-- `personas.persona_card`. Não removemos a coluna nesta migração para que um
-- erro de leitura não derrube o sistema em silêncio — ela some na 0008,
-- depois de o código estar todo migrado.
COMMENT ON COLUMN accounts.persona_card IS
    'HERANÇA — use personas.persona_card. Removida na 0008.';
COMMENT ON COLUMN accounts.device_binding IS
    'HERANÇA — use account_placements. Removida na 0008.';

-- Correção de semântica que pertencia à 0005, mas migração aplicada não se
-- edita: o comentário original dizia que este campo era "primeiro plano".
COMMENT ON COLUMN account_placements.foreground IS
    'Usuário Android EM EXECUÇÃO. NÃO é "está na tela": o Android mantém '
    'vários usuários rodando e o orchestrator reporta todos como ativos. '
    'Qual está em primeiro plano nenhum serviço informa.';
