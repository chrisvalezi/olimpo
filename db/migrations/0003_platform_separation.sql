-- =============================================================================
-- Olimpo — Migração 0003: separação por plataforma (parte 1, tipos)
-- =============================================================================
-- Instagram, WhatsApp e (em breve) TikTok não são variações do mesmo agente.
-- Uma conta de WhatsApp não tem feed, não tem seguidor e não roda o loop de
-- conteúdo — ela conversa. Tratar as três como "conta social" leva a despachar
-- `instagram.post-feed` numa conta de WhatsApp, que é erro silencioso: o
-- serviço aceita a task e ela morre lá dentro.
--
-- Esta migração só ADICIONA valores ao enum. O Postgres não permite usar um
-- valor novo de enum na mesma transação em que ele é criado, então a restrição
-- que amarra arquétipo à plataforma vem na 0004.
-- =============================================================================

-- Arquétipos que NÃO rodam o loop de conteúdo:
--   messaging — conta de mensageria (WhatsApp). Atende, distribui, conversa.
--   collector — conta operacional que só observa (recon, monitores). Nunca
--               publica; existe para não queimar conta de valor na coleta.
ALTER TYPE archetype ADD VALUE IF NOT EXISTS 'messaging';
ALTER TYPE archetype ADD VALUE IF NOT EXISTS 'collector';

-- Quais plataformas cada nó realmente atende, derivado do catálogo /actions
-- dele. Um nó de WhatsApp não deve receber task de Instagram só porque tem
-- device livre.
ALTER TABLE orchestrator_nodes
    ADD COLUMN IF NOT EXISTS platforms text[] NOT NULL DEFAULT '{}';

COMMENT ON COLUMN orchestrator_nodes.platforms IS
    'Prefixos de ação encontrados em actions_cache: instagram, whatsapp, tiktok';
