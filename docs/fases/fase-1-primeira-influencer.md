# Fase 1 — Uma influencer, humano no loop

**Duração estimada:** 2–3 semanas · **Depende de:** [Fase 0](fase-0-fundacao.md) · **Entrega para:** [Fase 2](fase-2-engajamento.md)

---

## 1. Resumo

Uma única conta influencer ganha Persona Card, memória episódica e história que evolui. O loop completo passa a produzir conteúdo de verdade via máquina de influencers, passar por QA e — depois de um clique humano — ser despachado ao android-orchestrator com verificação real. É a primeira vez que `DISPATCH_ENABLED=true`.

Inclui um **spike de 1–2 dias** decidindo entre Letta/MemGPT como runtime de memória ou pgvector puro. A decisão vira ADR e trava o desenho da Fase 2.

---

## 2. Pré-requisitos

| Item | Detalhe |
|---|---|
| Fase 0 com todos os critérios de saída marcados | Inclusive `make lint` e `make test` limpos |
| Persona definida | Nicho, nome, handle, estética — o item 1 dos próximos passos do PLANO |
| Conta real criada e logada | Feito no **android-manager**, fora do Olimpo; o Olimpo só recebe o `device_binding` |
| Persona existente na máquina de influencers | `persona_engine_id` válido, com face reference/seed fixa |
| `ORCHESTRATOR_TOKEN` + webhook alcançável | O orchestrator precisa conseguir chamar `ORCHESTRATOR_WEBHOOK_URL` de volta |
| Conta aquecida | `status='warming'` por ≥ 7 dias com tasks de consumo (`watch_reels`, `view_stories`) antes de qualquer publicação |

---

## 3. Escopo

### 3.0 SPIKE — Letta/MemGPT vs. pgvector puro (1–2 dias, primeira coisa da fase)

Timebox rígido. Protótipo descartável em `spikes/letta/`, resultado em `docs/adr/0005-camada-de-memoria.md`.

**O que se testa:** 200 memórias episódicas sintéticas de uma persona, 30 consultas de recuperação típicas do passo PLAN ("o que aconteceu comigo esta semana?", "já falei sobre X?", "quem é o seguidor @y?").

**Critérios de decisão (peso):**

| Critério | Peso | Como se mede | Limiar para adotar Letta |
|---|---|---|---|
| Qualidade da recuperação | 30% | Precisão@5 nas 30 consultas, julgada manualmente | ≥ 15% melhor que o baseline pgvector |
| Latência p95 do recall | 20% | Medida no spike, com o Mac mini no loop | ≤ 800 ms |
| Custo em tokens/dia por conta | 20% | Soma de `llm_calls` do spike, extrapolada para 50 contas | ≤ 2× o baseline |
| Multi-tenancy | 15% | 50 agentes isolados no mesmo processo | Suportado sem 1 processo por conta |
| Acoplamento | 10% | O estado continua inspecionável em SQL? Dá para sair? | Memória espelhada em `memories`, sem lock-in |
| Operação | 5% | Serviço extra no compose, upgrades, backup | Aceitável |

**Baseline pgvector:** `memories` + índice HNSW + score `relevância(cosseno) × recência(decay exponencial) × importância/10`, exatamente o esquema do generative_agents.

**Regra de desempate:** empate técnico → **pgvector puro**. Menos peça móvel, o schema já existe e a recuperação é código nosso.

**Saída obrigatória do spike:** ADR-0005 preenchido com os números medidos, não com impressões. Se Letta for adotado, `memories` continua sendo escrita como espelho auditável — nunca a única cópia fica dentro do Letta.

### 3.1 Persona e narrativa

1. **`docs/04-persona-card-spec.md`** — spec do Persona Card v1 no formato *character file* do ElizaOS: `name`, `bio`, `lore`, `style{voice, tone, taboos, emoji_policy}`, `topics`, `adjectives`, `aesthetic{palette, framing, locations}`, `constraints{forbidden_topics, disclosure}`, `engine{persona_engine_id, seed, face_ref}`. Compatibilidade futura com o ecossistema sem fork.
2. **`olimpo.agents.persona`** — carrega e valida `accounts.persona_card` (modelo pydantic), versiona em `persona_versions` a cada alteração (incrementa `accounts.persona_version`), e monta o bloco de prompt injetado em **toda** chamada de LLM da conta.
3. **`olimpo.agents.narrative`** (v1, mínimo) — um `story_arcs` ativo criado à mão, com `state` jsonb avançando por dia. Sem eventos aleatórios nem humor dinâmico (Fase 3).

### 3.2 Memória

4. **`olimpo.agents.memory`** — `write(account, kind, content, importance, arc_id)` e `recall(account, query, k, kinds)`. Só `kind='episodic'` nesta fase. A importância 0–10 é atribuída pela LLM na escrita, com prompt fixo e versionado.
5. **Decay e uso** — `recall` atualiza `memories.last_used_at`; o score combina similaridade de cosseno, recência sobre `occurred_at` e `importance`. Fórmula em `docs/05-loop-do-agente.md`, parametrizada no `Settings`.
6. **Job de embedding** — na fila `production`, gera `memories.embedding` via `olimpo-embed`. Memória sem embedding não bloqueia a escrita, mas nunca aparece no `recall` semântico (fallback por recência).

### 3.3 O loop, para valer

7. **`olimpo.agents.plan`** — recall das últimas 24 h + arco ativo + `accounts.cadence`/`active_hours`/`mood` → uma linha em `day_plans` com `narrative`, `intents` (lista de intenções tipadas) e `reasoning`. Um job por conta por dia, na fila `production`, disparado pelo beat no início da `active_hours` da conta (respeitando `accounts.timezone`).
8. **`olimpo.agents.sources.generated`** — `GeneratedSource`: traduz cada intenção do plano em um prompt de imagem/vídeo para a máquina de influencers (seed e face reference fixas do Persona Card), chama `generate_image`/`generate_video`, aguarda o asset e devolve `ContentDraft` com `media_refs`.
9. **`olimpo.agents.produce`** — caption + hashtags na voz da persona (`olimpo-smart`), gravando `content_items.caption`, `.hashtags`, `.reasoning`, `.source_strategy='generated'`.
10. **`olimpo.agents.qa`** — três checagens, resultado consolidado em `content_items.qa_report`:
    - **visual**: embedding do rosto gerado vs. rosto canônico da persona; abaixo do limiar → `qa_failed`;
    - **textual**: aderência à voz + `constraints.forbidden_topics` do Persona Card;
    - **brand safety**: lista de termos proibidos globais + guardrails de influencer (sem conselho de saúde/finanças, sem flerte com seguidor real, rótulo de IA quando exigido).
    Falha grave grava `guardrail_violations` com `severity='block'` e nunca chega ao humano.
11. **Gate de autonomia** — em `L0`, todo item aprovado no QA vira `content_items.status='approval_pending'` e uma linha em `approvals` (`subject_type='content_item'`). Nada é despachado sem `decision='approved'`.
12. **`olimpo.agents.dispatch`** — cria a task (`type='publish_post'|'publish_story'|'publish_reels'`, `origin='self'`, `scheduled_for` com jitter dentro da `active_hours`) e deixa o `dispatcher_tick` da Fase 0 fazer o resto — agora com `DISPATCH_ENABLED=true`.
13. **`olimpo.agents.verify`** — o webhook do orchestrator move a task para `verified` e cria a linha em `posts` (`external_id`, `permalink`). **Reconciliação por polling** em `maintenance` a cada 5 min para tasks presas em `dispatched`/`running` além do timeout; estouro de `max_attempts` → `failed` + evento `task.abandoned`.
14. **`olimpo.agents.record`** — grava a memória episódica do que foi publicado ("hoje postei X porque Y") ligada ao `arc_id`.
15. **`olimpo.agents.reflect`** — job noturno na fila `production`: sintetiza o dia em uma memória episódica de alta importância e atualiza `story_arcs.state` e `.summary`.

### 3.4 Painel e operação

16. **`olimpo.panel_api.approvals`** — `GET /panel/approvals` (fila aberta via `idx_approvals_open`), `POST /panel/approvals/{id}/approve|reject`, com `edited_payload` para o humano corrigir a caption antes de aprovar.
17. **Painel Next.js** — tela de fila de aprovação com preview da mídia, caption editável, o `reasoning` do agente e o `qa_report`; tela de conta com timeline de `events`; **kill switch por conta** (`POST /panel/accounts/{id}/suspend` → `status='suspended'`).
18. **`olimpo.core.guardrails`** — motor de regras com id estável por regra, avaliado no QA e no dispatch. Toda violação vira `guardrail_violations`.
19. **`docs/runbooks/conta-suspensa.md`** e **`docs/runbooks/task-presa.md`** — o que fazer quando o kill switch dispara e quando uma task não verifica.

---

## 4. Fora de escopo

- Responder comentários ou DM. A conta publica e cala. **Fase 2.**
- Memória semântica e reconhecimento de seguidores recorrentes. **Fase 2.**
- Métricas realimentando o PLAN. Coleta-se `post_metrics` para observar, não para decidir. **Fase 2.**
- Autonomia acima de L0. Nem L1. Todo post desta fase passa por humano.
- Mais de uma conta. Segunda conta é Fase 3 — e é lá que aparecem os problemas de coordenação.
- Campanhas, `spread_policy`, recon, crossovers. **Fase 3.**
- Eventos narrativos aleatórios e humor dinâmico. **Fase 3.**
- Trends. `trends` continua vazia.
- Qualquer arquétipo além de `influencer`.
- Otimização de custo do LLM. Mede-se em `llm_calls`.

---

## 5. Critérios de saída

- [ ] `docs/adr/0005-camada-de-memoria.md` existe, com a tabela de critérios preenchida com **números medidos** e um veredito explícito.
- [ ] `SELECT persona_card->'style'->>'voice' FROM accounts` devolve conteúdo real; `python -m olimpo.agents.persona validate --account <id>` sai `0`.
- [ ] Alterar o `persona_card` pelo painel incrementa `accounts.persona_version` e cria uma linha em `persona_versions` com `change_note`.
- [ ] Após 7 dias de operação: `SELECT count(*) FROM memories WHERE account_id=<id> AND kind='episodic'` ≥ 20, todas com `embedding IS NOT NULL`.
- [ ] `python -m olimpo.agents.memory recall --account <id> --query "o que fiz na praia"` devolve 5 memórias ordenadas, com o score decomposto (similaridade, recência, importância) no output.
- [ ] `SELECT count(*) FROM day_plans WHERE account_id=<id>` = número de dias corridos da fase, sem buracos; todo `day_plan` tem `reasoning` não-nulo e `llm_call_id` válido.
- [ ] Um `content_item` completo por dia com `media_refs` apontando para asset real da máquina de influencers e `source_strategy='generated'`.
- [ ] QA reprovando de verdade: `SELECT count(*) FROM guardrail_violations WHERE account_id=<id>` ≥ 1 em teste dirigido (injetar caption com tema proibido → `status='qa_failed'`, nada chega em `approvals`).
- [ ] QA visual: injetar imagem de rosto diferente → `qa_report->'face_match'->>'passed' = 'false'` e `status='qa_failed'`.
- [ ] Fila de aprovação no painel: aprovar em um clique cria a task; `SELECT count(*) FROM tasks WHERE content_item_id=<id>` = 1.
- [ ] Rejeitar não cria task e grava `approvals.decision='rejected'` com `decided_by`.
- [ ] Com `DISPATCH_ENABLED=true`: `SELECT status, external_task_id, verified_at FROM tasks WHERE type='publish_post' ORDER BY created_at DESC LIMIT 1` devolve `verified` com `external_task_id` e `verified_at` preenchidos.
- [ ] Linha correspondente em `posts` com `external_id` e `permalink`; o `permalink` abre o post real.
- [ ] **7 dias consecutivos** com ≥ 1 post publicado e verificado por dia, sem intervenção manual além do clique de aprovação.
- [ ] `story_arcs.state` mudou pelo menos 3 vezes na semana, e o `summary` do arco reflete o que foi publicado (verificação manual, registrada no handoff).
- [ ] Reconciliação: matar o worker no meio de um dispatch deixa a task em `dispatched`; o job de `maintenance` a move para `verified` ou `failed` em ≤ 10 min.
- [ ] Kill switch por conta: `POST /panel/accounts/{id}/suspend` → em ≤ 1 tick nenhuma task nova é despachada; evento `account.suspended` em `events`.
- [ ] Auditoria: dado o `id` de qualquer post, `SELECT * FROM events WHERE trace_id=(...)` reconstrói a cadeia plan → source → produce → qa → approval → dispatch → verify → record.
- [ ] `SELECT sum(cost_usd) FROM llm_calls WHERE created_at > now()-interval '1 day'` ≤ `LLM_DAILY_BUDGET_USD`.
- [ ] `make test` verde com cobertura ≥ 70% em `olimpo/agents`.

---

## 6. Riscos da fase

| Risco | Impacto | Mitigação |
|---|---|---|
| Spike do Letta vira projeto | Come a fase inteira | Timebox de 2 dias, protótipo descartável, empate → pgvector. Decidir e seguir |
| Primeiro despacho real bane a conta | Perde a cobaia e o aprendizado | Aquecimento de 7 dias em `status='warming'`; 1 post/dia; `ACCOUNT_MAX_ACTIONS_PER_HOUR=12`; jitter dentro da `active_hours`; começar por story antes de feed |
| Inconsistência facial entre posts | A persona "não é a mesma pessoa" | Seed + face reference fixas no Persona Card; QA visual por embedding bloqueando abaixo do limiar; limiar calibrado com 20 imagens antes de ligar o dispatch |
| Caption fora da persona | Quebra a ilusão | Persona Card no prompt sempre; QA textual; L0 mantém humano no loop a fase inteira |
| Memória vira lixo (tudo com importância 8) | `recall` deixa de discriminar | Prompt de importância com rubrica e exemplos; auditar a distribuição semanalmente (`SELECT importance, count(*) FROM memories GROUP BY 1`); se degenerar, calibrar antes da Fase 2 |
| Webhook do orchestrator não chega (NAT/firewall) | Tasks eternamente `dispatched` | Reconciliação por polling desde o primeiro dia; alerta quando > 3 tasks passam do timeout |
| Mac mini gargala e o PLAN atrasa | Post fora da janela | PLAN roda de madrugada, com folga; `production` tem prioridade menor que `realtime`; overflow para API externa no LiteLLM |
| Persona Card ganhando campos ad-hoc | Divergência entre conta e spec | O modelo pydantic é a validação; campo novo exige alterar `docs/04-persona-card-spec.md` no mesmo commit |

---

## 7. O que fica dormente

| Artefato | Estado nesta fase | Acorda em |
|---|---|---|
| `memories.kind='semantic'` | Enum existe, ninguém escreve | Fase 2 |
| `memories.kind='narrative'` | Enum existe, ninguém escreve | Fase 3 |
| `interactions` | Vazia; comentários chegam e são ignorados | Fase 2 |
| `post_metrics` | Coletada 1×/dia por curiosidade, não realimenta o PLAN | Fase 2 |
| `approvals.sampled` | Sempre `false` (tudo é revisão obrigatória em L0) | Fase 2 (L2 e `L2_SAMPLING_RATE`) |
| `autonomy_level` L1/L2/L3 | Enum existe, só L0 é usado | Fase 2 (L1/L2), Fase 3 (L3) |
| `accounts.mood` | Default `{}`, lido pelo PLAN mas sempre vazio | Fase 3 |
| `campaigns` e derivadas | Vazias; `campaign_id` sempre nulo | Fase 3 |
| `trends` | Vazia | Fase 3 |
| `task_type='extract_content'` | Não emitido | Fase 3 (recon) |
| `sources`, `ingested_items`, `monitors` | Vazias | Fase 4 |
| `content_items.claim_status` | Sempre `'unverified'`, sem efeito (não é fofoca) | Fase 4 |
| `content_items.rights_status` | Sempre `'own'` — correto, é conteúdo gerado | Fase 5 |
| `clients`, `accounts.client_id` | Vazio/nulo | Fase 5 |
| Fila `realtime` | Sem produtores | Fase 2 |
| `olimpo.agents.sources.curated` / `.derived` | **Não criar** | Fases 4 e 5 |

---

## 8. Handoff para a Fase 2

**Estado do sistema entregue**

- 1 influencer publicando 1×/dia, verificada em device real, com aprovação humana por clique.
- Memória episódica viva com embeddings, `recall` funcional e decay calibrado.
- Um arco narrativo evoluindo diariamente via `REFLECT`.
- QA com três eixos bloqueando de verdade; `guardrail_violations` populada.
- Reconciliação de tasks resolvendo webhook perdido.
- Painel com aprovação, edição de caption e kill switch por conta.

**Decisões tomadas**

| Decisão | Registro |
|---|---|
| Camada de memória: Letta vs. pgvector | `docs/adr/0005-camada-de-memoria.md` |
| Persona Card no formato character file do ElizaOS | `docs/adr/0006-persona-card-format.md` |
| Fórmula de recall = similaridade × recência × importância | `docs/05-loop-do-agente.md` |
| Verificação = webhook + reconciliação por polling (cinto e suspensório) | `docs/adr/0007-verificacao-de-task.md` |
| Limiar do QA visual de rosto | Calibrado e registrado no ADR-0006 |

**Dívidas técnicas aceitas conscientemente**

1. `story_arcs` é criado à mão. Não há motor gerando arcos — Fase 3.
2. `GeneratedSource` chama a máquina de influencers de forma síncrona com polling ingênuo; se a geração passar de minutos, o job ocupa um slot do worker. Aceitável com 1 conta, insustentável com 5 — refatorar para callback na Fase 3.
3. O QA textual é heurística + LLM, sem conjunto de avaliação. Fase 2 constrói o *golden set* de captions boas/ruins.
4. Não há rate limit global entre contas (só por conta). Com 1 conta não importa; a Fase 3 precisa disso antes de subir para 5.
5. `post_metrics` é coletado sem uso, então nunca foi validado a fundo. A Fase 2 vai encontrar bugs aí.
6. O painel não tem controle de acesso por usuário — `API_TOKEN` único.

**A primeira coisa que a Fase 2 faz:** ligar a coleta de `interactions` **em L0** (classificar e propor resposta, sem enviar nada) por uma semana, para medir a qualidade da classificação antes de promover a conta para L1.
