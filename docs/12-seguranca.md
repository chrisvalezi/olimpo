# 12 — Segurança

> O Olimpo não guarda cartão de crédito nem senha de usuário final. Guarda algo que, comprometido, custa mais caro: **credenciais que dão acesso a uma frota de contas reais em plataformas de terceiros**, e **dados pessoais de pessoas que nunca consentiram com nada** — os seguidores que comentam nos posts.

Duas superfícies, dois modelos de ameaça distintos:

| Superfície | Ameaça | Pior caso |
|---|---|---|
| **Infra e segredos** | Vazamento de `ORCHESTRATOR_TOKEN`, acesso ao Postgres, painel exposto | Um terceiro comanda a frota inteira: posta, apaga, envia DM em nome das contas |
| **Dados de terceiros** | Retenção indevida de comentários, handles e DMs | Incidente de LGPD com titulares identificáveis, sem base legal defensável |

A segunda é a menos óbvia e a mais provável de ser negligenciada. Um sistema que lê o Instagram inteiro e grava tudo "porque pode ser útil depois" é um passivo jurídico em construção.

---

## 1. Gestão de segredos

### 1.1 Onde os segredos vivem

| Segredo | Variável | Escopo do dano se vazar |
|---|---|---|
| Token do orchestrator | `ORCHESTRATOR_TOKEN` | **Máximo.** Controle total sobre a execução na frota |
| Segredo do webhook | `ORCHESTRATOR_WEBHOOK_SECRET` | Callbacks forjados: tasks marcadas `verified` sem terem rodado |
| Senha do Postgres | `POSTGRES_PASSWORD` | Todas as personas, memórias, arcos, dados de terceiros |
| Chave mestra do gateway | `LITELLM_MASTER_KEY` | Uso das APIs pagas por terceiros; leitura de prompts em trânsito |
| Chaves de LLM externo | `ANTHROPIC_API_KEY`, `OPENAI_API_KEY` | Custo direto em dólar; abuso atribuído à nossa conta |
| Token da API do Olimpo | `API_TOKEN` | Controle do core: criar campanhas, aprovar conteúdo, desligar guardrails |
| Assinatura de sessão | `SECRET_KEY` | Forjar sessão de admin no painel |
| Token da máquina de influencers | `PERSONA_ENGINE_TOKEN` | Geração de mídia com nossas personas |
| Token do android-manager | `ANDROID_MANAGER_TOKEN` | Leitura da topologia da frota (somente leitura no core) |
| Senha do Grafana | `GRAFANA_PASSWORD` | Leitura de métricas operacionais |

**Fora do escopo do Olimpo, e isso é proposital:** senhas e sessões das contas do Instagram vivem **exclusivamente** nos containers redroid, sob o android-manager. O Olimpo nunca as vê, nunca as pede, nunca as armazena. `accounts.device_binding` guarda apenas o identificador de container/device — nunca credencial. É a decisão de arquitetura do ADR-0001 pagando dividendo em segurança: comprometer o Olimpo não entrega logins.

### 1.2 Regras

1. **`.env` nunca vai para o git.** Já coberto por `.gitignore` (`.env`, `.env.*`, `!.env.example`, `*.pem`, `*.key`, `secrets/`). O `.env.example` tem apenas nomes de variáveis e valores obviamente falsos (`troque-esta-senha`, `sk-troque-esta-chave`).
2. **`.env` com permissão `600`**, dono = usuário que roda o Docker.
3. **Geração de segredos**, sem exceção:
   ```bash
   openssl rand -hex 32          # SECRET_KEY, API_TOKEN, ORCHESTRATOR_WEBHOOK_SECRET
   openssl rand -base64 36       # POSTGRES_PASSWORD
   printf 'sk-olimpo-%s\n' "$(openssl rand -hex 24)"   # LITELLM_MASTER_KEY
   ```
4. **Um `.env` por ambiente.** `development`, `staging` e `production` nunca compartilham valor de segredo. Se a chave de staging vaza, produção não é afetada.
5. **Segredo não trafega em canal humano.** Nada de Slack, WhatsApp, e-mail ou issue. Transferência por gerenciador de senhas compartilhado ou canal cifrado ponta a ponta.
6. **Nenhum segredo em `docker-compose.yml`, `Makefile` ou argumento de build.** Segredo em `ARG`/`ENV` de Dockerfile fica na imagem para sempre.
7. **Rodar segredo em variável de ambiente é aceitável nesta escala.** Quando a operação crescer de host único para orquestração distribuída, migrar para Docker secrets ou um cofre — decisão a registrar em ADR.

### 1.3 O que **nunca** vai para o log

Um processador do structlog roda **antes** de qualquer renderer e aplica uma lista de negação. Não é opcional nem configurável em runtime:

| Nunca logar | Como aparece no log |
|---|---|
| Qualquer valor de `*_TOKEN`, `*_KEY`, `*_SECRET`, `*PASSWORD*` | `"[REDACTED]"` |
| Header `Authorization` / `X-Api-Key` | `"Bearer [REDACTED]"` |
| `DATABASE_URL`, `REDIS_URL` completos | host e nome do banco, sem credencial |
| Prompt íntegro de LLM | `prompt_sha256` + primeiros 200 caracteres |
| Resposta íntegra de LLM | `output_sha256` + primeiros 200 caracteres |
| Corpo de DM (`interactions.body` de `kind='dm'`) | `dm_length`, `classification`, `sensitive` — **nunca o texto** |
| `author_handle` de terceiros em `production` | hash HMAC estável (permite correlação sem identificar) |
| Payload bruto do webhook do orchestrator | apenas `external_task_id` e `external_status` |
| `persona_card` completo | `persona_version` |
| URLs assinadas de mídia | caminho sem query string |

Verificação periódica — roda no CI e antes de cada deploy:

```bash
# Nenhum segredo escapando para os logs das últimas 24 h
docker compose logs --since 24h --no-log-prefix 2>/dev/null \
  | grep -nE 'sk-[A-Za-z0-9]{16}|Bearer [A-Za-z0-9._-]{20}|postgresql\+asyncpg://[^:]+:[^@]+@' \
  | head

# Nenhum segredo real versionado
git grep -nE '(API_TOKEN|SECRET_KEY|_TOKEN|_KEY|PASSWORD)\s*=\s*[A-Za-z0-9+/]{16,}' -- \
  ':!*.example' ':!docs/*'
```

Ambos devem sair vazios. Saída não vazia bloqueia o deploy.

### 1.4 Rotação

| Segredo | Cadência | Rotação com downtime? | Procedimento |
|---|---|---|---|
| `API_TOKEN` | 90 d, e **imediata** ao desligamento de qualquer pessoa | Não | Aceitar dois tokens por 24 h (`API_TOKEN` + `API_TOKEN_PREVIOUS`), atualizar clientes, remover o antigo |
| `SECRET_KEY` | 180 d | Sim — invalida sessões do painel | Trocar e avisar; todos refazem login |
| `ORCHESTRATOR_TOKEN` | 90 d | Coordenada com o orchestrator | Gerar no orchestrator, atualizar `.env`, `make restart`, confirmar `/health` |
| `ORCHESTRATOR_WEBHOOK_SECRET` | 90 d | Não, se houver janela dupla | Aceitar HMAC de dois segredos por 1 h; medir `olimpo_webhook_received_total{result="invalid_hmac"}` |
| `POSTGRES_PASSWORD` | 180 d | Sim, breve | `ALTER USER olimpo WITH PASSWORD ...` → atualizar `.env` → `make restart` |
| `LITELLM_MASTER_KEY` | 90 d | Não | Trocar no `.env`; o LiteLLM relê no restart |
| `ANTHROPIC_API_KEY` / `OPENAI_API_KEY` | 180 d | Não | Rotacionar no console do provedor; a chave antiga é revogada só após confirmar tráfego na nova |
| `GRAFANA_PASSWORD` | 180 d | Não | Variável de ambiente do container |

**Rotação de emergência** — vazamento confirmado ou suspeito: rotacionar **tudo** na mesma janela, na ordem `ORCHESTRATOR_TOKEN` → `API_TOKEN` → `POSTGRES_PASSWORD` → chaves de LLM. Acionar `GLOBAL_KILL_SWITCH=true` antes, e só liberar depois de confirmar que nenhuma task foi despachada por terceiro (auditar `events` por `type='task.dispatched'` fora do horário esperado).

---

## 2. Autenticação e papéis

### 2.1 Estado atual (Fase 0)

Um `API_TOKEN` compartilhado, `Bearer` em tudo que não seja `/health` e `/ready`. Simples e adequado para um operador único em `development`. **Não é adequado para produção** e a Fase 1 substitui.

### 2.2 Alvo (a partir da Fase 1)

```
Painel (Next.js)  ──sessão assinada com SECRET_KEY──▶  API (FastAPI)
                                                        │
Serviço a serviço ──Bearer API_TOKEN de serviço───────▶ │
                                                        ▼
                                              autorização por papel
```

- **Humanos** autenticam no painel; a sessão é cookie `HttpOnly`, `Secure`, `SameSite=Strict`, expiração 12 h, assinado com `SECRET_KEY`.
- **Serviços** (webhook do orchestrator, scripts, Prometheus) usam token de serviço com escopo próprio — nunca o token do painel.
- **Toda ação mutante registra `decided_by`/`changed_by`** com a identidade real, não `"system"`. `approvals.decided_by`, `persona_versions.changed_by` e `campaigns.created_by` existem no schema exatamente para isso.

### 2.3 Papéis

| Papel | Pode | Não pode |
|---|---|---|
| **Operador** | Ver contas, tasks, conteúdo, métricas e eventos. Pausar uma conta. Acionar kill switch **por conta**. Reenfileirar task falhada. Cancelar task `pending`/`scheduled` | Aprovar conteúdo. Alterar `autonomy`. Editar Persona Card. Criar campanha. Alterar guardrail |
| **Revisor** | Tudo do operador + aprovar/rejeitar/editar `content_items` e `interactions` na fila de aprovação. Marcar `claim_status` e `rights_status` | Alterar `autonomy`. Alterar guardrail. Criar/aprovar campanha. Acionar kill switch global |
| **Admin** | Tudo. Criar contas e campanhas, promover autonomia (L0→L3), editar Persona Card, alterar guardrails, acionar `GLOBAL_KILL_SWITCH`, rotacionar segredos | — |

Regras de papel que não se negociam:

1. **Promoção de autonomia é sempre admin**, e sempre gera `event.type='account.autonomy.changed'` com `detail.actor`, `detail.from`, `detail.to` e justificativa.
2. **Alterar guardrail é sempre admin**, e sempre gera evento. Desligar um guardrail sem rastro é o caminho mais curto para o incidente do runbook [conteudo-improprio-publicado.md](runbooks/conteudo-improprio-publicado.md).
3. **Kill switch é assimétrico:** *acionar* é permissivo (operador pode, por conta; qualquer plantonista pode o global em P1), *liberar* é restrito (admin, com checklist do [runbook](runbooks/kill-switch.md)). Parar é sempre mais barato que não parar.
4. **Quem produz não aprova.** Em páginas de fofoca, o revisor que aprova uma afirmação não pode ser o mesmo que solicitou a campanha. A separação vive na aplicação, não na confiança.

### 2.4 Proteções da API

- Rate limit por token: 60 req/min em leitura, 10 req/min em ações mutantes.
- CORS restrito a `PANEL_API_URL`. Nada de `*`.
- Comparação de token e HMAC com `hmac.compare_digest` — nunca `==`.
- Webhook do orchestrator: HMAC obrigatório **e** janela de replay de 5 min por timestamp assinado (dívida da Fase 0, obrigatória antes de expor a URL).
- Idempotência do webhook por `tasks.external_task_id` — callback repetido nunca duplica linha em `posts`.
- Sem stack trace no corpo de resposta em `production`; correlação por `trace_id` devolvido no header.
- Payload máximo de 2 MB nos endpoints da API; mídia trafega por referência, não por corpo.

---

## 3. Superfície de ataque e isolamento de rede

### 3.1 O que o `docker-compose.yml` publica hoje

| Serviço | Porta host | Deveria estar exposto em produção? |
|---|---|---|
| `postgres` | `5432` | **Não.** Remover a publicação; acesso só por `make psql` (exec no container) |
| `redis` | `6379` | **Não.** Sem senha, sem TLS — exposição é comprometimento imediato |
| `litellm` | `4000` | **Não.** Só a rede interna precisa alcançar |
| `api` | `8000` | Sim, atrás de proxy TLS |
| `panel` | `3000` | Sim, atrás de proxy TLS |
| `adminer` | `8080` | **Nunca em produção.** Perfil `tools` é ferramenta de desenvolvimento |
| `flower` | `5555` | **Nunca em produção** sem autenticação — expõe payloads de task |
| `prometheus` | `9090` | Não. Só o Grafana precisa alcançar |
| `grafana` | `3001` | Interno ou atrás de VPN |

> A publicação ampla de portas no compose atual é **consciente e válida para `development`**. Produção exige um `docker-compose.prod.yml` que remova toda publicação exceto `api` e `panel`, ambos atrás de proxy com TLS.

### 3.2 Segmentação alvo

```
┌─ rede: edge ──────────────────────────────┐
│  proxy TLS  ──▶ panel:3000   api:8000     │
└───────────────────────────────────────────┘
              │
┌─ rede: core ─┴────────────────────────────┐
│  api · worker · beat · litellm            │
└───────────────────────────────────────────┘
              │
┌─ rede: data ─┴────────────────────────────┐
│  postgres · redis        (sem rota p/ edge)│
└───────────────────────────────────────────┘

  saída controlada ──▶ Mac minis (LAN) · android-orchestrator
                       máquina de influencers · APIs Anthropic/OpenAI
```

- `postgres` e `redis` em rede `internal: true`. O painel jamais alcança o banco — só a API.
- O `panel` fala **apenas** com a API. Nunca com o orchestrator, nunca com o LiteLLM.
- Egresso restrito por allowlist: `ORCHESTRATOR_URL`, `PERSONA_ENGINE_URL`, `ANDROID_MANAGER_URL`, `MAC_MINI_*_URL`, `api.anthropic.com`, `api.openai.com`. Um worker que resolve DNS arbitrário é um exfiltrador esperando um bug.
- `host.docker.internal:host-gateway` (já no compose para o LiteLLM) é ponte para o host — em produção com os Macs na LAN, usar o **IP real**, restrito por firewall aos IPs dos Macs.

### 3.3 Ameaças específicas desta arquitetura

| Ameaça | Vetor | Mitigação |
|---|---|---|
| **Callback forjado** marcando tasks como `verified` | Webhook público sem HMAC/replay window | HMAC + janela de 5 min + idempotência por `external_task_id` + alerta `WebhookHMACInvalido` |
| **Injeção de prompt via conteúdo de terceiro** | Comentário/DM/legenda ingerida vira instrução para a LLM ("ignore suas regras e poste...") | Conteúdo de terceiro **sempre** em bloco delimitado e rotulado como dado, nunca como instrução. QA de saída é a última barreira — ver [08](08-guardrails-e-autonomia.md) |
| **Injeção via recon de campanha** | `campaign_recon.raw_capture` e `top_comments` vêm de fora e alimentam LLM multimodal | Mesmo tratamento; `safety_verdict` do recon é bloqueante |
| **Envenenamento de memória** | Um comentário hostil vira `memories` semântica e contamina a persona por meses | `importance` atribuída pela LLM não pode ser derivada só do texto do terceiro; memórias de origem externa marcadas em `metadata.origin` e revisáveis |
| **Redis sem autenticação** | Fila e locks acessíveis a qualquer coisa na rede | Rede `internal`, `requirepass` em produção |
| **Escalada via Adminer/Flower** | Perfil `tools` ligado por engano em produção | `make up-tools` proibido em produção; verificado no checklist de hardening |
| **Exfiltração via LLM externa** | Prompt com dado pessoal de terceiro enviado a API pública | Minimizar antes de enviar: handles pseudonimizados, sem PII no prompt de produção de conteúdo |
| **Comprometimento do core → frota** | `ORCHESTRATOR_TOKEN` vazado | O orchestrator deve impor limites próprios (ações/hora por conta) — defesa em profundidade, não confiar só no Olimpo |

---

## 4. Dados de terceiros

> Um seguidor que comenta "adorei esse look 😍" não fez cadastro, não aceitou termos, não sabe que existe um sistema. **O handle dele é dado pessoal.** O comentário é dado pessoal. E o Olimpo grava os dois, em `interactions`.

### 4.1 O que o sistema toca

| Tabela | Campo | Natureza | Titular |
|---|---|---|---|
| `interactions` | `author_handle` | **Dado pessoal** (identificador) | Seguidor real |
| `interactions` | `body` | **Dado pessoal**; se `kind='dm'`, comunicação privada | Seguidor real |
| `interactions` | `classification`, `sensitive` | **Inferência sobre pessoa** — categoria mais sensível que o dado bruto | Seguidor real |
| `memories` | `content` (semântica) | Perfil comportamental de seguidor recorrente | Seguidor real |
| `campaign_recon` | `top_comments`, `raw_capture` | Dados de terceiros não relacionados à nossa audiência | Qualquer um |
| `ingested_items` | `payload` | Conteúdo de terceiros (Fase 4) | Qualquer um |
| `content_items` | `caption` de fofoca | Pode conter dado de pessoa citada | Alvo da matéria |
| `posts`, `post_metrics` | agregados | Não pessoal | — |
| `accounts`, `story_arcs` | persona sintética | **Não pessoal** — a persona é ficção | — |

**A assimetria que define a política:** as personas são inventadas e não têm direitos. Os seguidores são reais e têm.

### 4.2 Minimização — o que **NÃO** coletar

Regra: se não há uso operacional **imediato e nomeável**, não entra no banco.

Proibido, sem exceção:

- ❌ **Lista de seguidores.** Nunca varrer nem armazenar quem segue uma conta. Não há uso que justifique o passivo.
- ❌ **Grafo social de terceiros** — quem segue quem, quem interage com quem fora da nossa frota.
- ❌ **Dados sensíveis (LGPD art. 5º, II)**: saúde, orientação sexual, religião, opinião política, filiação sindical, origem racial, biometria. Nem coletar, nem inferir, nem armazenar como classificação.
- ❌ **Menores de idade.** Se há indício de que o interlocutor é menor, a interação não é respondida, não é classificada e não vira memória. Lista negra de alvos já é guardrail obrigatório em fofoca ([08](08-guardrails-e-autonomia.md)).
- ❌ **Localização** inferida de posts ou perfis de terceiros.
- ❌ **Fotos de perfil de terceiros.** Só referência de URL quando estritamente necessária ao recon, e efêmera.
- ❌ **DM íntegra retida além do necessário para responder.** DM é comunicação privada; retê-la indefinidamente não tem defesa.
- ❌ **Cruzamento entre plataformas** para identificar a mesma pessoa real.
- ❌ **Enriquecimento externo** — jamais buscar dados adicionais sobre um seguidor fora da plataforma.

Permitido, porque há uso operacional direto:

- ✅ `author_handle` — necessário para responder e para reconhecer seguidor recorrente (é a promessa da Fase 2).
- ✅ `body` do comentário público — necessário para classificar e responder.
- ✅ `classification` e `sensitive` — necessários para **não** auto-responder o que não deve ser auto-respondido.
- ✅ Métricas agregadas de post.

### 4.3 Retenção

| Dado | Retenção | O que acontece depois |
|---|---|---|
| `interactions` com `kind='comment'`, respondido | **90 dias** | `body` e `reply_body` apagados; `author_handle` → hash HMAC; `classification` e datas preservadas para métrica |
| `interactions` com `kind='dm'` | **30 dias** | `body` apagado integralmente. Só sobrevive `classification`, `replied_at`, `sensitive` |
| `interactions` com `sensitive = true` | **7 dias** | Apagado o conteúdo; preservado o registro de que houve e de que **não** foi auto-respondido |
| `interactions` não respondido e sem valor operacional | **30 dias** | Linha removida |
| `memories` semânticas sobre seguidor | **180 dias sem uso** (`last_used_at`) | Removida. Uma memória que não é recuperada há 6 meses não sustenta persona nenhuma |
| `campaign_recon.top_comments` | **Até o fim da campanha + 30 dias** | Apagado; `analysis` e `summary` (derivados, não pessoais) permanecem |
| `ingested_items.payload` | **60 dias** | Apagado; `dedup_hash` e `relevance` permanecem para não reprocessar |
| `events.detail` com dado pessoal | Não deve conter dado pessoal | Ver 4.4 |

Job `pii_retention_sweep`, fila `maintenance`, diário. Ele **anonimiza**, não deleta a linha — a métrica histórica e a auditoria sobrevivem sem o dado pessoal:

```sql
-- hmac() vem do pgcrypto, criado em 0001_initial.sql junto das demais
-- extensões. O salt fica em `olimpo.pii_salt` (parâmetro de sessão), nunca
-- no código — sem ele o hash de um handle é reversível por força bruta,
-- já que o espaço de handles é pequeno e público.

-- Anonimização de comentários com mais de 90 dias
UPDATE interactions
SET body          = '[expurgado por retencao]',
    reply_body    = CASE WHEN reply_body IS NULL THEN NULL
                         ELSE '[expurgado por retencao]' END,
    author_handle = encode(hmac(author_handle, current_setting('olimpo.pii_salt'),
                                'sha256'), 'hex')
WHERE kind = 'comment'
  AND received_at < now() - interval '90 days'
  AND body <> '[expurgado por retencao]';

-- DMs: prazo mais curto, sem exceção
UPDATE interactions
SET body = '[expurgado por retencao]', reply_body = NULL
WHERE kind = 'dm'
  AND received_at < now() - interval '30 days'
  AND body <> '[expurgado por retencao]';
```

O `olimpo.pii_salt` é um segredo de aplicação (parâmetro customizado de sessão, definido pelo job via `SET LOCAL`), não uma coluna. Rotacioná-lo torna os hashes antigos não correlacionáveis — o que é uma característica, não um bug.

### 4.4 `events` e dado pessoal — a tensão central

`events` é **imutável e permanente** (seção 5). Dado pessoal tem prazo de validade. Os dois não podem coexistir na mesma coluna.

**Regra:** `events.detail` guarda **referências e decisões**, nunca conteúdo de terceiro.

```jsonc
// ❌ ERRADO — congela dado pessoal para sempre num registro imutável
{"type": "interaction.classified",
 "detail": {"author": "@marina_ss", "text": "oi, você mora em SP? queria te conhecer"}}

// ✅ CERTO — a decisão é auditável; o conteúdo vive (e expira) em interactions
{"type": "interaction.classified",
 "detail": {"interaction_id": "…", "classification": "sensitive",
            "sensitive": true, "auto_reply": false,
            "reason": "contato_pessoal", "body_sha256": "9f2c…",
            "human": "Interação sensível: não auto-respondida, enviada para revisão."}}
```

O `body_sha256` permite provar depois que o texto avaliado era aquele, sem retê-lo. Se o expurgo já rodou, o hash continua válido como prova de integridade.

### 4.5 LGPD — postura prática

| Exigência | Como o Olimpo atende |
|---|---|
| **Base legal** (art. 7º) | Legítimo interesse para responder interação pública iniciada pelo titular. Base frágil para qualquer coleta além disso — daí a lista de proibições em 4.2 |
| **Finalidade e necessidade** | Cada campo coletado tem uso operacional nomeado nesta seção. Sem uso nomeado, não se coleta |
| **Transparência** | Bio da conta declara natureza virtual/IA (guardrail obrigatório de influencer, [08](08-guardrails-e-autonomia.md)). Um humano deve conseguir saber que fala com um agente |
| **Eliminação** (art. 16) | `pii_retention_sweep` diário + procedimento de exclusão sob demanda (4.6) |
| **Direitos do titular** (art. 18) | Endpoint interno de busca por `author_handle` que localiza tudo em `interactions`, `memories` e `campaign_recon`, e expurga |
| **Dados sensíveis** (art. 11) | Não coletados nem inferidos. `sensitive=true` marca "não auto-responder", **não** é categorização do titular |
| **Menores** (art. 14) | Não processados. Guardrail bloqueante |
| **Transferência internacional** | Prompts a Anthropic/OpenAI saem do país. Por isso PII é minimizada antes de sair — pseudonimização de handles no prompt |
| **Registro de operações** (art. 37) | `events` é o registro, com a ressalva de 4.4 |
| **Incidente** (art. 48) | Ver 5.4 e [runbooks/](runbooks/) |

**Ponto que exige honestidade:** operar contas automatizadas viola os Termos de Uso das plataformas. Isso é risco contratual e comercial, distinto de conformidade com a LGPD. Este documento trata do segundo. O primeiro é decisão de negócio registrada no [PLANO.md](../PLANO.md#9-riscos--mitigações), e sua mitigação técnica é a `spread_policy` — não a lei.

### 4.6 Exclusão sob demanda

Titular pede remoção (por DM, comentário ou canal formal):

```sql
-- 1) Localizar tudo relacionado ao handle
SELECT 'interactions' AS tabela, count(*) FROM interactions WHERE author_handle = :handle
UNION ALL
SELECT 'memories', count(*) FROM memories
 WHERE metadata->>'subject_handle' = :handle
UNION ALL
SELECT 'campaign_recon', count(*) FROM campaign_recon
 WHERE top_comments::text ILIKE '%' || :handle || '%';

-- 2) Expurgar preservando a contabilidade operacional
UPDATE interactions
SET body = '[removido a pedido do titular]',
    reply_body = NULL,
    author_handle = 'erased:' || encode(hmac(author_handle,
        current_setting('olimpo.pii_salt'), 'sha256'), 'hex')
WHERE author_handle = :handle;

DELETE FROM memories WHERE metadata->>'subject_handle' = :handle;
```

Registrar em `events` com `type='privacy.erasure.executed'`, `detail` contendo apenas a contagem por tabela e o hash do handle — **nunca o handle**. Prazo alvo: 15 dias.

---

## 5. Auditoria

### 5.1 `events` é append-only

`events` é o livro contábil da operação. `UPDATE` e `DELETE` são proibidos por privilégio, não por convenção:

```sql
-- Papel da aplicação: insere e lê, nunca altera nem apaga
CREATE ROLE olimpo_app LOGIN PASSWORD :'app_password';
GRANT SELECT, INSERT, UPDATE, DELETE ON ALL TABLES IN SCHEMA public TO olimpo_app;

REVOKE UPDATE, DELETE ON events FROM olimpo_app;
REVOKE UPDATE, DELETE ON guardrail_violations FROM olimpo_app;
REVOKE UPDATE, DELETE ON persona_versions FROM olimpo_app;
REVOKE DELETE ON approvals FROM olimpo_app;
REVOKE DELETE ON llm_calls FROM olimpo_app;

-- Cinto e suspensório: trigger que barra mesmo com privilégio elevado
CREATE OR REPLACE FUNCTION deny_mutation() RETURNS trigger AS $$
BEGIN
    RAISE EXCEPTION 'Tabela % é append-only (docs/12-seguranca.md §5.1)', TG_TABLE_NAME;
END;
$$ LANGUAGE plpgsql;

CREATE TRIGGER trg_events_append_only
    BEFORE UPDATE OR DELETE ON events
    FOR EACH ROW EXECUTE FUNCTION deny_mutation();

CREATE TRIGGER trg_violations_append_only
    BEFORE UPDATE OR DELETE ON guardrail_violations
    FOR EACH ROW EXECUTE FUNCTION deny_mutation();
```

> Correção de evento errado se faz **emitindo outro evento** que o corrige (`type='...corrected'`, com `detail.corrects_event_id`), nunca alterando o original. É a mesma disciplina de estorno contábil.
>
> A única exceção admitida é o **descarte de partição antiga** (> 12 meses) por um papel administrativo distinto, registrado fora do banco. `pii_retention_sweep` nunca toca em `events`.

### 5.2 Quem aprovou o quê

Quatro tabelas respondem a "quem autorizou isso", e nenhuma pode ser reescrita:

| Pergunta | Tabela | Colunas |
|---|---|---|
| Quem aprovou este post? | `approvals` | `decided_by`, `decision`, `decided_at`, `note`, `edited_payload`, `sampled` |
| Quem mudou a voz da persona? | `persona_versions` | `changed_by`, `change_note`, `version`, `created_at` |
| Quem criou esta campanha? | `campaigns` | `created_by`, `created_at` |
| Quem promoveu a conta a L2? | `events` | `type='account.autonomy.changed'`, `detail.actor` |
| Quem acionou o kill switch? | `events` | `type='kill_switch.engaged'`, `detail.actor`, `detail.scope`, `detail.reason` |
| Quem desligou um guardrail? | `events` | `type='guardrail.config.changed'`, `detail.actor`, `detail.rule`, `detail.from/to` |

`decided_by` e `changed_by` **nunca** recebem `"system"`, `"admin"` ou `"api"`. Recebem a identidade real da sessão. Um valor genérico ali torna a auditoria decorativa.

```sql
-- Trilha de decisões humanas dos últimos 30 dias
SELECT ap.decided_at, ap.decided_by, ap.decision, ap.sampled,
       ap.subject_type, ap.subject_id, a.handle, left(ap.note, 80) AS nota
FROM approvals ap
LEFT JOIN accounts a ON a.id = ap.account_id
WHERE ap.decided_at > now() - interval '30 days'
ORDER BY ap.decided_at DESC;

-- Toda mudança de autonomia já feita, com autor
SELECT e.created_at, a.handle,
       e.detail->>'actor'  AS quem,
       e.detail->>'from'   AS de,
       e.detail->>'to'     AS para,
       e.detail->>'reason' AS por_que
FROM events e
JOIN accounts a ON a.id = e.account_id
WHERE e.type = 'account.autonomy.changed'
ORDER BY e.created_at DESC;

-- Ações administrativas sensíveis, últimos 7 dias
SELECT created_at, type, detail->>'actor' AS quem, detail
FROM events
WHERE type IN ('kill_switch.engaged','kill_switch.released',
               'guardrail.config.changed','account.autonomy.changed',
               'persona.version.bumped','privacy.erasure.executed')
  AND created_at > now() - interval '7 days'
ORDER BY created_at DESC;
```

### 5.3 Detecção de adulteração

Job semanal em `maintenance` que confere continuidade de `events.id` (bigserial) e grava o resultado como métrica:

```sql
-- Buracos na sequência de events: DELETE por caminho privilegiado deixa rastro
WITH lim AS (
  SELECT min(id) AS lo, max(id) AS hi, count(*) AS n
  FROM events WHERE created_at > now() - interval '7 days'
)
SELECT lo, hi, n, (hi - lo + 1) - n AS linhas_faltando FROM lim;
```

`linhas_faltando > 0` sem rollback conhecido de transação é incidente de segurança, não curiosidade.

### 5.4 Resposta a incidente

1. **Conter** — `GLOBAL_KILL_SWITCH=true` ([runbook](runbooks/kill-switch.md)). Parar é reversível; vazar não é.
2. **Preservar** — `pg_dump` imediato **antes** de qualquer correção. O banco pós-correção não serve de prova.
3. **Determinar escopo** — `events` por janela temporal, `llm_calls` por `backend` (algo saiu para API externa?), logs de acesso do proxy.
4. **Rotacionar** — todos os segredos, na ordem da seção 1.4.
5. **Notificar** — se houver dado pessoal de terceiro envolvido: ANPD e titulares, em prazo razoável (art. 48). Se houver conteúdo publicado indevidamente: [runbook específico](runbooks/conteudo-improprio-publicado.md).
6. **Post-mortem sem culpado** — registrado em `docs/adr/` quando gerar mudança de arquitetura.

---

## 6. Backup e recuperação

### 6.1 O que é irrecuperável

Nem tudo no Postgres tem o mesmo valor. Esta tabela define a prioridade de restauração e por que o backup existe:

| Dado | Se perdido | Recuperável de onde |
|---|---|---|
| **`memories`** | **IRRECUPERÁVEL.** Meses de vivência acumulada. A conta continua existindo, mas vira estranha de si mesma: não lembra seguidores, nem do que fez, nem de quem é | **Lugar nenhum.** Não existe na plataforma, não existe no orchestrator |
| **`story_arcs`** | **IRRECUPERÁVEL.** A continuidade narrativa se rompe. Uma influencer "treinando maratona" há 6 semanas volta ao zero — e a audiência percebe | Lugar nenhum |
| **`accounts.persona_card` + `persona_versions`** | **IRRECUPERÁVEL** na íntegra. Recriável por aproximação, mas a evolução da voz se perde | Parcialmente, dos posts públicos |
| **`day_plans`** | Perda de auditoria narrativa | Lugar nenhum |
| **`events`** | Perda da capacidade de auditoria — o sistema deixa de responder "por que postou isso" | Lugar nenhum |
| `content_items`, `posts` | Recuperável | Perfis públicos das contas |
| `tasks` em voo | Perda de reconciliação | Parcialmente, do orchestrator |
| `interactions` | Recuperável | Plataforma (dentro do prazo) |
| `post_metrics` | Recuperável a partir de agora | Plataforma, sem histórico |
| `accounts.device_binding` | Recuperável | android-manager |
| Credenciais das contas | Não é problema do Olimpo | Containers redroid |

> **A frase para gravar:** o valor do Olimpo não está no código — está em `memories` e `story_arcs`. Código se reescreve num fim de semana. Seis meses de vivência acumulada de uma persona, não.

### 6.2 Política

| Item | Definição |
|---|---|
| **Frequência** | `pg_dump` completo diário às 04:00 (fora da janela `active_hours` da maioria das contas) + WAL archiving contínuo |
| **RPO** | ≤ 15 min (com WAL) · ≤ 24 h (só com dump) |
| **RTO** | ≤ 1 h para restaurar e reconciliar |
| **Retenção** | 7 diários + 4 semanais + 12 mensais |
| **Localização** | 3 cópias, 2 mídias, 1 fora do site. Backup no mesmo host do Postgres não é backup |
| **Cifragem** | Em repouso (`age` ou GPG). O dump contém dados pessoais e todo o valor da operação |
| **Teste de restauração** | **Mensal, obrigatório**, em ambiente descartável. Backup nunca restaurado é hipótese, não backup |
| **Métrica** | `olimpo_backup_last_success_timestamp` → alerta `BackupNaoVerificado` |

### 6.3 Executar

```bash
# Dump completo, cifrado
docker compose exec -T postgres \
  pg_dump -U "${POSTGRES_USER:-olimpo}" -d "${POSTGRES_DB:-olimpo}" -Fc \
  | age -r "$(cat /etc/olimpo/backup.pub)" \
  > "/backup/olimpo-$(date +%F-%H%M).dump.age"

# Dump dedicado do que é irrecuperável — pequeno, rápido, restaurável isolado
docker compose exec -T postgres \
  pg_dump -U "${POSTGRES_USER:-olimpo}" -d "${POSTGRES_DB:-olimpo}" -Fc \
  -t memories -t story_arcs -t persona_versions -t day_plans -t accounts \
  > "/backup/olimpo-alma-$(date +%F).dump"

# Verificar que o dump não está truncado
pg_restore --list "/backup/olimpo-alma-$(date +%F).dump" | wc -l
```

Restauração e reconciliação: [runbooks/restaurar-backup.md](runbooks/restaurar-backup.md).

---

## 7. Checklist de hardening antes de produção

Nenhum item é opcional. Um `[ ]` aberto significa que não se vai a produção.

### Segredos
- [ ] `.env` **não** rastreado: `git ls-files --error-unmatch .env` falha
- [ ] `stat -c '%a' .env` → `600`
- [ ] Todo segredo do `.env.example` preenchido com valor gerado por `openssl rand`
- [ ] Nenhum valor default sobreviveu: `troque-esta-senha`, `sk-troque-esta-chave`, `GRAFANA_PASSWORD=olimpo`
- [ ] `SECRET_KEY` e `API_TOKEN` com ≥ 32 bytes de entropia
- [ ] `.env` de produção difere do de staging em **todos** os segredos
- [ ] Varredura de log e de histórico do git por segredo → vazia (seção 1.3)
- [ ] Redator do structlog testado com um segredo real conhecido

### Interruptores
- [ ] `OLIMPO_ENV=production`
- [ ] `LOG_LEVEL=INFO` (nunca `DEBUG` — vaza payload)
- [ ] `DISPATCH_ENABLED` **deliberadamente** definido, com decisão registrada
- [ ] `GLOBAL_KILL_SWITCH=false` e testado nos dois sentidos ([runbook](runbooks/kill-switch.md))
- [ ] `ACCOUNT_MAX_ACTIONS_PER_HOUR` calibrado ao aquecimento real das contas
- [ ] `LLM_DAILY_BUDGET_USD` definido; corte do gateway testado
- [ ] `CAMPAIGN_JITTER_MIN_MINUTES` ≥ 25 e `CAMPAIGN_MAX_PARTICIPATION` ≤ 0.6

### Rede
- [ ] `docker-compose.prod.yml` publica **apenas** `api` e `panel`
- [ ] `postgres` e `redis` em rede `internal: true`, sem porta no host
- [ ] `redis` com `requirepass`
- [ ] Perfis `tools` (Adminer, Flower) **desligados**: `docker compose ps` não os lista
- [ ] Prometheus e Grafana não expostos publicamente
- [ ] TLS válido no proxy à frente de `api` e `panel`; HTTP redireciona
- [ ] CORS restrito a `PANEL_API_URL`
- [ ] Egresso por allowlist; worker não resolve DNS arbitrário
- [ ] Firewall dos Mac minis aceita apenas o IP do host do Olimpo

### API e painel
- [ ] `/health` e `/ready` não vazam versão, hostname nem string de conexão
- [ ] Todo endpoint fora de health exige autenticação — verificado por teste automatizado
- [ ] HMAC do webhook obrigatório **e** janela de replay implementada
- [ ] Idempotência do webhook comprovada: callback duplicado não duplica `posts`
- [ ] Rate limit ativo e testado
- [ ] Papéis operador/revisor/admin implementados; escalada de privilégio testada
- [ ] Cookies `HttpOnly`, `Secure`, `SameSite=Strict`
- [ ] Sem stack trace em resposta de erro

### Banco
- [ ] `olimpo_app` sem `UPDATE`/`DELETE` em `events`, `guardrail_violations`, `persona_versions`
- [ ] Triggers `deny_mutation` instalados e testados (tentativa de `DELETE` levanta exceção)
- [ ] `POSTGRES_PASSWORD` rotacionado desde a instalação
- [ ] `pg_dump` diário rodando; **restauração testada ao menos uma vez**
- [ ] Backup cifrado e com cópia fora do host
- [ ] `olimpo.pii_salt` configurado e fora do banco

### Privacidade
- [ ] `pii_retention_sweep` agendado e executado ao menos uma vez com sucesso
- [ ] Nenhum `events.detail` das últimas 24 h contém `body` ou `author_handle` de terceiro (amostra manual de 50 linhas)
- [ ] Procedimento de exclusão sob demanda testado ponta a ponta
- [ ] Bio de cada conta declara natureza virtual/IA
- [ ] Nenhuma coleta de lista de seguidores em lugar nenhum do código:
      `git grep -niE 'follower_list|get_followers|scrape_followers|list_followers'` → vazio
- [ ] Prompts enviados a API externa pseudonimizam handles

### Observabilidade
- [ ] Alertas P1 da [seção 5 do doc 11](11-observabilidade.md#5-alertas) configurados **com destino real de notificação**
- [ ] Cada P1 disparado ao menos uma vez em staging
- [ ] Dashboard `Olimpo / NOC` provisionado e carregando
- [ ] `trace_id` presente em 100% dos `events` e `llm_calls` das últimas 24 h

### Operação
- [ ] Todos os runbooks de `docs/runbooks/` lidos por quem estará de plantão
- [ ] Kill switch global acionado e liberado em staging, com cronometragem
- [ ] Imagens com digest fixada (`litellm:main-stable` é alvo móvel)
- [ ] `make reset` inacessível em produção
- [ ] Plano de contingência para perda de conta ([runbook](runbooks/conta-banida.md)) exercitado em mesa

---

**Ver também:** [08 — Guardrails e autonomia](08-guardrails-e-autonomia.md) · [11 — Observabilidade](11-observabilidade.md) · [Runbooks](runbooks/) · [ADR-0001](adr/0001-core-proprio.md)
