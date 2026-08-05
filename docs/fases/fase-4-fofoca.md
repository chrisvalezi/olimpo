# Fase 4 — Páginas de fofoca e nicho

**Duração estimada:** 3–4 semanas · **Depende de:** [Fase 3](fase-3-multi-influencer-campanhas.md) · **Entrega para:** [Fase 5](fase-5-clipagem.md)

---

## 1. Resumo

Primeiro arquétipo não-influencer. A conta deixa de gerar conteúdo e passa a **curar**: monitores 24/7 vigiam fontes, a ingestão deduplica e rankeia, e o `CuratedSource` transforma o achado em post editorial na voz da página. O loop é o mesmo — só o SOURCE muda.

> **O risco dominante desta fase não é técnico, é jurídico.** Difamação, direito de imagem e presunção de inocência. `claim_status` é obrigatório e bloqueante, existe lista negra de alvos, e **L3 nunca é liberado para `archetype='gossip'`** — por decisão de arquitetura, não por configuração.

---

## 2. Pré-requisitos

| Item | Detalhe |
|---|---|
| Fase 3 completa | Panteão estável, campanhas rodando, limitador de frota validado |
| **Política editorial escrita e assinada** | Antes de qualquer código: lista negra de alvos, temas vedados, política de remoção e direito de resposta. Documento humano, não gerado |
| Revisor humano designado | A fila de revisão desta fase precisa de dono e SLA. Sem dono, não se liga a página |
| Conta de fofoca criada e aquecida | `archetype='gossip'`, ≥ 7 dias em `warming` |
| Pool de contas coletoras ampliado | Monitores consomem coletoras de forma intensa; separadas das personas |
| Fontes mapeadas | 10–30 `sources` do nicho, com `reliability` inicial atribuída à mão |

---

## 3. Escopo

### 3.1 Guardrails primeiro (ordem obrigatória)

1. **`docs/08-guardrails-e-autonomia.md` (seção fofoca)** — política editorial em regras executáveis, cada uma com id estável usado em `guardrail_violations.rule`.
2. **`olimpo.core.guardrails.legal`** — motor de regras jurídicas, avaliado no QA **e** de novo no dispatch (dupla checagem, porque o intervalo entre produzir e publicar é curto mas existe):
   - **lista negra de alvos** — menores, pessoas não-públicas, vítimas de crime, casos com segredo de justiça. Match por nome e por alias, com fuzzy (`pg_trgm`);
   - **temas vedados** — acusação de crime, doença, orientação sexual, vida sexual, morte não confirmada, paternidade;
   - **presunção de inocência** — verbo assertivo sobre conduta criminal só com `claim_status='fact'` e ≥ 2 `sources` com `reliability ≥ 0.8`;
   - **direito de imagem** — sem foto de pessoa privada; sem imagem de menor; sem print de conversa privada.
3. **`olimpo.core.guardrails.claims`** — `claim_status` deixa de ser decorativo:
   - a inferência do status vem do `sources.reliability` das fontes citadas e do texto do achado;
   - `'unverified'` **bloqueia publicação em qualquer nível de autonomia** — o default do schema vira o comportamento seguro;
   - `'rumor'` e `'reported'` exigem marcador linguístico no texto publicado (verificado por regex + LLM antes do dispatch): "segundo o veículo X", "circula que", "não confirmado";
   - `'opinion'` exige marcação explícita de opinião;
   - o marcador é checado **no texto final**, não na intenção. Caption que perdeu o marcador na reescrita é reprovada.
4. **Teto de autonomia por arquétipo** — `olimpo.core.autonomy` passa a consultar `accounts.archetype`: `gossip` tem teto **L1**. Tentar promover além disso devolve `409` na API e emite `guardrail_violations` com `severity='block'`. Não há flag de configuração que libere L3 para fofoca.

### 3.2 Ingestão e monitores

5. **`olimpo.monitors.registry`** — CRUD de `sources` e `monitors` via `POST /panel/monitors`, com `interval_seconds`, `collector_pool` e `trigger_rules`.
6. **`olimpo.monitors.runner`** — job em `maintenance` a cada 30 s: seleciona monitores vencidos por `idx_monitors_due` (`status='active' AND next_check_at <= now()`), emite `tasks(type='monitor_check', origin='monitor', priority=5)` rotacionando o `collector_pool`, e atualiza `last_check_at`/`next_check_at` com jitter (nunca intervalo cravado).
7. **Backoff de erro** — `consecutive_errors` incrementa a cada falha; ≥ 5 move o monitor para `status='error'`, alarga o intervalo exponencialmente e alerta. Fonte que mudou de layout não fica batendo em loop.
8. **`olimpo.monitors.ingest`** — o retorno do `monitor_check` vira `ingested_items`: `dedup_hash` (hash normalizado de conteúdo + fonte, com `UNIQUE` fazendo a deduplicação no banco), `payload`, `media_refs`, `monitor_id`, `source_id`.
9. **`olimpo.monitors.score`** — calcula `relevance` (aderência ao nicho da página) e `novelty` (distância dos `ingested_items` recentes via embedding). O par (relevância × novidade × `sources.reliability`) decide se dispara produção.
10. **`olimpo.monitors.trigger`** — avalia `trigger_rules`; disparo cria `monitor_hits` com `triggered=true` e `trigger_reason`, e chama o `CuratedSource`. Não-disparo também grava o hit (`triggered=false`) — auditoria do que foi visto e ignorado.

### 3.3 CuratedSource e linha editorial

11. **`olimpo.agents.sources.curated`** — implementa `ContentSource.propose()` para `archetype='gossip'`:
    - recupera o `ingested_item` e os correlatos (mesma história, fontes diferentes);
    - infere `claim_status` a partir das fontes;
    - redige o post na voz editorial da página, com o marcador linguístico exigido pelo status;
    - preenche `content_items.source_refs` (URLs/handles das fontes), `.source_strategy='curated'`, `.claim_status`, `.rights_status` (`'fair_use'` para print/citação, `'unknown'` quando não se sabe — e `'unknown'` bloqueia);
    - marca `ingested_items.consumed_by` e liga `monitor_hits.content_item_id`.
12. **Persona editorial** — o mesmo Persona Card, preenchido como linha editorial: `style.voice` (irônica, sem xingamento), `lore` (o que a página defende, quem ela cobre), `constraints.forbidden_topics` (herda a lista negra), `topics` (o nicho). Nenhuma mudança de schema — o insight central do PLANO se paga aqui.
13. **Memória para fofoca** — `memories(kind='semantic')` guarda o que a página já afirmou sobre cada alvo. Impede contradição entre posts e permite recuperar o histórico ("já dissemos que era rumor; agora saiu confirmação").

### 3.4 Pipeline de baixa latência

14. **Caminho rápido** — do `monitor_check` ao `approval_pending`, tudo na fila `realtime`, com `olimpo-fast` na redação e QA. Alvo: **p95 < 4 min** entre `ingested_at` e `content_items.status='approval_pending'`.
15. **`tasks.expires_at`** — furo tem validade. Task de publicação de fofoca nasce com `expires_at` (default 90 min, configurável por monitor); o `dispatcher_tick` move para `status='expired'` em vez de publicar tarde. Notícia velha não é só inútil — é sinal de conta automatizada mal calibrada.
16. **Prioridade** — `tasks.priority=5` para fofoca (menor = mais urgente), acima de interações (10) e produção de influencer (100).

### 3.5 Revisão humana dedicada

17. **`olimpo.panel_api.newsroom`** — fila de revisão **separada** da fila de aprovação de influencer, ordenada por urgência (tempo restante até `expires_at`), mostrando: texto proposto, `claim_status` inferido, fontes com `reliability`, o `ingested_item` bruto, e os alertas de guardrail.
18. **Painel — aba Redação** — aprovar/editar/rejeitar em um clique, com atalhos de teclado. O revisor pode **rebaixar** o `claim_status` (nunca promover sem justificativa escrita, gravada em `approvals.note`).
19. **Botão de remoção** — `POST /panel/posts/{id}/takedown` cria `tasks(type='delete_post')` de altíssima prioridade e registra o motivo. Meta: **< 15 min** entre a decisão e a remoção verificada.
20. **`docs/runbooks/pedido-de-remocao.md`** — o que fazer ao receber notificação extrajudicial: congelar a conta, remover o post, preservar o rastro (`events`, `campaign_recon`, `ingested_items` — nunca apagar a auditoria), acionar o responsável jurídico.
21. **`docs/runbooks/monitor-quebrado.md`** — fonte mudou de layout, `consecutive_errors` estourou.
22. **`docs/07-monitores.md`** — documento vivo de monitores, `trigger_rules` e scoring.

---

## 4. Fora de escopo

- **L2 e L3 para fofoca.** Teto arquitetural em L1. Não é meta futura, é decisão.
- Auto-resposta a comentários em posts de fofoca. Comentário em post de fofoca é o terreno mais jurídico que existe — tudo vai para humano, mesmo em L1.
- Clipagem, `media-forge`, `DerivedSource`. **Fase 5.**
- Multi-tenant (`clients`). **Fase 5.**
- Fofoca sobre política, eleição, religião e saúde pública. Fora do produto, não desta fase.
- Geração de imagem própria para o post (arte da máquina de influencers). Nesta fase o visual é print/citação com `rights_status='fair_use'` ou card de texto.
- Mais de uma página de fofoca. Uma, validada, com auditoria completa.
- Campanhas envolvendo a página de fofoca. Ela não participa de campanha nesta fase.
- Monitorização de fontes que exigem login de terceiro ou burlam paywall.

---

## 5. Critérios de saída

- [ ] Política editorial existe como documento humano assinado **e** como regras com id em `olimpo.core.guardrails.legal`; cada regra tem teste em `tests/guardrails/`.
- [ ] Teto de autonomia: `POST /panel/accounts/{id}/autonomy` com `L2` numa conta `archetype='gossip'` devolve `409`. Teste automatizado no CI.
- [ ] `SELECT count(*) FROM accounts WHERE archetype='gossip' AND autonomy IN ('L2','L3')` devolve **`0`** — invariante verificado por teste e por check diário em `maintenance`.
- [ ] `claim_status='unverified'` bloqueia: injetar item sem fonte confiável → `content_items.status='qa_failed'`, zero tasks, `guardrail_violations.rule='claim.unverified'`.
- [ ] Marcador linguístico: 20 posts com `claim_status='rumor'` revisados à mão — **100%** contêm marcador de não-confirmação. Regressão coberta por teste com casos fixos.
- [ ] Lista negra: injetar achado citando nome da lista negra → bloqueio com `severity='block'`, nada chega à fila humana. Testado com nome exato e com variação grafada (fuzzy `pg_trgm`).
- [ ] `SELECT count(*) FROM monitors WHERE status='active'` ≥ 10; `SELECT count(*) FROM monitor_hits WHERE detected_at > now()-interval '24 hours'` ≥ 50.
- [ ] Deduplicação: rodar o mesmo `monitor_check` 3× consecutivos cria **1** `ingested_items` (`UNIQUE (dedup_hash)` funcionando), e `monitor_hits` registra os 3 checks.
- [ ] Backoff: derrubar uma fonte propositalmente move o monitor para `status='error'` após 5 falhas e alarga o intervalo; alerta disparado no Grafana.
- [ ] Latência: `SELECT percentile_cont(0.95) WITHIN GROUP (ORDER BY c.created_at - i.ingested_at) FROM content_items c JOIN ingested_items i ON i.consumed_by=c.account_id …` < 4 min sobre 7 dias.
- [ ] Expiração: task de fofoca não publicada dentro de `expires_at` termina em `status='expired'`, nunca `dispatched`. `SELECT count(*) FROM tasks WHERE status='expired'` > 0 (o mecanismo foi exercitado de verdade).
- [ ] Auditoria por afirmação: para **qualquer** post publicado, `SELECT c.caption, c.claim_status, c.source_refs, i.payload FROM content_items c JOIN …` reconstrói quais fontes sustentaram a afirmação. Amostra de 10 posts verificada à mão.
- [ ] Fila de redação operando com SLA: p50 do tempo entre `approval_pending` e decisão humana < 10 min em horário editorial.
- [ ] Takedown: `POST /panel/posts/{id}/takedown` remove o post com verificação do orchestrator em < 15 min; a linha em `posts` é marcada, mas `events` e `ingested_items` permanecem intactos.
- [ ] Contradição evitada: teste dirigido em que a página afirmou "rumor" e depois recebe confirmação — a nova redação referencia a anterior (memória semântica por alvo). Validado à mão em ≥ 2 casos.
- [ ] **14 dias em L1** com ≥ 5 posts/dia, zero `guardrail_violations` com `severity IN ('block','kill')` **em conteúdo publicado**, zero pedido de remoção recebido, zero afirmação sem lastro em `source_refs`.
- [ ] `make test` verde, cobertura ≥ 80% em `olimpo/core/guardrails` (é a superfície mais cara de errar do projeto) e ≥ 70% em `olimpo/monitors`.

---

## 6. Riscos da fase

| Risco | Impacto | Mitigação |
|---|---|---|
| **Difamação** — afirmar rumor como fato | Processo judicial, indenização, fim da operação | `claim_status` bloqueante por código; verbo assertivo sobre crime exige `fact` + 2 fontes com `reliability ≥ 0.8`; marcador verificado no texto final; revisão humana obrigatória (L1 é o teto) |
| **Direito de imagem** — foto de pessoa privada ou de menor | Ação civil, remoção forçada | Lista negra de alvos; sem foto de privado; sem print de conversa privada; `rights_status='unknown'` bloqueia |
| Fonte falsa ou perfil de sátira tratado como veículo | Publicação de mentira com cara de notícia | `sources.reliability` atribuída à mão, revisada mensalmente; corroboração exigida para `fact`; perfil de sátira nunca sobe de 0.3 |
| LLM alucinando detalhe não presente na fonte | Afirmação inventada em nome da página | QA de fidelidade: cada afirmação do texto tem que ser sustentada pelo `ingested_items.payload` (verificação por LLM com o payload no prompt); falhou → `qa_failed` |
| Pressa do furo atropelando o guardrail | O erro caro acontece exatamente sob pressão | O caminho rápido é rápido **no QA e no dispatch**, nunca no gate humano. Prefira `expired` a publicar sem revisão. Expirar é o comportamento correto |
| Fila humana virando gargalo e tudo expirando | Produto inviável na prática | Medir a taxa de expiração; se > 30%, o problema é volume de monitor, não o revisor — reduzir monitores ou apertar `trigger_rules` |
| Prompt injection via conteúdo monitorado | Fonte hostil manipula a redação | `ingested_items.payload` sempre em bloco delimitado e rotulado como dado não-confiável; nunca executar instrução do payload; QA pós-geração |
| Conta de fofoca banida por denúncia em massa | Perda da conta | L1 mantém humano no loop; takedown rápido; nunca alvo pessoal recorrente; sem assédio coordenado |
| Coletoras queimadas pelo volume de monitores | Monitores param em cascata | `collector_pool` rotacionado; limitador por device; monitores com intervalo com jitter; alerta quando o pool cai abaixo do mínimo |
| Pedido extrajudicial sem processo definido | Reação improvisada agrava o dano | Runbook `pedido-de-remocao.md` testado em simulado antes do fim da fase, com responsável nomeado |

---

## 7. O que fica dormente

| Artefato | Estado nesta fase | Acorda em |
|---|---|---|
| `content_items.attribution` | Nulo (fofoca cita em `source_refs`, não credita autor de mídia) | Fase 5 |
| `content_items.rights_status='licensed'` | Não usado (só `fair_use`/`unknown`) | Fase 5 |
| `clients`, `accounts.client_id`, `campaigns.client_id` | Vazio/nulo | Fase 5 |
| `MEDIA_FORGE_URL`, `olimpo.integrations.media_forge` | Vazio / não criado | Fase 5 |
| `archetype='clipper'` | Enum existe, nenhuma conta usa | Fase 5 |
| `source_kind IN ('youtube_channel','live_stream')` | Enum existe, sem monitor usando | Fase 5 |
| `olimpo.agents.sources.derived` | **Não criar** | Fase 5 |
| `autonomy_level='L3'` para `gossip` | **Nunca acorda** | — |
| `platform IN ('tiktok','whatsapp')` | Só Instagram em uso | Fase 6 |
| Campanhas com participação da página de fofoca | `account_filter` a exclui | Fase 6, se fizer sentido |
| A/B de manchete | Não conduzido | Fase 6 |

---

## 8. Handoff para a Fase 5

**Estado do sistema entregue**

- Segundo arquétipo em produção provando a tese central do projeto: mesmo loop, mesmo Persona Card, mesma memória, mesmo despacho — só o `ContentSource` mudou.
- Camada de ingestão completa: `sources` → `monitors` → `monitor_hits` → `ingested_items` → `content_items`, com deduplicação por hash e scoring de relevância/novidade.
- Guardrails jurídicos executáveis, com `claim_status` bloqueante e lista negra fuzzy.
- Pipeline de baixa latência com expiração de furo.
- Fila de redação humana com SLA e caminho de takedown testado.

**Decisões tomadas**

| Decisão | Registro |
|---|---|
| `archetype='gossip'` tem teto arquitetural L1 — não existe caminho para L3 | `docs/adr/0012-teto-de-autonomia-por-arquetipo.md` |
| `claim_status='unverified'` bloqueia publicação em qualquer nível | `docs/08-guardrails-e-autonomia.md` |
| O marcador linguístico é verificado no texto final, não na intenção | ADR-0012 |
| Expirar um furo é preferível a publicar sem revisão humana | `docs/adr/0013-latencia-vs-revisao.md` |
| Auditoria (`events`, `ingested_items`) nunca é apagada, nem em takedown | `docs/12-seguranca.md` |
| Conteúdo de fonte externa é sempre dado não-confiável no prompt | `docs/12-seguranca.md` |

**Dívidas técnicas aceitas conscientemente**

1. `sources.reliability` é atribuída e revisada à mão. Não há aprendizado a partir de acertos/erros históricos da fonte — candidato natural à Fase 6.
2. A correlação de "mesma história em fontes diferentes" usa similaridade de embedding, sem resolução de entidade. Duas fontes falando do mesmo fato com nomes diferentes podem não se juntar.
3. O QA de fidelidade compara texto × payload com LLM, sem baseline quantificado de alucinação. Construir o conjunto de avaliação é trabalho pendente.
4. Não há verificação de que a página não está sendo usada para assédio coordenado contra um mesmo alvo ao longo do tempo. Só há regra por post, não por padrão temporal.
5. O painel continua com `API_TOKEN` único: não há registro de **qual pessoa** aprovou cada post. Numa fila de revisão jurídica isso é frágil — `approvals.decided_by` é preenchido à mão. **Resolver na Fase 5**, junto com o multi-tenant, que traz o mesmo problema por outro caminho.
6. Coletoras ainda são geridas manualmente; a rotação automática segue pendente desde a Fase 3 e agora dói mais.

**A primeira coisa que a Fase 5 faz:** resolver identidade de usuário no painel (dívida nº 5) — porque o multi-tenant de clipagem exige separar quem vê o quê, e a fila de fofoca exige saber quem aprovou.
