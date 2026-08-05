# 10 — Painel

> **Status:** especificação · **Implementação:** mínima na Fase 0, aprovação na Fase 1, campanhas na Fase 3, monitores na Fase 4
> **Código:** `panel/` · **Container:** serviço `panel` do `docker-compose.yml` · **URL:** http://localhost:3000

O painel é a única interface humana do Olimpo. Tudo o que um operador precisa fazer — aprovar, pausar, matar, promover, revisar campanha, entender uma decisão — acontece aqui. Não existe "console de admin" paralelo: o que não está no painel só existe via `psql`, e isso é considerado uma lacuna, não um recurso.

---

## 1. Princípios de design

### 1.1 É uma sala de controle, não um CMS

Um CMS te pergunta "o que você quer criar hoje?". Uma sala de controle te informa **o que está acontecendo e o que exige você**. O Olimpo produz conteúdo sozinho; o humano não é autor, é supervisor. O painel reflete isso: a tela inicial não tem botão "novo post" em destaque — tem uma fila de aprovação e um feed de eventos.

### 1.2 A hierarquia de atenção

Toda tela responde, nesta ordem:

| Prioridade | Pergunta | Como aparece |
|---|---|---|
| **1** | O que precisa da minha atenção **agora**? | Topo da tela, alto contraste, contagem exata. Fila de aprovação, alertas, guardrails `block`/`kill`, contas em `suspended`, monitores em `error`. |
| **2** | O que o sistema está fazendo **neste momento**? | Tasks em voo, campanhas `running`, monitores checando, workers ocupados. Atualização ao vivo, sem refresh. |
| **3** | **Por que** ele decidiu isso? | Sempre a um clique. `day_plans.reasoning`, `content_items.reasoning`, `campaign_recon.analysis`, `campaign_participants.local_adaptation`, e a trilha em `events` por `trace_id`. |

Nada que não responda a uma dessas três perguntas merece espaço acima da dobra.

### 1.3 Densidade alta, zero enfeite

- Tabelas densas, linhas de 32–36 px, tipografia de 13 px no corpo de dados. Um operador precisa ver 25 contas sem rolar.
- Números sempre acompanhados da unidade e do recorte temporal ("18 tasks · últimas 24 h"), nunca soltos.
- Sem card gigante com ícone decorativo, sem ilustração, sem carrossel, sem animação que não comunique estado.
- Cor é **significado**, nunca decoração (ver §7). Se uma cor não codifica status, severidade ou autonomia, ela não entra.
- Espaço em branco existe para separar grupos semânticos, não para "respirar".

### 1.4 Toda decisão é auditável na interface

O rastro que o ADR-0001 exige do backend precisa ser navegável na UI. Regra prática: **de qualquer artefato publicado dá para chegar em três cliques ao prompt/plano que o originou.** Post → `content_item` → `day_plan` → `events` com o mesmo `trace_id` → `llm_calls`.

### 1.5 Estado destrutivo nunca é acidente

Ver §8. Kill switch global, promoção de autonomia e aprovação de campanha exigem confirmação com digitação. Todo o resto é reversível ou tem desfazer.

### 1.6 Rápido em volume

A fila de aprovação é o gargalo humano do sistema em L0/L1. Ela precisa ser operável com as mãos no teclado, sem mouse, a uma taxa de ~1 item a cada 4 segundos quando o conteúdo é rotineiro.

---

## 2. Stack

| Camada | Escolha | Por quê |
|---|---|---|
| Framework | **Next.js 15** (App Router, React Server Components) | Server Components buscam da API sem expor token ao browser; streaming de UI encaixa com listas grandes |
| Linguagem | **TypeScript** estrito (`strict: true`, sem `any`) | Os tipos vêm do OpenAPI da FastAPI — erro de contrato aparece em build |
| UI | **React 19** + **Tailwind CSS** + **shadcn/ui** (Radix) | Radix dá acessibilidade de teclado e foco de graça; shadcn é copiado para dentro do repo (sem dependência de versão) |
| Dados (cliente) | **TanStack Query** | Cache, revalidação, mutação otimista, retry |
| Formulários | **react-hook-form** + **zod** | O mesmo zod valida `persona_card` e `spread_policy` antes de mandar para a API |
| Tabelas | **TanStack Table** | Virtualização, ordenação e filtro em listas de milhares de linhas |
| Gráficos | **Recharts** | Sparkline de métrica e custo de LLM. Nada além disso |
| Tempo real | **EventSource (SSE)** contra `GET /v1/events/stream` | Ver §6 |
| Datas | **date-fns** + `date-fns-tz` | Conta tem `accounts.timezone`; o painel nunca assume o fuso do browser |
| Testes | **Vitest** + **Testing Library** + **Playwright** | E2E cobre os fluxos destrutivos |
| Runtime | Node 22, `output: 'standalone'` | Imagem final pequena |

**Contrato de tipos.** `panel/src/lib/api/types.gen.ts` é gerado de `http://api:8000/openapi.json` via `openapi-typescript`, por `make panel-types`. Não se escreve tipo de resposta de API à mão. Enums do banco (`archetype`, `account_status`, `autonomy_level`, `task_status`, `content_status`, `claim_status`, `rights_status`, `campaign_status`, `monitor_status`, `memory_kind`) chegam como *string unions* e são a fonte da verdade para os mapas de cor.

**Autenticação.** Fase 0–3: `API_TOKEN` único, mantido **só no servidor** (`process.env.API_TOKEN`). O browser fala com Route Handlers do Next (`/app/api/**`), que fazem proxy para a FastAPI injetando o header. `NEXT_PUBLIC_API_URL` é usado apenas para montar links de documentação. Multiusuário com papéis entra junto com a Fase 5 (multi-tenant, `clients`).

---

## 3. Organização de pastas

```
panel/
├── Dockerfile                      # multi-stage; dev usa `next dev`, prod `next start`
├── next.config.ts
├── tailwind.config.ts
├── components.json                 # config do shadcn/ui
└── src/
    ├── app/
    │   ├── layout.tsx              # shell: sidebar, topbar, provedores, tema escuro
    │   ├── page.tsx                # (1) Visão geral — sala de controle
    │   ├── contas/
    │   │   ├── page.tsx            # (2) lista + filtros
    │   │   └── [id]/
    │   │       ├── page.tsx        # ficha: visão
    │   │       ├── persona/page.tsx
    │   │       ├── memoria/page.tsx
    │   │       ├── agenda/page.tsx
    │   │       ├── posts/page.tsx
    │   │       └── tasks/page.tsx
    │   ├── aprovacoes/page.tsx     # (3) fila de aprovação
    │   ├── campanhas/
    │   │   ├── page.tsx            # (4) lista
    │   │   ├── nova/page.tsx       # wizard de criação
    │   │   └── [id]/
    │   │       ├── page.tsx        # timeline de execução
    │   │       ├── recon/page.tsx  # bruto × análise, lado a lado
    │   │       └── plano/page.tsx  # participantes, slots, adaptações
    │   ├── monitores/
    │   │   ├── page.tsx            # (5)
    │   │   └── [id]/page.tsx
    │   ├── conteudo/
    │   │   ├── page.tsx            # (6) biblioteca
    │   │   └── [id]/page.tsx
    │   ├── interacoes/page.tsx     # (7)
    │   ├── observabilidade/
    │   │   ├── page.tsx            # (8) tasks + filas
    │   │   ├── llm/page.tsx
    │   │   └── guardrails/page.tsx
    │   ├── configuracoes/page.tsx  # (9)
    │   └── api/                    # Route Handlers: proxy + SSE relay
    │       ├── [...proxy]/route.ts
    │       └── stream/route.ts
    ├── components/
    │   ├── ui/                     # shadcn puro, não editado à mão
    │   ├── domain/                 # os 10 componentes de domínio (§5)
    │   └── layout/                 # Sidebar, Topbar, KillSwitchBar, CommandPalette
    ├── lib/
    │   ├── api/                    # client, types.gen.ts, hooks por recurso
    │   ├── realtime/               # useEventStream, reconciliação de cache
    │   ├── format/                 # números, datas por accounts.timezone, durações
    │   ├── domain/                 # mapas enum → rótulo PT, cor, ícone e explicação
    │   └── keys.ts                 # atalhos de teclado, registro único
    └── styles/globals.css          # tokens do design system
```

**Regra de camadas.** `components/ui` não conhece o domínio. `components/domain` não faz fetch — recebe dados por props. Fetch vive em Server Components ou em hooks de `lib/api`. Isso mantém o Storybook e os testes triviais.

---

## 4. Mapa de telas

Convenção das tabelas abaixo: **endpoint** é o contrato da FastAPI; **tabelas** são as do `db/migrations/0001_initial.sql`.

---

### 4.1 Visão geral — a sala de controle

`/` — a tela que fica aberta o dia inteiro em um monitor.

**Propósito.** Em cinco segundos responder: a frota está bem? tem alguém esperando por mim? o que está rodando agora? quanto isso está custando?

**Layout.** Três faixas horizontais, sem rolagem para as duas primeiras em 1440×900.

```
┌─ FAIXA 1 — atenção ──────────────────────────────────────────────────┐
│  [!] 7 aguardando aprovação   [!] 2 guardrails block (1h)             │
│  [!] 1 conta suspended        [!] 1 monitor em error (12 falhas)      │
├─ FAIXA 2 — estado ───────────────────────────────────────────────────┤
│ Frota      Contas por status     Em voo          LLM hoje            │
│ 12/14 ok   ▮active 9 ▮warm 2     18 tasks        US$ 1,84 / 5,00     │
│ devices    ▮paused 2 ▮susp 1     3 campanhas     412 chamadas        │
├─ FAIXA 3 — o que está acontecendo ───────────────────────────────────┤
│  [ Timeline ao vivo de eventos ]     │  [ Próximas 2h: agenda ]      │
└──────────────────────────────────────────────────────────────────────┘
```

| Componente | Dados | Origem |
|---|---|---|
| `AlertStrip` | contagens acionáveis; cada uma é link para a tela filtrada | `GET /v1/overview` → `approvals` abertas (`idx_approvals_open`), `guardrail_violations` com `severity IN ('block','kill')` na última hora, `accounts.status='suspended'`, `monitors.status='error'` |
| `FleetHealthCard` | devices saudáveis / total, containers redroid caídos | `GET /v1/fleet/health` (proxy somente leitura do **android-manager**) |
| `AccountStatusBreakdown` | barra segmentada por `account_status`, clicável | `GET /v1/overview` → `accounts` agrupadas por `status` |
| `InFlightPanel` | tasks `dispatched`/`running` com idade; campanhas `running`; monitores devidos | `tasks` (`idx_tasks_inflight`), `campaigns.status='running'`, `monitors.next_check_at <= now()` |
| `LlmBudgetGauge` | gasto do dia vs. `LLM_DAILY_BUDGET_USD`, split por `backend` | `GET /v1/llm/usage?window=today` → `llm_calls.cost_usd` agrupado por `backend` |
| `LiveEventFeed` | últimos eventos, filtro por tipo e conta | SSE `GET /v1/events/stream` + seed de `GET /v1/events?limit=100` → `events` |
| `UpcomingSchedule` | tasks `pending`/`scheduled` nas próximas 2 h, por conta, no fuso da conta | `tasks.scheduled_for` (`idx_tasks_due`) + `accounts.timezone` |
| `KillSwitchBar` | barra fixa vermelha quando `GLOBAL_KILL_SWITCH=true` ou `DISPATCH_ENABLED=false` | `GET /v1/settings/runtime` |

**Ações.** Ir para a fila de aprovação · abrir conta · abrir campanha · pausar conta direto do breakdown · acionar kill switch global (§8) · filtrar o feed.

> **Banner de dry-run.** Quando `DISPATCH_ENABLED=false`, uma faixa âmbar permanente diz *"Dry-run: nada está sendo despachado ao orchestrator. Ações são gravadas em `media/dryrun/`."* Ela não é dispensável. Um operador jamais deve confundir simulação com produção.

---

### 4.2 Contas

#### Lista — `/contas`

**Propósito.** Encontrar uma conta e ler o estado da frota linha a linha.

| Filtro | Campo |
|---|---|
| Arquétipo | `accounts.archetype` (influencer · gossip · clipper) |
| Status | `accounts.status` (draft · warming · active · paused · suspended · lost) |
| Autonomia | `accounts.autonomy` (L0–L3) |
| Cliente | `accounts.client_id` → `clients.name` *(oculto até a Fase 5)* |
| Plataforma | `accounts.platform` |
| Busca | `handle` (índice `pg_trgm`) |

Colunas: handle · plataforma · arquétipo · `AutonomyBadge` · status · seguidores · última ação (`last_action_at`, relativa) · posts 7 d · aprovações pendentes · saúde do device · cadência real vs. `accounts.cadence`.

`GET /v1/accounts?archetype=&status=&autonomy=&client_id=&q=&page=`

Ações em lote (com seleção): pausar, retomar, mudar de nível de autonomia (uma confirmação por conta, nunca em massa — ver §8).

#### Ficha da conta — `/contas/[id]`

O documento vivo de um agente. Cabeçalho fixo com handle, `AutonomyBadge`, status, device, e os botões de controle. Abaixo, abas.

| Aba | Conteúdo | Endpoint / tabelas |
|---|---|---|
| **Visão** | `AccountCard` expandido, arco ativo, plano de hoje, últimas 5 tasks, alerta de guardrail recente | `GET /v1/accounts/{id}` → `accounts`, `story_arcs` (`active`), `day_plans` de hoje, `tasks`, `guardrail_violations` |
| **Persona** | `PersonaCard` — leitura formatada de `accounts.persona_card` por seção (identidade, voz, tabus, estética, linha editorial, `persona_engine_id`); editor com diff e histórico | `GET/PUT /v1/accounts/{id}/persona`, `GET /v1/accounts/{id}/persona/versions` → `accounts.persona_card`, `accounts.persona_version`, `persona_versions` |
| **Memória** | linha do tempo de `memories` filtrável por `kind` (episódica · semântica · narrativa), com `importance` como peso visual e busca semântica | `GET /v1/accounts/{id}/memories?kind=&q=` → `memories` (busca por `embedding`, ordenação por `occurred_at`) |
| **Arco** | arco atual (`title`, `summary`, `state`), régua de `starts_at`→`ends_at`, arcos passados | `GET /v1/accounts/{id}/arcs` → `story_arcs` |
| **Agenda** | plano do dia: `narrative`, `intents` como cartões, `reasoning` no `ReasoningPanel`, e as tasks derivadas na `TaskTimeline` com `active_hours` sombreado | `GET /v1/accounts/{id}/day-plan?date=` → `day_plans`, `tasks` |
| **Posts** | grade de publicações com métrica mais recente e sparkline de evolução | `GET /v1/accounts/{id}/posts` → `posts`, `post_metrics` (última coleta por `collected_at`) |
| **Tasks** | histórico completo, filtro por `type` e `status`, com `external_task_id` e `result` do orchestrator | `GET /v1/accounts/{id}/tasks` → `tasks` |
| **Interações** | comentários/DMs desta conta, sensíveis no topo | `interactions` |
| **Eventos** | auditoria bruta, agrupável por `trace_id` | `events` |

**Edição de persona com versionamento.** Salvar não sobrescreve: a API grava o card anterior em `persona_versions` (`version`, `changed_by`, `change_note`), incrementa `accounts.persona_version` e emite um evento. A UI exige `change_note` — "por que ela mudou de voz?" é uma pergunta que precisa ter resposta. O editor mostra diff lado a lado (JSON estruturado, não texto cru) e valida contra o schema zod derivado de `docs/04-persona-card-spec.md`.

**Ações da ficha:**

| Ação | Efeito | Confirmação |
|---|---|---|
| Pausar | `accounts.status='paused'`; dispatcher ignora | simples |
| Retomar | volta para `active` | simples |
| **Kill** | `status='suspended'`, cancela tasks `pending`/`scheduled`/`dispatched` | **dupla** (§8) |
| **Promover/rebaixar autonomia** | altera `accounts.autonomy` | **dupla** para promoção; simples para rebaixamento |
| Forçar `plan_day` | cria task `plan_day` imediata | simples |
| Editar cadência / `active_hours` | `accounts.cadence`, `accounts.active_hours` | simples |
| Editar persona | nova linha em `persona_versions` | simples, com `change_note` obrigatório |

---

### 4.3 Fila de aprovação

`/aprovacoes` — **o coração da operação em L0 e L1.** Se esta tela for lenta ou confusa, o sistema inteiro fica lento.

**Propósito.** Decidir sobre conteúdo, interações e campanhas pendentes com contexto suficiente e mínimo atrito.

**Layout.** Duas colunas: lista compacta à esquerda (fila, com contagem e filtros), item ativo à direita ocupando o resto. Zero navegação — decidir avança para o próximo automaticamente.

**Anatomia do `ApprovalCard`** (o item ativo):

```
┌──────────────────────────────────────────────────────────────────┐
│ @luna.corre · L0 · influencer          reels · agendado 19:40    │
├───────────────────────────┬──────────────────────────────────────┤
│                           │  CAPTION                             │
│      MediaPreview         │  "corri 12k hoje e ainda sobrou..."  │
│   (vídeo/imagem, 9:16)    │  #maratona #corrida  · áudio: trend  │
│   [◀ 1/3 ▶]               │                                      │
│                           │  ┌─ POR QUE ─────────────────────┐   │
│                           │  │ ReasoningPanel                │   │
│                           │  │ plano do dia + memórias usadas│   │
│                           │  │ + trecho do arco + trace_id   │   │
│                           │  └───────────────────────────────┘   │
│                           │  ┌─ QA ──────────────────────────┐   │
│                           │  │ visual ✓ · texto ✓ · marca ✓  │   │
│                           │  │ legal ⚠ menção a suplemento   │   │
│                           │  │ rosto vs. canônico: 0.94      │   │
│                           │  └───────────────────────────────┘   │
├───────────────────────────┴──────────────────────────────────────┤
│  [A] Aprovar   [E] Editar e aprovar   [R] Rejeitar   [S] Pular   │
└──────────────────────────────────────────────────────────────────┘
```

| Bloco | Dados | Origem |
|---|---|---|
| Cabeçalho | conta, autonomia, arquétipo, tipo e horário-alvo | `accounts`, `content_items.kind`, `content_items.scheduled_for` |
| `MediaPreview` | mídia com aspecto correto por `kind`; vídeo com scrub e mudo por padrão | `content_items.media_refs` |
| Caption | `caption`, `hashtags`, `audio_ref`; badges de `claim_status` e `rights_status` quando ≠ default | `content_items` |
| `ReasoningPanel` | o "por quê": `content_items.reasoning`, `day_plans.narrative` + `.reasoning`, memórias recuperadas, arco, e a campanha de origem se houver | `content_items.reasoning`, `day_plans`, `memories`, `story_arcs`, `campaigns` |
| `QAReport` | veredito por dimensão com detalhe expansível | `content_items.qa_report` + `guardrail_violations` do mesmo `subject_id` |
| Proveniência | `source_strategy` (generated/curated/derived), `source_refs`, `attribution` | `content_items` — visível só quando preenchido |

**Endpoints.**

```
GET  /v1/approvals?status=open&subject_type=&account_id=&archetype=&sort=oldest
GET  /v1/approvals/{id}
POST /v1/approvals/{id}/decide      {decision: approved|rejected|edited, note, edited_payload}
POST /v1/approvals/batch-decide     {ids: [...], decision, note}
```

Tabelas: `approvals` (`subject_type`, `subject_id`, `decision`, `decided_by`, `note`, `edited_payload`, `sampled`), `content_items`, `interactions`, `campaigns`.

**Atalhos de teclado** (registro único em `lib/keys.ts`, com folha de ajuda em `?`):

| Tecla | Ação |
|---|---|
| `J` / `K` ou `↓` / `↑` | próximo / anterior |
| `A` | aprovar e avançar |
| `E` | abrir edição inline da caption |
| `R` | rejeitar — abre seletor de motivo (obrigatório) |
| `S` | pular (volta ao fim da fila) |
| `Espaço` | play/pause do vídeo |
| `Z` | desfazer a última decisão (janela de 10 s, ver abaixo) |
| `F` | expandir mídia em tela cheia |
| `X` | marcar/desmarcar para lote |
| `?` | ajuda de atalhos |

**Rejeição exige motivo.** Menu fechado — `fora da persona`, `qualidade visual`, `caption ruim`, `risco legal`, `repetitivo`, `horário errado`, `outro` (com texto). O motivo vai para `approvals.note` e realimenta o QA. Rejeitar sem motivo não é possível.

**Editar e aprovar.** Edita `caption`, `hashtags`, `scheduled_for` e a seleção de mídia. Salva em `approvals.edited_payload` **e** aplica em `content_items` — assim se preserva o que a máquina propôs versus o que o humano publicou. Essa diferença é a métrica de qualidade do agente (§ "taxa de edição" em `docs/11-observabilidade.md`).

**Desfazer.** Toda decisão fica 10 segundos em janela de desfazer antes de virar despacho (a task é criada com `scheduled_for = now() + 10s`). Um toast com contador e `Z` para reverter. Isso é o que permite operar rápido sem medo.

**Revisão em lote — quando é seguro.** O botão de lote só habilita quando **todos** os itens selecionados satisfazem:

- mesma conta ou mesma campanha;
- `qa_report` sem nenhuma dimensão `warn`/`fail`;
- nenhum `guardrail_violations` associado;
- `claim_status IN ('fact','opinion')` e `rights_status IN ('own','licensed')`;
- `archetype <> 'gossip'` — fofoca **nunca** é aprovada em lote (risco jurídico, ver `docs/08`);
- no máximo 20 itens.

Fora disso o botão fica desabilitado com o motivo escrito ao lado. O lote registra `approvals.note = 'batch'` e emite um evento por item, não um evento por lote.

**Modo amostragem (L2).** Itens com `approvals.sampled = true` aparecem em uma aba separada, marcados como *"não bloqueiam publicação — você está auditando"*. Reprovar ali gera um alerta e conta para o rebaixamento automático de autonomia previsto em `docs/08`.

---

### 4.4 Campanhas

`/campanhas` — lista com status, janela, participantes e progresso. O trabalho de verdade está nas subtelas.

#### Criação — `/campanhas/nova`

Wizard de quatro passos, cada um validado antes de avançar.

| Passo | Campos | Coluna |
|---|---|---|
| **1. Brief** | nome, tipo (`content` · `engagement` · `mixed`), brief em texto livre, cliente | `campaigns.name`, `.kind`, `.brief`, `.client_id` |
| **2. Alvo** | URL/handle do alvo (engajamento), assets de marca, áudio | `.target_url`, `.target_handle`, `.brand_refs`, `.audio_ref` |
| **3. Contas** | construtor de filtro: arquétipo, nicho, seguidores mínimos, autonomia mínima, exclusões explícitas. Mostra **em tempo real** quantas contas casam e quais | `.account_filter` (jsonb) → prévia via `POST /v1/campaigns/preview-audience` |
| **4. Janela e espalhamento** | início/fim + `SpreadPolicyEditor` | `.window_start`, `.window_end`, `.spread_policy` (**NOT NULL**) |

**`spread_policy` não tem caminho de escape.** O formulário nasce preenchido com os defaults do `.env` (`CAMPAIGN_JITTER_MIN_MINUTES=25`, `CAMPAIGN_JITTER_MAX_MINUTES=240`, `CAMPAIGN_MAX_PARTICIPATION=0.6`) e o editor **não permite** zerar jitter nem participação de 100%. Ao lado, uma prévia visual: a janela desenhada como faixa, com os slots sorteados marcados — o operador *vê* que não são todas às 19h. Se a política gerar aglomeração (dois slots a menos que o jitter mínimo), o editor acusa antes de salvar.

#### Recon — `/campanhas/[id]/recon`

**O painel do reconhecimento.** Uma campanha de engajamento não é planejada às cegas: uma conta Android abre o conteúdo (task `extract_content`) e uma LLM multimodal interpreta. A tela mostra os dois lados **lado a lado**, porque a discordância entre eles é o sinal mais valioso.

```
┌── BRUTO (o que o device viu) ────┬── ANÁLISE (o que a LLM entendeu) ──┐
│ screenshots / vídeo baixado      │ resumo: ...                        │
│ caption original                 │ tema · tom · público               │
│ autor · seguidores · contadores  │ sentimento: ...                    │
│ top comentários                  │ veredito de segurança: safe        │
│ coletado por @conta · 14:03      │ ângulos sugeridos:                 │
│ task extract_content #a13f       │  1. nostalgia ...                  │
│                                  │  2. humor autodepreciativo ...     │
│                                  │ analisado 14:05 · llm_call #7c2    │
└──────────────────────────────────┴────────────────────────────────────┘
```

| Painel | Campos | Tabela |
|---|---|---|
| Bruto | `raw_capture`, `media_refs`, `top_comments`, `collector_account_id`, `captured_at`, `task_id` | `campaign_recon` |
| Análise | `summary`, `analysis`, `sentiment`, `safety_verdict`, `safety_reasons`, `suggested_angles`, `analyzed_at`, `llm_call_id` | `campaign_recon` |

`safety_verdict = 'blocked'` pinta o painel inteiro de vermelho e **desabilita** o botão de planejar; `'risky'` exige que o operador marque um reconhecimento explícito antes de seguir. `GET /v1/campaigns/{id}/recon`, `POST /v1/campaigns/{id}/recon` (refazer com outra conta coletora).

#### Plano — `/campanhas/[id]/plano`

O plano gerado, item por item, antes de qualquer coisa acontecer.

Tabela de participantes: conta · `AutonomyBadge` · **o que ela vai fazer** (ações de `campaign_participants.actions`, com o texto do comentário visível e editável) · **como ela traduziu o brief** (`local_adaptation`) · **quando** (`slot_at`, no fuso da conta) · incluída/pulada com `skip_reason`.

Acima da tabela, a régua de espalhamento: a janela `window_start`→`window_end` com todos os `slot_at` plotados, cor por conta, e destaque em vermelho para qualquer par abaixo do jitter mínimo.

| Ação | Efeito | Confirmação |
|---|---|---|
| Editar comentário de uma conta | `campaign_participants.actions` | simples |
| Remover conta do plano | `skipped=true`, `skip_reason='manual'` | simples |
| Re-sortear slots | recalcula `slot_at` respeitando a `spread_policy` | simples |
| Regenerar adaptação de uma conta | nova chamada de LLM | simples |
| **Aprovar campanha** | `campaigns.status='approved'` → cria as tasks | **dupla** (§8) |
| **Abortar** | `status='aborted'`, cancela tasks não executadas | **dupla** |

`GET /v1/campaigns/{id}/participants`, `PATCH /v1/campaigns/{id}/participants/{pid}`, `POST /v1/campaigns/{id}/plan`, `POST /v1/campaigns/{id}/approve`, `POST /v1/campaigns/{id}/abort`.

#### Execução — `/campanhas/[id]`

Timeline ao vivo da campanha: cada participante como uma raia, cada ação como um marcador que muda de cor conforme `tasks.status` caminha `scheduled → dispatched → running → verified`. Barra de progresso por ações verificadas, não por tasks criadas — só `verified` conta como feito. Painel lateral com falhas e o botão de abortar sempre visível.

Dados: `campaigns.status`, `campaign_participants`, `tasks` (por `campaign_id`), `events` filtrados por `campaign_id`, via SSE.

---

### 4.5 Monitores 24/7 *(Fase 4)*

`/monitores`

**Propósito.** Ver o que os vigias estão observando, se estão saudáveis e o que virou conteúdo. Furo perde valor em minutos — latência aqui é a métrica.

| Coluna | Campo |
|---|---|
| Nome / conta dona | `monitors.name`, `owner_account_id` |
| O que observa | `sources.kind` + `sources.endpoint` (`@perfil`, `#tag`, RSS, canal) |
| Intervalo | `interval_seconds` |
| Último check / próximo | `last_check_at`, `next_check_at` (contador regressivo ao vivo) |
| Saúde | `status` + `consecutive_errors` — badge vermelho a partir de 3, `error` automático conforme `docs/07` |
| Hits 24 h | `monitor_hits` (total) e quantos com `triggered=true` |
| Coletores | `collector_pool` — quais contas Android rodam a checagem (rotação para não queimar uma só) |

**Detalhe — `/monitores/[id]`.** Regras de disparo (`trigger_rules`) em editor JSON validado; histórico de hits com `trigger_reason`, o `ingested_items.payload` que gerou, e link para o `content_items` produzido (`monitor_hits.content_item_id`); gráfico de latência detecção → publicação; erros consecutivos com a última mensagem.

**Ações.** Pausar/retomar (`monitors.status`) · checar agora (`POST /v1/monitors/{id}/check-now`, cria task `monitor_check`) · editar intervalo e regras · trocar o pool de coletores · desabilitar.

`GET /v1/monitors`, `GET /v1/monitors/{id}`, `GET /v1/monitors/{id}/hits`, `PATCH /v1/monitors/{id}`.

---

### 4.6 Conteúdo

`/conteudo` — a biblioteca de tudo que já foi produzido, publicado ou não.

**Propósito.** Buscar, auditar e reaproveitar. É a tela para responder "o que a conta X publicou sobre Y?" e "quantos drafts morreram em `qa_failed` esta semana?".

Grade densa de miniaturas (`MediaPreview` em modo compacto) com a caption truncada e uma faixa de cor por `content_status`. Alternável para modo tabela.

| Filtro | Campo |
|---|---|
| Status | `content_items.status` — draft · qa_pending · qa_failed · approval_pending · approved · rejected · scheduled · published · archived |
| Conta | `account_id` |
| Campanha | `campaign_id` |
| Tipo | `kind` (publish_post · publish_story · publish_reels) |
| Estratégia de origem | `source_strategy` (generated · curated · derived) |
| Direitos / afirmação | `rights_status`, `claim_status` |
| Período | `created_at`, `scheduled_for` |
| Texto | busca em `caption` (`pg_trgm`) |

**Detalhe — `/conteudo/[id]`.** Mídia, caption, QA, raciocínio, proveniência (`source_refs`, `attribution`), a task que o publicou, o post resultante e a série de `post_metrics`. Rodapé com a trilha de `events` do mesmo `trace_id`.

`GET /v1/content?...`, `GET /v1/content/{id}`, `PATCH /v1/content/{id}` (arquivar, reagendar).

---

### 4.7 Interações

`/interacoes` — comentários, DMs e menções.

**Propósito.** Não perder nada sensível e responder no tom certo.

Três abas: **Sensíveis** (padrão), **Aguardando resposta**, **Todas**.

| Coluna | Campo |
|---|---|
| Conta | `interactions.account_id` |
| Tipo | `kind` (comment · dm · mention) |
| Autor | `author_handle` |
| Texto | `body` |
| Classificação | `classification` — fan · hater · spam · opportunity · sensitive |
| Sensível | `sensitive` — badge vermelho persistente |
| Resposta | `reply_body`, `replied_by` (agent · human · none), `replied_at` |
| Post | `post_id` → link |

**A regra que a tela materializa:** `sensitive = true` **nunca** é respondido automaticamente, em nenhum nível de autonomia. Esses itens aparecem no topo com um aviso explícito, o campo de resposta vem **vazio** (sem sugestão da LLM) e o envio exige leitura do post de origem. Saúde, dinheiro, romance e assédio caem aqui — ver `docs/08`.

**Ações.** Responder manualmente · aprovar a resposta sugerida pelo agente (quando não sensível) · editar e enviar · ignorar · marcar como spam · escalar para revisão.

`GET /v1/interactions?sensitive=&replied=&account_id=&classification=`, `POST /v1/interactions/{id}/reply`, `POST /v1/interactions/{id}/ignore`.

---

### 4.8 Observabilidade

`/observabilidade` — três subtelas. Complementa o Grafana; não o substitui. Aqui fica o que é **por conta/task/decisão**; séries temporais de infra ficam no Grafana (`make up-obs`).

#### Tasks e filas — `/observabilidade`

Tabela global de `tasks` com filtro por `status`, `type`, `origin` (manager · self · human · campaign · monitor), conta e janela. Colunas: idade, `attempts`/`max_attempts`, `last_error`, `external_task_id`, `external_status`, `dispatched_at`, `verified_at`.

Painéis de fila: profundidade das quatro filas do Celery (`realtime`, `dispatch`, `production`, `maintenance`), workers ativos, taxa de verificação, tasks presas (`dispatched` há mais de N minutos sem webhook) e `expired`.

**Ações.** Retentar (`POST /v1/tasks/{id}/retry`) · cancelar · reagendar · abrir a trilha de `events` da task.

#### LLM — `/observabilidade/llm`

Custo e latência por `backend` (`mac_mini_1`, `mac_mini_2`, `anthropic`, `openai`) e por `purpose` (`plan_day`, `caption`, `recon_analysis`, `reflect`…). Gasto do dia contra `LLM_DAILY_BUDGET_USD` com projeção. Tabela de chamadas com `model`, tokens, `latency_ms`, `cost_usd`, `success`, `error`, `trace_id`. Taxa de fallback para API externa — se subir, os Mac minis viraram gargalo.

Tabela: `llm_calls`. `GET /v1/llm/calls`, `GET /v1/llm/usage?group_by=backend|purpose|account`.

#### Guardrails — `/observabilidade/guardrails`

Violações por `rule`, `severity` (info · warn · block · kill) e `action_taken` (blocked · flagged · suspended). Recorte por conta e por arquétipo — a taxa de violação por conta é o principal insumo para promover ou rebaixar autonomia.

Tabela: `guardrail_violations`. `GET /v1/guardrails/violations`.

---

### 4.9 Configurações

`/configuracoes` — políticas globais. Tela deliberadamente feia e cheia de avisos.

| Grupo | Controles | Origem |
|---|---|---|
| **Interruptores mestres** | `GLOBAL_KILL_SWITCH`, `DISPATCH_ENABLED` | `.env` / runtime; **confirmação dupla** |
| **Tetos de gasto** | `LLM_DAILY_BUDGET_USD`, comportamento ao estourar (cortar vs. só alertar) | `llm_calls` acumulado |
| **Anti-detecção** | `CAMPAIGN_JITTER_MIN_MINUTES`, `CAMPAIGN_JITTER_MAX_MINUTES`, `CAMPAIGN_MAX_PARTICIPATION`, `ACCOUNT_MAX_ACTIONS_PER_HOUR` | defaults de `spread_policy` e do dispatcher |
| **Amostragem** | `L2_SAMPLING_RATE` | fração de `approvals.sampled` em L2 |
| **Guardrails globais** | temas proibidos, blacklist de alvos, exigência de `claim_status` por arquétipo | `docs/08` |
| **Integrações** | URLs e saúde de orchestrator, máquina de influencers, android-manager, media-forge — só leitura, com teste de conectividade | `.env`, `GET /v1/health/integrations` |

Toda mudança aqui emite um evento em `events` com o autor. Não existe alteração silenciosa de política.

---

## 5. Componentes reutilizáveis

Todos em `panel/src/components/domain/`. Nenhum faz fetch.

| Componente | Propósito | Props principais |
|---|---|---|
| **`AccountCard`** | Identidade e estado de uma conta em três densidades (`compact`, `row`, `full`): avatar, handle, plataforma, arquétipo, autonomia, status, seguidores, última ação, saúde do device | `account`, `density`, `onAction` |
| **`AutonomyBadge`** | L0–L3 com cor, rótulo curto e tooltip explicando o que o nível permite. Aparece em toda tela que mostra conta — é o dado que muda o significado de todo o resto | `level`, `size`, `showLabel` |
| **`TaskTimeline`** | Sequência de tasks de uma conta/campanha em eixo temporal, com estado por cor, `active_hours` sombreado, marcador de "agora" e agrupamento por `trace_id` | `tasks`, `timezone`, `range`, `activeHours` |
| **`ReasoningPanel`** | O "por quê" de qualquer artefato: plano, memórias recuperadas, arco, campanha de origem, prompt e `llm_call`. Colapsado por padrão, expansível em camadas (resumo → detalhe → prompt cru) | `subject`, `reasoning`, `dayPlan`, `memories`, `traceId` |
| **`MediaPreview`** | Imagem/vídeo com aspecto correto por `content_items.kind` (1:1, 4:5, 9:16), carrossel, scrub de vídeo, mudo por padrão, tela cheia com `F`, fallback quando a ref não resolve | `mediaRefs`, `kind`, `mode` |
| **`QAReport`** | `content_items.qa_report` legível: uma linha por dimensão (visual, textual, marca, legal, consistência de rosto) com veredito, score e detalhe expansível; agrega `guardrail_violations` do mesmo sujeito | `report`, `violations`, `compact` |
| **`SpreadPolicyEditor`** | Edita `campaigns.spread_policy` com prévia visual dos slots na janela, validação de jitter mínimo, participação máxima e respeito à cadência da conta. Impede configuração detectável por construção | `value`, `window`, `eligibleAccounts`, `onChange` |
| **`ReconViewer`** | Bruto × análise lado a lado (§4.4), com veredito de segurança em destaque e ângulos sugeridos selecionáveis | `recon`, `onUseAngle` |
| **`LiveEventFeed`** | Stream de `events` com filtro por tipo/conta/campanha, agrupamento por `trace_id`, pausa automática ao rolar para cima, ícone e cor por tipo de evento | `filters`, `seed`, `maxItems` |
| **`ApprovalCard`** | O item da fila de aprovação: mídia + caption + `ReasoningPanel` + `QAReport` + ações. Suporta `content_item`, `interaction` e `campaign` como sujeito | `approval`, `subject`, `onDecide`, `keyboard` |

Auxiliares menores, mesma pasta: `StatusDot` (mapa `account_status`/`task_status`/`content_status` → cor), `ClaimStatusBadge`, `RightsStatusBadge`, `MetricSparkline`, `RelativeTime` (renderiza no fuso da conta), `EmptyState`, `ConfirmDialog` (§8), `CommandPalette` (`⌘K`: ir para conta, campanha, task ou `trace_id`).

---

## 6. Tempo real

### 6.1 Transporte

**SSE é o padrão.** O tráfego é unidirecional (servidor → painel) e SSE atravessa proxy, reconecta sozinho e não exige um segundo protocolo na stack. WebSocket só entraria se surgisse interação bidirecional de baixa latência, que não existe no escopo.

```
GET /v1/events/stream?types=&account_id=&campaign_id=      (text/event-stream)
```

A FastAPI publica em Redis pub/sub o que grava em `events`; o endpoint de stream assina o canal e reemite filtrado. O painel consome via um Route Handler (`/app/api/stream/route.ts`) que injeta o `API_TOKEN` — o browser nunca vê o token.

Cada mensagem: `{id, type, account_id, campaign_id, task_id, detail, trace_id, created_at}`. O `id` é `events.id` (bigserial), o que dá `Last-Event-ID` de graça: na reconexão o cliente manda o último id visto e o servidor repõe o intervalo perdido a partir de `events`. Sem buracos.

### 6.2 Reconciliação de cache

O evento não carrega o objeto inteiro — ele **invalida**. `useEventStream` mapeia tipo de evento → chaves do TanStack Query a invalidar (`task.verified` → `['tasks', account_id]` + `['overview']`). Isso mantém uma fonte da verdade só: a API. Exceção única é o `LiveEventFeed`, que renderiza o evento diretamente porque o evento *é* o dado.

### 6.3 Fallback

`EventSource` com backoff exponencial (1 s → 30 s). Após 3 falhas, o painel cai para polling e mostra um indicador discreto *"tempo real indisponível — atualizando a cada 15 s"*. O polling nunca é silencioso: um operador precisa saber que o que ele vê pode estar atrasado.

### 6.4 Quem precisa de tempo real de verdade

| Tela | Necessidade | Mecanismo |
|---|---|---|
| Visão geral | **Alta** — é a razão da tela existir | SSE |
| Campanha em execução | **Alta** — decisão de abortar depende de ver ao vivo | SSE |
| Fila de aprovação | **Média** — só a contagem e itens novos; o item aberto **nunca** é alterado sob o cursor | SSE apenas para o badge de contagem |
| Monitores | **Média** — contador de próximo check + hits novos | SSE + timer local |
| Tasks / filas | **Média** | SSE com throttle de 2 s |
| Ficha da conta | **Baixa** | polling de 30 s enquanto a aba está visível |
| Conteúdo, interações | **Baixa** | revalidação ao focar a janela |
| Configurações, persona | **Nenhuma** | busca no carregamento |

**Regra dura:** conteúdo sob o cursor não se mexe. Chegou item novo na fila enquanto você decide? Aparece como *"3 novos — pressione R para recarregar"*, nunca como re-render que desloca o botão que você ia clicar.

---

## 7. Design system

### 7.1 Tema escuro por padrão

Sala de controle fica aberta o dia inteiro, frequentemente em ambiente com pouca luz e ao lado do painel do android-manager. Escuro é o default; claro existe e é completo (nenhuma tela pode ser exclusiva de um tema). Preferência persistida em cookie, aplicada no servidor para não haver flash.

Fundo não é preto puro: `#0B0D10`. Preto puro sobre OLED cria halo em texto branco e cansa em leitura longa.

### 7.2 Cor com significado

**Status de conta** (`account_status`):

| Status | Cor | Token | Leitura |
|---|---|---|---|
| `draft` | cinza | `--st-draft` `#6B7280` | existe, não opera |
| `warming` | âmbar | `--st-warming` `#D97706` | aquecendo, só consome |
| `active` | verde | `--st-active` `#16A34A` | operando |
| `paused` | azul-acinzentado | `--st-paused` `#64748B` | pausa humana, reversível |
| `suspended` | vermelho | `--st-suspended` `#DC2626` | kill switch — exige ação |
| `lost` | vinho apagado | `--st-lost` `#7F1D1D` | banida, arquivo |

**Severidade de alerta** (`guardrail_violations.severity`):

| Severidade | Cor | Uso |
|---|---|---|
| `info` | `#38BDF8` | registro, não interrompe |
| `warn` | `#F59E0B` | revisar quando puder |
| `block` | `#EF4444` | publicação impedida — aparece na faixa de atenção |
| `kill` | `#B91C1C` + fundo sólido | conta suspensa automaticamente — topo absoluto |

**Nível de autonomia** (`autonomy_level`) — escala fria→quente, deliberadamente distinta das cores de status para não competir:

| Nível | Cor | Rótulo |
|---|---|---|
| `L0` | `#94A3B8` | Estagiário — só drafts |
| `L1` | `#38BDF8` | Júnior — posta, interação revisada |
| `L2` | `#818CF8` | Pleno — posta e responde, amostragem |
| `L3` | `#C084FC` | Sênior — full auto |

**Estado de task** (`task_status`): `pending` cinza · `scheduled` azul · `dispatched` índigo · `running` índigo pulsante · `verified` verde · `failed` vermelho · `cancelled` cinza riscado · `expired` âmbar riscado.

**Reservadas.** Vermelho é exclusivo de erro, bloqueio e destruição — nunca "marca". Verde é exclusivo de verificado/saudável — nunca "ok, prossiga". Âmbar é exclusivo de dry-run e atenção não bloqueante.

### 7.3 Tipografia e densidade

| Uso | Fonte | Tamanho / peso |
|---|---|---|
| Interface e dados | Inter (variável) | 13 px / 400 · rótulos 11 px / 500 · maiúsculas com `tracking-wide` |
| Números, ids, tokens | JetBrains Mono | 12 px, `tabular-nums` — coluna de número sempre alinha |
| Títulos de tela | Inter | 18 px / 600 — sem hierarquia de seis níveis |
| Texto longo (caption, brief, resumo) | Inter | 14 px / 400, `max-width: 68ch` |

Escala de espaçamento de 4 px. Linha de tabela 32 px (compacta) / 40 px (confortável), alternável e persistida. Densidade é preferência do operador, não decisão do designer.

### 7.4 Acessibilidade

- **Contraste** mínimo 4.5:1 para texto e 3:1 para elementos gráficos, verificado nos dois temas. As cores de status acima já foram escolhidas contra `#0B0D10` e `#FFFFFF`.
- **Cor nunca sozinha.** Todo estado carrega forma além da cor: `StatusDot` usa formato distinto (círculo cheio, anel, quadrado, losango) e todo badge tem texto. Isso resolve daltonismo e impressão em preto e branco.
- **Foco visível sempre.** Anel de 2 px com offset, alto contraste. `:focus-visible` nunca é removido — regra de lint no Tailwind.
- **Navegação por teclado completa.** Toda ação alcançável sem mouse. Ordem de tabulação segue a ordem visual. `Esc` fecha qualquer sobreposição. Armadilha de foco correta em diálogo (Radix).
- **Leitores de tela.** `aria-live="polite"` nas contagens da faixa de atenção; `aria-live="assertive"` só para `kill`. Tabelas com `<caption>` e cabeçalhos associados.
- **Movimento.** `prefers-reduced-motion` desliga a pulsação de `running` e as transições do feed.
- **Skip link** para o conteúdo principal, primeira parada de tabulação.

---

## 8. Ações destrutivas e confirmação

Três níveis.

| Nível | Mecanismo | Aplicação |
|---|---|---|
| **Simples** | Clique direto + toast com desfazer (10 s) | Aprovar conteúdo, pausar conta, editar caption, pular item, reagendar task, editar cadência |
| **Confirmação** | Diálogo descrevendo o efeito concreto e o alcance ("isto cancela 4 tasks agendadas") | Rejeitar conteúdo, cancelar task, remover conta de campanha, desabilitar monitor, rebaixar autonomia |
| **Dupla** | Diálogo + **digitar a palavra exigida** + botão desabilitado por 3 s | Ver abaixo |

**Exigem confirmação dupla:**

| Ação | Palavra a digitar | O diálogo mostra |
|---|---|---|
| **Kill switch global** | `PARAR TUDO` | número de contas ativas, tasks que serão canceladas, campanhas interrompidas |
| **Kill de conta** | o `handle` da conta | tasks canceladas, conteúdo agendado que não sairá |
| **Promover autonomia** | o nível de destino (`L2`) | o que passa a acontecer sem humano, taxa de rejeição da conta nos últimos 30 dias, violações de guardrail no período |
| **Aprovar campanha** | `APROVAR` | nº de contas, nº de ações, janela, resumo da `spread_policy`, veredito do recon |
| **Ligar `DISPATCH_ENABLED`** | `PRODUCAO` | aviso de que ações passam a acontecer em contas reais |
| **Abortar campanha em execução** | `ABORTAR` | ações já verificadas (irreversíveis) vs. canceláveis |

Regras que valem para as três:

- O diálogo **descreve consequência**, não pede confirmação genérica. Nunca "Tem certeza?".
- Ação destrutiva nunca é o botão primário de um formulário nem fica adjacente a uma ação frequente.
- Promoção de autonomia é **sempre uma conta por vez**, mesmo com seleção múltipla na lista.
- Toda ação destrutiva grava `events` com autor, alvo e motivo. Kill switch e mudança de autonomia também geram `guardrail_violations`-adjacente na trilha de auditoria.
- Rebaixar autonomia e pausar **não** exigem dupla confirmação: o caminho seguro tem que ser o mais barato.

---

## 9. O que NÃO fazer no painel — anti-requisitos

| Anti-requisito | Por quê |
|---|---|
| **Não é um editor de posts.** Não existe "criar post do zero" | Quem cria conteúdo é o agente. Se o humano está escrevendo caption, o loop falhou — corrija o Persona Card ou o prompt, não o post. A exceção é "editar e aprovar", que edita algo que a máquina propôs |
| **Não é um cliente de Instagram.** Sem feed navegável, sem responder DM de graça, sem explorar perfis | O painel opera o Olimpo, não a plataforma. Quem toca a plataforma é o android-orchestrator |
| **Não replica o android-manager.** Nada de gerenciar containers redroid, fazer login de conta ou curar device | Saúde da frota é exibida somente leitura. Infra é responsabilidade de outro painel |
| **Não vira Grafana.** Sem construtor de dashboard, sem série temporal de infra | `make up-obs` já entrega isso. Aqui só entra métrica ligada a decisão |
| **Sem escrita direta no banco.** Nenhuma tela edita `events`, `memories`, `tasks.status` ou `llm_calls` à mão | Auditoria imutável é premissa. Corrigir estado é ação de API com efeito colateral registrado, não `UPDATE` |
| **Sem automação escondida na UI.** Nenhuma regra de negócio implementada só no front | Se o painel decide algo, o worker não decide igual. Toda lógica no backend; o painel apresenta e comanda |
| **Sem aprovação em lote irrestrita** | O lote existe, mas com as travas de §4.3. Aprovar 200 itens com um clique é o mesmo que estar em L3 sem admitir |
| **Sem "aprovar tudo" nem auto-aprovar por timeout** | Fila crescendo é sinal de que a autonomia está errada ou o agente está ruim. Esconder isso quebra o modelo de governança |
| **Sem gamificação, streak, confete ou celebração** | É uma sala de controle de operação de risco. Emoção fabricada atrapalha julgamento |
| **Sem infinite scroll em lista operacional** | Paginação explícita com total. "Quantos itens existem" é informação operacional |
| **Sem modal empilhado.** No máximo um nível de sobreposição | Contexto perdido em modal aninhado é como se aprova a coisa errada |
| **Sem dado sem recorte temporal** | Todo número traz janela. "18 tasks" sem "últimas 24 h" é ruído |
| **Sem esconder o dry-run** | A faixa âmbar de `DISPATCH_ENABLED=false` não é dispensável, não é discreta e não some com o scroll |
| **Sem tela exclusiva de um tema** | Claro e escuro têm paridade total de recursos |
| **Sem persona baseada em pessoa real** — o editor de persona recusa upload de referência facial externa | Princípio inegociável do projeto (`docs/00-visao-geral.md`) |

---

## 10. Estados vazios, carregamento e erro

| Situação | Tratamento |
|---|---|
| Fila de aprovação vazia | Estado positivo e informativo: "Nada aguardando. 14 itens decididos hoje · tempo médio de decisão 22 s". Não é celebração, é relatório |
| Lista filtrada sem resultado | Repete o filtro aplicado em texto e oferece limpá-lo. Nunca só "nenhum resultado" |
| Carregamento | Skeleton com a forma exata do conteúdo final, para não deslocar layout. Sem spinner de tela cheia |
| API fora | Faixa vermelha persistente com o erro e botão de tentar novamente. O painel **não** mostra cache velho como se fosse atual — dado obsoleto é marcado com a idade |
| Mídia não resolve | Placeholder com a `media_ref` bruta copiável e link para o storage. Nunca imagem quebrada silenciosa |
| Fase não implementada | Telas de fase futura (monitores antes da Fase 4) aparecem no menu desabilitadas, com a fase em que chegam. O operador vê o mapa completo do produto |

---

## 11. Entrega por fase

| Fase | O que o painel ganha |
|---|---|
| **0** | Página única read-only: contas, tasks, últimos 100 `events`. Sem ações. Prova o encanamento |
| **1** | Fila de aprovação completa (L0) com `ApprovalCard`, `ReasoningPanel`, `QAReport` e atalhos · ficha da conta com persona, memória e agenda · pausar/kill · SSE |
| **2** | Tela de interações · promoção de autonomia com confirmação dupla · aba de amostragem L2 · métricas de post na ficha |
| **3** | Campanhas inteiras: wizard, `SpreadPolicyEditor`, `ReconViewer`, revisão de plano, timeline de execução · biblioteca de conteúdo |
| **4** | Monitores 24/7 · fila de revisão dedicada a fofoca com `claim_status` obrigatório e lote proibido |
| **5** | Direitos e atribuição no `ApprovalCard` · filtro por cliente (`clients`) em tudo · papéis de usuário |
| **6** | Analytics por nicho/horário/formato · comparação A/B conduzida pelos agentes |

---

## 12. Referências

- `docs/01-arquitetura.md` — onde o painel se encaixa
- `docs/02-modelo-de-dados.md` — as tabelas citadas aqui
- `docs/04-persona-card-spec.md` — schema que o editor de persona valida
- `docs/05-loop-do-agente.md` — as oito etapas que a `TaskTimeline` desenha
- `docs/06-campanhas.md` — recon, `spread_policy`, participantes
- `docs/07-monitores.md` — regras de disparo e saúde
- `docs/08-guardrails-e-autonomia.md` — o que cada nível permite e o que a fila precisa bloquear
- `docs/11-observabilidade.md` — métricas, `events` e `trace_id`
- `db/migrations/0001_initial.sql` — fonte da verdade do schema
