-- =============================================================================
-- Olimpo — Migração 0008: absorve a máquina de influencers
-- =============================================================================
-- A máquina de influencers tinha a própria tabela `influencers`, que é a MESMA
-- entidade que `personas` — quase campo a campo. Duas fontes de verdade para a
-- identidade de uma personagem é dívida que só cresce; o momento de menor
-- custo para pagar é agora, antes de qualquer conta operar.
--
-- O que entra:
--   • os campos que ALIMENTAM a geração de imagem (gênero, descrição física,
--     energia de personalidade, nicho) viram colunas de verdade, porque o
--     motor de prompt os lê a cada geração e eles precisam ser validáveis
--   • as 4 imagens de identidade viram referências para `media_assets`
--   • guarda-roupa, marcas, mídia e rascunhos de vídeo viram tabelas próprias
--
-- O que fica em `persona_card` (jsonb): voz com exemplos, tabus, arcos — o que
-- o Olimpo acrescentou e a máquina não tinha.
--
-- ⚠️ CONSISTÊNCIA FACIAL: não existe seed, LoRA nem embedding. Ela depende de
-- (a) as 4 imagens existirem, (b) a ORDEM do array de referências, (c) a
-- tradução das tags @imageN para ordinais e (d) os textos de trava no prompt.
-- Quebrar qualquer um dos quatro faz o rosto derivar. Ver ADR-0014.
-- =============================================================================

-- =============================================================================
-- MÍDIA
-- =============================================================================

CREATE TYPE media_kind AS ENUM ('image', 'video', 'audio');

CREATE TYPE media_role AS ENUM (
    -- As quatro que ancoram a identidade. Nunca podem sumir.
    'face',            -- o rosto canônico
    'character_sheet', -- turnaround de 4 painéis: roupa e corpo
    'close_up_face',   -- headshot: textura de pele
    'close_up_detail', -- cartela macro: olho, sobrancelha, lábio, mão
    -- Derivadas
    'wardrobe',        -- figurino travado na identidade
    'brand_product',   -- folha de 6 ângulos de um produto
    'reference',       -- referência extra do mesmo rosto em outro ângulo
    'generated',       -- conteúdo produzido
    'inspiration'
);

CREATE TABLE media_assets (
    id          uuid PRIMARY KEY DEFAULT uuid_generate_v4(),
    persona_id  uuid REFERENCES personas(id) ON DELETE CASCADE,
    kind        media_kind NOT NULL DEFAULT 'image',
    role        media_role NOT NULL DEFAULT 'generated',

    -- Caminho no nosso armazenamento. A URL do provider expira e o CDN some;
    -- por isso tudo é baixado para cá.
    storage_path text,
    -- De onde veio originalmente. Guardado para rastrear, nunca para servir.
    source_url  text,
    content_hash text,              -- dedupe: o mesmo arquivo nunca duplica
    mime        text,
    width       integer,
    height      integer,
    duration_seconds real,
    bytes       bigint,

    -- Reprodutibilidade: o prompt exato que gerou isto.
    prompt      text,
    provider    text,
    model       text,
    aspect_ratio text,
    params      jsonb NOT NULL DEFAULT '{}',
    -- Quais imagens foram usadas como referência, NA ORDEM. A ordem é o
    -- contrato semântico do prompt (@image1 = rosto, @image2 = roupa…);
    -- guardar embaralhado inutilizaria o registro.
    reference_ids uuid[] NOT NULL DEFAULT '{}',

    label       text,
    source      text,               -- photo_studio | profile | wardrobe | …
    legacy_id   text,               -- id na máquina, para a importação
    created_at  timestamptz NOT NULL DEFAULT now()
);

CREATE INDEX idx_media_persona ON media_assets(persona_id, created_at DESC);
CREATE INDEX idx_media_role    ON media_assets(persona_id, role);
CREATE INDEX idx_media_kind    ON media_assets(kind, created_at DESC);
CREATE UNIQUE INDEX idx_media_hash ON media_assets(content_hash)
    WHERE content_hash IS NOT NULL;

-- =============================================================================
-- PERSONAS: os campos que a geração precisa
-- =============================================================================

ALTER TABLE personas
    -- Dirige o pool de guarda-roupa, as poses e os pronomes do prompt de
    -- vídeo. É o campo mais consultado pelo motor de prompt.
    ADD COLUMN gender text,
    ADD COLUMN apparent_age integer,
    -- String canônica montada dos atributos ("latina, cabelo ondulado
    -- castanho médio, olhos castanhos, pele oliva, porte atlético").
    -- Entra LITERALMENTE no bloco de sujeito de todo prompt.
    ADD COLUMN physical_desc text,
    ADD COLUMN backstory text,
    -- 0–100. Não é decorativo: define a janela de energia do guarda-roupa,
    -- a escolha de pose e a nota de styling.
    ADD COLUMN personality_energy integer NOT NULL DEFAULT 50
        CHECK (personality_energy BETWEEN 0 AND 100),
    ADD COLUMN niches text[] NOT NULL DEFAULT '{}',
    ADD COLUMN vibe_words text[] NOT NULL DEFAULT '{}',
    ADD COLUMN clothing_style text,
    ADD COLUMN palette text[] NOT NULL DEFAULT '{}',
    ADD COLUMN content_pillars text[] NOT NULL DEFAULT '{}',
    -- Resultado da classificação por LLM (tags de estilo + nicho de cena).
    -- Na origem isto era recalculado a cada geração porque não persistia.
    ADD COLUMN backstory_context jsonb NOT NULL DEFAULT '{}',

    -- As quatro âncoras de identidade.
    ADD COLUMN face_asset_id uuid REFERENCES media_assets(id) ON DELETE SET NULL,
    ADD COLUMN character_sheet_asset_id uuid REFERENCES media_assets(id) ON DELETE SET NULL,
    ADD COLUMN close_up_face_asset_id uuid REFERENCES media_assets(id) ON DELETE SET NULL,
    ADD COLUMN close_up_detail_asset_id uuid REFERENCES media_assets(id) ON DELETE SET NULL,

    -- Fotos extras do MESMO rosto em outros ângulos. Entram no fim do array
    -- de referências para não deslocar as posições das tags.
    ADD COLUMN reference_asset_ids uuid[] NOT NULL DEFAULT '{}',
    -- Cenários recorrentes da personagem (a "casa" dela).
    ADD COLUMN home_slots jsonb NOT NULL DEFAULT '[]',

    ADD COLUMN legacy_id text UNIQUE;

CREATE INDEX idx_personas_niches ON personas USING gin (niches);

COMMENT ON COLUMN personas.personality_energy IS
    '0–100. Dirige guarda-roupa, pose e styling — não é enfeite.';

-- =============================================================================
-- GUARDA-ROUPA
-- =============================================================================
-- Cada item é uma folha de 4 painéis do MESMO rosto com outro figurino, não
-- uma foto de roupa. É um figurino reutilizável travado na identidade.

CREATE TABLE wardrobe_items (
    id          uuid PRIMARY KEY DEFAULT uuid_generate_v4(),
    persona_id  uuid NOT NULL REFERENCES personas(id) ON DELETE CASCADE,
    name        text NOT NULL,
    asset_id    uuid REFERENCES media_assets(id) ON DELETE SET NULL,
    description text,
    tags        text[] NOT NULL DEFAULT '{}',
    sort_order  integer NOT NULL DEFAULT 0,
    legacy_id   text,
    created_at  timestamptz NOT NULL DEFAULT now()
);

CREATE INDEX idx_wardrobe_persona ON wardrobe_items(persona_id, sort_order);

-- =============================================================================
-- MARCAS
-- =============================================================================
-- Uma marca com `persona_id` nulo é do catálogo geral e serve a qualquer
-- persona; preenchido, é exclusiva daquela.

CREATE TABLE brands (
    id          uuid PRIMARY KEY DEFAULT uuid_generate_v4(),
    persona_id  uuid REFERENCES personas(id) ON DELETE CASCADE,
    name        text NOT NULL,
    category    text,
    description text,
    -- Fotos do produto em ângulos diferentes.
    asset_ids   uuid[] NOT NULL DEFAULT '{}',
    -- A folha de 6 painéis do produto. É ela que dá consistência quando o
    -- produto aparece em qualquer pose — o ativo real da marca.
    product_sheet_asset_id uuid REFERENCES media_assets(id) ON DELETE SET NULL,
    -- Como o produto entra na cena: segurado na mão ou vestido no corpo.
    -- Muda a instrução no prompt e não pode ser inferido da imagem.
    default_mode text NOT NULL DEFAULT 'holding'
        CHECK (default_mode IN ('holding', 'wearing')),
    active      boolean NOT NULL DEFAULT true,
    metadata    jsonb NOT NULL DEFAULT '{}',
    legacy_id   text,
    created_at  timestamptz NOT NULL DEFAULT now(),
    updated_at  timestamptz NOT NULL DEFAULT now()
);

CREATE INDEX idx_brands_persona ON brands(persona_id);
CREATE INDEX idx_brands_catalogo ON brands(name) WHERE persona_id IS NULL;

CREATE TRIGGER trg_brands_touch BEFORE UPDATE ON brands
    FOR EACH ROW EXECUTE FUNCTION touch_updated_at();

-- =============================================================================
-- FILA DE GERAÇÃO
-- =============================================================================
-- O estado vive aqui; a execução é do Celery. Na origem, um job síncrono que
-- morresse no meio ficava preso em `running` para sempre e o crédito gasto se
-- perdia — com ack tardio e retry isso desaparece.

CREATE TYPE generation_status AS ENUM (
    'queued', 'running', 'done', 'error', 'cancelled'
);

CREATE TABLE generation_jobs (
    id           uuid PRIMARY KEY DEFAULT uuid_generate_v4(),
    persona_id   uuid REFERENCES personas(id) ON DELETE CASCADE,
    kind         media_kind NOT NULL DEFAULT 'image',
    status       generation_status NOT NULL DEFAULT 'queued',
    progress     integer NOT NULL DEFAULT 0,

    provider     text,
    model        text,
    -- Por onde passou: o gateway cobre texto→imagem; referência de imagem,
    -- vídeo e voz exigem cliente direto. Registrado para atribuir custo.
    route        text,
    request      jsonb NOT NULL DEFAULT '{}',
    -- Handle do provider em geração assíncrona (fila do fal, predição do
    -- Replicate). Sem ele, um restart perde o trabalho já pago.
    provider_ref text,

    result_asset_ids uuid[] NOT NULL DEFAULT '{}',
    error        text,
    attempts     integer NOT NULL DEFAULT 0,
    cost_usd     numeric(10,6) NOT NULL DEFAULT 0,
    latency_ms   integer,

    created_at   timestamptz NOT NULL DEFAULT now(),
    started_at   timestamptz,
    finished_at  timestamptz,
    next_poll_at timestamptz
);

CREATE INDEX idx_genjobs_pendentes ON generation_jobs(next_poll_at)
    WHERE status IN ('queued', 'running');
CREATE INDEX idx_genjobs_persona ON generation_jobs(persona_id, created_at DESC);

-- =============================================================================
-- RASCUNHOS DE VÍDEO
-- =============================================================================
-- Na origem viviam dentro de um jsonb de configurações, o que impedia buscar,
-- listar e reaproveitar.

CREATE TABLE video_drafts (
    id           uuid PRIMARY KEY DEFAULT uuid_generate_v4(),
    persona_id   uuid NOT NULL REFERENCES personas(id) ON DELETE CASCADE,
    dialogue     text,
    prompt       text NOT NULL,
    duration_seconds integer,
    aspect_ratio text,
    shot_mode    text,
    camera       text,
    vibe         text,
    voice        text,
    environment  text,
    product_worn boolean NOT NULL DEFAULT false,
    notes        text,
    params       jsonb NOT NULL DEFAULT '{}',
    created_at   timestamptz NOT NULL DEFAULT now()
);

CREATE INDEX idx_drafts_persona ON video_drafts(persona_id, created_at DESC);

-- =============================================================================
-- LIMPEZA DA HERANÇA
-- =============================================================================
-- Prometido na 0007: com o código todo lendo de `personas`, as duas colunas
-- que restavam em `accounts` saem agora.

ALTER TABLE accounts
    DROP COLUMN IF EXISTS persona_card,
    DROP COLUMN IF EXISTS persona_version,
    DROP COLUMN IF EXISTS persona_engine_id,
    DROP COLUMN IF EXISTS device_binding;

-- `persona_versions` era chaveada por conta, o que ficou estranho assim que
-- uma persona passou a ter vários logins. O histórico do card pertence à
-- persona.
ALTER TABLE persona_versions
    ADD COLUMN persona_id uuid REFERENCES personas(id) ON DELETE CASCADE;

UPDATE persona_versions pv
   SET persona_id = a.persona_id
  FROM accounts a
 WHERE a.id = pv.account_id AND a.persona_id IS NOT NULL;

CREATE INDEX idx_persona_versions_persona
    ON persona_versions(persona_id, version DESC);
