# 02 — Modelo de dados

> Fonte da verdade: os arquivos em [`db/migrations/`](../db/migrations/) — [`0001_initial.sql`](../db/migrations/0001_initial.sql) para o núcleo, [`0002_orchestrator_nodes.sql`](../db/migrations/0002_orchestrator_nodes.sql) para a ligação com o orchestrator, [`0005_servers_and_services.sql`](../db/migrations/0005_servers_and_services.sql) e [`0006_account_inventory.sql`](../db/migrations/0006_account_inventory.sql) para servidores, serviços, devices e inventário de contas, e [`0007_personas_e_contas.sql`](../db/migrations/0007_personas_e_contas.sql) para a separação entre persona, login e lugar onde ele roda ([ADR-0013](adr/0013-persona-separada-da-conta.md)).
> Este documento **descreve** aqueles arquivos. Se os dois divergirem, o SQL vence e este documento está com bug.

Postgres 16 + três extensões: `uuid-ossp` (chaves), `vector` (pgvector, memória semântica), `pg_trgm` (busca aproximada em texto — dedup e QA de caption).

**Índice**

1. [Princípios do modelo](#1-princípios-do-modelo)
2. [Mapa de relacionamentos](#2-mapa-de-relacionamentos)
3. [Tabelas](#3-tabelas)
4. [ENUMs e máquinas de estado](#4-enums-e-máquinas-de-estado)
5. [Índices](#5-índices)
6. [Consultas de referência](#6-consultas-de-referência)
7. [Convenções de migração](#7-convenções-de-migração)

---

## 1. Princípios do modelo

### 1.1 Campos dormentes nascem na v1

O schema da Fase 0 já contém colunas e tabelas inteiras que **nenhuma linha de código lê na Fase 1**: `sources`, `ingested_items`, `monitors`, `monitor_hits`, `clients`, e as colunas `content_items.source_refs`, `attribution`, `claim_status`, `accounts.client_id`.

Isso é deliberado. O raciocínio:

- **O custo hoje é zero.** Uma coluna `nullable` com default e uma tabela vazia não custam performance, não custam manutenção e não confundem quem lê o SQL — porque estão marcadas `[DORMENTE]`.
- **O custo depois é alto.** Adicionar `rights_status NOT NULL` em `content_items` com 200 mil linhas e 50 contas em produção significa: janela de manutenção, backfill, revisão de todo código que faz `INSERT`, e um período em que metade do sistema conhece a coluna e a outra metade não.
- **O schema é o contrato do produto inteiro, não da fase atual.** O PLANO já sabe que a Fase 4 (fofoca) precisa de `claim_status` e a Fase 5 (clipagem) precisa de `rights_status` e `clients`. Escrever isso agora é registrar a decisão no lugar onde ela não se perde.
- **Força o desenho a ser honesto.** Se `content_items` só fizesse sentido para influencer, a abstração de arquétipo único (ADR-0006) seria falsa. Preencher os campos dos três arquétipos na v1 é o teste de que a espinha é mesmo compartilhada.

**Regra de disciplina:** campo dormente **não** vira campo zumbi. Todo dormente tem uma fase declarada em que acorda. Se uma fase passa e o campo continua sem uso e sem dono, ele sai numa migração de limpeza — não fica "por via das dúvidas".

### 1.2 Identificadores em inglês, documentação em português

Tabelas, colunas, tipos e valores de ENUM são em inglês. Comentários, documentação e a interface do painel são em português.

- O código é Python com SQLAlchemy: `account.persona_card`, `task.scheduled_for`, `ContentStatus.APPROVAL_PENDING`. Misturar idiomas dentro de uma expressão (`conta.persona_card`) produz código feio e ambíguo em conversões `snake_case`/`camelCase`.
- Os serviços com que o core conversa (android-orchestrator, máquina de influencers) expõem APIs em inglês. Traduzir na fronteira do banco criaria duas nomenclaturas para a mesma coisa.
- Prompts de LLM e few-shots citam nomes de campo. Modelos são melhores em esquemas em inglês.
- O mapeamento PT ↔ EN de todo termo de domínio vive em [`docs/glossario.md`](glossario.md) — é lá que se resolve "conta" = `accounts`, "furo" = `ingested_items` com `novelty` alta, "espalhamento" = `spread_policy`.

### 1.3 Auditoria é tabela de primeira classe

`events` existe desde a linha 1 da Fase 0, antes de existir agente. Não é logging: é uma tabela transacional, com FKs para `accounts`, `campaigns` e `tasks`, escrita na **mesma transação** da decisão que ela registra.

A pergunta que o sistema precisa responder em qualquer momento é: *"por que a conta X postou isso às 19h?"*. A resposta é uma cadeia — plano do dia → intenção → draft → QA → aprovação → task → dispatch → verificação → post — e cada elo escreve um `event` com o mesmo `trace_id`. Sem isso:

- Um incidente de guardrail vira arqueologia em arquivo de log rotacionado.
- Não há como distinguir "a LLM decidiu" de "o humano mandou" de "a campanha forçou".
- Risco jurídico real (fofoca, Fase 4): publicar uma afirmação sem conseguir provar qual fonte a originou e quem aprovou é indefensável.

Como consequência, `llm_calls` também é de primeira classe: cada chamada de modelo é uma linha com custo, latência, backend e `trace_id`. Custo de LLM não é métrica de infra, é métrica de produto — decide se um agente é viável.

### 1.4 O agendamento mora no banco

Não existe fila em memória decidindo *quando* algo acontece. `tasks.scheduled_for`, `monitors.next_check_at` e `campaign_participants.slot_at` são colunas indexadas, e o dispatcher é um *tick* que faz `SELECT ... FOR UPDATE SKIP LOCKED`. Redis é usado para transporte e locks, nunca como fonte da verdade do agendamento.

Por quê:

- **Reinício não perde nada.** Derrubar a stack no meio de uma campanha de 6 horas não desagenda ninguém.
- **O agendamento é auditável e editável.** Um humano pode olhar no painel o que está agendado para as próximas 4 horas, e mover um slot com um `UPDATE`. Fila em memória não tem essa propriedade.
- **Anti-detecção depende de consulta.** `spread_policy` precisa perguntar "quantas contas da casa já agiram nesta janela?" e "esta conta já bateu o teto de ações/hora?". Isso é um `GROUP BY`, não um estado distribuído em workers.
- **Idempotência barata.** `external_task_id` + `status` no banco permitem que um webhook duplicado do orchestrator seja descartado com um `UPDATE ... WHERE status = 'dispatched'`.

O preço aceito é polling (o dispatcher acorda a cada poucos segundos) e pressão de escrita no Postgres. Para a ordem de grandeza do projeto — dezenas a algumas centenas de contas, milhares de tasks/dia — é irrelevante.

---

## 2. Mapa de relacionamentos

```
                          ┌───────────┐
                          │  clients  │ [DORMENTE — Fase 5]
                          └─────┬─────┘
                    ┌───────────┴────────────┐
                    ▼                        ▼
             ┌────────────┐            ┌───────────┐
   ┌─────────│  accounts  │            │ campaigns │
   │         └─────┬──────┘            └─────┬─────┘
   │               │ (1 conta = 1 agente)    │
   │               │                    ┌────┴──────────────────┐
   ▼               │                    ▼                       ▼
┌──────────────┐   │           ┌────────────────┐   ┌───────────────────────┐
│persona_      │   │           │ campaign_recon │   │ campaign_participants │
│versions      │   │           └───────┬────────┘   └───────────┬───────────┘
└──────────────┘   │                   │ task_id                │ account_id
                   │                   └──────────┐  ┌──────────┘
   ╔═══════════════╧════════════════╗             ▼  ▼
   ║  NARRATIVA / MEMÓRIA           ║        ┌──────────┐
   ║  ┌────────────┐                ║        │  tasks   │◄──── origin:
   ║  │ story_arcs │◄──┐            ║        └────┬─────┘  manager|self|
   ║  └────────────┘   │ arc_id     ║             │        human|campaign|
   ║  ┌────────────┐   │            ║             │        monitor
   ║  │  memories  │───┘            ║             │
   ║  └────────────┘                ║             ▼
   ║  ┌────────────┐                ║        ┌─────────┐     ┌──────────────┐
   ║  │ day_plans  │───┐            ║        │  posts  │────▶│ post_metrics │
   ║  └────────────┘   │            ║        └────┬────┘     └──────────────┘
   ╚═══════════════════╪════════════╝             │
                       │ day_plan_id              ▼
                       ▼                   ┌──────────────┐
              ┌────────────────┐           │ interactions │
              │ content_items  │──────────▶└──────────────┘
              └───────┬────────┘  (via posts)
                      │ ▲
                      │ │ content_item_id
                      │ │
   ╔══════════════════╪═╪══════════════════════════╗
   ║  INGESTÃO  [DORMENTE — Fase 4]                ║
   ║  ┌─────────┐   ┌────────────────┐   ┌───────┐ ║
   ║  │ sources │──▶│ ingested_items │◄──│monitor│ ║
   ║  └─────────┘   └───────┬────────┘   │  s    │ ║
   ║                        │            └───┬───┘ ║
   ║                        ▼                │     ║
   ║                  ┌──────────────┐◄──────┘     ║
   ║                  │ monitor_hits │─────────────╫──▶ content_items
   ║                  └──────────────┘             ║
   ╚═══════════════════════════════════════════════╝

   ┌─────────┐  compartilhado por todos os arquétipos, sem FK
   │ trends  │  (consultado no PLAN e no PRODUCE)
   └─────────┘

   ╔═══════════════════════════════════════════════════════════════╗
   ║  GOVERNANÇA — referenciam accounts/campaigns/tasks,           ║
   ║  nunca são referenciadas                                      ║
   ║  ┌───────────┐ ┌──────────────────────┐ ┌────────┐ ┌────────┐ ║
   ║  │ approvals │ │ guardrail_violations │ │ events │ │llm_call│ ║
   ║  └───────────┘ └──────────────────────┘ └────────┘ └───┬────┘ ║
   ╚═════════════════════════════════════════════════════════╪═════╝
                                    llm_call_id ◄────────────┘
                                    (day_plans, campaign_recon)

   ┌────────────────────┐
   │ schema_migrations  │  controle de versão do schema
   └────────────────────┘
```

**Leitura do mapa em uma frase:** `accounts` é o centro; tudo que é *trabalho* (conteúdo → task → post → métrica) flui da esquerda para a direita; campanhas injetam trabalho lateralmente; ingestão alimenta conteúdo por baixo; governança observa tudo por fora.

> ⚠️ O mapa acima é o da migração 0001, quando `accounts` também era a identidade. **A migração 0007 tirou a *vida* de cima do login** ([ADR-0013](adr/0013-persona-separada-da-conta.md)): memória, arcos e planos do dia pendem agora de `personas`. Leia o trecho abaixo por cima do mapa.

```
   ┌────────────┐  a IDENTIDADE: nome, slug, persona_card, persona_engine_id
   │  personas  │  existe mesmo sem login nenhum
   └─────┬──────┘
         │ persona_id (NULO é válido: coletora não tem persona)
         │
         ├───────────────┬───────────────┬───────────────┐
         ▼               ▼               ▼               ▼
   ┌────────────┐  ┌────────────┐  ┌────────────┐  ┌────────────┐
   │  accounts  │  │  memories  │  │ story_arcs │  │ day_plans  │
   │ os LOGINS  │  └────────────┘  └────────────┘  └────────────┘
   └─────┬──────┘        └──── a vida da personagem, uma só ────┘
         │ account_id
         ▼
   ┌────────────────────┐  ONDE cada login está rodando agora.
   │ account_placements │  Sumir daqui não apaga nada.
   └────────────────────┘

   ┌──────────────┐  observações datadas sobre conta, persona, device
   │ entity_notes │  ou servidor — polimórfica, sem FK
   └──────────────┘
```

**Três coisas, três ciclos de vida.** A personagem existe antes de qualquer login e sobrevive à perda de todos eles. O login pode ser banido e recriado. O lugar onde ele roda muda toda semana. Empilhar as três em uma linha só foi o que a 0007 desfez.

### 2.1 FKs criadas depois (ciclos)

Quatro FKs são adicionadas via `ALTER TABLE` no fim da migração porque formam ciclos de definição. Isso não é acidente, é ordenação:

| FK | De → Para | Por que fica pro fim |
|---|---|---|
| `fk_ingested_monitor` | `ingested_items.monitor_id` → `monitors.id` | `ingested_items` é declarada antes de `monitors` |
| `fk_hit_content` | `monitor_hits.content_item_id` → `content_items.id` | ingestão é declarada antes de conteúdo |
| `fk_content_campaign` | `content_items.campaign_id` → `campaigns.id` | conteúdo antes de campanha |
| `fk_recon_task` | `campaign_recon.task_id` → `tasks.id` | recon antes de tasks |
| `fk_plan_llm` / `fk_recon_llm` | `day_plans` / `campaign_recon` → `llm_calls.id` | governança é a última seção |

**Armadilha:** ao escrever uma nova migração que recria alguma dessas tabelas, lembre que a constraint tem **nome próprio** (`fk_recon_task`, não o nome gerado pelo Postgres). Um `DROP CONSTRAINT` com o nome errado falha silenciosamente em scripts com `IF EXISTS`.

---

## 3. Tabelas

Ordem: controle → tenants → contas → memória → ingestão → monitores → tendências → conteúdo → campanhas → tasks → publicações → governança → **infraestrutura** ([3.26](#326-infraestrutura-um-servidor-vários-serviços) a [3.31](#331-campos-novos-em-accounts-e-account_placements), migrações 0005/0006) → **identidade** ([3.32](#332-personas) em diante, migração 0007).

Convenções desta seção:
- Todas as colunas `id uuid` usam `DEFAULT uuid_generate_v4()`, exceto `events.id` (`bigserial`).
- `created_at`/`updated_at` são `timestamptz NOT NULL DEFAULT now()`. `updated_at` é mantido por trigger nas tabelas listadas em [3.25](#325-triggers-de-updated_at).
- "obrig." = `NOT NULL` sem default útil (precisa vir do código).

---

### 3.1 `schema_migrations`

Registro de quais migrações já rodaram neste banco. Lida e escrita apenas pelo runner de migração.

| Coluna | Tipo | Descrição |
|---|---|---|
| `version` | `text` PK | Nome do arquivo sem extensão: `0001_initial` |
| `applied_at` | `timestamptz` | Quando foi aplicada |
| `checksum` | `text` obrig. | SHA-256 do conteúdo do arquivo no momento da aplicação |

**Quando usar / armadilhas.** O `checksum` existe para detectar edição de migração já aplicada. O runner **aborta** se o hash do arquivo em disco divergir do gravado — é a defesa mecânica da regra da seção 7. Nunca "conserte" essa divergência dando `UPDATE` no checksum: ou você reverte o arquivo, ou escreve uma migração nova.

---

### 3.2 `clients` **[DORMENTE — Fase 5]**

Tenant. Existe porque clipagem é tipicamente serviço prestado a terceiros, e contas/campanhas precisarão ser atribuíveis a um contratante.

| Coluna | Tipo | Descrição |
|---|---|---|
| `id` | `uuid` PK | |
| `name` | `text` obrig. | Nome do cliente/contratante |
| `contract` | `jsonb` = `{}` | Termos operacionais: volume contratado, SLA, nichos permitidos, quem aprova |
| `active` | `boolean` = `true` | Contrato vigente |
| `created_at` / `updated_at` | `timestamptz` | `updated_at` por trigger |

**Quando usar / armadilhas.** Até a Fase 5, toda linha do sistema tem `client_id IS NULL` — o "cliente" é a própria casa. **Não** escreva query que assuma `client_id NOT NULL`, e **não** use `client_id` como chave de particionamento de permissão antes da Fase 5: o modelo de autorização multi-tenant ainda não foi desenhado (`contract` é jsonb livre justamente para não congelar isso cedo).

---

### 3.3 `accounts`

**1 linha = 1 login numa plataforma.** Tudo que o Olimpo faz é atribuível a uma linha desta tabela.

> ⚠️ Até a migração 0006, a frase acima era *"1 conta = 1 agente"*, e `accounts` era também a identidade. **Não é mais.** A identidade é [`personas`](#332-personas) ([ADR-0013](adr/0013-persona-separada-da-conta.md)): uma persona pode ter vários logins (o Instagram e o WhatsApp da mesma personagem), e um login pode não ter persona nenhuma (as coletoras). Os campos que a 0007 acrescentou estão em [3.34](#334-campos-novos-em-accounts-migração-0007).

| Coluna | Tipo | Descrição |
|---|---|---|
| `id` | `uuid` PK | |
| `handle` | `text` obrig. | @ da conta, sem o `@` |
| `platform` | `platform` obrig. | `instagram` \| `tiktok` \| `whatsapp` |
| `archetype` | `archetype` = `influencer` | Define qual `ContentSource` o loop usa no passo SOURCE |
| `status` | `account_status` = `registered` | **Situação:** o que *nós* queremos da conta. Era `draft` até a 0007 — ver [3.35](#335-situação--presença-dois-eixos-ortogonais) |
| `autonomy` | `autonomy_level` = `L0` | Quanto o agente pode fazer sem humano |
| `client_id` | `uuid` → `clients` | **[DORMENTE]** `ON DELETE SET NULL` |
| `persona_card` | `jsonb` = `{}` | **[HERANÇA — removida na 0008]** Use [`personas.persona_card`](#332-personas). Marcada por `COMMENT ON COLUMN` |
| `persona_version` | `integer` = `1` | **[HERANÇA]** Espelha o card antigo. A versão vigente é `personas.persona_version` |
| `persona_engine_id` | `text` | **[HERANÇA]** ID na **máquina de influencers**; a fonte é `personas.persona_engine_id` |
| `device_binding` | `jsonb` = `{}` | **[HERANÇA — removida na 0008]** Use `account_placements`. Marcada por `COMMENT ON COLUMN` |
| `cadence` | `jsonb` = `{"posts_per_day":1,"stories_per_day":3}` | Ritmo natural. **Teto** — campanha não pode ultrapassar |
| `timezone` | `text` = `America/Sao_Paulo` | Fuso da persona, não do servidor |
| `active_hours` | `int4range` = `[8,23)` | Janela "acordada" em horas locais. Fora dela o agente não age |
| `mood` | `jsonb` = `{}` | Estado dinâmico (humor, energia). Entra no prompt e modula tom e frequência |
| `followers` | `integer` = `0` | Último valor coletado. Usado em `campaigns.account_filter` |
| `warmup_started_at` | `timestamptz` | Início do aquecimento — base para promover `warming` → `active` |
| `last_action_at` | `timestamptz` | Última ação executada; alimenta rate limit e detecção de conta parada |
| `notes` | `text` | Anotação humana livre |
| `created_at` / `updated_at` | `timestamptz` | `updated_at` por trigger |

Restrição: `UNIQUE (platform, handle)` — o mesmo @ pode existir no Instagram e no TikTok como dois logins. Desde a 0007, os dois podem pertencer à **mesma persona**, e aí a memória é compartilhada.

> A tabela acima é a da migração 0001. Os campos de **inventário** (`external_id`, `following`, `media_count`, `jid`, `last_synced_at`, `discovered_from`) vieram na 0006 e estão em [3.31](#331-campos-novos-em-accounts-e-account_placements); os de **identidade e operação** (`persona_id`, `role`, `profile`, `secret_ref`, `labels`, `metadata`, `last_seen_in_inventory`, `previous_handles`) vieram na 0007 e estão em [3.34](#334-campos-novos-em-accounts-migração-0007).

**Quando usar / armadilhas.**
- `active_hours` é `int4range` de **horas inteiras locais**, não `timestamptz`. `[8,23)` = das 08:00 às 22:59. Range que cruza a meia-noite (ex.: madrugada de conta noturna) **não** é representável em um único range — use `[0,3)` + política no código, ou reveja a persona. Isto é uma limitação conhecida do schema v1.
- `cadence` aqui é o **teto operacional**; `persona_card.cadence` é a **intenção editorial**. Divergiram? O banco vence. O painel deve mostrar os dois.
- `mood` é jsonb sem schema fixo de propósito: o formato ainda está sendo descoberto na Fase 1/3. Não crie índice nele antes de estabilizar.
- **`device_binding` é herança e não é fonte da verdade.** Onde a conta vive passou a ser `account_placements` (+ `devices`, migrações 0002/0005): a mesma conta pode estar logada em vários containers, o que um jsonb escalar não representa. Leia dos placements; a 0007 marcou a coluna com `COMMENT ON COLUMN` e a 0008 a remove.
- **`persona_card` também é herança**, pelo mesmo mecanismo e com o mesmo prazo. A janela em que as duas colunas convivem é o custo declarado da [ADR-0013](adr/0013-persona-separada-da-conta.md): uma consulta escrita por hábito lê o card velho e **não falha** — devolve dado desatualizado em silêncio. Ao tocar em qualquer código que leia persona, confira se ele já lê de `personas`.
- Deletar uma `account` cascateia conteúdo, posts, interações, tasks, aprovações e violações — e **não** apaga memória, arcos nem planos, que desde a 0007 pertencem à persona. **Nunca delete conta perdida — use `status = 'lost'`.** O `DELETE` existe para limpeza de ambiente de teste, e a API exige `confirm=apagar-de-verdade`.
- **Ausência de inventário não é ausência de conta.** Se todos os logins saírem dos devices, somem os `account_placements`; a conta, a persona, as observações e o histórico continuam. Ver [3.35](#335-situação--presença-dois-eixos-ortogonais).

---

### 3.4 `persona_versions`

Histórico imutável do Persona Card. Responde "por que ela mudou de voz na semana passada?".

| Coluna | Tipo | Descrição |
|---|---|---|
| `id` | `uuid` PK | |
| `account_id` | `uuid` → `accounts` obrig. | `ON DELETE CASCADE` |
| `version` | `integer` obrig. | Inteiro monotônico por conta |
| `persona_card` | `jsonb` obrig. | Snapshot **completo** do card nessa versão (não é diff) |
| `changed_by` | `text` | `human:chris` \| `agent:reflect` \| `migration:0007` |
| `change_note` | `text` | Justificativa em texto livre — obrigatória por convenção do painel |
| `created_at` | `timestamptz` | |

Restrição: `UNIQUE (account_id, version)`.

**Quando usar / armadilhas.** Grave a versão **antes** de sobrescrever o card, na mesma transação. Snapshot completo é escolha consciente: diffs economizam bytes e custam a capacidade de reconstruir o prompt exato de um post antigo. Nunca faça `UPDATE` em uma linha aqui — é append-only.

> ⚠️ **A tabela continua ancorada em `account_id`, mas o card mudou de casa.** Desde a 0007 o card vive em `personas.persona_card` e é editado por `PATCH /v1/personas/{id}`, que incrementa `personas.persona_version` e grava **uma linha por conta vinculada** à persona. Uma persona com três logins gera três linhas por bump, com o mesmo `version` e o mesmo snapshot. Funciona e é auditável, mas é modelagem torta: o alvo natural é `persona_versions.persona_id`, e está registrado como dívida da 0008 ([ADR-0013](adr/0013-persona-separada-da-conta.md) § Consequências).

---

### 3.5 `story_arcs`

Arco narrativo em curso ("treinando para a maratona", "mudança de cidade"). É o que impede a conta de virar um gerador de posts sem continuidade.

| Coluna | Tipo | Descrição |
|---|---|---|
| `id` | `uuid` PK | |
| `account_id` | `uuid` → `accounts` obrig. | `ON DELETE CASCADE`. **[HERANÇA desde a 0007]** — o arco é da personagem |
| `persona_id` | `uuid` → `personas` | `ON DELETE CASCADE`. Coluna da 0007; é por aqui que se lê |
| `title` | `text` obrig. | Nome curto do arco |
| `summary` | `text` obrig. | Sinopse injetada no prompt de PLAN |
| `state` | `jsonb` = `{}` | Onde está no arco: fase, marcos batidos, próximos beats |
| `starts_at` | `date` obrig. | |
| `ends_at` | `date` | `NULL` = arco aberto |
| `active` | `boolean` = `true` | |
| `created_at` / `updated_at` | `timestamptz` | `updated_at` por trigger |

**Quando usar / armadilhas.** Nada impede vários arcos ativos por persona — e isso é intencional (um arco pessoal + um profissional). Mas **mais de 2–3 arcos ativos degradam o plano do dia**: o prompt fica com objetivos conflitantes e a LLM produz posts genéricos. Trate `count(*) WHERE active` > 3 como alerta operacional. Encerrar arco é `active = false` + `ends_at`, nunca `DELETE` (memórias apontam para ele).

Desde a 0007, `account_id` e `persona_id` coexistem: a coluna antiga é herança, e o índice novo é `idx_arcs_persona` (parcial em `active`). Um arco escrito só com `account_id` some da leitura por persona — preencha `persona_id` sempre.

---

### 3.6 `memories`

O *memory stream* do blueprint generative_agents. Tudo que o agente sabe sobre si e sobre o mundo dele.

| Coluna | Tipo | Descrição |
|---|---|---|
| `id` | `uuid` PK | |
| `account_id` | `uuid` → `accounts` obrig. | `ON DELETE CASCADE`. **[HERANÇA desde a 0007]** |
| `persona_id` | `uuid` → `personas` | `ON DELETE CASCADE`. Coluna da 0007 — **a memória é da personagem, não do login** |
| `kind` | `memory_kind` obrig. | `episodic` \| `semantic` \| `narrative` |
| `content` | `text` obrig. | A memória em linguagem natural, primeira pessoa |
| `embedding` | `vector(1024)` | Embedding de `content`. `NULL` = ainda não indexada |
| `importance` | `real` = `5.0` | 0..10, atribuída pela LLM **na escrita** |
| `metadata` | `jsonb` = `{}` | Refs: post_id, handle do seguidor, task_id, fonte |
| `arc_id` | `uuid` → `story_arcs` | `ON DELETE SET NULL` |
| `occurred_at` | `timestamptz` = `now()` | Quando o fato aconteceu (≠ quando foi gravado) |
| `last_used_at` | `timestamptz` | Última vez que foi recuperada para um prompt |
| `created_at` | `timestamptz` | Quando foi gravada |

**Quando usar / armadilhas.**
- `vector(1024)` fixa a dimensão do modelo de embedding. **Trocar de modelo de embedding exige migração e re-embedding de tudo** — não é configuração, é decisão de schema. Modelo escolhido e sua dimensão devem estar registrados em [docs/09](09-llm-gateway.md).
- `occurred_at` vs `created_at`: uma reflexão escrita à noite sobre a manhã tem `occurred_at` de manhã. A recência da recuperação usa `occurred_at`. Confundir os dois faz o agente achar que tudo aconteceu na hora da reflexão.
- `importance` é atribuída **uma vez**, na escrita, pela LLM que criou a memória. Não recalcule em lote: isso reescreve a história do agente.
- `last_used_at` não participa do ranking na v1 — é telemetria para decidir política de esquecimento (Fase 6). Escreva, não leia.
- O índice HNSW cobre `embedding`, mas as consultas reais **sempre** filtram por dono primeiro. Memória de uma personagem nunca pode vazar para outra: esse `WHERE` é regra de segurança, não otimização.
- **Desde a 0007 o dono é `persona_id`**, não `account_id` — e a mudança é o ponto da [ADR-0013](adr/0013-persona-separada-da-conta.md): se a memória ficasse presa ao login, perder uma conta apagaria a história, e a mesma personagem no Instagram e no WhatsApp lembraria de coisas diferentes. Índice novo: `idx_memories_persona (persona_id, kind, occurred_at DESC)`. As consultas de referência da [seção 6](#6-consultas-de-referência) ainda estão escritas com `account_id` — leia `persona_id` onde o dono for a personagem.

---

### 3.7 `day_plans`

Saída do passo **PLAN**: o que esta conta pretende fazer hoje e por quê. Uma linha por conta por dia.

| Coluna | Tipo | Descrição |
|---|---|---|
| `id` | `uuid` PK | |
| `account_id` | `uuid` → `accounts` obrig. | `ON DELETE CASCADE`. **[HERANÇA desde a 0007]** |
| `persona_id` | `uuid` → `personas` | `ON DELETE CASCADE`. Coluna da 0007 — o dia é da personagem, e vale para todos os logins dela |
| `plan_date` | `date` obrig. | Data **no fuso da conta**, não em UTC |
| `narrative` | `text` obrig. | "Hoje ela está na praia, humor alto, foco em leveza" |
| `intents` | `jsonb` obrig. | Lista de intenções que viram `content_items` e `tasks` |
| `reasoning` | `text` | Por que este plano — auditoria e depuração de prompt |
| `llm_call_id` | `uuid` → `llm_calls` | `ON DELETE SET NULL`. Rastreia custo/latência do plano |
| `created_at` | `timestamptz` | |

Restrição: `UNIQUE (account_id, plan_date)`.

**Quando usar / armadilhas.** O `UNIQUE` é uma trava de idempotência: rodar o PLAN duas vezes no mesmo dia deve dar conflito, não duplicar o dia. Use `INSERT ... ON CONFLICT DO NOTHING` e trate o conflito como "já planejado". Replanejar (evento novo, campanha urgente) é `UPDATE` da linha existente + `event` registrando o replanejamento — **nunca** delete e recrie, ou os `content_items` perdem `day_plan_id`.

`plan_date` derivado de `accounts.timezone`: uma conta em `America/Sao_Paulo` que planeja às 23:30 local está planejando o dia D, não D+1 (que é o que UTC diria). O fuso continua sendo **do login**, porque é ele que age; o plano é **da persona**.

> A restrição de unicidade continua sendo `(account_id, plan_date)` — não existe `UNIQUE (persona_id, plan_date)`. Uma persona com dois logins pode ter dois planos no mesmo dia, e isso é intencional enquanto o loop roda por conta. ❓ Se o PLAN passar a rodar por persona, esta unicidade precisa ser revista na mesma migração.

---

### 3.8 `sources` **[DORMENTE — Fase 4]**

Catálogo de fontes de conteúdo externo. É cadastro estático: *o que existe para observar*.

| Coluna | Tipo | Descrição |
|---|---|---|
| `id` | `uuid` PK | |
| `kind` | `source_kind` obrig. | `ig_account`, `hashtag`, `rss`, `youtube_channel`, `live_stream`, ... |
| `endpoint` | `text` obrig. | `@handle`, `#tag`, URL, id de canal |
| `niche` | `text` | Nicho ao qual a fonte serve |
| `reliability` | `real` = `0.5` | 0..1. **Alimenta o `claim_status` do conteúdo derivado** |
| `active` | `boolean` = `true` | |
| `metadata` | `jsonb` = `{}` | Credenciais de leitura, seletores, idioma |
| `created_at` | `timestamptz` | |

Restrição: `UNIQUE (kind, endpoint)`.

**Quando usar / armadilhas.** Distinção que o schema faz de propósito: `sources` é **catálogo**, `monitors` é **processo vivo**. Uma fonte sem monitor nunca é lida. Um monitor sem fonte (`source_id NULL`) é válido — é o caso do monitor que vigia algo ad-hoc.

`reliability` não é enfeite: na Fase 4 ele é a entrada do cálculo que decide se uma afirmação sai como `reported` ou `rumor`. Cadastrar tudo com 0.5 anula o mecanismo.

---

### 3.9 `ingested_items` **[DORMENTE — Fase 4]**

Item bruto coletado de uma fonte, antes de virar conteúdo. É onde o "furo" aterrissa.

| Coluna | Tipo | Descrição |
|---|---|---|
| `id` | `uuid` PK | |
| `source_id` | `uuid` → `sources` | `ON DELETE SET NULL` |
| `monitor_id` | `uuid` → `monitors` | FK adicionada via `ALTER TABLE`; `ON DELETE SET NULL` |
| `dedup_hash` | `text` obrig. **UNIQUE** | Hash do conteúdo normalizado — impede reprocessar o mesmo furo |
| `payload` | `jsonb` obrig. | O item como veio: texto, autor, timestamps, URL |
| `media_refs` | `jsonb` = `[]` | Refs de mídia baixada/screenshot |
| `relevance` | `real` | 0..1, quão relevante para o nicho |
| `novelty` | `real` | 0..1, quão novo vs. o que já vimos |
| `consumed_by` | `uuid` → `accounts` | Conta que já usou este item. `NULL` = disponível |
| `ingested_at` | `timestamptz` | |

**Quando usar / armadilhas.** O `UNIQUE (dedup_hash)` é a defesa contra o mesmo furo chegando por cinco fontes. Consequência prática: a inserção **deve** usar `ON CONFLICT (dedup_hash) DO NOTHING` — se o código explodir na colisão, o monitor entra em loop de erro. O hash precisa ser de conteúdo *normalizado* (sem UTM, sem timestamp de coleta), senão dedup não dedupa nada.

`consumed_by` é escalar, ou seja, **um item é consumido por no máximo uma conta**. Isso é intencional: duas páginas de fofoca da casa postando o mesmo furo com o mesmo enquadramento é sinal de farm. Se um dia for necessário compartilhamento, será tabela de junção — não troque para array.

---

### 3.10 `monitors` **[DORMENTE — Fase 4]**

O vigia persistente: checa um alvo em intervalo curto, detecta novidade e dispara produção. É o processo, não o catálogo.

| Coluna | Tipo | Descrição |
|---|---|---|
| `id` | `uuid` PK | |
| `name` | `text` obrig. | Nome operacional ("perfis de TV aberta") |
| `owner_account_id` | `uuid` → `accounts` | Conta dona do resultado. `ON DELETE CASCADE` |
| `source_id` | `uuid` → `sources` | `ON DELETE SET NULL` |
| `status` | `monitor_status` = `active` | `active` \| `paused` \| `error` \| `disabled` |
| `interval_seconds` | `integer` = `300` | Período de checagem |
| `collector_pool` | `jsonb` = `[]` | Lista de contas Android que fazem a coleta, em rodízio |
| `trigger_rules` | `jsonb` = `{}` | O que conta como "novidade que merece post" |
| `last_check_at` | `timestamptz` | |
| `next_check_at` | `timestamptz` | **Coluna que o scheduler consulta** |
| `consecutive_errors` | `integer` = `0` | Zera no sucesso; ao estourar o limite → `status = 'error'` |
| `created_at` / `updated_at` | `timestamptz` | `updated_at` por trigger |

**Quando usar / armadilhas.** `collector_pool` existe por uma razão específica: uma única conta Android checando 40 perfis a cada 5 minutos é comportamento não-humano e queima a conta. O rodízio é obrigatório, não opcional — deixar a pool com um elemento só anula o mecanismo.

`interval_seconds` baixo (< 60) multiplica ações no farm. O teto real não é este campo, é `ACCOUNT_MAX_ACTIONS_PER_HOUR` por conta coletora: com 12 ações/hora por conta, um monitor de 300s precisa de pelo menos 1 conta dedicada.

`consecutive_errors` deve mover o monitor para `error` e **parar de agendar**, não continuar tentando. Monitor em `error` é item de runbook.

---

### 3.11 `monitor_hits` **[DORMENTE — Fase 4]**

Cada checagem que encontrou algo. É o log de detecção: liga "o monitor viu isto" a "virou este conteúdo".

| Coluna | Tipo | Descrição |
|---|---|---|
| `id` | `uuid` PK | |
| `monitor_id` | `uuid` → `monitors` obrig. | `ON DELETE CASCADE` |
| `ingested_id` | `uuid` → `ingested_items` | `ON DELETE SET NULL` |
| `triggered` | `boolean` = `false` | Passou nas `trigger_rules`? |
| `trigger_reason` | `text` | Qual regra disparou (ou por que não disparou) |
| `content_item_id` | `uuid` → `content_items` | FK via `ALTER TABLE`. Conteúdo que nasceu deste hit |
| `detected_at` | `timestamptz` | |

**Quando usar / armadilhas.** Grave hits **também quando `triggered = false`**. É o que permite ajustar `trigger_rules` com dado real ("nas últimas 24h, 300 hits, 4 disparos, e os 3 furos que perdemos estavam entre os não-disparados"). Sem os negativos, calibrar regra vira chute.

Volume: um monitor de 300s gera ~288 hits/dia. Dez monitores = ~3k linhas/dia. Planeje retenção/particionamento antes da Fase 4 virar produção.

---

### 3.12 `trends`

Sinais de tendência da plataforma (áudio em alta, hashtag, formato, tópico). Compartilhado pelos três arquétipos; sem FK para conta.

| Coluna | Tipo | Descrição |
|---|---|---|
| `id` | `uuid` PK | |
| `platform` | `platform` obrig. | |
| `kind` | `text` obrig. | `audio` \| `hashtag` \| `format` \| `topic` (texto livre por escolha) |
| `ref` | `text` obrig. | ID do áudio, texto da hashtag, nome do formato |
| `label` | `text` | Rótulo legível |
| `score` | `real` = `0` | Força da tendência |
| `metadata` | `jsonb` = `{}` | Origem do sinal, contadores, amostras |
| `window_start` | `timestamptz` = `now()` | Início da janela de observação |
| `window_end` | `timestamptz` | `NULL` = **tendência viva** |
| `created_at` | `timestamptz` | |

**Quando usar / armadilhas.** `kind` é `text` e não ENUM porque tendência é o domínio que mais muda — congelar em ENUM garantiria migração a cada trimestre. O preço é que o código precisa validar; mantenha a lista canônica em um só lugar no Python.

`window_end IS NULL` é o marcador de "viva" e é o que o índice parcial usa. Encerrar tendência é **preencher** `window_end`, nunca deletar: o histórico de qual áudio estava em alta quando um post foi feito é insumo de análise (Fase 6).

Não há unicidade em `(platform, kind, ref)` — o mesmo áudio pode ter várias janelas ao longo do tempo. Consultar sem `window_end IS NULL` traz lixo histórico.

---

### 3.13 `content_items`

A peça de conteúdo em produção: da ideia ao publicado. É onde QA, direitos e status epistêmico moram.

| Coluna | Tipo | Descrição |
|---|---|---|
| `id` | `uuid` PK | |
| `account_id` | `uuid` → `accounts` obrig. | `ON DELETE CASCADE` |
| `day_plan_id` | `uuid` → `day_plans` | De qual plano nasceu. `ON DELETE SET NULL` |
| `campaign_id` | `uuid` → `campaigns` | FK via `ALTER TABLE`. `ON DELETE SET NULL` |
| `kind` | `task_type` obrig. | Reusa o ENUM: `publish_post` \| `publish_story` \| `publish_reels` |
| `status` | `content_status` = `draft` | Ver máquina de estados em [4.7](#47-content_status) |
| `caption` | `text` | Texto final na voz da persona |
| `hashtags` | `text[]` | Sem o `#` |
| `audio_ref` | `text` | Áudio da plataforma (normalmente vindo de `trends.ref`) |
| `media_refs` | `jsonb` = `[]` | Refs no storage / máquina de influencers |
| `source_strategy` | `text` = `generated` | `generated` \| `curated` \| `derived` — qual `ContentSource` produziu |
| `source_refs` | `jsonb` = `[]` | **[DORMENTE]** De onde veio (URL, conta, live, `ingested_items.id`) |
| `attribution` | `text` | **[DORMENTE]** Crédito obrigatório ao criador original (clipagem) |
| `rights_status` | `rights_status` = `own` | `own` \| `licensed` \| `fair_use` \| `unknown`. `unknown` **bloqueia** publicação |
| `claim_status` | `claim_status` = `unverified` | **[DORMENTE]** Status epistêmico; `unverified` bloqueia em L≥1 |
| `qa_report` | `jsonb` = `{}` | Saída do passo QA: checks, scores, motivo de reprovação |
| `reasoning` | `text` | Por que este conteúdo — auditoria |
| `scheduled_for` | `timestamptz` | Quando deve ir ao ar |
| `created_at` / `updated_at` | `timestamptz` | `updated_at` por trigger |

**Quando usar / armadilhas.**
- `kind` reusar `task_type` é economia consciente de ENUM, mas **permite valores absurdos** (`kind = 'like'`). Não há CHECK no banco na v1 — a validação é do código. Se isso vazar em produção, a correção é um `CHECK constraint` em migração nova.
- Os defaults `rights_status = 'own'` e `claim_status = 'unverified'` estão calibrados para o arquétipo **influencer** (conteúdo gerado por nós, sem afirmação factual). Para fofoca e clipagem, o `ContentSource` **tem que** sobrescrever explicitamente. Um item de fofoca salvo com `own`/`unverified` por inércia é exatamente o bug jurídico que a Fase 4 precisa evitar.
- `content_items` é o **draft**; `posts` é o **publicado**. Não é a mesma coisa e não devem ser fundidas: um item pode ser reprovado, reagendado ou republicado.
- `scheduled_for` aqui é a **intenção editorial**; `tasks.scheduled_for` é o **agendamento real após jitter**. Divergem de propósito. O painel deve mostrar o da task.

---

### 3.14 `campaigns`

Brief coordenado emitido pelo manager. Cada conta elegível traduz o brief pela própria realidade.

| Coluna | Tipo | Descrição |
|---|---|---|
| `id` | `uuid` PK | |
| `name` | `text` obrig. | |
| `kind` | `campaign_kind` = `content` | `content` \| `engagement` \| `mixed` |
| `status` | `campaign_status` = `draft` | Ver máquina de estados em [4.8](#48-campaign_status) |
| `client_id` | `uuid` → `clients` | **[DORMENTE]** `ON DELETE SET NULL` |
| `brief` | `text` obrig. | O que o humano pediu, em português |
| `target_url` | `text` | Alvo externo a inspecionar (campanha de engajamento) |
| `target_handle` | `text` | Perfil alvo |
| `brand_refs` | `jsonb` = `[]` | Assets de marca para a máquina de influencers |
| `audio_ref` | `text` | Áudio obrigatório da campanha |
| `account_filter` | `jsonb` = `{}` | Elegibilidade: nicho, arquétipo, followers mínimo, plataforma |
| `spread_policy` | `jsonb` **obrig., sem default** | Política anti-detecção. Ver [docs/06](06-campanhas.md) |
| `window_start` | `timestamptz` obrig. | |
| `window_end` | `timestamptz` obrig. | |
| `created_by` | `text` | Quem emitiu |
| `created_at` / `updated_at` | `timestamptz` | `updated_at` por trigger |

Restrição: `CHECK (window_end > window_start)`.

**Quando usar / armadilhas.** `spread_policy` é `NOT NULL` **sem default** — é a única coluna do schema com essa combinação, e é intencional: é impossível criar campanha sem declarar espalhamento. `{}` passa no banco mas **tem que** ser rejeitado na validação da aplicação (jitter, participação parcial e limite de grafo são obrigatórios). Ver [docs/06 § Espalhamento](06-campanhas.md#espalhamento).

Campanha de engajamento (`kind IN ('engagement','mixed')` com `target_url`) **não pode** sair de `draft` direto para `planning`: passa obrigatoriamente por `recon_pending`. Planejar sem ver o alvo é o cenário em que 20 contas comentam num post que virou crime/tragédia.

---

### 3.15 `campaign_recon`

O resultado de "mandar uma conta Android abrir o conteúdo alvo e olhar". Separa o que foi **capturado** do que foi **entendido**.

| Coluna | Tipo | Descrição |
|---|---|---|
| `id` | `uuid` PK | |
| `campaign_id` | `uuid` → `campaigns` obrig. | `ON DELETE CASCADE` |
| `task_id` | `uuid` → `tasks` | FK via `ALTER TABLE`. A task `extract_content` que coletou |
| `collector_account_id` | `uuid` → `accounts` | Conta que fez a coleta. `ON DELETE SET NULL` |
| `raw_capture` | `jsonb` = `{}` | Bruto: caption, autor, contadores, data |
| `media_refs` | `jsonb` = `[]` | Screenshots / vídeo baixado |
| `top_comments` | `jsonb` = `[]` | Comentários em destaque — sinal de clima |
| `analysis` | `jsonb` = `{}` | Entendido pela LLM multimodal: tema, tom, público, gancho |
| `summary` | `text` | Resumo em uma frase |
| `sentiment` | `text` | Clima geral |
| `safety_verdict` | `text` | `safe` \| `risky` \| `blocked` |
| `safety_reasons` | `text[]` | Motivos do veredito |
| `suggested_angles` | `jsonb` = `[]` | Ângulos de comentário para as personas adaptarem |
| `llm_call_id` | `uuid` → `llm_calls` | `ON DELETE SET NULL` |
| `captured_at` | `timestamptz` | Quando a conta Android olhou |
| `analyzed_at` | `timestamptz` | Quando a LLM analisou |
| `created_at` | `timestamptz` | |

**Quando usar / armadilhas.** A separação `raw_capture` / `analysis` e `captured_at` / `analyzed_at` existe porque as duas etapas falham por motivos diferentes: captura falha por device/login, análise falha por modelo. Uma linha com `captured_at` preenchido e `analyzed_at NULL` é "coletado, aguardando análise" — estado normal, não erro.

`safety_verdict = 'blocked'` **aborta a campanha**, não apenas alerta. Essa transição (`recon_pending`/`recon_done` → `aborted`) é o principal freio de mão do subsistema.

Não há `UNIQUE (campaign_id)`: recon pode ser refeito (alvo mudou, captura falhou). Consulte sempre o mais recente por `created_at DESC`, ou o painel mostrará um recon velho.

---

### 3.16 `campaign_participants`

Quem entra na campanha, o que cada um faz e **quando** — o resultado material do espalhamento.

| Coluna | Tipo | Descrição |
|---|---|---|
| `id` | `uuid` PK | |
| `campaign_id` | `uuid` → `campaigns` obrig. | `ON DELETE CASCADE` |
| `account_id` | `uuid` → `accounts` obrig. | `ON DELETE CASCADE` |
| `local_adaptation` | `text` | Como esta persona traduziu o brief para a própria realidade |
| `actions` | `jsonb` = `[]` | `[{type:'comment', text:'...', at:'...'}]` |
| `slot_at` | `timestamptz` obrig. | Slot sorteado dentro da janela (jitter) |
| `skipped` | `boolean` = `false` | Conta sorteada mas fora |
| `skip_reason` | `text` | Por que ficou fora (cadência, nicho, rate limit, humor) |
| `created_at` | `timestamptz` | |

Restrição: `UNIQUE (campaign_id, account_id)`.

**Quando usar / armadilhas.** Registre também os **pulados** (`skipped = true` + `skip_reason`). Participação parcial é regra anti-detecção; sem o registro do "não", é impossível auditar se o sorteio está realmente sorteando ou se o filtro está sempre pegando as mesmas 5 contas.

`slot_at` deve ser gerado com jitter dentro de `[window_start, window_end)` respeitando `CAMPAIGN_JITTER_MIN_MINUTES`. Dois `slot_at` a menos de ~25 minutos entre contas da casa é o padrão que denuncia farm.

---

### 3.17 `tasks`

A ponte com o android-orchestrator. Toda ação com efeito no mundo — e algumas puramente internas — é uma linha aqui.

| Coluna | Tipo | Descrição |
|---|---|---|
| `id` | `uuid` PK | |
| `account_id` | `uuid` → `accounts` | `ON DELETE CASCADE`. `NULL` só para tasks de sistema |
| `type` | `task_type` obrig. | Ver [4.4](#44-task_type) |
| `status` | `task_status` = `pending` | Ver máquina de estados em [4.6](#46-task_status) |
| `priority` | `integer` = `100` | **Menor = mais urgente** |
| `origin` | `text` = `self` | `manager` \| `self` \| `human` \| `campaign` \| `monitor` |
| `payload` | `jsonb` = `{}` | Parâmetros da ação para o orchestrator |
| `content_item_id` | `uuid` → `content_items` | `ON DELETE SET NULL` |
| `campaign_id` | `uuid` → `campaigns` | `ON DELETE SET NULL` |
| `monitor_id` | `uuid` → `monitors` | `ON DELETE SET NULL` |
| `scheduled_for` | `timestamptz` = `now()` | **Quando pode ser despachada** (já com jitter) |
| `expires_at` | `timestamptz` | Depois disto a task perde valor → `expired` |
| `dispatched_at` | `timestamptz` | |
| `verified_at` | `timestamptz` | Preenchido só quando o orchestrator **verifica** |
| `external_task_id` | `text` | ID no android-orchestrator |
| `external_status` | `text` | Último status bruto reportado pelo orchestrator |
| `result` | `jsonb` = `{}` | Retorno da execução (ids, screenshots, erros) |
| `attempts` | `integer` = `0` | |
| `max_attempts` | `integer` = `3` | |
| `last_error` | `text` | |
| `created_at` / `updated_at` | `timestamptz` | `updated_at` por trigger |

**Quando usar / armadilhas.**
- `verified` significa **verificado**, não "enviado com sucesso". O orchestrator confirma por leitura de tela. Tratar `dispatched` como sucesso é o erro clássico: a conta pode ter caído em checkpoint e o post nunca existiu.
- `priority` invertido (menor = mais urgente) segue convenção de fila Unix. Resposta a comentário ≈ 10; post planejado ≈ 100; aquecimento ≈ 500.
- `expires_at` é a diferença entre um post de fofoca e um post de influencer: furo com 40 minutos não vale nada. Task sem `expires_at` nunca expira — correto para conteúdo evergreen, errado para reativo.
- `external_task_id` é a chave de idempotência do webhook. O handler deve ser `UPDATE ... WHERE external_task_id = $1 AND status IN ('dispatched','running')`, para que reentrega não reviva task cancelada.
- `account_id` é nullable, mas na prática quase toda task tem conta. Task sem conta não é contabilizada no rate limit por conta — não use isso para "furar a fila".

---

### 3.18 `posts`

O que efetivamente foi publicado numa plataforma. Só nasce depois de uma task `verified`.

| Coluna | Tipo | Descrição |
|---|---|---|
| `id` | `uuid` PK | |
| `account_id` | `uuid` → `accounts` obrig. | `ON DELETE CASCADE` |
| `content_item_id` | `uuid` → `content_items` | `ON DELETE SET NULL` |
| `task_id` | `uuid` → `tasks` | A task que publicou. `ON DELETE SET NULL` |
| `platform` | `platform` obrig. | |
| `external_id` | `text` | ID do post na plataforma |
| `permalink` | `text` | URL pública |
| `published_at` | `timestamptz` = `now()` | Momento real da publicação |
| `created_at` | `timestamptz` | |

**Quando usar / armadilhas.** `external_id` e `permalink` são nullable porque nem toda ação verificada devolve identificador (story, por exemplo). Não trate `external_id NULL` como falha.

Não há `UNIQUE (platform, external_id)` na v1 — reentrega de webhook pode duplicar. A guarda é no código: só crie `post` na transição `running → verified`, uma vez.

---

### 3.19 `post_metrics`

Série temporal de métricas por post. Uma linha por coleta, não uma linha por post.

| Coluna | Tipo | Descrição |
|---|---|---|
| `id` | `uuid` PK | |
| `post_id` | `uuid` → `posts` obrig. | `ON DELETE CASCADE` |
| `likes`, `comments`, `shares`, `saves`, `reach`, `views` | `integer` = `0` | Contadores no momento da coleta |
| `raw` | `jsonb` = `{}` | Payload bruto do orchestrator |
| `collected_at` | `timestamptz` | |

**Quando usar / armadilhas.** São valores **absolutos e cumulativos**, não deltas. Somar linhas dá número sem sentido; para crescimento, use `LAG()`/diferença entre coletas. Métrica "atual" é sempre `DISTINCT ON (post_id) ... ORDER BY collected_at DESC`.

Tabela de maior crescimento do sistema. Defina política de retenção (ex.: alta frequência nas primeiras 48h, depois diária) antes de escalar para dezenas de contas.

---

### 3.20 `interactions`

Comentários, DMs e menções recebidas — e como foram (ou não) respondidas.

| Coluna | Tipo | Descrição |
|---|---|---|
| `id` | `uuid` PK | |
| `account_id` | `uuid` → `accounts` obrig. | `ON DELETE CASCADE` |
| `post_id` | `uuid` → `posts` | `ON DELETE SET NULL`. `NULL` para DM |
| `kind` | `text` obrig. | `comment` \| `dm` \| `mention` |
| `external_id` | `text` | ID na plataforma |
| `author_handle` | `text` | Quem interagiu |
| `body` | `text` obrig. | Texto recebido |
| `classification` | `text` | `fan` \| `hater` \| `spam` \| `opportunity` \| `sensitive` |
| `sensitive` | `boolean` = `false` | **Nunca auto-respondido** em nenhum nível de autonomia |
| `reply_body` | `text` | Resposta enviada |
| `replied_by` | `text` | `agent` \| `human` \| `none` |
| `replied_at` | `timestamptz` | `NULL` = pendente |
| `received_at` | `timestamptz` | |
| `created_at` | `timestamptz` | |

**Quando usar / armadilhas.** `sensitive` é flag de segurança independente de `classification`: saúde, dinheiro, romance, assédio, menor de idade. Mesmo em **L3**, sensível vai para humano. Implemente essa checagem no dispatcher, não só no prompt — prompt não é guardrail.

`replied_by = 'none'` (decisão explícita de não responder) é diferente de `replied_at IS NULL` (ainda pendente). Marcar "não vou responder" fecha o item; deixar `NULL` mantém a fila crescendo para sempre.

`author_handle` é dado de terceiro real. Vale a política de retenção/anonimização de [docs/12](12-seguranca.md).

---

### 3.21 `approvals`

Fila de decisão humana. Polimórfica: vale para conteúdo, campanha ou interação.

| Coluna | Tipo | Descrição |
|---|---|---|
| `id` | `uuid` PK | |
| `subject_type` | `text` obrig. | `content_item` \| `campaign` \| `interaction` |
| `subject_id` | `uuid` obrig. | ID na tabela correspondente (**sem FK**) |
| `account_id` | `uuid` → `accounts` | `ON DELETE CASCADE` |
| `decision` | `text` | `approved` \| `rejected` \| `edited` |
| `decided_by` | `text` | Quem decidiu |
| `note` | `text` | Justificativa |
| `edited_payload` | `jsonb` | Versão corrigida pelo humano (quando `edited`) |
| `sampled` | `boolean` = `false` | `true` = revisão por amostragem (L2); `false` = exigência (L0/L1) |
| `requested_at` | `timestamptz` | |
| `decided_at` | `timestamptz` | `NULL` = aberta |

**Quando usar / armadilhas.** `subject_id` é polimórfico e **não tem integridade referencial**. Deletar o sujeito deixa a aprovação órfã. Aceito conscientemente (o alternativo seriam três tabelas quase idênticas), mas obriga o painel a lidar com sujeito inexistente.

`sampled` separa duas métricas que não podem se misturar: taxa de rejeição em revisão obrigatória (mede o agente sob supervisão) vs. em amostragem (mede o agente solto, e é o número que autoriza promover para L3).

`edited_payload` é ouro para treino/ajuste de prompt: é o humano mostrando exatamente o que estava errado.

---

### 3.22 `guardrail_violations`

Toda vez que uma regra de segurança foi acionada — bloqueando, sinalizando ou suspendendo.

| Coluna | Tipo | Descrição |
|---|---|---|
| `id` | `uuid` PK | |
| `account_id` | `uuid` → `accounts` | `ON DELETE CASCADE` |
| `subject_type` / `subject_id` | `text` / `uuid` | O que foi barrado (sem FK) |
| `rule` | `text` obrig. | ID estável da regra: `no_health_advice`, `claim_status_required` |
| `severity` | `text` obrig. | `info` \| `warn` \| `block` \| `kill` |
| `detail` | `jsonb` = `{}` | Trecho ofensor, score, contexto |
| `action_taken` | `text` | `blocked` \| `flagged` \| `suspended` |
| `created_at` | `timestamptz` | |

**Quando usar / armadilhas.** Grave violação mesmo em `severity = 'info'`. A curva de `info`/`warn` por conta é o **indicador antecedente** de que uma persona está derivando — muito antes do primeiro `block`.

`rule` precisa ser um identificador **estável**, versionado junto com o catálogo de regras em [docs/08](08-guardrails-e-autonomia.md). Se o texto da regra mudar de nome, a série histórica quebra.

`severity = 'kill'` implica `accounts.status = 'suspended'`. As duas escritas vão na mesma transação.

---

### 3.23 `events`

Auditoria total. Toda decisão do sistema vira uma linha aqui. É a tabela que responde "por que a conta X postou isso às 19h?".

| Coluna | Tipo | Descrição |
|---|---|---|
| `id` | `bigserial` PK | Sequencial — a ordem de inserção importa |
| `account_id` | `uuid` → `accounts` | `ON DELETE SET NULL` (evento sobrevive à conta) |
| `campaign_id` | `uuid` → `campaigns` | `ON DELETE SET NULL` |
| `task_id` | `uuid` → `tasks` | `ON DELETE SET NULL` |
| `type` | `text` obrig. | `plan.created`, `content.qa_failed`, `task.dispatched`, `post.verified` |
| `detail` | `jsonb` = `{}` | Contexto estruturado do evento |
| `trace_id` | `text` | **Correlaciona toda a cadeia de uma decisão** |
| `created_at` | `timestamptz` | |

**Quando usar / armadilhas.**
- `bigserial` e não `uuid`: aqui a ordem total de inserção é a feature principal. `ORDER BY id` é a linha do tempo real, imune a relógio.
- `ON DELETE SET NULL` em todas as FKs é deliberado: apagar uma conta **não** pode apagar a trilha de auditoria dela.
- `trace_id` é o campo mais importante da tabela e o mais fácil de esquecer. Ele nasce no PLAN e **precisa ser propagado** por content_item → task → dispatch → webhook → post. Um `trace_id` que morre no meio transforma auditoria em adivinhação. Use o mesmo valor em `llm_calls.trace_id`.
- `type` é hierárquico por convenção (`dominio.acao`), o que permite `LIKE 'task.%'`. Mantenha o catálogo em [docs/11](11-observabilidade.md).
- Append-only. Nunca `UPDATE`, nunca `DELETE` fora de política de retenção declarada.

---

### 3.24 `llm_calls`

Custo, latência e resultado de cada chamada de modelo. Mac mini vs. API externa.

| Coluna | Tipo | Descrição |
|---|---|---|
| `id` | `uuid` PK | |
| `account_id` | `uuid` → `accounts` | `ON DELETE SET NULL` |
| `purpose` | `text` obrig. | `plan_day` \| `caption` \| `reply_comment` \| `recon_analysis` \| `reflect` |
| `model` | `text` obrig. | Nome do modelo |
| `backend` | `text` obrig. | `mac_mini_1` \| `mac_mini_2` \| `anthropic` \| `openai` |
| `prompt_tokens` / `output_tokens` | `integer` | |
| `latency_ms` | `integer` | |
| `cost_usd` | `numeric(10,6)` = `0` | **0 para backend local** — por isso `backend` é obrigatório |
| `success` | `boolean` = `true` | |
| `error` | `text` | |
| `trace_id` | `text` | Mesmo `trace_id` de `events` |
| `created_at` | `timestamptz` | |

**Quando usar / armadilhas.** `cost_usd = 0` em backend local é correto e desejado, mas torna "custo total" um número enganoso: o gargalo dos Mac minis aparece em `latency_ms`, não em dólar. Monitore os dois eixos — dólar para overflow externo (`LLM_DAILY_BUDGET_USD`), latência para capacidade local.

`numeric(10,6)` suporta até ~9.999,999999 USD por chamada. Suficiente com folga; nunca use `float` para dinheiro.

Registre **também** as chamadas que falharam (`success = false`). Taxa de falha por backend é o sinal que dispara failover no LiteLLM.

Ligações: `day_plans.llm_call_id` e `campaign_recon.llm_call_id` apontam para cá (FKs adicionadas no fim da migração), permitindo ir do artefato à chamada que o produziu.

---

### 3.25 Triggers de `updated_at`

A função `touch_updated_at()` é aplicada via `CREATE TRIGGER ... BEFORE UPDATE` a **sete** tabelas da migração 0001:

`clients` · `accounts` · `story_arcs` · `monitors` · `content_items` · `campaigns` · `tasks`

**Armadilha:** as demais tabelas **não têm** `updated_at` — são append-only por design (`events`, `llm_calls`, `memories`, `persona_versions`, `post_metrics`, `monitor_hits`, `guardrail_violations`) ou têm marco temporal próprio (`approvals.decided_at`, `interactions.replied_at`, `campaign_recon.analyzed_at`). Ao criar tabela nova, decida explicitamente em qual grupo ela entra e, se mutável, **adicione o trigger na mesma migração** — o bloco `DO $$` original não é reexecutado.

As tabelas de infraestrutura da migração 0005 (`servers`, `server_services`, `devices`) **têm** o trigger, declarado na própria migração. `inventory_syncs` não tem: é append-only.

Da migração 0007: `personas` **tem** o trigger (`trg_personas_touch`) — o card é editado e o `updated_at` importa. `entity_notes` **não tem**: é append-only, só `created_at`. Anotação não se edita; escreve-se outra.

---

### 3.26 Infraestrutura: um servidor, vários serviços

> Fonte: [`0005_servers_and_services.sql`](../db/migrations/0005_servers_and_services.sql) e [`0006_account_inventory.sql`](../db/migrations/0006_account_inventory.sql). Decisão em [ADR-0012](adr/0012-servidor-com-varios-servicos.md).

Até a migração 0004, "nó" era sinônimo de "uma instância do android-orchestrator": uma URL, uma credencial, uma saúde. O contato com um host real derrubou isso. O **mesmo IP** roda o orchestrator (worker `:8078`), o android-manager (backend `:4000` e nginx `:443`, que serve a interface e proxia `/api`) e o gateway de tela (`:9000`) — endereços, credenciais, TLS e ciclos de vida diferentes, de um único servidor físico.

```
   ┌───────────┐
   │  servers  │  um HOST: identidade, endereços, rótulos
   └─────┬─────┘
         ├──────────────┐
         ▼              ▼
┌──────────────────┐  ┌──────────┐
│ server_services  │  │ devices  │  containers redroid daquele host
└────────┬─────────┘  └────┬─────┘
         │ service_id       │ device_id
         ▼                  ▼
┌─────────────────────┐  ┌────────────────────┐
│ orchestrator_nodes  │  │ account_placements │──▶ accounts
└─────────────────────┘  └────────────────────┘
   (extensão do serviço
    do tipo orchestrator)

   ┌──────────────────┐
   │ inventory_syncs  │  histórico de cada varredura (server_id, service_id)
   └──────────────────┘
```

---

### 3.27 `servers`

Um host. Identidade e endereços — nada de "o que ele sabe fazer", que é do serviço.

| Coluna | Tipo | Descrição |
|---|---|---|
| `id` | `uuid` PK | |
| `name` | `text` obrig. **UNIQUE** | Nome operacional: `i9-14900k`, `media-dev` |
| `description` | `text` | |
| `hostname` | `text` | DNS interno / MagicDNS do Tailscale |
| `tailscale_ip` | `text` | |
| `lan_ip` | `text` | |
| `preferred_host` | `text` | Qual dos caminhos usar por padrão |
| `status` | `server_status` = `active` | Derivado da saúde dos serviços — ver [4.12](#412-server_status) |
| `labels` | `text[]` = `{}` | Rótulos livres (`gpu`, `casa`, `datacenter`) |
| `notes` | `text` | |
| `created_at` / `updated_at` | `timestamptz` | `updated_at` por trigger |

**Quando usar / armadilhas.** Os três endereços existem porque o mesmo host é alcançável por Tailscale, LAN e DNS interno, e **qual funciona depende de onde o painel está rodando**. O core usa `preferred_host`; o painel monta link clicável com o melhor caminho. Não colapse tudo em um campo só — foi por não ter isso que o cadastro precisava ser refeito ao trocar de rede.

`servers.status` é **derivado**, nunca fonte: a sondagem recalcula como `active` (todos os serviços habilitados online), `degraded` (algum online, algum não) ou `unreachable`. Ler o status do host sem olhar os serviços é como ler "degradado" sem saber o que caiu.

Deletar um servidor cascateia `server_services`, `devices` e `orchestrator_nodes`. `accounts.discovered_from` e `account_placements.device_id` são `SET NULL` — a conta sobrevive ao servidor que a descobriu.

---

### 3.28 `server_services`

Cada API HTTP que roda num servidor. **É aqui que mora o contrato de integração**: endereço, credencial, TLS e saúde — todos por serviço.

| Coluna | Tipo | Descrição |
|---|---|---|
| `id` | `uuid` PK | |
| `server_id` | `uuid` → `servers` obrig. | `ON DELETE CASCADE` |
| `kind` | `service_kind` obrig. | Ver [4.9](#49-service_kind) |
| `label` | `text` | Distingue dois serviços do mesmo tipo no mesmo host |
| `base_url` | `text` obrig. | Onde a **API** responde |
| `web_url` | `text` | Onde **abrir no navegador**. Nem sempre igual à API |
| `health_path` | `text` = `/healthz` | Caminho da sondagem genérica |
| `auth_scheme` | `auth_scheme` = `none` | Ver [4.10](#410-auth_scheme) |
| `auth_user` / `auth_secret` | `text` | Credencial **deste** serviço |
| `verify_tls` | `boolean` = `true` | Desligar é decisão **por serviço** |
| `enabled` | `boolean` = `true` | Serviço desabilitado não é sondado nem usado |
| `status` | `service_status` = `unknown` | Ver [4.11](#411-service_status) |
| `last_seen_at` | `timestamptz` | Última sondagem **bem-sucedida** |
| `last_error` | `text` | |
| `consecutive_errors` | `integer` = `0` | Zera no sucesso |
| `latency_ms` | `integer` | Da última sondagem |
| `version` | `text` | Quando o serviço reporta |
| `capabilities` | `jsonb` = `{}` | O que este serviço sabe fazer, **descoberto na sondagem** |
| `metadata` | `jsonb` = `{}` | |
| `created_at` / `updated_at` | `timestamptz` | `updated_at` por trigger |

Restrição: `UNIQUE (server_id, kind, label)` — dois serviços do mesmo tipo no mesmo host são válidos, desde que rotulados.

**Quando usar / armadilhas.**
- **`base_url` ≠ `web_url` não é redundância.** O android-manager atende a API em `/api` e a interface na raiz, atrás de HTTPS. Sem os dois campos, ou o cliente quebra ou o link do painel abre um JSON.
- **`verify_tls = false` é decisão por serviço, nunca global.** Certificado autoassinado é a regra em rede interna; o painel mostra cadeado aberto âmbar em todo serviço HTTPS sem verificação, para que a escolha fique visível e não escondida no banco.
- **`auth_secret` é texto na tabela.** Dívida declarada na ADR-0012, mitigada por rede fechada. Nunca exponha a coluna em endpoint de listagem.
- **`capabilities` é guardado, não deduzido.** A interface precisa saber o que oferecer sem conhecer cada serviço (ex.: `screenshot`, `auth_enabled`, `screencast_devices`, `platforms`). O preço é um contrato implícito em `jsonb` — validado só pelo código que escreve e pelo que lê.
- **A sondagem é por serviço e em paralelo.** Um android-manager fora do ar **não** impede o orchestrator do mesmo host de funcionar; tratar saúde por host foi o bug que originou esta tabela.
- **`unauthorized` não é `offline`.** `401`/`403` viram `unauthorized` porque o conserto é outro: credencial, não infraestrutura.
- Serviço do tipo `orchestrator` **também** cria/atualiza a linha correspondente em `orchestrator_nodes` (extensão). Escrever em um só dessincroniza os dois.

---

### 3.29 `devices`

Containers redroid de um host. Antes viviam só em `orchestrator_nodes.devices_cache` — JSON bom para exibir e imprestável para referenciar, guardar histórico ou sustentar a transmissão de tela.

| Coluna | Tipo | Descrição |
|---|---|---|
| `id` | `uuid` PK | |
| `server_id` | `uuid` → `servers` obrig. | `ON DELETE CASCADE` |
| `container` | `text` obrig. | `samsung-galaxy-s24-ultra-2` |
| `alive` | `boolean` = `false` | Container em execução |
| `android_version` | `text` | |
| `adb_target` | `text` | Endereço ADB (`ip:5555`). **Alvo do gateway de tela** |
| `video_device` | `text` | `/dev/videoN` do v4l2loopback |
| `screencast_capable` | `boolean` = `false` | Derivado: verdadeiro sse há `video_device` |
| `under_external_management` | `boolean` = `false` | Outro sistema mexendo neste device |
| `labels` | `text[]` = `{}` | |
| `last_seen_at` | `timestamptz` = `now()` | |
| `metadata` | `jsonb` = `{}` | O objeto cru como veio do serviço |
| `created_at` / `updated_at` | `timestamptz` | `updated_at` por trigger |

Restrição: `UNIQUE (server_id, container)` — o nome do container é único **dentro do host**, nunca globalmente.

**Quando usar / armadilhas.**
- **Duas fontes escrevem aqui**: o orchestrator (`GET /devices`) e o android-manager (`GET /api/devices`). Quem vê primeiro cria; o outro complementa (o `UPSERT` usa `COALESCE` para não apagar campo que a outra fonte preencheu). Em divergência, **quem executa ganha** — o orchestrator.
- **`last_seen_at` sempre exibido como idade.** O android-manager cacheia devices por ~1 minuto: um device que morreu há 40 s ainda aparece vivo. Dado velho apresentado como atual é pior que dado ausente.
- **`screencast_capable = false` não é defeito, é limitação real.** Sem dispositivo de vídeo não há transmissão **contínua**: o modo alternativo derruba quem já estava assistindo. O painel desabilita o botão com o motivo, em vez de abrir uma tela preta.
- **`under_external_management` é sinal de exclusão para despacho.** O android-manager roda watchdog próprio, que pode reiniciar container. Um restart no meio de uma publicação quebra a ação e pode custar a conta. Ver [contrato do android-manager](contratos/android-manager.md).
- `metadata` guarda o objeto cru porque o esquema do android-manager não é documentado e varia entre versões. Não indexe; é rede de segurança, não caminho de consulta.

---

### 3.30 `inventory_syncs`

Histórico append-only de cada varredura de inventário. Responde *"desde quando esta conta sumiu?"* — pergunta que aparece toda vez que uma conta cai.

| Coluna | Tipo | Descrição |
|---|---|---|
| `id` | `uuid` PK | |
| `server_id` | `uuid` → `servers` | `ON DELETE CASCADE` |
| `service_id` | `uuid` → `server_services` | `ON DELETE SET NULL` |
| `kind` | `text` obrig. | `accounts` \| `devices` \| `actions` \| `full` |
| `ok` | `boolean` obrig. | |
| `accounts_found` / `accounts_new` / `accounts_gone` | `integer` = `0` | Contagens da varredura |
| `devices_found` | `integer` = `0` | |
| `duration_ms` | `integer` | |
| `forced_refresh` | `boolean` = `false` | Varredura completa (`?refresh=1`) |
| `error` | `text` | |
| `detail` | `jsonb` = `{}` | |
| `created_at` | `timestamptz` | |

Índice: `idx_syncs_server (server_id, created_at DESC)` — a listagem de servidores mostra sempre a **última** sincronia.

**Quando usar / armadilhas.** `kind` é `text` e não ENUM: o conjunto de varreduras ainda está mudando. Grave **também** as varreduras que falharam (`ok = false` + `error`) — a série de falhas é o que distingue "o serviço está instável" de "o serviço caiu agora".

`forced_refresh = true` custa caro (~90 s por nó, porque o orchestrator revisita cada device). Registrar quem forçou o quê é o que impede a varredura completa de virar comportamento padrão por descuido.

---

### 3.31 Campos novos em `accounts` e `account_placements`

A sincronização buscava as contas no orchestrator e **jogava fora quase tudo**: só o handle sobrevivia. O resultado era uma lista tecnicamente sincronizada e praticamente inútil — todo mundo com "0 seguidores". A migração 0006 grava o inventário inteiro.

#### `accounts` (novos)

| Coluna | Tipo | Descrição |
|---|---|---|
| `external_id` | `text` | Id da conta **na plataforma** (o `32822973107` do Instagram) |
| `following` | `integer` = `0` | Quantas contas ela segue |
| `media_count` | `integer` = `0` | Publicações |
| `jid` | `text` | Telefone/JID no WhatsApp. **Nunca** preenchido em conta de rede social |
| `last_synced_at` | `timestamptz` | Última varredura que tocou esta linha |
| `discovered_from` | `uuid` → `servers` | Servidor de onde foi importada. `ON DELETE SET NULL`. `NULL` = criada à mão |

Restrição: `UNIQUE (platform, external_id) WHERE external_id IS NOT NULL`.

**Quando usar / armadilhas.**
- **`external_id` é o único identificador estável: handle muda, id não.** O índice único parcial existe porque duas linhas com o mesmo id seriam a mesma conta duplicada — exatamente o que acontece quando alguém troca o @ entre duas varreduras.
- O índice é **parcial**: contas criadas à mão, sem `external_id`, não colidem entre si.
- `accounts.followers` (que já existia) e os novos contadores são o **último valor coletado**, não série temporal. Para evolução, use `post_metrics` ou uma coleta dedicada.
- `discovered_from` marca a origem, não a propriedade: uma conta descoberta no servidor A e migrada para o B continua apontando para A até alguém corrigir. É procedência, não localização — localização é `account_placements`.
- Conta importada nasce em `registered`/`L0` (era `draft` antes da 0007), com o inventário real e **sem persona vinculada** — `persona_id IS NULL`. Ela **existe e não age** até alguém decidir de quem ela é e liberá-la.

#### `account_placements` (novos)

| Coluna | Tipo | Descrição |
|---|---|---|
| `device_id` | `uuid` → `devices` | O device onde este placement vive. `ON DELETE SET NULL` |
| `foreground` | `boolean` = `false` | ⚠️ Usuário Android **em execução** — ver o alerta abaixo |
| `platform` | `platform` | Plataforma do placement (migração 0004), para o dispatcher não precisar de JOIN com `accounts` no caminho quente |
| `external_id` | `text` | Id da conta na plataforma, **como visto neste lugar** |
| `followers` / `following` / `media_count` | `integer` | Métricas **por lugar**, não por conta |

**Quando usar / armadilhas.**

> ⚠️ **`foreground` NÃO significa "está na tela".** O orchestrator devolve `active`, que quer dizer *usuário Android em execução*. O Android mantém **vários usuários rodando ao mesmo tempo** e o serviço reporta **todos** como ativos. Qual está de fato em primeiro plano ele não diz — e trocar de conta é trocar o usuário do device inteiro. Qualquer regra do tipo "só age na conta que está na tela" construída sobre este campo está errada por construção. O nome é herança do que dá para saber, não do que se gostaria de saber.

- **As métricas são por lugar de propósito.** A mesma conta em dois devices pode reportar números diferentes, e **a divergência é o sinal**: um dos dois está com sessão velha, deslogada ou apontando para outra conta. Consolidar cedo em `accounts` apagaria exatamente o sintoma.
- **`logged_in` ausente não é "deslogada".** No WhatsApp a conta **é** o usuário Android, e o serviço não reporta o campo. O código grava `logged_in = (valor is not False)` — ausência conta como logada.
- **`device_id` é anulável e pode ficar `NULL`** quando o placement chega antes de o device ser visto pela primeira vez. O `UPSERT` preenche na varredura seguinte. Não trate `NULL` como "device inexistente".
- A chave continua sendo `UNIQUE (account_id, node_id, container)`, não `device_id` — a linha nasce do inventário do nó, e o device é resolvido depois.

---

### 3.32 `personas`

> Fonte: [`0007_personas_e_contas.sql`](../db/migrations/0007_personas_e_contas.sql). Decisão em [ADR-0013](adr/0013-persona-separada-da-conta.md).

**A identidade.** Quem a personagem é, independente de onde ela está logada. Existe mesmo que nenhuma conta exista, e sobrevive à perda de todas elas.

| Coluna | Tipo | Descrição |
|---|---|---|
| `id` | `uuid` PK | |
| `name` | `text` obrig. | Nome da personagem: `Luna Prado` |
| `slug` | `text` obrig. **UNIQUE** | Identificador legível: `luna-prado`. É por ele que se fala dela fora do banco |
| `description` | `text` | Resumo em uma linha, para a lista |
| `persona_card` | `jsonb` = `{}` | **A "alma": voz, tabus, estética, lore, cadência. Fonte da verdade.** Spec em [docs/04](04-persona-card-spec.md) |
| `persona_version` | `integer` = `1` | Versão corrente do card. Incrementada a cada edição |
| `persona_engine_id` | `text` | ID na **máquina de influencers**. É a referência facial que impede a personagem de mudar de rosto entre posts |
| `client_id` | `uuid` → `clients` | **[DORMENTE]** `ON DELETE SET NULL` |
| `labels` | `text[]` = `{}` | Rótulos livres, com índice gin — servem para filtrar e agir em lote |
| `notes` | `text` | Anotação humana livre. Histórico datado é [`entity_notes`](#333-entity_notes) |
| `active` | `boolean` = `true` | Personagem aposentada é `false`, nunca `DELETE` |
| `created_at` / `updated_at` | `timestamptz` | `updated_at` por trigger |

Índices: `idx_personas_active on (name) WHERE active` · `idx_personas_labels` gin em `labels`.

**Quando usar / armadilhas.**
- **Uma persona, vários logins.** `accounts.persona_id` aponta para cá; o Instagram e o WhatsApp da mesma personagem são duas linhas de `accounts` e **uma** linha aqui. O `persona_card` vale para todos eles.
- **`persona_card = '{}'` significa "card por escrever", não "não tem persona".** Quem não tem persona é a conta com `persona_id IS NULL`. Persona sem card não deve sair de `L0`: sem voz declarada, a LLM escreve genérico.
- **`slug` é único globalmente.** A migração 0007 gera o slug a partir do handle da conta de origem por regex; duas contas homônimas em plataformas diferentes colidem e a segunda cai no `ON CONFLICT (slug) DO NOTHING`, ficando sem persona. É esperado e precisa de conferência manual.
- **Apagar a persona não apaga os logins** (`accounts.persona_id` é `ON DELETE SET NULL`) — mas apaga memória, arcos e planos (`ON DELETE CASCADE`). Aposente com `active = false`.
- `persona_engine_id` aqui é a fonte; a coluna homônima em `accounts` é herança.

---

### 3.33 `entity_notes`

**Observações datadas** sobre qualquer entidade operacional: conta, persona, device ou servidor.

| Coluna | Tipo | Descrição |
|---|---|---|
| `id` | `uuid` PK | |
| `subject_type` | `text` obrig. | `account` \| `persona` \| `device` \| `server` |
| `subject_id` | `uuid` obrig. | ID na tabela correspondente (**sem FK**) |
| `body` | `text` obrig. | A observação |
| `author` | `text` | Quem anotou |
| `pinned` | `boolean` = `false` | Fixada aparece sempre no topo da ficha |
| `created_at` | `timestamptz` | |

Índices: `idx_notes_subject (subject_type, subject_id, created_at DESC)` · `idx_notes_pinned (subject_type, subject_id) WHERE pinned`.

**Quando usar / armadilhas.**
- **É diferente de `accounts.notes`.** Aquele é um campo único: só sobrevive quem escreveu por último. Operação real precisa de histórico — *quem* anotou *o quê* e *quando*. Os dois coexistem: `notes` é a anotação corrente da ficha; `entity_notes` é o diário.
- **`pinned` é para o que muda a decisão de quem chega depois:** "esta conta já caiu uma vez", "não usar para publi". Fixar tudo é não fixar nada.
- **Polimórfica e sem integridade referencial**, pelo mesmo motivo de [`approvals`](#321-approvals): três tabelas quase idênticas custariam mais. O preço é anotação órfã quando o sujeito é apagado, e o painel precisa tolerar isso.
- **Append-only na prática.** Não há `updated_at` e não há endpoint de edição — só criação e remoção (`DELETE /v1/notes/{id}`). Corrigir é anotar de novo.

---

### 3.34 Campos novos em `accounts` (migração 0007)

A conta deixa de carregar a identidade e passa a carregar o que é **do login**: quem é o dono, qual é o papel, os dados operacionais e onde está a credencial.

| Coluna | Tipo | Descrição |
|---|---|---|
| `persona_id` | `uuid` → `personas` | **Nulável de propósito** — conta coletora não representa personagem nenhuma. `ON DELETE SET NULL` |
| `role` | `text` = `agent` | `agent` \| `collector` \| `utility`, com `CHECK (accounts_role_valido)`. Papel operacional, ≠ arquétipo |
| `profile` | `jsonb` = `{}` | Dados operacionais do login: e-mail, telefone, nascimento, origem, proxy, geo. **Campos livres, sem schema fixo** |
| `secret_ref` | `text` | **Referência a ONDE a credencial vive** — cofre, gerenciador, gaveta. ⚠️ A coluna **não é cifrada**: senha não entra aqui |
| `labels` | `text[]` = `{}` | Rótulos livres, com índice gin. É o eixo de filtro que o operador inventa sozinho |
| `metadata` | `jsonb` = `{}` | Qualquer coisa estruturada que não mereça coluna |
| `last_seen_in_inventory` | `timestamptz` | Quando a varredura viu este login pela última vez. Entrada do cálculo de **presença** |
| `previous_handles` | `text[]` = `{}` | Handles anteriores, empilhados a cada troca de `handle` |

Índices novos:

| Índice | Definição | Consulta que serve |
|---|---|---|
| `idx_accounts_persona` | `(persona_id)` | "Quais logins são desta personagem?" — a ficha de persona e o `LEFT JOIN` da listagem |
| `idx_accounts_labels` | gin em `(labels)` | Filtro por rótulo em lista grande, com `:label = ANY(labels)` |
| `idx_accounts_handle_trgm` | gin `(handle gin_trgm_ops)` | **Busca "contém"**, que é o que o operador digita. Com milhares de contas, `ILIKE '%luna%'` sem trigrama é seq scan a cada tecla |

**Quando usar / armadilhas.**
- **`persona_id IS NULL` é normal, não é dado faltando.** Todo código que toca conta precisa tolerá-lo: prompt sem card, ficha sem identidade, listagem que não pode virar `INNER JOIN`. A faceta `unbound` da API conta exatamente essas contas, para que a lacuna seja visível em vez de silenciosa.
- **`role` ≠ `archetype`.** O arquétipo diz que tipo de conteúdo a conta produz (`influencer`, `gossip`, `clipper`, `messaging`, `collector`) e escolhe o `ContentSource` ([4.1](#41-archetype)); o papel diz o que ela faz na operação. A 0007 preencheu `role = 'utility'` para `archetype = 'messaging'` e `role = 'collector'` para `archetype = 'collector'`, mas os dois eixos podem divergir depois — uma conta de influencer pode ser rebaixada a `utility` sem perder o arquétipo (que é imutável).
- **`secret_ref` guarda o endereço do segredo, nunca o segredo.** A coluna é texto puro num banco sem cifra em repouso; colar a senha aqui cria um alvo. O painel rotula o campo como *"onde está a credencial"* justamente para não convidar ao erro. A barreira é social enquanto não houver cofre na stack.
- **`profile` é `jsonb` livre por decisão.** Cada operação tem seus campos, e congelá-los em colunas seria migração por mês. O painel sugere um conjunto (e-mail, telefone, nascimento, origem, proxy, geo, 2FA) e aceita chaves extras. Não indexe antes de o formato estabilizar.
- **`previous_handles` existe para não confundir renomeação com sumiço.** O `PATCH` empilha o handle antigo quando ele muda. Sem isso, uma conta que trocou de @ parece ter desaparecido do inventário — e o identificador estável continua sendo `external_id` ([3.31](#331-campos-novos-em-accounts-e-account_placements)).
- **`last_seen_in_inventory` é presença, não intenção.** Não confunda com `last_action_at` (última ação nossa) nem com `last_synced_at` (última varredura que tocou a linha).

---

### 3.35 Situação × presença: dois eixos ortogonais

Duas perguntas diferentes sobre a mesma conta, e é **a divergência entre elas** que o operador precisa enxergar.

| Eixo | Onde vive | Quem escreve | Pergunta |
|---|---|---|---|
| **Situação** (`status`) | `accounts.status`, ENUM `account_status` | humano e guardrails | *O que **nós** queremos que esta conta faça?* |
| **Presença** | calculada no SQL a partir de `account_placements` + `accounts.last_seen_in_inventory` | a varredura de inventário | *O que a **realidade** diz sobre onde ela está?* |

**Situação — o que nós queremos:**

| Valor | Significado |
|---|---|
| `registered` | Cadastrada, ainda não liberada para agir. É onde toda conta nasce, inclusive as importadas do inventário |
| `warming` | Em aquecimento: só consome conteúdo (assiste reels, vê stories), não publica |
| `active` | Opera normalmente, dentro do nível de autonomia dela |
| `paused` | Parada por decisão humana. Nada é despachado; nada se perde |
| `suspended` | Contida por guardrail ou incidente. Exige revisão humana para voltar |
| `lost` | Banida ou inacessível. **Fica no sistema de propósito** — o histórico é o que explica por que ela caiu |

**Presença — o que a realidade diz:**

| Valor | Significado | O que o operador faz |
|---|---|---|
| `online` | Logada num container visto na **última hora** | nada |
| `stale` | Esteve num device, mas o inventário não a confirma há **mais de uma hora** | observar; pode ser só cache |
| `missing` | Tem histórico de device, mas não aparece no inventário há **mais de um dia** | investigar: device caído, sessão expirada, ou conta caiu de verdade |
| `unbound` | **Nunca** esteve num device, ou o vínculo foi removido | cadastro válido; só não tem onde rodar |

O cálculo vive no router de contas (`PRESENCE_SQL`) e é aplicado tanto na listagem quanto nas facetas:

```sql
CASE
  WHEN NOT EXISTS (SELECT 1 FROM account_placements p WHERE p.account_id = a.id)
    THEN 'unbound'
  WHEN EXISTS (SELECT 1 FROM account_placements p
                WHERE p.account_id = a.id AND p.logged_in
                  AND p.last_seen_at > now() - interval '1 hour')
    THEN 'online'
  WHEN a.last_seen_in_inventory > now() - interval '24 hours'
    THEN 'stale'
  ELSE 'missing'
END
```

**Por que dois eixos e não um.** Uma conta pode estar `active` — queremos que opere — e há três dias fora do inventário: não dá. Nenhum status sozinho conta essa história, e colapsar os dois num campo só (um `status = 'offline'`, por exemplo) apagaria a informação mais útil, que é o **desencontro** entre intenção e realidade. Por isso os dois aparecem lado a lado na mesma linha da tabela do painel.

> **"Se eu sair de todas as contas nos devices, eu perco a conta?" — NÃO.**
> Some o `account_placements`. A conta, a persona, as observações, as tasks e todo o histórico continuam. A presença vira `unbound` ou `missing`, e é só isso que muda. **Nada é apagado por ausência de inventário.**
> Apagar de verdade exige `DELETE /v1/accounts/{id}?confirm=apagar-de-verdade` e é desaconselhado: conta banida deve virar `status = 'lost'`.

**Ordem da avaliação importa.** `unbound` é testado primeiro porque "nunca teve device" não é o mesmo que "sumiu": uma conta cadastrada à mão hoje não deve aparecer como `missing`. E `online` exige `logged_in` **e** `last_seen_at` recente — placement antigo de container morto não conta como presença.

---

## 4. ENUMs e máquinas de estado

Catorze ENUMs na migração 0001, mais `node_status` (0002) e quatro de infraestrutura na 0005 ([4.9](#49-service_kind) a [4.12](#412-server_status)). Regra geral: adicionar valor a ENUM em Postgres é barato (`ALTER TYPE ... ADD VALUE`), **remover é caro** (recriar o tipo). Portanto: só entra valor que já tem significado operacional definido.

### 4.1 `archetype`

Define qual implementação de `ContentSource` o passo SOURCE usa. É o único eixo em que os três produtos divergem.

| Valor | Significado | `ContentSource` | Fase |
|---|---|---|---|
| `influencer` | Persona fictícia com vida própria | `GeneratedSource` (máquina de influencers) | 1–3 |
| `gossip` | Página de fofoca/nicho, curadoria editorial | `CuratedSource` (monitores + ingestão) | 4 |
| `clipper` | Clipagem de conteúdo longo | `DerivedSource` (media-forge) | 5 |

**Transições:** nenhuma. Arquétipo é imutável após criação da conta — trocar significaria descartar memória, arcos e persona. Para mudar de arquétipo, crie outra conta.

### 4.2 `autonomy_level`

| Valor | Nome | Comportamento |
|---|---|---|
| `L0` | Estagiário | Só drafts. Humano aprova **tudo** |
| `L1` | Júnior | Auto-posta; toda interação vai para revisão |
| `L2` | Pleno | Auto-posta e auto-responde; revisão por amostragem (`L2_SAMPLING_RATE`, default 10%) |
| `L3` | Sênior | Full auto; humano só vê alertas e métricas |

**Transições permitidas:**

```
  L0 ──▶ L1 ──▶ L2 ──▶ L3          promoção: SEMPRE manual, um degrau por vez
   ▲      ▲      ▲      │
   │      │      └──────┘          rebaixamento: pode pular degraus
   │      └─────────────┘          e pode ser AUTOMÁTICO (guardrail)
   └────────────────────┘
```

- **Promoção** exige decisão humana explícita e nunca pula degrau. Critério sugerido: N dias sem violação `block`/`kill` e taxa de rejeição em amostragem abaixo do limiar.
- **Rebaixamento** pode ser automático e pular degraus: uma violação `severity = 'kill'` leva de L3 direto a L0 (na prática, junto com `status = 'suspended'`).
- **Fofoca nunca atinge L3.** Restrição de produto, não de schema — o ENUM permite, o guardrail proíbe. Ver [docs/08](08-guardrails-e-autonomia.md).

### 4.3 `account_status`

É o eixo da **situação** — o que *nós* queremos. O eixo ortogonal, o da **presença**, está em [3.35](#335-situação--presença-dois-eixos-ortogonais) e não é ENUM: é calculado.

| Valor | Significado |
|---|---|
| `registered` | Cadastrada, **ainda não liberada para agir**. Era `draft` até a 0007 |
| `warming` | Em aquecimento: só consome conteúdo (assiste reels, segue, curte), não posta |
| `active` | Operando normalmente |
| `paused` | Pausada por humano |
| `suspended` | Kill switch acionado (guardrail ou incidente) |
| `lost` | Conta banida/perdida na plataforma |

> **`draft` foi renomeado para `registered` na migração 0007** (`ALTER TYPE account_status RENAME VALUE`). "Draft" não dizia nada a quem opera — sugeria rascunho editável, e o estado real é *cadastrada, existe, e ainda não age*. É o estado em que nascem tanto a conta criada à mão quanto a importada do inventário. Renomear valor de ENUM é atômico no banco e **visível em todo lugar**: código, seed, teste e tela que comparassem com a string `'draft'` deixam de casar.

```
registered ─▶ warming ──▶ active ◄──────┐
                            │          │
                 ┌──────────┼──────────┤
                 ▼          ▼          │
             paused    suspended ──────┘  (retomada exige revisão humana)
                 │          │
                 └────┬─────┘
                      ▼
                    lost        ◄── terminal
```

Permitidas: `registered→warming`, `warming→active`, `warming→lost`, `active→paused`, `active→suspended`, `active→lost`, `paused→active`, `paused→lost`, `suspended→active` (só por humano), `suspended→lost`, qualquer → `lost`.
Proibidas: `registered→active` (sem aquecimento é o caminho mais curto para o ban), `lost→qualquer`.

**`lost` é terminal e fica no sistema de propósito.** A conta banida não é apagada: o histórico — tasks, posts, violações, observações e a linha do tempo em `events` — é o que explica *por que* ela caiu, e é o único insumo para não repetir o erro na próxima. Apagar a linha destrói exatamente a informação pela qual ela ainda vale alguma coisa. Se a personagem continua, um login novo é vinculado à mesma `persona_id` e a memória segue intacta.

### 4.4 `task_type`

Três famílias. A família determina se a task toca device.

| Família | Valores | Toca device? |
|---|---|---|
| **Produção** (roda no core) | `plan_day`, `produce_content`, `reflect` | Não |
| **Execução** (vai ao orchestrator) | `publish_post`, `publish_story`, `publish_reels`, `like`, `comment`, `follow`, `unfollow`, `save`, `share`, `watch_reels`, `view_stories`, `send_dm`, `reply_comment` | **Sim** |
| **Coleta** | `extract_content`, `collect_metrics`, `collect_interactions`, `monitor_check` | Sim (exceto quando via API) |

Notas: `extract_content` é a task de **recon** — abrir um conteúdo alvo e extrair o que é; alimenta `campaign_recon`. `watch_reels` e `view_stories` não geram conteúdo: existem para produzir rastro de comportamento humano, especialmente em `warming`. Só a família de execução conta para `ACCOUNT_MAX_ACTIONS_PER_HOUR`.

### 4.5 `platform`, `campaign_kind`, `monitor_status`, `memory_kind`, `source_kind`

| ENUM | Valores | Observação |
|---|---|---|
| `platform` | `instagram`, `tiktok`, `whatsapp` | Sem transições. Conta não muda de plataforma |
| `campaign_kind` | `content`, `engagement`, `mixed` | `engagement`/`mixed` **exigem recon** antes de planejar |
| `monitor_status` | `active`, `paused`, `error`, `disabled` | `active↔paused` por humano; `active→error` automático por `consecutive_errors`; `error→active` só após intervenção; `disabled` é aposentadoria (não volta sem decisão explícita) |
| `memory_kind` | `episodic`, `semantic`, `narrative` | `episodic` = diário bruto; `semantic` = fato consolidado, produzido pelo REFLECT a partir de episódicas; `narrative` = estado do arco. Sem transição: uma memória não muda de tipo, o REFLECT **cria** uma nova |
| `source_kind` | `ig_account`, `tiktok_account`, `hashtag`, `keyword`, `rss`, `youtube_channel`, `live_stream`, `http_api` | Define o coletor. `ig_account`/`tiktok_account`/`hashtag` passam pelo farm Android; `rss`/`http_api` são HTTP puro |

### 4.6 `task_status`

```
                    ┌──────────────────────────────────────┐
                    │                                      │
   ┌─────────┐  janela   ┌───────────┐  dispatcher  ┌────────────┐
   │ pending │──────────▶│ scheduled │─────────────▶│ dispatched │
   └────┬────┘           └─────┬─────┘              └──────┬─────┘
        │                      │                           │ orchestrator
        │                      │                           │ confirma início
        │                      │                           ▼
        │                      │                     ┌──────────┐
        │                      │                     │ running  │
        │                      │                     └────┬─────┘
        │                      │                          │
        │                      │              ┌───────────┴──────────┐
        │                      │   verificado │                      │ erro
        │                      │              ▼                      ▼
        │                      │        ┌──────────┐           ┌────────┐
        │                      │        │ verified │           │ failed │
        │                      │        └──────────┘           └───┬────┘
        │                      │          terminal ✔               │
        │                      │                                   │ attempts <
        │                      └───────────────────────────────────┘ max_attempts
        │                                    (retry)
        │
        ├──── expires_at ultrapassado ────▶ ┌─────────┐  terminal ✘
        │                                   │ expired │
        │                                   └─────────┘
        │
        └──── humano / kill switch ───────▶ ┌───────────┐  terminal ✘
                                            │ cancelled │
                                            └───────────┘
```

| De → Para | Quando | Efeito colateral |
|---|---|---|
| `pending → scheduled` | Entrou na janela (`scheduled_for` ≤ agora, dentro de `active_hours`) | — |
| `pending/scheduled → dispatched` | Dispatcher enviou ao orchestrator | grava `dispatched_at`, `external_task_id`, `attempts += 1` |
| `dispatched → running` | Orchestrator confirmou início | — |
| `dispatched → failed` | Falha de envio / device indisponível | `last_error` |
| `running → verified` | Orchestrator **verificou** a execução | grava `verified_at`; cria `posts`; escreve memória e evento |
| `running → failed` | Falha na execução | `last_error`, `result` |
| `failed → scheduled` | Retry, se `attempts < max_attempts` | novo `scheduled_for` com backoff |
| `pending/scheduled → expired` | `expires_at` < agora | Furo que perdeu validade |
| `pending/scheduled/dispatched → cancelled` | Humano, kill switch ou campanha abortada | Cancelamento em voo exige aviso ao orchestrator |

Terminais: `verified`, `cancelled`, `expired`, e `failed` quando `attempts >= max_attempts`.
**Proibidas:** `dispatched → verified` (pular `running` mascara falha de verificação), qualquer transição saindo de terminal, e `verified → *`.

### 4.7 `content_status`

```
   ┌───────┐   submete   ┌────────────┐
   │ draft │────────────▶│ qa_pending │
   └───────┘             └─────┬──────┘
       ▲                       │
       │ regenera        ┌─────┴──────────────┐
       │            reprova              aprova no QA
       │                 │                    │
       │                 ▼            ┌───────┴────────┐
       │          ┌───────────┐   exige humano    dispensa humano
       │          │ qa_failed │   (L0, amostra,   (L1+, sem risco)
       │          └─────┬─────┘    risco)              │
       └────────────────┤          │                   │
                        │          ▼                   │
                        │  ┌──────────────────┐        │
                        │  │ approval_pending │        │
                        │  └───┬─────────┬────┘        │
                        │      │         │             │
                        │ rejeita     aprova/edita     │
                        │      │         │             │
                        │      ▼         ▼             ▼
                        │ ┌──────────┐  ┌──────────────────┐
                        │ │ rejected │  │     approved     │
                        │ └────┬─────┘  └────────┬─────────┘
                        │      │                 │ agenda
                        │      │                 ▼
                        │      │          ┌─────────────┐
                        │      │          │  scheduled  │
                        │      │          └──────┬──────┘
                        │      │                 │ task verified
                        │      │                 ▼
                        │      │          ┌─────────────┐
                        │      │          │  published  │
                        │      │          └──────┬──────┘
                        ▼      ▼                 ▼
                     ┌──────────────────────────────┐
                     │          archived            │  terminal
                     └──────────────────────────────┘
```

| De → Para | Quando |
|---|---|
| `draft → qa_pending` | `ContentSource` terminou de produzir |
| `qa_pending → qa_failed` | QA visual, textual, de marca ou legal reprovou |
| `qa_pending → approval_pending` | QA passou, mas autonomia exige humano (**L0**, amostragem em **L2**, ou `rights_status='unknown'` / `claim_status='unverified'`) |
| `qa_pending → approved` | QA passou e a autonomia dispensa humano (L1+ sem sinalização de risco) |
| `qa_failed → draft` | Regeneração com o feedback do `qa_report` |
| `qa_failed → archived` | Desistiu do item |
| `approval_pending → approved` | Humano aprovou (ou editou: `approvals.decision='edited'` + `edited_payload`) |
| `approval_pending → rejected` | Humano rejeitou |
| `approved → scheduled` | Task de publicação criada, com jitter aplicado |
| `scheduled → published` | Task correspondente atingiu `verified` |
| `scheduled → archived` | Cancelado antes de ir ao ar |
| `rejected → draft` | Retrabalho a partir da nota do humano |
| `rejected → archived` | Descartado de vez |
| `published → archived` | Despublicado / arquivado por retenção |

**Proibidas e por quê:**
- `draft → approved` — pular QA anula brand safety e checagem legal.
- `draft/qa_pending → published` — nada é publicado sem passar por `approved` + `scheduled`.
- `approved → published` sem passar por `scheduled` — o registro do agendamento com jitter é o que prova que o espalhamento foi aplicado.
- `published → draft` — o que foi ao ar não volta a ser rascunho; crie um item novo.

### 4.8 `campaign_status`

```
   ┌───────┐
   │ draft │
   └───┬───┘
       │
       ├──── kind='content' e sem alvo externo ──────────────┐
       │                                                     │
       │ kind IN ('engagement','mixed')                      │
       ▼                                                     │
  ┌────────────────┐   task extract_content   ┌────────────┐ │
  │ recon_pending  │─────────────────────────▶│ recon_done │ │
  └───────┬────────┘   + análise da LLM       └──────┬─────┘ │
          │                                          │       │
          │ safety_verdict='blocked'                  ▼       ▼
          │                                    ┌────────────────┐
          │                                    │   planning     │
          │                                    └───────┬────────┘
          │                                            │ participantes
          │                                            │ + slots sorteados
          │                                            ▼
          │                                  ┌──────────────────┐
          │                                  │ approval_pending │
          │                                  └───┬──────────┬───┘
          │                                      │          │ rejeita
          │                             aprova   │          └────┐
          │                                      ▼               │
          │                                ┌──────────┐          │
          │                                │ approved │          │
          │                                └────┬─────┘          │
          │                                     │ window_start   │
          │                                     ▼                │
          │                                ┌─────────┐           │
          │                                │ running │           │
          │                                └────┬────┘           │
          │                     ┌───────────────┤                │
          │        kill switch  │               │ window_end     │
          ▼                     ▼               ▼                ▼
      ┌─────────┐          ┌─────────┐   ┌───────────┐    ┌───────────┐
      │ aborted │◄─────────┤ aborted │   │ completed │    │ cancelled │
      └─────────┘          └─────────┘   └───────────┘    └───────────┘
       terminal ✘                         terminal ✔       terminal ✘
```

| De → Para | Quando |
|---|---|
| `draft → recon_pending` | Campanha de engajamento/mista: task `extract_content` criada |
| `draft → planning` | Campanha de conteúdo puro, sem alvo externo a inspecionar |
| `draft → cancelled` | Descartada antes de começar |
| `recon_pending → recon_done` | Captura + análise concluídas, `safety_verdict IN ('safe','risky')` |
| `recon_pending → aborted` | `safety_verdict = 'blocked'`, ou recon falhou de forma irrecuperável |
| `recon_done → planning` | Manager filtra contas elegíveis e monta o plano |
| `recon_done → aborted` | Revisão humana vetou após ler a análise |
| `planning → approval_pending` | Participantes e slots definidos; espera aval humano |
| `planning → cancelled` | Nenhuma conta elegível, ou desistência |
| `approval_pending → approved` | Humano aprovou o conjunto (participantes, textos, slots) |
| `approval_pending → cancelled` | Humano rejeitou |
| `approved → running` | `window_start` alcançado; tasks começam a ser despachadas |
| `running → completed` | `window_end` alcançado e nenhuma task da campanha em voo |
| `running → aborted` | Kill switch, guardrail `severity='kill'`, ou alvo mudou de contexto |

**Diferença `cancelled` vs `aborted`:** `cancelled` = interrompida **antes** de qualquer ação no mundo. `aborted` = interrompida **depois** de já ter agido (ou com recon bloqueado). A distinção importa para o pós-morte: `aborted` exige runbook (o que já foi publicado? precisa remover?).

**Proibidas:** `draft → planning` quando `kind IN ('engagement','mixed')` e `target_url IS NOT NULL` — planejar sem ver o alvo é o cenário de risco número um do subsistema. `planning → running` sem `approved` — campanha sempre passa por humano na v1, em qualquer nível de autonomia das contas participantes.

### 4.9 `service_kind`

O que um serviço **é**. Determina qual cliente a sondagem usa e o que a interface oferece.

| Valor | Serviço | Porta sugerida | Auth típica | Fase |
|---|---|---|---|---|
| `orchestrator` | android-orchestrator (worker) — executa e verifica ações | `8078` | `basic` (opt-in) | 1 |
| `android_manager` | painel da frota redroid — leitura para nós | `443` (nginx) | `login` | 1 |
| `screen_gateway` | transmissão de tela dos devices | `9000` | `none` hoje | 1 |
| `persona_engine` | máquina de influencers | `8081` | `none` | 2 |
| `media_forge` | cortes de vídeo | `8300` | `bearer` | 5 |
| `custom` | qualquer outra API HTTP | — | — | — |

**Transições:** nenhuma. Um serviço não muda de tipo — remove-se e cadastra-se outro.

As portas acima são **sugestão de formulário**, não regra: o servidor é quem manda, e o operador sobrescreve. A única proibição mecânica é `orchestrator` com `:8077` na URL — essa é a porta do **executor**, que monta o `docker.sock` e escuta só em loopback; quem fala com ela tem root no host. A API recusa o cadastro ([ADR-0011](adr/0011-polling-e-registro-de-nos.md)).

### 4.10 `auth_scheme`

| Valor | Como funciona | Quem usa |
|---|---|---|
| `none` | Sem credencial | Gateway de tela; orchestrator sem `ORCH_PANEL_PASS` |
| `basic` | `Authorization: Basic` | orchestrator worker, nginx |
| `bearer` | Token estático no header | media-forge (Fase 5) |
| `login` | `POST` de login devolve token com validade | **android-manager** |

`login` existe como valor próprio porque o custo operacional é outro: o token expira, o cliente precisa refazer o login e repetir a chamada, e **não há como emitir credencial de menor privilégio** — o android-manager não tem chave de API nem escopo read-only. É a razão de a barreira ser a allowlist do nosso cliente.

### 4.11 `service_status`

| Valor | Significado | O que o operador conserta |
|---|---|---|
| `unknown` | Nunca sondado | Sondar |
| `online` | Última sondagem OK | — |
| `degraded` | Responde, mas com parte das funções indisponível | Depende de `last_error` |
| `offline` | Não responde | Infraestrutura, rede, processo caído |
| `unauthorized` | `401`/`403` | **Credencial** — não é queda |
| `disabled` | `enabled = false` | Nada; está desligado por decisão |

A separação `offline` × `unauthorized` não é cosmética: são consertos diferentes, e tratar credencial errada como "fora do ar" faz o operador reiniciar serviço saudável. O painel usa cores distintas pelo mesmo motivo.

### 4.12 `server_status`

| Valor | Significado |
|---|---|
| `active` | **Todos** os serviços habilitados online |
| `degraded` | Algum online, algum não |
| `unreachable` | Nenhum serviço habilitado respondendo |
| `disabled` | Servidor desligado por decisão |

**É derivado, recalculado ao fim de cada sondagem** — nunca escrito à mão pelo fluxo normal. E é resumo, não diagnóstico: um host `degraded` não diz o que caiu. A resposta está em `server_services.status` + `last_error`, serviço por serviço.

---

## 5. Índices

Vinte e sete índices na migração 0001, mais os de infraestrutura (0005/0006) e os de identidade e busca em massa da 0007 ([5.5](#55-índices-da-identidade-e-da-busca-em-massa-migração-0007)). Muitos são **parciais** (`WHERE ...`): indexam só a fatia quente da tabela. O ganho é duplo — índice menor (cabe em cache) e escrita mais barata (linha que não bate no predicado não entra no índice). É a técnica certa para tabelas onde 99% das linhas estão em estado terminal e as consultas só olham o 1% ativo.

### 5.1 Índices parciais (o coração da operação)

| Índice | Predicado | Consulta que serve |
|---|---|---|
| `idx_tasks_due` on `(scheduled_for, priority)` | `status IN ('pending','scheduled')` | **O tick do dispatcher.** A cada poucos segundos: "que tasks já venceram, em ordem de urgência?". Sem o predicado, o índice cresceria para sempre com tasks `verified` que ninguém mais consulta. A ordem das colunas importa: filtra por tempo, depois ordena por prioridade |
| `idx_tasks_inflight` on `(external_task_id)` | `status IN ('dispatched','running')` | **Handler do webhook do orchestrator.** Chega um callback com `external_task_id`: encontra a task em voo em O(log n). Restringir a "em voo" também dá idempotência — reentrega de task já terminal não acha nada e é descartada |
| `idx_content_pending_approval` on `(created_at)` | `status = 'approval_pending'` | **Fila de aprovação do painel**, em ordem de chegada. Fatia minúscula de `content_items`; o índice fica com dezenas de entradas mesmo com milhões de itens |
| `idx_approvals_open` on `(requested_at)` | `decided_at IS NULL` | Aprovações abertas de qualquer tipo (conteúdo, campanha, interação). Serve o badge de pendências e o alerta de SLA de revisão |
| `idx_interactions_pending` on `(account_id, received_at DESC)` | `replied_at IS NULL` | Fila de resposta por conta: "o que a Nina ainda não respondeu, mais recente primeiro" |
| `idx_monitors_due` on `(next_check_at)` | `status = 'active'` | **Tick do scheduler de monitores** (Fase 4), gêmeo do `idx_tasks_due`. Monitores em `paused`/`error`/`disabled` não sujam o índice |
| `idx_ingested_unconsumed` on `(ingested_at DESC)` | `consumed_by IS NULL` | Pauta disponível: itens ingeridos que nenhuma conta usou ainda, mais novos primeiro (furo velho não interessa) |
| `idx_trends_live` on `(platform, kind, score DESC)` | `window_end IS NULL` | "Top 10 áudios em alta no Instagram agora". Tendências encerradas ficam para análise histórica, fora do caminho quente |
| `idx_participants_slot` on `(slot_at)` | `NOT skipped` | Timeline de execução da campanha e verificação de jitter entre slots. Participantes pulados não entram — eles nunca serão executados |
| `idx_accounts_status` on `(status)` | `status = 'active'` | Toda varredura periódica ("para cada conta ativa, ..."). Predicado quase redundante com a coluna indexada, mas mantém o índice do tamanho da frota operante, não do histórico |
| `idx_arcs_account_active` on `(account_id)` | `active` | Contexto do PLAN: arcos vivos de uma conta. Arcos encerrados nunca entram em prompt |

### 5.2 Índices completos

| Índice | Colunas | Consulta que serve |
|---|---|---|
| `idx_accounts_archetype` | `(archetype)` | Filtro de elegibilidade de campanha e painéis por arquétipo |
| `idx_accounts_client` | `(client_id)` | **[DORMENTE]** Isolamento por tenant (Fase 5) |
| `idx_memories_account_kind` | `(account_id, kind, occurred_at DESC)` | Recuperação **por recência** dentro de um tipo — o fallback quando não há embedding e o filtro obrigatório por conta |
| `idx_memories_embedding` | HNSW `(embedding vector_cosine_ops)` | Busca por **similaridade** (pgvector). HNSW dá recall alto com latência baixa; custa mais na escrita e mais RAM que IVFFlat. Escolhido porque memória é lida muito mais do que escrita |
| `idx_content_account_status` | `(account_id, status)` | Painel da conta: "o que a Nina tem em cada estágio" |
| `idx_campaigns_status` | `(status)` | Lista de campanhas por estado; alimenta o monitor de campanhas travadas |
| `idx_recon_campaign` | `(campaign_id)` | Recon de uma campanha (lembrar: pegar o mais recente) |
| `idx_tasks_account` | `(account_id, created_at DESC)` | Histórico de ações da conta; base do cálculo de rate limit por hora |
| `idx_posts_account` | `(account_id, published_at DESC)` | Feed da conta no painel; "último post foi quando?" |
| `idx_metrics_post` | `(post_id, collected_at DESC)` | Métrica mais recente de um post (`DISTINCT ON`) e série temporal |
| `idx_monitor_hits_monitor` | `(monitor_id, detected_at DESC)` | Depuração de `trigger_rules`: os últimos hits de um monitor |
| `idx_violations_recent` | `(created_at DESC)` | Mural de incidentes: "o que deu errado nas últimas 24h" em toda a frota |
| `idx_events_account` | `(account_id, created_at DESC)` | Linha do tempo de uma conta |
| `idx_events_type` | `(type, created_at DESC)` | Séries por tipo de evento (taxa de `content.qa_failed`/dia) |
| `idx_events_trace` | `(trace_id)` | **Reconstruir uma decisão inteira.** É o índice que faz a auditoria ser consulta e não investigação |
| `idx_llm_calls_time` | `(created_at DESC)` | Agregação de custo/latência por janela |

### 5.3 Unicidades como regra de negócio

| Constraint | O que impede |
|---|---|
| `accounts (platform, handle)` | Duas linhas para a mesma conta real — dois agentes brigando pelo mesmo device |
| `personas (slug)` | Duas identidades com o mesmo identificador legível. É por ele que se fala da personagem fora do banco |
| `persona_versions (account_id, version)` | Duas versões com o mesmo número; garante linha do tempo íntegra |
| `day_plans (account_id, plan_date)` | Planejar o mesmo dia duas vezes (idempotência do PLAN) |
| `sources (kind, endpoint)` | Cadastrar a mesma fonte duas vezes e dobrar a coleta |
| `ingested_items (dedup_hash)` | Reprocessar o mesmo furo |
| `campaign_participants (campaign_id, account_id)` | Mesma conta agir duas vezes na mesma campanha — padrão que denuncia automação |

### 5.4 Faltas conhecidas

Registradas aqui para não serem redescobertas como bug:

- **Sem `UNIQUE (platform, external_id)` em `posts`** — reentrega de webhook pode duplicar. Guarda hoje é no código.
- **Sem índice em `approvals (subject_type, subject_id)`** — buscar "as aprovações deste conteúdo" faz seq scan. Aceitável enquanto a tabela é pequena; adicionar quando passar de ~10⁵ linhas.
- **Sem índice em `guardrail_violations (account_id, created_at DESC)`** — a query "violações desta conta" usa `idx_violations_recent` e filtra. Revisar quando a frota crescer.
- **Sem índice em `content_items (campaign_id)`** — "conteúdo desta campanha" é seq scan.
- **`llm_calls` sem índice em `trace_id`** — correlacionar chamada de LLM com evento por trace hoje é scan. Candidato imediato quando a auditoria virar rotina.
- **A presença não é indexável hoje** ([3.35](#335-situação--presença-dois-eixos-ortogonais)). Filtrar por `presence` avalia o `CASE` linha a linha, com subconsultas em `account_placements`. Aceitável na escala atual porque a listagem é paginada; se doer, a saída é materializar a presença numa coluna atualizada pela varredura — ao custo de ela deixar de ser sempre atual.

---

### 5.5 Índices da identidade e da busca em massa (migração 0007)

Todos existem por causa da escala: a operação real tem **milhares** de logins, e a tela de contas é varrida, filtrada e ordenada o dia inteiro.

| Índice | Definição | Consulta que serve |
|---|---|---|
| `idx_accounts_persona` | `(persona_id)` | "Quais logins são desta personagem?" — a ficha de persona e o `LEFT JOIN personas` da listagem |
| `idx_accounts_labels` | gin em `(labels)` | Filtro por rótulo (`:label = ANY(labels)`). Rótulo é o eixo que o operador inventa sozinho, e é o que sustenta a ação em lote |
| `idx_accounts_handle_trgm` | gin `(handle gin_trgm_ops)` | **Busca "contém"** — `handle ILIKE '%luna%'`. É o que o operador digita, e sem trigrama é seq scan a cada tecla |
| `idx_personas_active` | `(name) WHERE active` | Lista de personas em ordem alfabética, sem as aposentadas |
| `idx_personas_labels` | gin em `(labels)` | Idem `accounts`, do lado da identidade |
| `idx_memories_persona` | `(persona_id, kind, occurred_at DESC)` | Recuperação de memória por recência dentro de um tipo, agora **por personagem** |
| `idx_arcs_persona` | `(persona_id) WHERE active` | Contexto do PLAN: arcos vivos da personagem |
| `idx_notes_subject` | `(subject_type, subject_id, created_at DESC)` | Observações de uma entidade, mais recentes primeiro |
| `idx_notes_pinned` | `(subject_type, subject_id) WHERE pinned` | As fixadas, que a ficha mostra no topo antes de tudo |

**Por que trigrama e não `LIKE 'prefixo%'`.** Índice B-tree serve busca por prefixo; o operador não digita prefixo, digita pedaço — lembra "luna" de `@lunaprado.oficial`. `pg_trgm` é a extensão já habilitada para dedup e QA de caption, então o custo marginal é um índice, não uma dependência nova.

**As facetas não têm índice próprio, e é de propósito.** As contagens por plataforma, situação, papel, arquétipo e presença varrem `accounts` inteira — mas em **uma única consulta** com todas as agregações, em vez de seis idas ao banco. Índice de contagem só ajudaria com `count` por coluna isolada; o gargalo, se aparecer, é a passada única e se resolve com cache curto.

---

## 6. Consultas de referência

Consultas testadas contra o schema da migração 0001. Servem de base para o código e para depuração via `make psql`.

### 6.1 Tasks vencidas prontas para dispatch (o tick do dispatcher)

Respeita janela, prioridade, expiração, `active_hours` da conta no fuso dela, e kill switch por status. `SKIP LOCKED` permite N workers sem coordenação externa.

```sql
WITH claimable AS (
    SELECT t.id
    FROM tasks t
    JOIN accounts a ON a.id = t.account_id
    WHERE t.status IN ('pending', 'scheduled')
      AND t.scheduled_for <= now()
      AND (t.expires_at IS NULL OR t.expires_at > now())
      AND t.attempts < t.max_attempts
      AND a.status = 'active'
      -- só age dentro da janela "acordada", na hora local da persona
      AND EXTRACT(HOUR FROM now() AT TIME ZONE a.timezone)::int
          <@ a.active_hours
    ORDER BY t.priority ASC, t.scheduled_for ASC
    LIMIT 50
    FOR UPDATE OF t SKIP LOCKED
)
UPDATE tasks t
   SET status        = 'dispatched',
       dispatched_at = now(),
       attempts      = t.attempts + 1
  FROM claimable c
 WHERE t.id = c.id
RETURNING t.id, t.account_id, t.type, t.payload, t.campaign_id, t.content_item_id;
```

> `idx_tasks_due` serve o `WHERE`+`ORDER BY`. O `JOIN` com `accounts` usa a PK. O `LIMIT 50` é o tamanho do lote por tick — ajuste com `WORKER_CONCURRENCY`.

### 6.2 Fila de aprovação pendente

Une as três origens de aprovação (conteúdo, campanha, interação) em uma fila só, com idade e tipo de revisão.

```sql
-- Conteúdo aguardando decisão humana
SELECT
    'content_item'                          AS subject_type,
    ci.id                                   AS subject_id,
    a.handle,
    a.archetype,
    a.autonomy,
    ci.kind::text                           AS item_kind,
    left(ci.caption, 120)                   AS preview,
    ci.rights_status,
    ci.claim_status,
    ci.scheduled_for,
    ci.created_at                           AS requested_at,
    now() - ci.created_at                   AS idade,
    ap.sampled
FROM content_items ci
JOIN accounts a         ON a.id = ci.account_id
LEFT JOIN approvals ap  ON ap.subject_type = 'content_item'
                       AND ap.subject_id  = ci.id
                       AND ap.decided_at IS NULL
WHERE ci.status = 'approval_pending'

UNION ALL

-- Interações pendentes de resposta que exigem humano
SELECT
    'interaction', i.id, a.handle, a.archetype, a.autonomy,
    i.kind, left(i.body, 120),
    NULL, NULL, NULL,
    i.received_at, now() - i.received_at,
    false
FROM interactions i
JOIN accounts a ON a.id = i.account_id
WHERE i.replied_at IS NULL
  AND (i.sensitive                       -- sensível nunca é auto-respondido
       OR a.autonomy IN ('L0', 'L1'))    -- L0/L1 mandam tudo para revisão

UNION ALL

-- Campanhas aguardando aval
SELECT
    'campaign', c.id, NULL, NULL, NULL,
    c.kind::text, left(c.brief, 120),
    NULL, NULL, c.window_start,
    c.updated_at, now() - c.updated_at, false
FROM campaigns c
WHERE c.status = 'approval_pending'

ORDER BY idade DESC;
```

> Serve-se de `idx_content_pending_approval`, `idx_interactions_pending` e `idx_campaigns_status`. Ordenar por idade descrescente coloca o que está apodrecendo na fila no topo — é o que evita furo perdido por revisão esquecida.

### 6.3 Recuperação de memória (similaridade × recência × importância)

A fórmula do generative_agents, com os três eixos normalizados em 0..1 e somados com pesos. Recência é decaimento exponencial sobre `occurred_at`.

```sql
-- $1 = account_id
-- $2 = embedding da consulta (vector(1024))
-- $3 = meia-vida da recência em horas (ex.: 72)
-- $4 = quantas memórias devolver (ex.: 12)
WITH scored AS (
    SELECT
        m.id,
        m.kind,
        m.content,
        m.importance,
        m.occurred_at,
        -- 1) RELEVÂNCIA: 1 - distância de cosseno  →  0..1
        (1 - (m.embedding <=> $2::vector))                       AS relevance,
        -- 2) RECÊNCIA: decaimento exponencial, meia-vida $3 horas → 0..1
        exp(
            -ln(2) * (EXTRACT(EPOCH FROM now() - m.occurred_at) / 3600.0) / $3
        )                                                        AS recency,
        -- 3) IMPORTÂNCIA: escala 0..10 atribuída pela LLM  →  0..1
        (m.importance / 10.0)                                    AS importance_norm
    FROM memories m
    WHERE m.account_id = $1              -- NUNCA remover: isolamento entre agentes
      AND m.embedding IS NOT NULL
    ORDER BY m.embedding <=> $2::vector  -- usa o índice HNSW
    LIMIT 200                            -- pré-filtro: top-200 por similaridade
)
SELECT
    id, kind, content, importance, occurred_at,
    round(relevance::numeric, 4)       AS relevance,
    round(recency::numeric, 4)         AS recency,
    round(importance_norm::numeric, 4) AS importance_norm,
    round((1.0 * relevance + 1.0 * recency + 1.0 * importance_norm)::numeric, 4)
        AS score
FROM scored
ORDER BY score DESC
LIMIT $4;
```

**Notas de implementação.**
- Os pesos `1.0 / 1.0 / 1.0` são os do paper. Ajuste por arquétipo: fofoca quer recência dominante (`0.7 / 2.0 / 0.5`); influencer quer narrativa (`1.0 / 0.8 / 1.5`).
- O `LIMIT 200` interno é o que permite usar o índice HNSW: o ranking completo é calculado só sobre os candidatos mais similares. Sem ele, a query vira seq scan com aritmética em toda a tabela.
- `<=>` é distância de cosseno (0 = idêntico). Por isso `1 - distância`.
- Depois de usar as memórias no prompt, marque-as:
  ```sql
  UPDATE memories SET last_used_at = now() WHERE id = ANY($1::uuid[]);
  ```

### 6.4 Reconstruir a história de um post (`events` + `trace_id`)

Do plano do dia à publicação verificada, incluindo cada chamada de LLM e cada intervenção humana.

```sql
-- $1 = posts.id
WITH alvo AS (
    SELECT p.id AS post_id, p.task_id, p.content_item_id, p.account_id
    FROM posts p WHERE p.id = $1
),
-- o trace_id nasce no PLAN e é propagado; pegamos por qualquer elo conhecido
traces AS (
    SELECT DISTINCT e.trace_id
    FROM events e, alvo t
    WHERE e.trace_id IS NOT NULL
      AND (e.task_id = t.task_id OR e.detail->>'content_item_id' = t.content_item_id::text)
)
SELECT * FROM (
    SELECT e.created_at, 'event'    AS fonte, e.type       AS rotulo,
           e.detail                 AS detalhe, e.trace_id
    FROM events e JOIN traces tr ON tr.trace_id = e.trace_id

    UNION ALL

    SELECT l.created_at, 'llm_call',
           l.purpose || ' @ ' || l.backend || '/' || l.model,
           jsonb_build_object('tokens_in',  l.prompt_tokens,
                              'tokens_out', l.output_tokens,
                              'latency_ms', l.latency_ms,
                              'cost_usd',   l.cost_usd,
                              'success',    l.success,
                              'error',      l.error),
           l.trace_id
    FROM llm_calls l JOIN traces tr ON tr.trace_id = l.trace_id

    UNION ALL

    SELECT ap.decided_at, 'approval',
           ap.subject_type || ' → ' || coalesce(ap.decision, 'pendente'),
           jsonb_build_object('by', ap.decided_by, 'note', ap.note,
                              'sampled', ap.sampled),
           NULL
    FROM approvals ap, alvo t
    WHERE ap.subject_id IN (t.content_item_id, t.post_id)

    UNION ALL

    SELECT gv.created_at, 'guardrail',
           gv.rule || ' [' || gv.severity || ']',
           gv.detail, NULL
    FROM guardrail_violations gv, alvo t
    WHERE gv.subject_id IN (t.content_item_id, t.post_id, t.task_id)
) linha_do_tempo
WHERE created_at IS NOT NULL
ORDER BY created_at;
```

> Usa `idx_events_trace`. **É esta consulta que justifica a disciplina de propagar `trace_id`** — se o trace se perde entre o PLAN e o dispatch, a história aparece cortada ao meio. Considere adicionar índice em `llm_calls(trace_id)` (ver [5.4](#54-faltas-conhecidas)).

### 6.5 Custo de LLM por dia e por backend

```sql
SELECT
    date_trunc('day', created_at AT TIME ZONE 'America/Sao_Paulo')::date AS dia,
    backend,
    count(*)                                            AS chamadas,
    count(*) FILTER (WHERE NOT success)                 AS falhas,
    sum(prompt_tokens)                                  AS tokens_entrada,
    sum(output_tokens)                                  AS tokens_saida,
    round(avg(latency_ms))                              AS latencia_media_ms,
    percentile_disc(0.95) WITHIN GROUP (ORDER BY latency_ms) AS latencia_p95_ms,
    round(sum(cost_usd), 4)                             AS custo_usd
FROM llm_calls
WHERE created_at >= now() - interval '30 days'
GROUP BY ROLLUP (dia, backend)
ORDER BY dia DESC NULLS LAST, custo_usd DESC NULLS LAST;
```

Variante operacional — **quanto já gastamos hoje em API externa** (compare com `LLM_DAILY_BUDGET_USD`):

```sql
SELECT
    round(sum(cost_usd), 4) AS gasto_hoje_usd,
    count(*)                AS chamadas_externas
FROM llm_calls
WHERE backend NOT LIKE 'mac_mini%'
  AND created_at >= date_trunc('day', now() AT TIME ZONE 'America/Sao_Paulo');
```

E **onde o dinheiro está indo**, por propósito e por conta:

```sql
SELECT a.handle, l.purpose, count(*) AS chamadas, round(sum(l.cost_usd), 4) AS usd
FROM llm_calls l
LEFT JOIN accounts a ON a.id = l.account_id
WHERE l.created_at >= now() - interval '7 days'
GROUP BY 1, 2
HAVING sum(l.cost_usd) > 0
ORDER BY usd DESC
LIMIT 25;
```

> `idx_llm_calls_time` serve todas as três.

### 6.6 Contas que passaram do teto de ações/hora

Só a família de **execução** conta (produção e coleta local não tocam device). Teto default: `ACCOUNT_MAX_ACTIONS_PER_HOUR = 12`.

```sql
-- $1 = teto de ações/hora (ex.: 12)
WITH acoes AS (
    SELECT
        t.account_id,
        date_trunc('hour', coalesce(t.verified_at, t.dispatched_at)) AS hora,
        count(*) AS n
    FROM tasks t
    WHERE t.type IN (
            'publish_post','publish_story','publish_reels',
            'like','comment','follow','unfollow','save','share',
            'watch_reels','view_stories','send_dm','reply_comment')
      AND t.status IN ('dispatched','running','verified')
      AND coalesce(t.verified_at, t.dispatched_at) >= now() - interval '24 hours'
    GROUP BY 1, 2
)
SELECT
    a.handle, a.platform, a.archetype, a.autonomy,
    ac.hora, ac.n AS acoes_na_hora, $1 AS teto,
    ac.n - $1 AS excedente
FROM acoes ac
JOIN accounts a ON a.id = ac.account_id
WHERE ac.n > $1
ORDER BY excedente DESC, ac.hora DESC;
```

Versão **preventiva**, a ser chamada pelo dispatcher antes de despachar (janela deslizante de 60 min, não hora cheia):

```sql
-- $1 = account_id ; $2 = teto
SELECT count(*) >= $2 AS estourou
FROM tasks
WHERE account_id = $1
  AND type NOT IN ('plan_day','produce_content','reflect')
  AND status IN ('dispatched','running','verified')
  AND coalesce(dispatched_at, created_at) > now() - interval '1 hour';
```

> A versão de hora cheia é **relatório**; a de janela deslizante é **guardrail**. Um farm detectado por rajada de 12 ações em 4 minutos passa ileso pela primeira e é barrado pela segunda.

### 6.7 Campanhas aguardando recon

```sql
SELECT
    c.id,
    c.name,
    c.kind,
    c.status,
    c.target_handle,
    c.target_url,
    c.window_start,
    now() - c.updated_at                     AS parada_ha,
    -- há task de recon? em que estado?
    t.id                                     AS recon_task_id,
    t.status                                 AS recon_task_status,
    t.attempts,
    t.last_error,
    -- há recon já gravado?
    r.captured_at,
    r.analyzed_at,
    r.safety_verdict,
    CASE
        WHEN t.id IS NULL                        THEN 'sem task de recon — criar'
        WHEN t.status = 'failed'                 THEN 'recon falhou — ver last_error'
        WHEN t.status = 'expired'                THEN 'recon expirou — realvo?'
        WHEN r.captured_at IS NULL               THEN 'aguardando captura no device'
        WHEN r.analyzed_at IS NULL               THEN 'capturado, aguardando LLM'
        WHEN r.safety_verdict = 'blocked'        THEN 'BLOQUEADO — abortar campanha'
        ELSE                                          'pronto para planning'
    END                                      AS diagnostico
FROM campaigns c
LEFT JOIN LATERAL (
    SELECT * FROM tasks
    WHERE campaign_id = c.id AND type = 'extract_content'
    ORDER BY created_at DESC LIMIT 1
) t ON true
LEFT JOIN LATERAL (
    SELECT * FROM campaign_recon
    WHERE campaign_id = c.id
    ORDER BY created_at DESC LIMIT 1
) r ON true
WHERE c.status = 'recon_pending'
ORDER BY c.window_start ASC;
```

> Os dois `LATERAL ... ORDER BY created_at DESC LIMIT 1` são a forma correta de pegar "o recon/task mais recente" — lembre que não há `UNIQUE (campaign_id)` em `campaign_recon` ([3.15](#315-campaign_recon)). Campanha em `recon_pending` com `window_start` no passado é incidente: a janela está queimando sem que nada tenha sido planejado.

---

## 7. Convenções de migração

### 7.1 SQL puro, versionado

Sem Alembic, sem autogeração. As migrações são arquivos `.sql` em [`db/migrations/`](../db/migrations/), aplicados em ordem lexicográfica por um runner simples (`make migrate`), que registra cada aplicação em `schema_migrations`.

Por que SQL puro:

- **O schema é o documento.** Alguém que abre `0001_initial.sql` lê o modelo inteiro, com comentários em português explicando *por que* cada campo existe. Um autogenerador produziria `op.add_column(...)` sem contexto.
- **Índices parciais, HNSW, `int4range`, `CHECK`, triggers em `DO $$` — nada disso sobrevive bem a autogeração.** Metade do valor do nosso schema está exatamente nesses detalhes.
- **Revisão de PR é leitura de SQL**, não de diff de modelo Python. É onde um `DROP COLUMN` acidental é pego.
- **Zero dependência de framework de migração** — coerente com a ADR-0001 (core fino).

Custo aceito: escrevemos o `UP` à mão e não temos `DOWN` automático (ver 7.4).

### 7.2 Nomenclatura

```
NNNN_descricao_curta.sql
```

- `NNNN` — quatro dígitos, sequencial, **sem buracos**: `0001`, `0002`, `0003`.
- `descricao_curta` — `snake_case`, em **português**, no infinitivo ou como substantivo do que a migração faz. Consistente com "documentação em português, identificadores em inglês": o nome do arquivo é documentação.
- Sem timestamp no nome. Ordem sequencial explícita evita o clássico "duas branches criaram `20260805_x`" com ordem de aplicação ambígua.

Exemplos válidos:

```
0001_initial.sql
0002_indice_llm_calls_trace.sql
0003_check_kind_content_items.sql
0004_particiona_post_metrics.sql
0005_acorda_ingestao_fase4.sql
```

**Colisão de número** entre branches: quem faz merge por último renumera. É trabalho manual de 30 segundos e é o preço de ter ordem determinística.

### 7.3 Nunca edite migração já aplicada

**Regra absoluta.** Migração aplicada em qualquer ambiente compartilhado (staging, produção — e, na prática, no banco do colega) é imutável.

Mecanismo de defesa: o runner grava `schema_migrations.checksum` (SHA-256 do arquivo) e **compara a cada execução**. Divergência = falha imediata com a lista de arquivos alterados. Não existe flag para ignorar.

Por quê:
- O banco já foi transformado. Editar o arquivo não desfaz o `ALTER TABLE` que rodou ontem — só cria a ilusão de que o histórico é outro.
- Ambientes divergem em silêncio: o seu banco tem a coluna, o de produção não, e o SQL parece o mesmo nos dois lugares.
- A trilha de auditoria do schema morre — e este é um projeto cujo princípio nº 3 é auditoria.

**O que fazer em vez disso:**

| Situação | Ação |
|---|---|
| Erro de digitação em migração já aplicada | Nova migração corrigindo |
| Coluna que não deveria existir | Nova migração com `DROP COLUMN` (e prazo de carência, ver 7.5) |
| Migração ainda **não** aplicada em lugar nenhum, nem no seu banco local, nem em branch de outra pessoa | Pode editar. Este é o único caso |
| Migração aplicada só no seu local, mas já **enviada** | Trate como imutável: outra pessoa pode ter rodado |

### 7.4 Estrutura de um arquivo de migração

```sql
-- =============================================================================
-- Olimpo — Migração 0002: índice de trace em llm_calls
-- =============================================================================
-- Motivo: a consulta de auditoria (docs/02 § 6.4) faz seq scan em llm_calls
-- ao correlacionar por trace_id. Com ~40k chamadas/dia isso já custa ~800ms.
--
-- Reversão: DROP INDEX CONCURRENTLY idx_llm_calls_trace;
-- =============================================================================

CREATE INDEX CONCURRENTLY IF NOT EXISTS idx_llm_calls_trace
    ON llm_calls(trace_id) WHERE trace_id IS NOT NULL;
```

Convenções de conteúdo:

1. **Cabeçalho obrigatório** com número, título, *motivo* e como reverter. O motivo é o que faz a migração ser legível daqui a um ano.
2. **Uma migração = uma mudança coerente.** Não junte "adiciona coluna de fofoca" com "corrige índice de tasks".
3. **`IF NOT EXISTS` / `IF EXISTS`** sempre que possível — torna a reaplicação inofensiva.
4. **Idempotência onde der.** O runner protege com `schema_migrations`, mas migração idempotente sobrevive a intervenção manual atrapalhada.
5. **Sem `DOWN` automático.** Reversão é descrita em comentário e, quando realmente necessária, escrita como uma migração nova (`0009_reverte_0008.sql`). Downgrade automático em produção com dados é ilusão de segurança.
6. **DDL pesada fora de transação.** `CREATE INDEX CONCURRENTLY` e `ALTER TYPE ... ADD VALUE` não rodam dentro de bloco transacional; sinalize no cabeçalho para o runner não envolver o arquivo em `BEGIN/COMMIT`.
7. **Migração de dados vai em arquivo separado** da migração de schema, com `LIMIT`/lote quando a tabela for grande.

### 7.5 Mudanças perigosas: o padrão em três passos

Para qualquer mudança que possa quebrar código em execução (renomear, remover, apertar `NOT NULL`), use expansão → migração → contração, em **três migrações separadas** e três deploys:

| Passo | Migração | Deploy |
|---|---|---|
| **Expandir** | Cria a nova coluna, nullable, com default | Código passa a escrever nos **dois** lugares |
| **Migrar** | Backfill em lotes | Código passa a ler do novo |
| **Contrair** | `DROP` do antigo, aplica `NOT NULL` | Código para de escrever no antigo |

Nunca faça `ALTER TABLE ... RENAME COLUMN` em uma tacada: entre o `COMMIT` e o deploy do código novo existe uma janela em que a aplicação está quebrada.

Casos específicos deste schema:

- **`ALTER TYPE ... ADD VALUE`** (novo valor de ENUM) não é transacional e não é revertível. Adicione o valor numa migração isolada, faça deploy, e só então use o valor.
- **Trocar a dimensão de `memories.embedding`** exige recriar a coluna e re-embeddar toda a memória. Trate como projeto, não como migração ([3.6](#36-memories)).
- **Novo campo mutável** exige adicionar o trigger `touch_updated_at` na mesma migração se a tabela for nova ([3.25](#325-triggers-de-updated_at)).

### 7.6 Checklist de revisão de migração

- [ ] O número é o próximo da sequência, sem buraco e sem colisão.
- [ ] O cabeçalho diz **por que**, não só o quê.
- [ ] Nenhum arquivo já aplicado foi tocado (o checksum confirma).
- [ ] Coluna nova ou tabela nova está documentada em `docs/02` (esta página) na mesma PR.
- [ ] Se dormente, está marcada `[DORMENTE]` **com a fase em que acorda**.
- [ ] Índice novo tem uma consulta real que o justifica — se possível, com `EXPLAIN` antes/depois no corpo da PR.
- [ ] `DROP`/`NOT NULL`/`RENAME` seguem o padrão de três passos ([7.5](#75-mudanças-perigosas-o-padrão-em-três-passos)).
- [ ] Tabela nova e mutável ganhou o trigger de `updated_at`.
- [ ] Rodou em banco limpo (`make reset && make migrate`) **e** em cópia de banco com dados.

---

## Ver também

- [`db/migrations/0001_initial.sql`](../db/migrations/0001_initial.sql) — a fonte da verdade do núcleo
- [`db/migrations/0005_servers_and_services.sql`](../db/migrations/0005_servers_and_services.sql) · [`0006_account_inventory.sql`](../db/migrations/0006_account_inventory.sql) — infraestrutura e inventário ([3.26](#326-infraestrutura-um-servidor-vários-serviços))
- [`db/migrations/0007_personas_e_contas.sql`](../db/migrations/0007_personas_e_contas.sql) — personas, papéis, observações e presença ([3.32](#332-personas) a [3.35](#335-situação--presença-dois-eixos-ortogonais))
- [ADR-0001](adr/0001-core-proprio.md) — por que core próprio; por que o modelo de dados é nosso
- [ADR-0012](adr/0012-servidor-com-varios-servicos.md) — por que servidor e serviço são tabelas diferentes
- [ADR-0013](adr/0013-persona-separada-da-conta.md) — por que a identidade, o login e o lugar onde ele roda são três coisas
- [docs/contratos/android-manager.md](contratos/android-manager.md) — o serviço que alimenta `devices`
- [docs/04 — Persona Card](04-persona-card-spec.md) — o schema de `personas.persona_card`
- [docs/05 — Loop do agente](05-loop-do-agente.md) — quem escreve em qual tabela, em qual passo
- [docs/06 — Campanhas](06-campanhas.md) — `spread_policy`, recon e espalhamento
- [docs/08 — Guardrails e autonomia](08-guardrails-e-autonomia.md) — catálogo de `rule` e política por nível
- [docs/11 — Observabilidade](11-observabilidade.md) — catálogo de `events.type` e propagação de `trace_id`
- [docs/glossario.md](glossario.md) — mapeamento PT ↔ EN
