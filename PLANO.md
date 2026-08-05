# 🌌 Olimpo — Core de Agentes Sociais Autônomos

> **v2** — incorpora os serviços já existentes (android-manager, android-orchestrator, máquina de influencers), a decisão de arquitetura (core próprio e fino) e a abstração de **arquétipos de conta** (influencer → fofoca → clipagem → o que vier).

---

## 1. O que estamos construindo

Já existe a **execução** (farm de Android real, contas logadas, task com verificação) e a **fábrica de mídia** (personas com história, fotos/vídeos, suporte a brands). O que falta é o **cérebro**:

> Um core que dá **vida, memória e agenda** a cada conta, e um **manager** que emite campanhas coordenadas — onde cada conta traduz a campanha pela própria realidade.

O core **não fala com as plataformas**. Ele decide *o quê / quando / por quê*, produz o conteúdo chamando os serviços e **despacha tasks para o android-orchestrator**, que executa em device real. Essa separação é o ativo estratégico do projeto.

### 1.1 Ativos existentes (o que já está pronto)

| Serviço | Papel no sistema | Estado |
|---|---|---|
| **android-orchestrator** (API) | Camada de execução: localiza conta logada, executa e **verifica** qualquer ação de IG/WhatsApp (postar, DM, assistir reels, comentar), agenda por hora/conta | Pronto |
| **máquina de influencers** (API) | Fábrica de persona: história + geração de foto/vídeo, com suporte a **brands** (persona X segurando produto Y) | Em dev, funcional |
| **android-manager** (painel) | Frota de containers redroid, curadoria manual, login de contas | Pronto — camada de infra, o core não toca nele |
| **2× Mac mini M4** | Workers de LLM (Ollama/MLX) para narrativa, captions, respostas, decisões | Disponível |
| **APIs externas** (Anthropic/OpenAI) | Overflow e tarefas "de pensar" que exigem qualidade | Disponível |

### 1.2 Buracos conhecidos (a preencher)

| Buraco | Onde entra |
|---|---|
| **Edição/cortes de vídeo** (montagem, legenda queimada, reframe vertical, trilha) | Novo microserviço `media-forge` — obrigatório para clipagem, útil para influencer |
| **Cérebro narrativo + manager de campanhas** | Este projeto |
| **Ingestão de fontes** (monitorar contas, notícias, lives, tendências) | Camada de ingestão — dorme na v1, acorda na fofoca/clipagem |

---

## 2. Decisão de arquitetura

**Core próprio e fino** — não forkar framework. Justificativa:

- Todo framework de agente social (ElizaOS, LangChain social-agent) assume que **ele** fala com a plataforma via API/conector. Na nossa arquitetura quem fala é o farm de Android — herdaríamos abstrações que brigam com o nosso maior diferencial.
- O que falta construir é **~80% orquestração + engenharia de prompt**, pouco código. Não justifica carregar uma codebase de terceiros por anos.

**Referências que aproveitamos (sem fork):**
- **[generative_agents (Stanford)](https://github.com/joonspk-research/generative_agents)** — blueprint do loop *memory stream → reflection → planning*. É literalmente o "história atualizada todo dia".
- **[ElizaOS](https://github.com/elizaOS/eliza)** — adotar o formato de **character file** como spec de persona (padrão bom, testado, mantém compatibilidade futura com o ecossistema).
- **[Letta / MemGPT](https://github.com/letta-ai/letta)** — candidato a camada de memória de longo prazo. **Spike de 1–2 dias na Fase 1** para decidir: adotar como runtime de memória ou implementar direto em pgvector.

**Stack:** Python/FastAPI · Postgres + pgvector · Redis (fila + locks) · LiteLLM (roteia entre os 2 Mac minis + fallback pra API externa) · APScheduler · dashboard HTMX.

---

## 3. Arquitetura macro

```
┌───────────────────────────────────────────────────────────────┐
│                    MANAGER (o "CEO")                          │
│  campanhas · scheduler global · políticas · aprovações        │
│  espalhamento anti-detecção · métricas · kill switch          │
└───────┬───────────────────────────────────────────────────────┘
        │ tasks / campanhas
        ▼
┌───────────────────────────────────────────────────────────────┐
│                  AGENTES (1 por conta)                        │
│  ┌─────────────┐  ┌─────────────┐  ┌─────────────┐            │
│  │ INFLUENCER  │  │   FOFOCA    │  │  CLIPAGEM   │  ...       │
│  │ (Fase 1-3)  │  │  (Fase 4)   │  │  (Fase 5)   │            │
│  └─────────────┘  └─────────────┘  └─────────────┘            │
│  MESMA ESPINHA: plan → source → produce → QA → dispatch       │
│                 → verify → record → reflect                   │
│  Persona/voz · memória (episódica|semântica|narrativa) · humor │
└───────┬───────────────────────────────────────────────────────┘
        │
        ▼
┌───────────────────────────────────────────────────────────────┐
│                    CAMADA DE SERVIÇOS                         │
│  LLM Gateway (2× M4 + APIs)  │  máquina de influencers        │
│  media-forge (cortes/edição) │  camada de ingestão (fontes)   │
│  trend engine                │  QA (visual/textual/legal)     │
└───────┬───────────────────────────────────────────────────────┘
        │ tasks verificáveis
        ▼
┌───────────────────────────────────────────────────────────────┐
│  ANDROID-ORCHESTRATOR → redroid farm (android-manager)        │
│  postar · story · reels · DM · comentar · assistir · seguir   │
└───────────────────────────────────────────────────────────────┘
```

---

## 4. A espinha única: arquétipos de conta

> **A decisão mais importante do documento.** Influencer, fofoca e clipagem **não são sistemas diferentes** — são o mesmo loop com uma peça trocada: **de onde vem o conteúdo**.

### 4.1 O loop compartilhado (idêntico para todos os arquétipos)

```
1. PLAN     — o que faço hoje? (arco narrativo + métricas + campanhas do manager)
2. SOURCE   — de onde vem o material?      ◄── ÚNICA PEÇA QUE VARIA POR ARQUÉTIPO
3. PRODUCE  — caption/roteiro na voz da conta (LLM local)
4. QA       — visual + textual + brand safety + legal
5. DISPATCH — vira task no android-orchestrator (com jitter)
6. VERIFY   — orchestrator confirma execução
7. RECORD   — diário + métricas
8. REFLECT  — sintetiza o dia, atualiza a história/editorial
```

### 4.2 A peça que varia: `ContentSource`

Interface única, três implementações:

```python
class ContentSource(Protocol):
    def propose(self, account: Account, plan: DayPlan) -> list[ContentDraft]: ...
```

| Arquétipo | Implementação | Conteúdo vem de | Serviços que usa |
|---|---|---|---|
| **Influencer** | `GeneratedSource` | **Gerado** — a partir da história/vivência do dia | máquina de influencers |
| **Fofoca / nicho** | `CuratedSource` | **Curado** — ingestão de fontes, ranking de relevância, redação editorial | ingestão + trend engine + (opcional) máquina de influencers p/ arte |
| **Clipagem** | `DerivedSource` | **Derivado** — corte de conteúdo longo (live, podcast, YouTube) | ingestão + ASR + `media-forge` |

Tudo o mais — persona/voz, memória, agenda, campanhas, QA, despacho, métricas, guardrails — **é reaproveitado 100%**.

### 4.3 O que muda de fato em cada arquétipo

| Dimensão | Influencer | Fofoca | Clipagem |
|---|---|---|---|
| Cadência | 1–3/dia | 5–15/dia (reativo a notícia) | 3–10/dia (lote) |
| Latência crítica | Baixa | **Altíssima** (furo perde valor em minutos) | Média |
| "História" | Vida fictícia (arco pessoal) | **Linha editorial** (o que a página defende, tom, quem ela ama/odeia) | **Curadoria** (que criadores/nichos ela clipa) |
| Risco dominante | Consistência visual | **Jurídico** (difamação, direito de imagem) | **Copyright / fair use** |
| Métrica-chave | Engajamento/seguidor | Velocidade + alcance | Retenção + reaproveitamento |

**Insight:** persona não é exclusividade do influencer. Uma página de fofoca tem voz ("irônica, usa memes, nunca xinga"), opiniões e memória de quem ela já cobriu. O mesmo Persona Card serve — muda o preenchimento, não o schema.

---

## 5. Modelo de dados (já preparado para os 3 arquétipos)

```sql
accounts          id, handle, plataforma, archetype(influencer|gossip|clipper),
                  persona_card jsonb, nivel_autonomia, device_binding,
                  client_id NULL, status

-- persona_card guarda voz/tom/tabus/estética; para influencer inclui o
-- persona_id da máquina de influencers; para fofoca, a linha editorial.

memories          id, account_id, tipo(episodica|semantica|narrativa),
                  conteudo, embedding, importancia, criado_em
story_arcs        id, account_id, titulo, resumo, inicio, fim, estado jsonb

campaigns         id, nome, client_id NULL, brief, brand_asset_refs,
                  audio_ref, target_filter jsonb, janela_inicio, janela_fim,
                  spread_policy jsonb, status
campaign_tasks    id, campaign_id, account_id, task_id, adaptacao_local

tasks             id, account_id, tipo, payload jsonb, prioridade,
                  origem(manager|self|humano|campaign), agendado_para,
                  status, external_task_id  -- id no android-orchestrator
                  , verificado_em, resultado jsonb

content_items     id, account_id, archetype_source, tipo(post|story|reels),
                  caption, media_refs jsonb, status,
                  -- campos que só a fofoca/clipagem usam, mas já existem:
                  source_refs jsonb,      -- de onde veio (URL, conta, live)
                  attribution text,       -- crédito obrigatório
                  rights_status,          -- own|licensed|fair_use|unknown
                  claim_status            -- fato|rumor|opiniao|nao_verificado

sources           id, tipo(conta|rss|youtube|live|api), endpoint,
                  nicho, confiabilidade, ativo        -- dorme na v1
ingested_items    id, source_id, hash_dedup, conteudo jsonb,
                  score_relevancia, consumido_por_account_id, criado_em

trends            id, plataforma, tipo(audio|hashtag|formato), ref,
                  score, janela                       -- compartilhado por todos

posts             id, content_item_id, external_id, publicado_em, metrics jsonb
interactions      id, post_id, autor, texto, classificacao, resposta, respondido_por
events            id, account_id, tipo, detalhe jsonb, criado_em   -- auditoria total
clients           id, nome, contrato jsonb                          -- multi-tenant
```

**Regra:** os campos de fofoca/clipagem (`source_refs`, `attribution`, `rights_status`, `claim_status`, `client_id`) **nascem na v1 mesmo sem uso**. Custo hoje: zero. Custo de adicionar depois com 50 contas em produção: migração dolorosa.

---

## 6. Campanhas coordenadas

Fluxo: manager emite brief → filtra contas elegíveis → cada agente **traduz pela própria realidade** → tasks espalhadas no orchestrator.

```
Brief: "todas postem usando o áudio X, tema: superação"
  ├─ Luna (fitness, arco "treinando maratona") → reels correndo ao amanhecer
  ├─ Kai (gamer, arco "rank up")              → clipe de comeback em partida
  └─ Página de fofoca                          → post "o áudio que tomou conta do TL"
```

### ⚠️ Espalhamento obrigatório (anti-detecção)

N contas fazendo a mesma ação na mesma janela é **o sinal nº 1 de detecção de farm**. O manager nasce com `spread_policy` obrigatória:

- **Jitter temporal** — janela mínima de horas entre contas da mesma campanha (nunca "todas às 19h").
- **Texto nunca repetido** — cada caption gerada na voz da persona (grátis, já temos LLM local).
- **Participação parcial** — nem toda conta entra em toda campanha (amostragem por relevância + aleatoriedade).
- **Sem grafo suspeito** — limite de quantas contas da casa interagem entre si; interação cruzada é poderosa mas é rastro.
- **Respeitar o ritmo natural da conta** — campanha não pode dobrar a cadência habitual da conta.

---

## 7. Autonomia e guardrails

| Nível | Comportamento |
|---|---|
| **L0 – Estagiário** | Só drafts; humano aprova tudo |
| **L1 – Júnior** | Auto-posta; interações vão para revisão |
| **L2 – Pleno** | Auto-posta e auto-responde; revisão por amostragem (10%) |
| **L3 – Sênior** | Full auto; humano só vê alertas e métricas |

**Sempre ativo, em qualquer nível:** brand safety em todo texto/imagem · kill switch por conta e global · log auditável de toda decisão ("por que postou isso?") · rate limits com jitter · temas proibidos por conta.

**Guardrails específicos por arquétipo (documentar agora, ativar depois):**
- **Fofoca**: nunca afirmar rumor como fato (`claim_status` obrigatório e refletido no texto) · lista negra de alvos (menores, pessoas comuns não-públicas) · sem acusação criminal/doença/orientação sexual · direito de resposta e política de remoção rápida. **Risco jurídico real — L3 provavelmente nunca vai valer a pena aqui.**
- **Clipagem**: crédito obrigatório ao criador (`attribution`) · lista de criadores que autorizaram vs. não · nunca clipar conteúdo com strike/DMCA conhecido · duração e transformação mínimas (fair use).
- **Influencer**: rosto 100% sintético, nunca baseado em pessoa real · rótulo de IA / bio assumindo persona virtual · sem conselho de saúde/finanças · sem flerte com seguidor real.

---

## 8. Roadmap

### Fase 0 — Fundação (1–2 semanas)
Repo, docker-compose (Postgres+pgvector, Redis), FastAPI. LLM Gateway (LiteLLM → 2 Mac minis). **Clientes das 3 APIs existentes**, todos com **modo dry-run** (grava em disco, não despacha). Modelo de dados completo (incluindo campos dormentes). Tabela `events` desde o dia 1.
**Saída:** task `criar_post` roda ponta a ponta em dry-run.

### Fase 1 — Uma influencer, humano no loop (2–3 semanas)
Persona Card v1 (formato character file) + memória episódica. Loop completo `plan→source→produce→QA→dispatch→verify→record→reflect` com `GeneratedSource`. Despacho real no android-orchestrator com verificação. Dashboard mínimo de aprovação (L0). **Spike Letta (1–2 dias): adotar ou pgvector puro.**
**Saída:** 1 influencer postando 1×/dia, história evoluindo sozinha, um clique de aprovação.

### Fase 2 — Engajamento + autonomia parcial (3–4 semanas)
Interações (comentários/DM) via orchestrator: classificar → responder na voz da persona. Memória semântica (reconhecer seguidores recorrentes). Promoção a L1/L2. Métricas realimentando o `PLAN`.
**Saída:** 1 semana em L2 sem incidente.

### Fase 3 — Multi-influencer + campanhas (4–6 semanas)
Narrative engine completo (arcos, eventos aleatórios, humor). Motor de campanhas + `spread_policy`. 3–5 influencers, nichos distintos, crossovers controlados. Trend engine v1 (áudios em alta).
**Saída:** panteão em L2/L3 rodando campanhas coordenadas sem intervenção diária.

### Fase 4 — Páginas de fofoca / nicho (3–4 semanas)
Camada de ingestão (`sources`, dedup por hash, ranking de relevância). `CuratedSource`. Linha editorial como persona. Pipeline de baixa latência (furo). Guardrails jurídicos + `claim_status` obrigatório. Fila de revisão humana dedicada.
**Saída:** 1 página de fofoca em L1 (nunca L3), com auditoria de cada afirmação publicada.

### Fase 5 — Clipagem (3–4 semanas)
`media-forge`: ingest de vídeo longo → ASR (Whisper) → detecção de momento ("o que vira clipe") → corte + reframe vertical + legenda queimada + trilha. `DerivedSource`. Controle de direitos e atribuição. Multi-tenant (`clients`) — clipagem geralmente é serviço para terceiros.
**Saída:** conta de clipagem processando 1 fonte longa/dia em lote.

### Fase 6 — Escala e negócio (contínuo)
Analytics por nicho/horário/formato · A/B testing conduzido pelos próprios agentes · onboarding de conta nova em < 1 dia · banco de conteúdo reaproveitável entre contas · monetização (publipost com disclosure, afiliados, clipagem como serviço).

---

## 9. Riscos & mitigações

| Risco | Mitigação |
|---|---|
| **Detecção de farm** (padrão coordenado) | `spread_policy` obrigatória, texto sempre único, grafo de interação limitado, cadência natural por conta |
| Ban / perda de conta | Aquecimento gradual, tasks de comportamento humano (assistir reels do nicho), device/identidade estáveis via android-manager |
| Inconsistência visual | Face reference/seed fixa na máquina de influencers + QA por embedding de rosto vs. canônico |
| Agente fora da persona | Persona Card em todo prompt + QA textual + amostragem humana |
| **Processo judicial (fofoca)** | `claim_status`, lista negra de alvos, revisão humana obrigatória, remoção rápida, nunca L3 |
| **DMCA (clipagem)** | Atribuição obrigatória, whitelist de criadores, transformação mínima, monitorar strikes |
| Mac minis viram gargalo | LiteLLM com prioridade de fila (interação > ideação) + overflow pra API externa |
| Acoplamento com os 3 serviços | Clientes atrás de interface + dry-run; core nunca chama plataforma direto |

---

## 10. Próximos passos concretos

1. **Definir a primeira persona** (nicho, nome, estética) — cobaia de tudo.
2. **Mapear os contratos de API** dos 3 serviços existentes (endpoints, payloads, callbacks de verificação do orchestrator).
3. **Benchmark dos Mac minis**: tokens/s dos modelos candidatos (Qwen 2.5 7B / Llama 3.1 8B / um 14B).
4. **Fase 0.**
