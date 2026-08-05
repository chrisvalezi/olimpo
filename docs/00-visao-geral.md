# 00 — Visão geral

> Documento de entrada do Olimpo. Se você só vai ler um arquivo, leia este.
> **Leia depois:** [01 — Arquitetura](01-arquitetura.md) · [05 — Loop do agente](05-loop-do-agente.md) · [Glossário](glossario.md)

---

## 1. O problema

Operar contas sociais em escala hoje se resolve de duas formas, ambas ruins.

**Agendador de posts.** Você enfileira conteúdo e ele publica no horário. O resultado é uma conta sem memória: o post de terça não sabe que existiu o de segunda, não reage ao que aconteceu com ela, não tem opinião nem continuidade. Funciona como distribuição, falha como presença. E quando você replica isso em N contas, todas com o mesmo tom e o mesmo horário, você não construiu uma operação — construiu uma assinatura de bot.

**Operação manual.** Funciona, mas custa uma pessoa por punhado de contas e não sobrevive à décima.

Há ainda um problema técnico embaixo dos dois: **acesso à plataforma**. Ferramentas que dependem da API oficial esbarram em limites, revisão de app e escopos que simplesmente não cobrem o que uma conta de verdade faz. Ferramentas que dependem de bibliotecas não-oficiais são banidas por padrão de rede e de comportamento.

O Olimpo ataca os três: presença com continuidade, escala sem uma pessoa por conta, e execução em Android real em vez de API.

---

## 2. A tese

> **Uma conta com história própria performa melhor que um agendador porque ela produz conteúdo que só ela poderia produzir — e porque parece humana no eixo onde as plataformas realmente olham: comportamento ao longo do tempo.**

Três razões, na ordem em que importam.

**2.1 Continuidade gera conteúdo não-genérico.** Um agendador precisa que alguém decida cada post. Um agente com memória decide sozinho porque tem de onde tirar: ele viveu o dia anterior, está no meio de um arco ("treinando para a maratona", "semana da praia"), lembra que um seguidor recorrente comentou algo, tem humor variável. A pergunta "o que posto hoje?" deixa de ser um vazio criativo e vira uma consulta ao próprio estado. É a diferença entre gerar texto sobre um tema e gerar texto a partir de uma vida — o segundo tem detalhe específico, e detalhe específico é o que engaja.

**2.2 Consistência é o que constrói audiência.** Voz estável, obsessões recorrentes, relações que evoluem: é isso que faz alguém seguir. O Persona Card (`accounts.persona_card`) entra em **todo** prompt e a memória semântica lembra quem é quem. Um agendador não tem onde guardar isso.

**2.3 Coordenação sem padrão detectável.** N contas postando a mesma coisa no mesmo horário é o sinal nº 1 de detecção de farm. Como cada agente **traduz** a campanha pela própria realidade — o texto sai da voz da persona, o horário sai de um sorteio dentro de uma janela, e nem toda conta participa — a mesma campanha vira N artefatos genuinamente diferentes. Isso não é um recurso extra: `campaigns.spread_policy` é `NOT NULL` no schema. Não existe campanha sem espalhamento.

**O corolário estratégico.** O que o Olimpo constrói não é conteúdo — é um **conjunto de contas com história acumulada, operadas por comportamento verificado em device real**. Isso é um ativo que não se copia com prompt melhor.

---

## 3. Os ativos existentes

O Olimpo não começa do zero. A execução e a fábrica de mídia já existem; falta o cérebro.

| Serviço | Papel | Estado | Como o Olimpo usa |
|---|---|---|---|
| **android-orchestrator** | Camada de execução: localiza a conta logada, executa e **verifica** qualquer ação (postar, story, reels, DM, comentar, seguir, assistir) | Pronto | Todo despacho vira task aqui. O Olimpo só considera feito o que volta `verified` (`tasks.status='verified'`, `verified_at`) |
| **máquina de influencers** | Fábrica de persona: história + geração de foto/vídeo, com suporte a *brands* (persona X segurando produto Y) | Em dev, funcional | `GeneratedSource` chama para produzir mídia; o vínculo é `accounts.persona_engine_id` |
| **android-manager** | Frota de containers redroid, curadoria manual, login de contas | Pronto | **Somente leitura.** O painel exibe saúde de device; o core nunca escreve na frota |
| **2× Mac mini M4** | Workers de LLM (Ollama/MLX) | Disponível | Backends `mac_mini_1`/`mac_mini_2` no LLM Gateway. Fazem o volume: plano, caption, reflexão, classificação |
| **APIs externas** (Anthropic/OpenAI) | Overflow e tarefas que exigem qualidade | Disponível | Fallback do gateway, com teto diário (`LLM_DAILY_BUDGET_USD`) |
| **media-forge** | Cortes, reframe vertical, legenda queimada, trilha | **A construir (Fase 5)** | `DerivedSource`. Obrigatório para clipagem, útil para influencer |

### 3.1 O que o Olimpo acrescenta

| Peça | O que faz |
|---|---|
| **Persona e voz** | `accounts.persona_card` (formato de *character file*) entra em todo prompt. Versionado em `persona_versions` |
| **Memória** | `memories` com pgvector — episódica, semântica e narrativa. Recuperação por relevância × recência × importância |
| **Narrativa** | `story_arcs` — a conta está sempre no meio de alguma coisa, com começo, estado e fim |
| **Agenda** | `day_plans` — o que ela vai fazer hoje e **por quê** (`reasoning`), respeitando `cadence`, `active_hours` e `timezone` |
| **Manager de campanhas** | `campaigns` + `campaign_recon` + `campaign_participants`. Brief vira participação espalhada e adaptada |
| **Governança** | `approvals`, `guardrail_violations`, níveis L0–L3, kill switch por conta e global |
| **Auditoria** | `events` desde o primeiro commit. Toda decisão tem linha e `trace_id` |
| **Contabilidade** | `llm_calls` — custo e latência por backend e por propósito |

**A fronteira, dita de forma dura:** o core **nunca** fala com a plataforma. Ele decide *o quê, quando e por quê*, produz chamando serviços, e despacha tasks. Quem toca o Instagram é um Android real. Essa separação é o ativo estratégico do projeto — ver [ADR-0001](adr/0001-core-proprio.md).

---

## 4. O loop

Todo agente, de todo arquétipo, roda as mesmas oito etapas. É o mesmo blueprint do paper *generative_agents* (memory stream → reflection → planning), adaptado para execução remota e verificada.

```
PLAN ─▶ SOURCE ─▶ PRODUCE ─▶ QA ─▶ DISPATCH ─▶ VERIFY ─▶ RECORD ─▶ REFLECT
         │                                                            │
         └─ única peça que varia por arquétipo                        └─▶ alimenta o PLAN de amanhã
```

| Etapa | O que acontece | Escreve em |
|---|---|---|
| **PLAN** | Lê arco ativo, memórias relevantes, métricas recentes e campanhas atribuídas. Decide as intenções do dia e registra o motivo | `day_plans` (`narrative`, `intents`, `reasoning`) |
| **SOURCE** | Obtém o material: gerado, curado ou derivado — conforme o arquétipo | `content_items` (draft), `ingested_items` |
| **PRODUCE** | Escreve caption/roteiro na voz da conta, escolhe hashtags e áudio | `content_items` (`caption`, `hashtags`, `audio_ref`, `reasoning`) |
| **QA** | Visual, textual, brand safety e legal. Reprova ou marca para revisão | `content_items.qa_report`, `guardrail_violations` |
| **DISPATCH** | Vira task no android-orchestrator, com jitter e respeito a `active_hours` e teto horário | `tasks` (`dispatched_at`, `external_task_id`) |
| **VERIFY** | O orchestrator confirma por webhook. Só `verified` conta como feito | `tasks.status='verified'`, `posts` |
| **RECORD** | Registra o que aconteceu no diário e coleta métricas | `memories` (episódica), `post_metrics` |
| **REFLECT** | Sintetiza o dia: memória episódica vira semântica e narrativa; atualiza o estado do arco | `memories` (semântica/narrativa), `story_arcs.state` |

Detalhamento em [05 — Loop do agente](05-loop-do-agente.md).

---

## 5. Os três arquétipos e a espinha única

**A decisão mais importante do projeto:** influencer, fofoca e clipagem **não são sistemas diferentes**. São o mesmo loop com uma peça trocada — de onde vem o conteúdo.

```python
class ContentSource(Protocol):
    def propose(self, account: Account, plan: DayPlan) -> list[ContentDraft]: ...
```

| Arquétipo | `archetype` | Implementação | Conteúdo vem de | Serviços |
|---|---|---|---|---|
| **Influencer** | `influencer` | `GeneratedSource` | **Gerado** da história e da vivência do dia | máquina de influencers |
| **Fofoca / nicho** | `gossip` | `CuratedSource` | **Curado** de monitores e ingestão, com ranking de relevância e redação editorial | monitores + trend engine |
| **Clipagem** | `clipper` | `DerivedSource` | **Derivado** de conteúdo longo (live, podcast, YouTube) | ingestão + ASR + media-forge |

Tudo o mais é reaproveitado 100%: persona, memória, agenda, campanhas, QA, despacho, verificação, métricas, guardrails, painel.

### 5.1 Por que compartilham a espinha

Porque a diferença entre eles é de **origem do material**, não de comportamento. Uma página de fofoca tem voz ("irônica, usa meme, nunca xinga"), tem opinião, e lembra de quem já cobriu — isso é persona e memória, exatamente como a influencer. Uma conta de clipagem tem curadoria (que criadores ela clipa) e continuidade editorial. Os três precisam planejar o dia, produzir, passar por QA, despachar para um device, verificar e refletir.

O mesmo Persona Card serve para os três: **muda o preenchimento, não o schema**. Para a influencer, o card guarda vida fictícia e estética; para a fofoca, a linha editorial; para a clipagem, o critério de curadoria.

### 5.2 O que muda de fato

| Dimensão | Influencer | Fofoca | Clipagem |
|---|---|---|---|
| Cadência | 1–3/dia | 5–15/dia, reativo | 3–10/dia, em lote |
| Latência crítica | Baixa | **Altíssima** — furo perde valor em minutos | Média |
| "História" | Vida fictícia (arco pessoal) | Linha editorial | Curadoria de criadores |
| Risco dominante | Consistência visual | **Jurídico** — difamação, direito de imagem | **Copyright / fair use** |
| Campo do schema que acorda | — | `claim_status`, `sources`, `monitors` | `rights_status`, `attribution`, `source_refs` |
| Métrica-chave | Engajamento por seguidor | Velocidade + alcance | Retenção + reaproveitamento |
| Teto de autonomia | L3 possível | **L1 — nunca L3** | L2 |

Os campos de fofoca e clipagem **nascem na v1 mesmo sem uso**. Custo hoje: zero. Custo de adicioná-los depois, com 50 contas em produção: migração dolorosa. Ver `db/migrations/0001_initial.sql`, marcações `[DORMENTE]`.

---

## 6. Roadmap

| Fase | Nome | Duração | Entrega | Critério de saída |
|---|---|---|---|---|
| **0** | Fundação | 1–2 sem | Stack Docker, schema completo, LLM Gateway, clientes das 3 APIs em dry-run, `events` desde o dia 1 | Uma task `publish_post` atravessa as 8 etapas em dry-run, com rastro por `trace_id` |
| **1** | Uma influencer, humano no loop | 2–3 sem | Persona Card v1, memória episódica, loop completo com `GeneratedSource`, despacho real verificado, painel de aprovação L0. Spike Letta | 1 influencer postando 1×/dia, história evoluindo, um clique de aprovação |
| **2** | Engajamento + autonomia parcial | 3–4 sem | Interações (comentário/DM) classificadas e respondidas na voz da persona, memória semântica, promoção a L1/L2, métricas realimentando o PLAN | 1 semana em L2 sem incidente |
| **3** | Multi-influencer + campanhas | 4–6 sem | Motor narrativo completo, campanhas com recon e `spread_policy`, 3–5 contas em nichos distintos, crossover controlado, trend engine v1 | Panteão em L2/L3 rodando campanhas coordenadas sem intervenção diária |
| **4** | Páginas de fofoca / nicho | 3–4 sem | Monitores 24/7, ingestão com dedup, `CuratedSource`, pipeline de baixa latência, guardrails jurídicos e `claim_status` obrigatório | 1 página de fofoca em L1 com auditoria de cada afirmação publicada |
| **5** | Clipagem | 3–4 sem | `media-forge` (ASR → detecção de momento → corte → reframe → legenda), `DerivedSource`, controle de direitos, multi-tenant (`clients`) | Conta de clipagem processando 1 fonte longa/dia em lote |
| **6** | Escala e negócio | contínuo | Analytics por nicho/horário/formato, A/B conduzido pelos agentes, onboarding de conta em < 1 dia, monetização | — |

Cada fase tem documento de handoff em [`fases/`](fases/), com escopo, fora de escopo, riscos e o que fica dormente.

---

## 7. Princípios inegociáveis

Estes cinco não se negociam por prazo, por demanda de cliente nem por "só desta vez". Mudá-los exige ADR.

### 7.1 Dry-run por padrão

`DISPATCH_ENABLED=false` é o default do `.env.example`. Nada é enviado ao android-orchestrator: toda chamada de escrita é serializada em `media/dryrun/`. Ligar o despacho é uma decisão explícita, com confirmação dupla no painel, e o estado de dry-run é exibido em faixa permanente enquanto durar. **É assim que se valida o loop sem arriscar uma conta.**

### 7.2 Toda decisão é auditável

`events` existe desde o primeiro commit e recebe uma linha por decisão do sistema, com `trace_id` propagado por todo o loop. A pergunta *"por que a conta X postou isso às 19h?"* precisa ser respondível por query — e navegável no painel em três cliques, até o `day_plans.reasoning` e o `llm_calls` que o produziu. Nenhuma tela edita `events`. Auditoria é imutável.

### 7.3 Guardrails nunca desligam

Independem do nível de autonomia. Valem em L0 e valem em L3:

- brand safety em todo texto e toda imagem, sem exceção;
- kill switch por conta e global, sempre a um comando;
- rate limit com jitter — `ACCOUNT_MAX_ACTIONS_PER_HOUR` não é ultrapassado nem em campanha;
- temas proibidos por conta, definidos no Persona Card;
- interação classificada como **sensível** (saúde, dinheiro, romance, assédio) **nunca** é auto-respondida, em nenhum nível;
- `claim_status='unverified'` e `rights_status='unknown'` bloqueiam publicação.

Autonomia muda **quem revisa**, nunca **se as regras valem**.

### 7.4 Espalhamento obrigatório em campanhas

`campaigns.spread_policy` é `NOT NULL`. Não existe caminho no código ou no painel que crie campanha sem política de espalhamento, e o editor não permite jitter zero nem participação de 100%. Na prática: janela mínima entre contas, texto sempre único (gerado na voz de cada persona), participação parcial por amostragem, limite de interação entre contas da casa, e proibição de dobrar a cadência natural da conta.

Não é otimização — é a diferença entre uma operação e um farm detectável.

### 7.5 Nenhuma persona baseada em pessoa real

Rosto 100% sintético, seed e referência facial próprias, nunca derivadas de uma pessoa existente. O editor de persona recusa upload de referência facial externa. Sem clonagem de voz de pessoa real. Persona virtual assumida na bio conforme a política da plataforma. Para páginas de fofoca, o alvo da cobertura é pessoa pública e a afirmação carrega `claim_status` — o que é diferente de *encarnar* alguém.

---

## 8. Riscos principais

| Risco | Mitigação | Onde vive |
|---|---|---|
| **Detecção de farm** | `spread_policy` obrigatória, texto sempre único, grafo de interação limitado, cadência natural por conta | [06](06-campanhas.md) |
| Ban / perda de conta | Aquecimento gradual (`status='warming'`), tasks de comportamento humano, device e identidade estáveis | [08](08-guardrails-e-autonomia.md) |
| Inconsistência visual | Seed e referência facial fixas na máquina de influencers + QA por embedding de rosto contra o canônico | [05](05-loop-do-agente.md) |
| Agente fora da persona | Persona Card em todo prompt + QA textual + amostragem humana (`L2_SAMPLING_RATE`) | [04](04-persona-card-spec.md) |
| **Processo judicial (fofoca)** | `claim_status` obrigatório e refletido no texto, blacklist de alvos, revisão humana sempre, remoção rápida, nunca L3 | [08](08-guardrails-e-autonomia.md) |
| **DMCA (clipagem)** | `attribution` obrigatória, whitelist de criadores, transformação mínima, monitoramento de strikes | [08](08-guardrails-e-autonomia.md) |
| Mac minis viram gargalo | LiteLLM com prioridade de fila (interação > ideação) e overflow para API externa com teto | [09](09-llm-gateway.md) |
| Acoplamento com os serviços externos | Clientes atrás de interface única + dry-run; o core nunca chama plataforma | [03](03-integracoes.md) |

---

## 9. Estado atual

**Fase 0 — Fundação.** Ver [fases/fase-0-fundacao.md](fases/fase-0-fundacao.md).

Próximo passo de produto, antes de qualquer código de persona: definir a primeira persona (nicho, nome, estética) — ela é a cobaia de tudo até a Fase 3.
