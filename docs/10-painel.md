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

O mesmo vale para a lista de contas, por outro motivo: a operação real tem **milhares** de logins. Toda lista operacional em volume é **paginada e filtrada no banco**, com contagem total visível e ação em lote — nunca "traz tudo e filtra na tela", que derruba o navegador e some com o tempo de resposta.

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
    │   │   ├── page.tsx            # (2) tabela densa: busca, facetas, lote
    │   │   └── [id]/
    │   │       ├── page.tsx        # ficha do login: identidade, perfil, onde roda, notas
    │   │       ├── memoria/page.tsx
    │   │       ├── agenda/page.tsx
    │   │       ├── posts/page.tsx
    │   │       └── tasks/page.tsx
    │   ├── personas/
    │   │   ├── page.tsx            # (2b) identidades + logins vinculados
    │   │   └── [id]/page.tsx       # editor do card: modo guiado e modo JSON
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
    │   ├── servidores/
    │   │   ├── page.tsx            # (10) hosts, serviços, contas descobertas
    │   │   └── [id]/page.tsx       # devices do host + telas ao vivo
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

Convenção das tabelas abaixo: **endpoint** é o contrato da FastAPI; **tabelas** são as de `db/migrations/` — `0001_initial.sql` para o núcleo e `0005_servers_and_services.sql` / `0006_account_inventory.sql` para a tela de Servidores (§4.10).

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

### 4.2 Contas e personas

> **Uma conta é um login numa plataforma; a identidade por trás dela é a persona, que pode ter vários logins** ([ADR-0013](adr/0013-persona-separada-da-conta.md)). São duas telas porque são duas coisas, e cada uma responde a uma pergunta diferente: *"onde está esse login e ele está operando?"* × *"quem é essa personagem?"*.

**O princípio que manda nesta tela: com quatro mil contas, cartão não serve.** Uma grade de cartões bonitos é ótima para vinte contas e inútil para quatro mil — não cabe na tela, não carrega no navegador e não deixa agir sobre um conjunto. O que o operador precisa é **varrer, filtrar e agir em lote**: tabela densa, paginada no servidor, com facetas contadas no banco e seleção múltipla. É a mesma lógica de §1.3 e §1.6, levada ao caso em que o volume é a dificuldade principal.

#### Lista — `/contas`

**Propósito.** Achar um login entre milhares, entender o estado da frota linha a linha e agir sobre um conjunto de uma vez.

**Paginada no banco, nunca na tela.** `GET /v1/accounts` devolve uma página (`page`, `per_page` até 500, default 50), o total e o número de páginas. Trazer tudo e filtrar no cliente derruba o navegador e some com o tempo de resposta — e é o erro que esta tela existe para não cometer.

**Busca.** Um campo só, que procura em quatro lugares: `handle` (índice trigrama, busca "contém" — o operador digita pedaço, não prefixo), `external_id` exato, `jid` (telefone do WhatsApp) e **nome da persona**. Achar a conta pelo nome da personagem é o caminho natural quando se sabe quem, não qual login.

**Facetas clicáveis, contadas no banco.** Acima da tabela, uma linha por eixo, com a contagem de cada valor:

```
plataforma   [IG 2891] [WA 940] [TT 173]
situação     [cadastrada 1204] [ativa 1902] [aquecendo 611] [pausada 190] [perdida 97]
presença     [em device 1740] [sem sinal 402] [sumida 118] [sem device 1744]
papel        [agente 3388] [coletora 511] [utilitária 105]
sem persona  [ 862 sem identidade vinculada ]
```

As contagens vêm **sem filtro aplicado**, numa única consulta agregada: são o mapa da operação inteira, e é a partir delas que o operador decide o que olhar primeiro. Clicar alterna o filtro; clicar de novo remove.

| Filtro | Campo |
|---|---|
| Plataforma | `accounts.platform` |
| Situação | `accounts.status` (registered · warming · active · paused · suspended · lost) |
| Presença | calculada dos placements (online · stale · missing · unbound) — [docs/02 § 3.35](02-modelo-de-dados.md#335-situação-e-presença-dois-eixos-ortogonais) |
| Papel | `accounts.role` (agent · collector · utility) |
| Arquétipo | `accounts.archetype` (influencer · gossip · clipper · messaging · collector) |
| Autonomia | `accounts.autonomy` (L0–L3) |
| Rótulo | `accounts.labels` (índice gin) |
| Persona | `accounts.persona_id`, e o atalho `unbound` para "sem identidade vinculada" |
| Cliente | `accounts.client_id` → `clients.name` *(oculto até a Fase 5)* |
| Busca | `q` sobre handle (trigrama), `external_id`, `jid` e nome da persona |

Colunas: **conta** (badge de plataforma + handle + marcador de observações, com arquétipo e papel na segunda linha) · **persona** (ou *sem persona*, em cinza) · **situação** · **presença** (com `×N` quando o login está em mais de um device) · seguidores · autonomia · **visto** (`last_seen_in_inventory`, relativo).

**Situação e presença são duas colunas de propósito.** Uma diz o que queremos; a outra, o que a realidade responde. Ver uma conta `ativa` e `sumida` na mesma linha é exatamente o que faz o operador agir — e é informação que um campo só apagaria.

**Ordenação:** handle · seguidores · criação · último visto · situação. Feita no banco, junto com a paginação.

**Seleção múltipla e ações em lote.** Caixa por linha e caixa no cabeçalho (marca a página inteira). Com algo selecionado, aparece uma barra com a contagem e as ações: mudar situação · mudar papel · vincular à persona · desvincular · adicionar rótulos · remover rótulos · mudar autonomia. Teto de **2000 ids** por chamada.

> **O que NÃO entra no lote:** apagar (§8, e nem existe no endpoint) e promoção de autonomia sem confirmação por conta. Rebaixar em massa é contenção e é legítimo; promover em massa é abrir mão da supervisão de 500 contas com um clique.

**Cadastrar conta é o primeiro passo, não o último.** `Nova conta` pede plataforma, handle, papel e (opcionalmente) persona. **O login é cadastrado antes de existir em qualquer device** — o vínculo com o container aparece sozinho quando o inventário o encontrar. Era a ordem impossível no modelo antigo, em que a conta só existia se um device a revelasse. O arquétipo é validado contra a plataforma (WhatsApp aceita `messaging`/`collector`; Instagram e TikTok, os de conteúdo).

`GET /v1/accounts?q=&platform=&status=&role=&archetype=&autonomy=&presence=&label=&persona_id=&unbound=&sort=&page=&per_page=` · `POST /v1/accounts` · `POST /v1/accounts/bulk`

#### Ficha da conta — `/contas/[id]`

Tudo sobre um login. Cabeçalho com badge de plataforma, handle, presença, `external_id`, idade e — quando houver — a linha *"já se chamou: …"* vinda de `previous_handles`.

| Bloco | Conteúdo |
|---|---|
| **Identidade e operação** | handle · **persona vinculada** (select, com a opção explícita *"— sem identidade vinculada —"*) · situação · autonomia · papel · arquétipo · `external_id` · telefone/JID (só WhatsApp) · rótulos · fuso |
| **Inventário** | seguidores, seguindo, publicações — com o aviso de que vêm da varredura, não de digitação |
| **Dados da conta** | `accounts.profile`: e-mail, e-mail de recuperação, telefone, nascimento, criada em, origem, proxy, geo, 2FA — **campos sugeridos, não schema fechado**; chaves extras são aceitas e editáveis |
| **Onde está a credencial** | `accounts.secret_ref`, em bloco próprio com ícone de chave: *"referência ao cofre, não a senha em si — esta coluna não é cifrada"* |
| **Onde esta conta roda** | os `account_placements`: servidor, container, usuário Android, logada/deslogada, aviso de watchdog, idade do último avistamento. Sem placement, o texto diz que a conta existe normalmente e só não tem onde operar |
| **Observações** | `entity_notes` datadas, com autor e fixação. Diferente do campo de anotação livre acima: aqui nada é sobrescrito |
| **Zona de risco** | apagar a conta e todo o histórico, em bloco separado com borda vermelha |

**Faixa de presença.** Quando `presence = 'missing'`, uma faixa âmbar no topo diz em texto: *"Esta conta não aparece no inventário há mais de um dia. **Ela não foi perdida** — o cadastro, a persona e o histórico continuam aqui. O que sumiu foi o login dentro do device."* É a resposta à pergunta que o operador faz, no lugar em que ele a faz.

**Zona de risco, e por que ela é feia de propósito.** Apagar cascateia conteúdo, posts, tasks e aprovações. O botão exige digitação (`apagar`) e a API recusa sem `confirm=apagar-de-verdade`, respondendo com a alternativa correta: **conta banida deve virar `perdida`** — o histórico é o que explica por que ela caiu. Ver §8.

**Campos livres com formulário guiado.** `profile` é `jsonb` sem schema porque cada operação tem seus campos; o painel oferece os nove mais comuns e deixa acrescentar quaisquer outros. É a única forma de o formulário não virar migração toda semana.

`GET /v1/accounts/{id}` (conta + placements com servidor/container/device + observações + últimas tasks) · `PATCH /v1/accounts/{id}` · `POST /v1/accounts/{id}/notes` · `DELETE /v1/notes/{id}` · `DELETE /v1/accounts/{id}?confirm=apagar-de-verdade`

> **Desvincular persona precisa de campo próprio.** Em `PATCH`, `null` significa *"não mexer"* — não *"apague o vínculo"*. Por isso o corpo tem `unbind_persona: true` como campo separado. É detalhe de contrato, e é o tipo de coisa que, sem estar escrita, vira um bug de "não consigo tirar a persona errada dessa conta".

> **Trocar o handle empilha o anterior** em `previous_handles`. A conta não sumiu, ela se renomeou — e concluir o contrário é como se perde histórico.

#### Personas — `/personas`

**Propósito.** A identidade, separada do login. A Luna Prado existe mesmo que nenhuma conta exista, e o Instagram e o WhatsApp dela são a **mesma** personagem, com uma memória só.

Um cartão por persona — e aqui cartão **cabe**, porque personas são dezenas, não milhares. Cada um mostra **o rosto** (a âncora `face`), nome, `slug`, nicho principal do card, contagem de memórias, arco ativo, e a lista de **logins vinculados** (badge de plataforma, handle, situação, seguidores), cada um linkando para a ficha da conta.

**A lista mostra o rosto porque a lista é sobre pessoas.** Uma grade de nomes obriga a abrir cada ficha para saber de quem se está falando; a miniatura resolve isso em um relance. Persona sem âncora de rosto aparece com o lugar vazio marcado — e o vazio é informação, não falta de polimento.

| Elemento | O que mostra |
|---|---|
| Miniatura | `personas.face_asset_id` → `GET /v1/media/{id}/arquivo`. Sem âncora, placeholder marcado |
| Busca | por nome da persona |
| Aviso de card vazio | faixa âmbar: *"Sem persona card. Sem ele a LLM não tem voz para escrever — a conta não deve sair de L0"* |
| Sem logins | *"nenhum login vinculado"* — persona cadastrada e ainda sem conta é estado válido |
| `Nova persona` | nome, `slug` (derivado do nome se vazio), descrição, rótulos, observações |

> ⚠️ **A miniatura vem em tamanho cheio.** Não existem thumbnails ([docs/13 § 9](13-geracao-de-midia.md#9-o-que-não-existe)); uma lista de 40 personas baixa 40 originais. Enquanto personas são dezenas, isso é aceitável — quando forem centenas, deixa de ser.

`GET /v1/personas?q=` · `POST /v1/personas` · `POST /v1/personas/{id}/notes` · `GET /v1/media/{id}/arquivo`

#### Editor da persona — `/personas/[id]`

**A ordem da página é decisão, não acaso.** Identidade visual primeiro, fora de qualquer aba; o resto — Mídias, Figurinos, Ficha, Logins — em abas abaixo. É a organização da máquina de influencers, e o motivo é a §1.2: as quatro âncoras são o que define quem a personagem *é*, e antes ficavam espremidas entre um formulário e dezenas de campos de persona card, com a galeria de mídias no rodapé, depois de tudo.

As abas não são só arrumação de tela: **o conteúdo da aba inativa não é montado**. Numa persona com centenas de mídias, montar tudo dispara todas as requisições de uma vez.

| Bloco | Onde | Por quê |
|---|---|---|
| Identidade visual | topo, fixo | é o que se olha antes de tudo, e o que precisa estar à mão enquanto se mexe no resto |
| Fotos de referência | logo abaixo, fixo | sem rosto elas não salvam nada; com rosto melhoram toda geração |
| Mídias · Figurinos · Ficha · Logins | abas | campos demais para empilhar |

O card em dois modos, alternáveis por um botão, editando o **mesmo** objeto.

| Modo | Para quem | Como funciona |
|---|---|---|
| **Guiado** | quem está escrevendo a personagem | Blocos com rótulo em português e texto de ajuda dizendo *por que* o campo existe — identidade, voz, visual, nicho, tabus. Listas viram uma linha por item; textos longos viram área |
| **JSON** | quem já conhece a spec | O card inteiro, cru, com validação de sintaxe antes de salvar. O modo guiado cobre os blocos mais editados; **o resto do card continua acessível só por aqui** |

Alternar entre os modos preserva o conteúdo: sair do JSON reinterpreta o texto; entrar no JSON serializa o que o modo guiado montou. JSON inválido bloqueia o salvamento com a mensagem do parser, em vez de gravar pela metade.

**Salvar o card incrementa a versão.** `PATCH /v1/personas/{id}` com `persona_card` incrementa `personas.persona_version` e grava o snapshot em `persona_versions` — *"por que ela mudou de voz?"* precisa continuar respondível ([docs/04 § 5](04-persona-card-spec.md#5-versionamento)). A ficha também lista os logins vinculados, os arcos e as observações da persona.

`GET /v1/personas/{id}` (persona + contas + arcos + observações) · `PATCH /v1/personas/{id}`

> ❓ O editor ainda **não** exige `change_note` nem mostra diff lado a lado das versões, que a spec de persona pede. O histórico é gravado; a interface para lê-lo ainda não existe.

#### Identidade visual — bloco da ficha da persona

> Migração 0008 · [ADR-0014](adr/0014-absorver-a-maquina-de-influencers.md) · mecanismo em [docs/13 § 3](13-geracao-de-midia.md#3-consistência-facial--as-quatro-coisas-frágeis)

A ficha da persona ganhou um bloco que **não é uma galeria**: são as quatro imagens que ancoram o rosto da personagem, mostradas **na ordem em que entram no prompt**, com a numeração visível.

```
Identidade visual                                        3 de 4 âncoras

┌──────────────┐ ┌──────────────┐ ┌──────────────┐ ┌──────────────┐
│              │ │              │ │              │ │      —       │
│   [ rosto ]  │ │  [ folha ]   │ │  [ close ]   │ │   ausente    │
│              │ │              │ │              │ │              │
├──────────────┤ ├──────────────┤ ├──────────────┤ ├──────────────┤
│ @image1      │ │ @image2      │ │ @image3      │ │ @image4      │
│ rosto        │ │ folha de     │ │ close de     │ │ cartela de   │
│              │ │ personagem   │ │ rosto        │ │ detalhe      │
└──────────────┘ └──────────────┘ └──────────────┘ └──────────────┘
```

| Elemento | O que mostra | Por quê |
|---|---|---|
| **A numeração `@image1..@image4`** | A posição de cada âncora no array de referências | **A ordem é contrato, não enfeite.** É como o prompt endereça cada imagem; expô-la é o que impede alguém de reordenar achando que está organizando |
| **O rótulo em português** | rosto · folha de personagem · close de rosto · cartela de detalhe | O papel de cada uma, para quem não decorou os nomes técnicos |
| **A contagem** | *"3 de 4 âncoras"* | Quantas existem importa mais do que quais |
| **Posições vazias** | Ficam no lugar, marcadas como ausentes | Uma grade que só mostra o que existe esconde exatamente o que precisa ser visto |
| **Aviso de rosto ausente** | Faixa âmbar quando falta `face_asset_id` | Ver abaixo |
| **Referências extras** | `reference_asset_ids`, em fila separada **depois** das quatro | Elas entram no fim do array; mostrá-las misturadas sugeriria que a posição delas é negociável |

**A faixa quando falta a referência de rosto.** `pode_gerar_com_identidade = false` significa que qualquer geração produz uma pessoa diferente — e isso precisa estar dito em texto, no lugar em que a decisão é tomada:

> ⚠️ *"Esta persona não tem imagem de rosto. Sem ela não há identidade a preservar: **qualquer geração produz uma pessoa diferente**. Gere a folha de identidade antes de produzir conteúdo."*

**Por que expor a numeração para o operador.** É a única defesa de interface contra o modo de falha central do subsistema. Reordenar as âncoras não gera erro, não gera alerta, e o sintoma — rostos que derivam — aparece semanas depois, quando já há posts publicados. Um rótulo `@image2` na tela transforma uma armadilha invisível em algo que se lê.

`GET /v1/media/personas/{id}/identidade` · `GET /v1/media/{id}/arquivo`

#### Aba Mídias — tudo o que existe da persona

A versão anterior mostrava só `role=generated`, e isso escondia a maior parte do acervo: âncoras, figurinos e fotos de referência ficavam invisíveis. Quem procurava *"aquela foto que eu gerei semana passada"* não tinha onde procurar.

| Elemento | Detalhe |
|---|---|
| Filtros | **todas · identidade · figurinos · produzido · referências**, com contagem ao lado. "identidade" agrupa as quatro âncoras porque é assim que se pensa nelas |
| Cada item | miniatura com o papel escrito por cima — o papel tem consequência sobre o que a geração faz com a imagem, não é etiqueta |
| Ao abrir | modelo, provedor, formato, dimensões, tamanho e origem; navegação por setas; **o prompt exato que gerou aquilo** (`media_assets.prompt`) |
| Apagar | recusado para âncora ligada — `409` dizendo para desligá-la primeiro ([§ identidade](#identidade-visual--bloco-da-ficha-da-persona)) |
| Paginação | no servidor (`page`, `per_page` até 200) — a mesma disciplina de §4.2 |

**A contagem por papel é da página corrente**, e a tela diz isso. O servidor não devolve agregado por papel, e um número que parece total mas não é vale menos que nenhum.

**Um papel, uma âncora.** Definir uma âncora nova rebaixa a anterior para `reference`. Sem isso o papel passava a mentir: dois assets diziam ser a folha de personagem e só um era apontado pela persona — a galeria contava "identidade 6" numa persona com quatro âncoras.

**O prompt visível é o que fecha o ciclo de auditoria visual.** O equivalente do `ReasoningPanel` para imagem: a pergunta *"por que essa foto saiu assim?"* precisa ter resposta sem `psql`. E `escolhas` do motor de prompt registra cena, horário, pose, prop, look e paleta sorteados — ❓ ainda não exposto na interface.

`GET /v1/media?persona_id=&role=&kind=&page=&per_page=`

---

#### Abas da conta — planejadas

O restante da ficha do agente, ainda por implementar. A tabela abaixo é especificação, não estado atual.

| Aba | Conteúdo | Endpoint / tabelas |
|---|---|---|
| **Visão** | `AccountCard` expandido, arco ativo, plano de hoje, últimas 5 tasks, alerta de guardrail recente | `GET /v1/accounts/{id}` → `accounts`, `story_arcs` (`active`), `day_plans` de hoje, `tasks`, `guardrail_violations` |
| **Persona** | `PersonaCard` — leitura formatada de `personas.persona_card` por seção (identidade, voz, tabus, estética, linha editorial) **e das quatro âncoras de identidade visual**, com link para o editor da persona. **A edição não é por conta:** o card é da personagem e vale para todos os logins dela | `GET /v1/personas/{id}` → `personas`, `persona_versions`; `GET /v1/media/personas/{id}/identidade` |
| **Memória** | linha do tempo de `memories` filtrável por `kind` (episódica · semântica · narrativa), com `importance` como peso visual e busca semântica | `GET /v1/accounts/{id}/memories?kind=&q=` → `memories` (busca por `embedding`, ordenação por `occurred_at`) |
| **Arco** | arco atual (`title`, `summary`, `state`), régua de `starts_at`→`ends_at`, arcos passados | `GET /v1/accounts/{id}/arcs` → `story_arcs` |
| **Agenda** | plano do dia: `narrative`, `intents` como cartões, `reasoning` no `ReasoningPanel`, e as tasks derivadas na `TaskTimeline` com `active_hours` sombreado | `GET /v1/accounts/{id}/day-plan?date=` → `day_plans`, `tasks` |
| **Posts** | grade de publicações com métrica mais recente e sparkline de evolução | `GET /v1/accounts/{id}/posts` → `posts`, `post_metrics` (última coleta por `collected_at`) |
| **Tasks** | histórico completo, filtro por `type` e `status`, com `external_task_id` e `result` do orchestrator | `GET /v1/accounts/{id}/tasks` → `tasks` |
| **Interações** | comentários/DMs desta conta, sensíveis no topo | `interactions` |
| **Eventos** | auditoria bruta, agrupável por `trace_id` | `events` |

**Edição de persona com versionamento.** Salvar não sobrescreve: a API grava o card anterior em `persona_versions` (`version`, `changed_by`, `change_note`), incrementa `personas.persona_version` e emite um evento. A UI deve exigir `change_note` — "por que ela mudou de voz?" é uma pergunta que precisa ter resposta — e mostrar diff lado a lado (JSON estruturado, não texto cru), validando contra o schema zod derivado de `docs/04-persona-card-spec.md`. ❓ Hoje o editor grava a versão mas não pede a nota nem mostra o diff.

**Ações da ficha:**

| Ação | Efeito | Confirmação |
|---|---|---|
| Pausar | `accounts.status='paused'`; dispatcher ignora | simples |
| Retomar | volta para `active` | simples |
| **Kill** | `status='suspended'`, cancela tasks `pending`/`scheduled`/`dispatched` | **dupla** (§8) |
| **Promover/rebaixar autonomia** | altera `accounts.autonomy` | **dupla** para promoção; simples para rebaixamento |
| Forçar `plan_day` | cria task `plan_day` imediata | simples |
| Editar cadência / `active_hours` | `accounts.cadence`, `accounts.active_hours` | simples |
| Vincular / desvincular persona | `accounts.persona_id`; desvincular usa `unbind_persona: true` | simples |
| Editar persona | nova linha em `persona_versions` (na tela da persona, não na da conta) | simples, com `change_note` obrigatório |
| **Apagar conta** | cascateia conteúdo, posts, tasks e aprovações | **dupla, com digitação** (§8). Conta banida deve virar `perdida` |

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

### 4.10 Servidores

`/servidores` — o índice que nenhum serviço tem.

**Propósito.** Um servidor é um **host**; nele rodam vários serviços — o orchestrator que executa, o android-manager que gerencia a frota, o gateway que transmite as telas. Nenhum deles conhece os outros, e nenhum conhece a frota inteira ([ADR-0012](adr/0012-servidor-com-varios-servicos.md)). Esta tela responde: quais hosts existem, o que roda em cada um, o que está no ar, quantos devices estão vivos e **quais contas estão logadas lá que o Olimpo ainda não conhece**.

#### Lista — `/servidores`

Um cartão por servidor. Recarrega sozinho a cada 20 s.

```
┌─ i9-14900k                                    [ativo]  [gpu]  ──────────┐
│  host i9-14900k.taila41397.ts.net   tailscale 100.x.x.x                  │
│                                        [ sondar ] [ varredura completa ] │
├──────────────────────────────────────────────────────────────────────────┤
│  serviços 3/3      devices 6/8      contas online 11   sincronia há 2min  │
├─ Serviços ───────────────────────────────────────────────────────────────┤
│  [Orchestrator]     http://…:8078        41ms   no ar              ↗      │
│  [Android Manager]  https://…/api    🔓  88ms   no ar              ↗      │
│  [Gateway de tela]  https://…:9000       —      não sondado        ↗      │
├──────────────────────────────────────────────────────────────────────────┤
│  ⚠ 3 contas logadas que o Olimpo não conhece      [ importar todas ]      │
├──────────────────────────────────────────────────────────────────────────┤
│  📱 ver os 8 devices ›                                                    │
└──────────────────────────────────────────────────────────────────────────┘
```

| Elemento | O que mostra | Endpoint / tabelas |
|---|---|---|
| Cabeçalho | nome, `server_status`, `labels`, e os **três endereços** (`hostname`, `tailscale_ip`, `lan_ip`) em fonte monoespaçada | `GET /v1/servers` → `servers` |
| Métricas | serviços online/total · devices vivos/total · contas logadas · idade da última sincronia com contas encontradas e duração | `server_services`, `devices`, `account_placements`, `inventory_syncs` (a mais recente) |
| Linha de serviço | tipo com cor própria, `label`, `base_url`, **badge de TLS**, latência, status com `last_error` no tooltip, **link ↗ para a interface** e remoção | `server_services` |
| Contas descobertas | contas logadas nos devices que **não existem** em `accounts`, com plataforma, seguidores e em quais containers aparecem | `GET /v1/servers/{id}/discovered` |
| Rodapé | link para o detalhe do servidor; erro da última sincronia quando houver | `GET /v1/servers/{id}/devices` |

**Badge de TLS.** Todo serviço `https://` mostra um cadeado: **fechado verde** quando `verify_tls = true`, **aberto âmbar** quando não. Certificado autoassinado é a regra em rede interna e desligar a verificação é uma decisão legítima — mas é **por serviço** e precisa ficar visível, não escondida no banco. Serviço em `http://` não mostra cadeado nenhum: não há o que afirmar.

**Sondagem é por serviço, não por host.** `sondar` bate em todos os serviços habilitados **em paralelo** e cada um responde por si — um android-manager sem credencial não derruba a leitura do orchestrator do mesmo servidor. O status do host é derivado do pior dos serviços, e o cartão sempre mostra os dois níveis. `varredura completa` (`?refresh=true`) força o nó a revisitar cada device: custa cerca de 90 s e por isso **nunca** é o comportamento padrão — é um segundo botão, com aviso no `title`.

**Estado `credencial inválida` tem cor própria.** `401`/`403` não são "fora do ar": o operador conserta credencial, não infraestrutura. Confundir os dois faz reiniciar serviço saudável.

**Contas descobertas.** A varredura do orchestrator encontra contas logadas nos devices que o Olimpo não tem cadastradas. A faixa âmbar oferece importar (todas ou selecionadas). Contas importadas nascem em `registered`/`L0` e **sem persona vinculada**, já ligadas ao device e com o inventário real (`external_id`, seguidores, seguindo, publicações, `jid`) — **existem e não agem** até alguém decidir de quem elas são e liberá-las. Elas aparecem na faceta *sem identidade vinculada* da tela de Contas (§4.2), que é onde essa pendência fica visível em vez de se perder.

**Cadastro.** `Novo servidor` pede nome e endereços; `+ serviço` pede tipo, URL, credencial e TLS, com **sugestões por tipo** (porta, esquema, caminho de saúde, dica). Duas travas: a API recusa orchestrator apontando para `:8077` (é o executor — quem fala com ele tem root no host), e o formulário avisa que o android-manager **não tem chave de API**, só usuário e senha.

`GET|POST /v1/servers` · `PATCH|DELETE /v1/servers/{id}` · `POST /v1/servers/{id}/services` · `PATCH|DELETE /v1/servers/services/{id}` · `POST /v1/servers/{id}/probe[?refresh=true]` · `GET /v1/servers/{id}/discovered` · `POST /v1/servers/{id}/import`

#### Detalhe do servidor — `/servidores/[id]`

Os devices daquele host, em grade de cartões, e o lugar onde o monitoramento de tela vai morar.

| Elemento | Conteúdo |
|---|---|
| Cartão de device | `container`, versão do Android, `adb_target`, `video_device`, vivo/parado, idade de `last_seen_at` |
| Contas no device | handle, plataforma, `android_user`, e o marcador de **usuário ativo** |
| Aviso de vigilância | faixa âmbar quando `under_external_management` — o watchdog do android-manager pode reiniciar este container |
| Ações por device | `captura` (desabilitada até haver android-manager cadastrado no servidor) · `tela` (desabilitada sem gateway ou sem dispositivo de vídeo) |
| `grade ao vivo` | todas as telas aptas lado a lado, com **teto explícito** de telas simultâneas |

> ⚠️ **"usuário ativo" não é "está na tela".** O serviço reporta *usuário Android em execução*, e o Android mantém vários rodando ao mesmo tempo. Qual está em primeiro plano ninguém sabe. O rótulo diz o que o dado significa, não o que gostaríamos que significasse ([docs/02 § 3.31](02-modelo-de-dados.md#331-campos-novos-em-accounts-e-account_placements)).

**Monitoramento de tela está desligado hoje, e a tela explica por quê.** A base já está montada — device com endereço ADB e dispositivo de vídeo, gateway cadastrável como serviço, link montado pela interface — mas o gateway ainda não existe do lado de lá. Em vez de esconder o recurso, a tela mostra o estado real:

| Situação | O que aparece |
|---|---|
| Nenhum gateway cadastrado no servidor | Faixa neutra: *"Transmissão de tela não configurada. A interface já está pronta; falta o endereço."* |
| Gateway cadastrado, nenhum device apto | Faixa âmbar explicando que a transmissão contínua exige dispositivo de vídeo no container — sem ele só existe o modo que derruba quem já estava assistindo |
| Gateway + devices aptos | Botão `grade ao vivo` habilitado |

Três regras que valem para **qualquer transporte** (hoje WebSocket + H.264; WebRTC está por vir, e por isso o gateway é modelado como serviço opaco com URL própria):

1. **O painel precisa estar em HTTPS.** Página segura não embute origem insegura, e a decodificação de vídeo exige contexto seguro.
2. **O espectador é sempre passivo.** O parâmetro que garante isso **no servidor** é obrigatório na montagem do link — o Olimpo nunca pode roubar o controle de um curador humano operando o device. A tela diz isso em texto, acima da grade.
3. **Teto de telas simultâneas.** Cada tela aberta é um processo de codificação no host; sem teto, abrir a grade derruba o servidor que se queria observar.

`GET /v1/servers/{id}/devices` → `devices` + `account_placements` + o `screen_gateway` do servidor (ou `null`, que é o que desabilita os botões).

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

**Situação da conta** (`account_status`) — o que *nós* queremos:

| Situação | Rótulo na tela | Cor | Token | Leitura |
|---|---|---|---|---|
| `registered` | cadastrada | cinza | `--st-draft` `#6B7280` | existe, não opera |
| `warming` | aquecendo | âmbar | `--st-warming` `#D97706` | aquecendo, só consome |
| `active` | ativa | verde | `--st-active` `#16A34A` | operando |
| `paused` | pausada | azul-acinzentado | `--st-paused` `#64748B` | pausa humana, reversível |
| `suspended` | suspensa | vermelho | `--st-suspended` `#DC2626` | kill switch — exige ação |
| `lost` | perdida | vinho apagado | `--st-lost` `#7F1D1D` | banida; fica no sistema de propósito |

**Presença** — o que a *realidade* diz, calculada dos placements ([docs/02 § 3.35](02-modelo-de-dados.md#335-situação-e-presença-dois-eixos-ortogonais)). Escala própria, **nunca a de situação**, porque são eixos ortogonais e confundi-los é o erro que a tela existe para evitar:

| Presença | Rótulo na tela | Cor | Leitura |
|---|---|---|---|
| `online` | em device | verde | logada num container visto na última hora |
| `stale` | sem sinal | âmbar | sem confirmação do inventário há mais de uma hora |
| `missing` | sumida | vermelho | fora do inventário há mais de um dia — **não é o mesmo que perdida** |
| `unbound` | sem device | cinza escuro | nunca esteve num device, ou o vínculo foi removido |

**Papel** (`accounts.role`): `agent` agente · `collector` coletora · `utility` utilitária. Sem cor própria — aparece como texto secundário abaixo do handle, porque é qualificador, não estado.

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
| **Apagar conta** | `apagar` | o que cascateia (conteúdo, posts, tasks, aprovações) e a alternativa: **conta banida deve virar `perdida`**. A API recusa sem `confirm=apagar-de-verdade` |

Regras que valem para as três:

- O diálogo **descreve consequência**, não pede confirmação genérica. Nunca "Tem certeza?".
- Ação destrutiva nunca é o botão primário de um formulário nem fica adjacente a uma ação frequente.
- Promoção de autonomia é **sempre uma conta por vez**, mesmo com seleção múltipla na lista.
- Toda ação destrutiva grava `events` com autor, alvo e motivo. Kill switch e mudança de autonomia também geram `guardrail_violations`-adjacente na trilha de auditoria.
- Rebaixar autonomia e pausar **não** exigem dupla confirmação: o caminho seguro tem que ser o mais barato.
- **Apagar não existe em lote.** As ações em massa de `/contas` mudam situação, papel, autonomia, rótulos e vínculo de persona — nenhuma delas destrói histórico, e todas são reversíveis. Destruição é sempre uma conta por vez, com digitação.

---

## 9. O que NÃO fazer no painel — anti-requisitos

| Anti-requisito | Por quê |
|---|---|
| **Não é um editor de posts.** Não existe "criar post do zero" | Quem cria conteúdo é o agente. Se o humano está escrevendo caption, o loop falhou — corrija o Persona Card ou o prompt, não o post. A exceção é "editar e aprovar", que edita algo que a máquina propôs |
| **Não é um cliente de Instagram.** Sem feed navegável, sem responder DM de graça, sem explorar perfis | O painel opera o Olimpo, não a plataforma. Quem toca a plataforma é o android-orchestrator |
| **Não replica o android-manager.** Nada de gerenciar containers redroid, fazer login de conta ou curar device | Saúde da frota é exibida somente leitura, e a tela de Servidores (§4.10) **linka** para o painel de cada serviço em vez de reimplementá-lo. Infra é responsabilidade de outro painel — a garantia técnica é a allowlist do cliente ([contrato](contratos/android-manager.md)) |
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
| **Sem grade de cartões em lista de contas** | Cartão é ótimo para vinte contas e inútil para quatro mil. Lista operacional em volume é tabela densa, paginada no servidor, com facetas e seleção múltipla (§4.2). Cartão sobra para **personas**, que são dezenas |
| **Sem apagar conta como caminho normal** | Conta banida vira `perdida`. Apagar destrói o histórico que explica por que ela caiu — e é justamente o histórico que impede repetir o erro |

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
| **1** | Fila de aprovação completa (L0) com `ApprovalCard`, `ReasoningPanel`, `QAReport` e atalhos · ficha da conta com persona, memória e agenda · pausar/kill · SSE · **tela de Servidores** (§4.10): cadastro de host e serviços, sondagem, devices e importação de contas descobertas · **Contas em tabela densa com facetas e lote, e a tela de Personas** (§4.2) · **bloco de identidade visual e galeria de mídia na ficha da persona** (§4.2) |
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
- `docs/adr/0012-servidor-com-varios-servicos.md` — o modelo por trás da tela de Servidores
- `docs/adr/0013-persona-separada-da-conta.md` — o modelo por trás das telas de Contas e Personas
- `docs/13-geracao-de-midia.md` — o que o bloco de identidade visual expõe, e por que a ordem aparece na tela
- `docs/14-motores-de-prompt.md` — o `escolhas` que a galeria ainda não mostra
- `docs/adr/0014-absorver-a-maquina-de-influencers.md` — por que Photo Studio e Content Studio precisam nascer de novo em Next.js
- `docs/contratos/android-manager.md` — o que a frota expõe, e o que ela **não** expõe
- `db/migrations/0001_initial.sql` — fonte da verdade do schema
