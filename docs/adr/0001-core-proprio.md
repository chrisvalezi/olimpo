# ADR-0001: Core próprio e fino, com empréstimo de especificações

- **Status:** Aceito
- **Data:** 2026-08-05
- **Decisores:** equipe Olimpo

## Contexto

O Olimpo precisa de um runtime de agentes: persona, memória de longo prazo, planejamento diário, reflexão, ferramentas e uma fila de ações. Existe um ecossistema maduro que resolve boa parte disso — ElizaOS (~19k estrelas, MIT, TypeScript, character files, sistema de plugins), Letta/MemGPT (runtime de memória hierárquica com API própria), LangChain e seu `social-media-agent` de referência. Forkar qualquer um deles pareceria economia de meses.

O problema é que **todos esses frameworks assumem que o agente fala com a plataforma**. A abstração central do ElizaOS é o *client/connector*: `client-twitter`, `client-discord`, `client-telegram` — o runtime recebe eventos da plataforma e responde na plataforma, via API oficial ou biblioteca não-oficial. O `social-media-agent` do LangChain é literalmente um pipeline "gera post → chama a API do X/LinkedIn".

No Olimpo, quem fala com o Instagram é o **android-orchestrator**, que executa em container redroid — Android real, conta real logada, ação verificada por leitura de tela. Esse é o diferencial competitivo: não temos conta de API, temos comportamento de dispositivo. Adotar um framework cujo modelo mental é "o agente possui um conector de plataforma" significaria:

1. Herdar e desabilitar toda a camada de conectores (código morto que continuamos versionando).
2. Contorcer o modelo de execução: no framework a ação é síncrona e local; aqui ela é **assíncrona, remota e verificada por callback** — uma task que pode levar minutos, falhar no device, ou ser reagendada.
3. Perder a auditabilidade: precisamos responder "por que a conta X postou isso às 19h?" com uma linha em `events`. Loops de framework de terceiros não expõem esse rastro no formato que precisamos.

Do outro lado, o trabalho real que resta é **~80% orquestração e engenharia de prompt** sobre serviços que já existem (orchestrator, máquina de influencers, futuro media-forge). Isso é pouco código — algumas milhares de linhas de Python — mas é código que precisa ser 100% nosso porque é onde mora a regra de negócio.

## Decisão

**Construímos um core próprio e fino em Python/FastAPI; não forkamos nenhum framework de agentes — mas adotamos duas especificações de terceiros como blueprint, sem importar código.**

Detalhamento:

1. **Core próprio.** O loop `plan → source → produce → QA → dispatch → verify → record → reflect` é implementado direto sobre SQLAlchemy + Celery + Postgres. Sem runtime de agente intermediário.

2. **Formato de character file do ElizaOS como spec do Persona Card.** O campo `accounts.persona_card jsonb` segue o shape do character file (`name`, `bio`, `lore`, `adjectives`, `topics`, `style.{all,chat,post}`, `messageExamples`, `postExamples`), estendido com os campos que precisamos (`taboos`, `editorial_line`, `visual_identity`, `persona_engine_id`). Motivo: é um formato já testado por milhares de personas, força o autor a preencher as dimensões certas, e mantém uma porta aberta para importar/exportar personas do ecossistema. Copiamos o **schema**, não a implementação. Ver `docs/04-persona-card-spec.md`.

3. **Loop do paper generative_agents (Stanford) como blueprint cognitivo.** `memories` implementa o *memory stream* com os três eixos de recuperação do paper — relevância (similaridade de cosseno via pgvector), recência (decaimento sobre `occurred_at`/`last_used_at`) e importância (`importance real`, atribuída pela LLM na escrita). *Reflection* é o passo REFLECT que sintetiza memórias episódicas em semânticas e narrativas. *Planning* é `day_plans`. Novamente: lemos o paper e o repositório de referência, escrevemos nosso próprio código.

4. **Letta/MemGPT fica como spike, não como dependência.** Fase 1 reserva 1–2 dias para avaliar se o Letta como serviço de memória supera pgvector puro. Default é pgvector; adotar Letta exige que o spike prove ganho concreto.

## Alternativas consideradas

| Alternativa | Prós | Contras | Por que não |
|---|---|---|---|
| **Forkar ElizaOS** | Character files prontos, plugins, comunidade grande, MIT, memória e loop já implementados | TypeScript (nosso ecossistema de ML/vídeo é Python); arquitetura centrada em conectores de plataforma; runtime síncrono; fork = merge de upstream para sempre ou divergência total | O conector é a abstração central e é exatamente a parte que **não** usamos. Sobra o character file — que copiamos de graça como spec. |
| **Letta/MemGPT como runtime completo** | Memória hierárquica de alta qualidade, self-editing memory, resolve o problema mais difícil | Vira mais um serviço com estado próprio, fora do nosso Postgres; opinado sobre o formato de agente; dificulta joins entre memória e o resto (campanhas, métricas, eventos) | Ter memória fora do banco transacional quebra a auditoria unificada. Rebaixado a spike de camada de memória (ADR-0003). |
| **LangChain / `social-media-agent`** | Pipeline de referência, muitos integradores, abstrações de prompt | O pipeline de referência é "gera e posta via API"; camadas de abstração que mudam a cada release; overhead de debug em cima de código nosso simples | Resolve o problema errado. Usamos LiteLLM (ADR-0010) para o que realmente precisamos do ecossistema: roteamento de modelo. |
| **AutoGen / CrewAI (multi-agente)** | Bom em colaboração entre agentes | Nossos agentes quase não conversam entre si; coordenação acontece no manager via campanhas, não via chat multi-agente | Modelo de colaboração não bate com o nosso (campanha + spread policy, ADR-0007). |
| **Core próprio** (escolhida) | Modelo de dados desenhado para dispatch remoto verificado; auditoria total; sem dependência de roadmap alheio; time entende 100% do código | Escrevemos memória, planejamento e reflexão do zero; sem comunidade para resolver bugs; risco de reinventar mal | Aceito — o volume de código é pequeno e é justamente onde está o valor. |

## Consequências

### Positivas
- O modelo de execução (task assíncrona → orchestrator → webhook de verificação) é cidadão de primeira classe, não um plugin torcido.
- Tudo — memória, plano, conteúdo, task, métrica, evento — vive no mesmo Postgres. Uma query responde "por que postou isso".
- Zero pressão de upgrade de framework. Nenhum release de terceiro quebra o Olimpo.
- Persona Card compatível com character file mantém a porta aberta se um dia quisermos plugar uma persona em outro runtime.
- Stack única em Python, alinhada com ASR/vídeo/embeddings do media-forge.

### Negativas / custos aceitos
- Implementamos recuperação de memória, decaimento de importância e síntese de reflexão à mão. Estimativa: 1–2 semanas de trabalho que o ElizaOS daria de graça (em TypeScript).
- Sem ecossistema de plugins: cada integração nova é código nosso.
- Perdemos melhorias que a comunidade do ElizaOS/Letta descobre — teremos de lê-las e reimplementar quando valer a pena.
- O time carrega a manutenção do loop cognitivo para sempre.

### Riscos e como mitigamos
- **Risco: reinventar mal o loop de memória.** Mitigação: seguir o paper generative_agents literalmente na v1 (relevância × recência × importância, sem invenção), só otimizar depois de ter dado com uso real.
- **Risco: o "core fino" engorda e vira um framework ruim.** Mitigação: regra explícita — nada entra no core que não seja usado por pelo menos um arquétipo em produção. Abstração só nasce na segunda ocorrência concreta (ver `ContentSource`, ADR-0006).
- **Risco: subestimar o custo e atrasar a Fase 1.** Mitigação: Fase 0 entrega o loop em dry-run com memória burra (só recência); a recuperação ponderada entra na Fase 1 com o spike do Letta como plano B.

## Revisitar quando

- O ElizaOS (ou equivalente) publicar uma abstração de execução **assíncrona e externa** — algo como um "remote actuator" — que aceite "a plataforma é operada por um terceiro e a confirmação chega por webhook".
- O custo de manter memória + planejamento próprios passar de ~20% do tempo de engenharia por trimestre.
- O spike do Letta (Fase 1) mostrar ganho mensurável de qualidade narrativa que não conseguimos reproduzir em pgvector.
- Surgir necessidade real de interoperar com o ecossistema de personas de terceiros (ex.: vender persona como ativo portátil).
