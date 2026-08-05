-- =============================================================================
-- Olimpo — Migração 0004: separação por plataforma (parte 2, restrições)
-- =============================================================================
-- Depende dos valores de enum criados na 0003.
-- =============================================================================

-- Contas de WhatsApp importadas antes desta migração entraram como
-- 'influencer' por falta de opção. Corrige antes de impor a restrição.
UPDATE accounts
   SET archetype = 'messaging'
 WHERE platform = 'whatsapp' AND archetype <> 'messaging';

-- A regra que impede o erro de classe: só conta de rede social roda os
-- arquétipos de conteúdo; WhatsApp é mensageria e nada mais.
ALTER TABLE accounts
    ADD CONSTRAINT accounts_archetype_matches_platform CHECK (
        (platform = 'whatsapp' AND archetype IN ('messaging', 'collector'))
        OR
        (platform IN ('instagram', 'tiktok')
         AND archetype IN ('influencer', 'gossip', 'clipper', 'collector'))
    );

-- Uma task só pode ir para um nó que atenda a plataforma daquela conta.
-- Sem isto, um nó só de WhatsApp com device livre receberia uma postagem
-- de Instagram e a task morreria dentro dele.
CREATE INDEX IF NOT EXISTS idx_nodes_platforms
    ON orchestrator_nodes USING gin (platforms);

-- Placements também carregam a plataforma, para que a escolha de nó não
-- precise de join com accounts no caminho quente do dispatcher.
ALTER TABLE account_placements
    ADD COLUMN IF NOT EXISTS platform platform;

UPDATE account_placements p
   SET platform = a.platform
  FROM accounts a
 WHERE a.id = p.account_id AND p.platform IS NULL;
