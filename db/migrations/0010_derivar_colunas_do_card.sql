-- =============================================================================
-- Olimpo — Migração 0010: derivar as colunas de geração do persona card
-- =============================================================================
-- A 0008 criou colunas para o que o motor de prompt lê — gênero, descrição
-- física, energia, nichos — e as preencheu a partir da máquina de
-- influencers. Mas persona criada de outro jeito (pelo seed, pelo painel)
-- tem esses dados dentro do `persona_card`, e as colunas ficam vazias.
--
-- O sintoma é confuso: a persona parece completa na tela, tem card, tem
-- história — e a geração recusa dizendo que falta descrição física.
--
-- Esta migração deriva as colunas do card onde elas estão vazias. O card
-- continua sendo a fonte da identidade COMPORTAMENTAL (voz, tabus); as
-- colunas são a fonte da identidade VISUAL, que é o que a geração lê.
-- =============================================================================

UPDATE personas SET
    gender = COALESCE(gender, CASE
        -- O card não tem campo de gênero; ele aparece na descrição física.
        WHEN persona_card -> 'visual' ->> 'canonical' ILIKE '%mulher%'
          OR persona_card -> 'visual' ->> 'canonical' ILIKE '%woman%'
          OR persona_card -> 'visual' ->> 'canonical' ILIKE '%female%' THEN 'Female'
        WHEN persona_card -> 'visual' ->> 'canonical' ILIKE '%homem%'
          OR persona_card -> 'visual' ->> 'canonical' ILIKE '%man%'
          OR persona_card -> 'visual' ->> 'canonical' ILIKE '%male%' THEN 'Male'
        ELSE NULL END),

    apparent_age = COALESCE(
        apparent_age,
        NULLIF(persona_card -> 'identity' ->> 'apparent_age', '')::integer
    ),

    physical_desc = COALESCE(
        NULLIF(physical_desc, ''),
        persona_card -> 'visual' ->> 'canonical'
    ),

    backstory = COALESCE(
        NULLIF(backstory, ''),
        persona_card -> 'lore' ->> 'backstory'
    ),

    clothing_style = COALESCE(
        NULLIF(clothing_style, ''),
        persona_card -> 'visual' ->> 'wardrobe'
    ),

    niches = CASE
        WHEN cardinality(niches) > 0 THEN niches
        WHEN persona_card -> 'niche' ->> 'primary' IS NOT NULL
            THEN ARRAY[persona_card -> 'niche' ->> 'primary']
        ELSE niches END,

    -- `persona_engine_id` do card é a referência facial na máquina de
    -- influencers. Vale preservar mesmo agora que a geração é nossa: é o
    -- rastro de onde a personagem veio.
    persona_engine_id = COALESCE(
        persona_engine_id,
        NULLIF(persona_card -> 'visual' ->> 'face_ref', '')
    )
 WHERE persona_card <> '{}'::jsonb;

-- Energia da personalidade: o card guarda traços em texto, não um número.
-- Sem como derivar, fica no meio da escala — que é o default e significa
-- "sem preferência", não "morna".
COMMENT ON COLUMN personas.personality_energy IS
    '0–100. Dirige guarda-roupa, pose e styling. Não é derivável do card: '
    'quem preenche é quem conhece a personagem.';
