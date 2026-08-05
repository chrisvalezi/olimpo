-- =============================================================================
-- Olimpo — Migração 0009: tornar a importação idempotente de verdade
-- =============================================================================
-- O importador da 0008 usava `ON CONFLICT DO NOTHING` em marcas, figurinos e
-- rascunhos de vídeo — mas não existia restrição para o conflito acontecer.
-- O resultado: rodar a importação duas vezes duplicou tudo (marcas 8 em vez
-- de 4, figurinos 20 em vez de 10, rascunhos 12 em vez de 6).
--
-- A mídia escapou porque `media_assets.content_hash` tem índice único — que
-- é exatamente o mecanismo que faltava nas outras três.
--
-- Esta migração deduplica o que já entrou e cria as restrições, para que a
-- promessa de idempotência passe a ser garantida pelo banco em vez de
-- depender de o código lembrar.
-- =============================================================================

-- =============================================================================
-- DEDUPLICAÇÃO
-- =============================================================================
-- Mantém a linha mais antiga de cada grupo: foi a primeira a ser criada e é
-- a que outras referências, se existirem, apontam.

DELETE FROM brands b
 WHERE b.legacy_id IS NOT NULL
   AND b.id <> (
     SELECT b2.id FROM brands b2
      WHERE b2.legacy_id = b.legacy_id
      ORDER BY b2.created_at, b2.id LIMIT 1
   );

DELETE FROM wardrobe_items w
 WHERE w.legacy_id IS NOT NULL
   AND w.id <> (
     SELECT w2.id FROM wardrobe_items w2
      WHERE w2.legacy_id = w.legacy_id
      ORDER BY w2.created_at, w2.id LIMIT 1
   );

-- Rascunhos não tinham identificador de origem nenhum. O par
-- (persona, prompt) serve: o prompt é longo e específico o bastante para
-- que dois rascunhos idênticos da mesma persona sejam de fato o mesmo.
DELETE FROM video_drafts v
 WHERE v.id <> (
   SELECT v2.id FROM video_drafts v2
    WHERE v2.persona_id = v.persona_id AND v2.prompt = v.prompt
    ORDER BY v2.created_at, v2.id LIMIT 1
 );

-- =============================================================================
-- RESTRIÇÕES
-- =============================================================================

CREATE UNIQUE INDEX idx_brands_legacy ON brands(legacy_id)
    WHERE legacy_id IS NOT NULL;

CREATE UNIQUE INDEX idx_wardrobe_legacy ON wardrobe_items(legacy_id)
    WHERE legacy_id IS NOT NULL;

-- `md5` em vez do texto inteiro: um prompt de vídeo passa de 2 KB, e índice
-- sobre texto longo desse tamanho é desperdício.
CREATE UNIQUE INDEX idx_drafts_unico
    ON video_drafts(persona_id, md5(prompt));
