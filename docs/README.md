# Documentação do Olimpo

Índice mestre. Toda a documentação do projeto vive aqui, em português; identificadores de código e banco são em inglês — o dicionário está no [glossário](glossario.md).

**Fonte da verdade do modelo de dados:** [`db/migrations/0001_initial.sql`](../db/migrations/0001_initial.sql). Quando um documento discordar do schema, o schema está certo e o documento é bug.

---

## Conceito

Por que o projeto existe e como ele pensa. Comece aqui.

| Documento | O que contém |
|---|---|
| [00-visao-geral.md](00-visao-geral.md) | O problema, a tese (por que agente com história bate agendador de posts), os ativos existentes, o loop, os três arquétipos, o roadmap e os cinco princípios inegociáveis |
| [01-arquitetura.md](01-arquitetura.md) | Arquitetura macro: manager, agentes, camada de serviços e a fronteira com o android-orchestrator. Componentes, filas, fluxo de dados e limites de responsabilidade |
| [glossario.md](glossario.md) | Todo termo do projeto definido, com o campo/tabela onde ele vive, mais a tabela de mapeamento PT ↔ EN |

## Especificações

O contrato detalhado de cada peça central.

| Documento | O que contém |
|---|---|
| [02-modelo-de-dados.md](02-modelo-de-dados.md) | Cada tabela e coluna explicada, relacionamentos, enums, índices e por que os campos dormentes nascem na v1 |
| [04-persona-card-spec.md](04-persona-card-spec.md) | Schema do `personas.persona_card` (formato de *character file* estendido), como preencher para cada arquétipo, versionamento, como ele entra no prompt — e a divisão entre a identidade comportamental (card) e a visual (colunas + assets) |
| [05-loop-do-agente.md](05-loop-do-agente.md) | As oito etapas — PLAN, SOURCE, PRODUCE, QA, DISPATCH, VERIFY, RECORD, REFLECT — com entradas, saídas, prompts e o `ContentSource` que varia por arquétipo |

## Subsistemas

Os motores que rodam em cima da espinha.

| Documento | O que contém |
|---|---|
| [06-campanhas.md](06-campanhas.md) | Ciclo brief → recon → análise → plano → espalhamento → execução; `spread_policy` obrigatória, jitter, participação parcial e limite de crossover |
| [07-monitores.md](07-monitores.md) | Vigias 24/7: intervalo, pool de coletores, regras de disparo, dedup, saúde e o pipeline de baixa latência do furo *(Fase 4)* |
| [09-llm-gateway.md](09-llm-gateway.md) | LiteLLM roteando os 2 Mac minis e as APIs externas: aliases lógicos, prioridade de fila, fallback, teto diário e contabilidade em `llm_calls` |
| [10-painel.md](10-painel.md) | Especificação do painel Next.js: princípios de sala de controle, mapa das 9 telas, componentes, tempo real por SSE, design system e anti-requisitos |
| [13-geracao-de-midia.md](13-geracao-de-midia.md) | O subsistema de imagem e vídeo: a fronteira gateway × cliente fal direto, as quatro coisas frágeis da consistência facial, o registro de modelos com as armadilhas por modelo, a fila de geração, o armazenamento e o que não existe |
| [14-motores-de-prompt.md](14-motores-de-prompt.md) | O ativo intelectual absorvido: motores determinísticos sem LLM, as camadas do prompt de imagem, o mecanismo de tags e ordinais, o algoritmo de guarda-roupa, os arquétipos de profissão e a política de porte verificada por teste diferencial |

## Operação

Como manter isso vivo sem quebrar uma conta.

| Documento | O que contém |
|---|---|
| [08-guardrails-e-autonomia.md](08-guardrails-e-autonomia.md) | Níveis L0–L3, o que cada um permite, guardrails que nunca desligam, regras específicas por arquétipo, kill switch e critérios de promoção/rebaixamento |
| [11-observabilidade.md](11-observabilidade.md) | `events` e `trace_id`, métricas do sistema e de negócio, dashboards do Grafana, alertas e como responder "por que a conta X postou isso?" |
| [12-seguranca.md](12-seguranca.md) | Segredos, tokens de serviço, HMAC do webhook do orchestrator, superfície de exposição do painel, isolamento por cliente e retenção de dados |
| [runbooks/](runbooks/) | Procedimentos de plantão: conta banida, orchestrator fora do ar, fila entupida, monitor em erro, estouro de orçamento de LLM, restaurar backup |

## Integrações

Os serviços que o core chama — e nunca duplica.

| Documento | O que contém |
|---|---|
| [03-integracoes.md](03-integracoes.md) | Como o core fala com android-orchestrator, android-manager e media-forge: interface única, retry, dry-run e propagação de `trace_id`. ⚠️ A **máquina de influencers** deixou de ser integração — foi absorvida ([ADR-0014](adr/0014-absorver-a-maquina-de-influencers.md)) e agora é [docs/13](13-geracao-de-midia.md) |
| [contratos/](contratos/) | Um arquivo por serviço com endpoints, payloads e exemplos **reais** de request/response. É o que as fixtures de dry-run e os mocks de teste espelham |

## Processo

Como o projeto avança e o que já foi decidido.

| Documento | O que contém |
|---|---|
| [fases/](fases/) | Um handoff por fase: escopo, fora de escopo, critérios de saída, riscos, o que fica dormente e o que a fase seguinte faz primeiro. Começa em [fase-0-fundacao.md](fases/fase-0-fundacao.md) |
| [adr/](adr/) | Decisões de arquitetura com contexto, alternativas consideradas, consequências e quando revisitar. A primeira é [0001 — core próprio](adr/0001-core-proprio.md) |
| [../CHANGELOG.md](../CHANGELOG.md) | O que mudou, por versão |

### ADRs registradas

| ADR | Decisão |
|---|---|
| [0001](adr/0001-core-proprio.md) | Core próprio e fino, sem fork de framework de agentes; character file e generative_agents como blueprint |
| [0002](adr/0002-separacao-core-execucao.md) | O core nunca fala com a plataforma; toda ação atravessa o android-orchestrator, que verifica |
| [0003](adr/0003-postgres-pgvector-unico.md) | Um Postgres com pgvector para o relacional e o vetorial, sem banco vetorial dedicado |
| [0004](adr/0004-agendamento-no-banco.md) | O agendamento é o Postgres (`tasks.scheduled_for`); o Celery beat só acorda o dispatcher |
| [0005](adr/0005-migracoes-sql-puro.md) | Migrações em SQL puro com runner próprio, não Alembic |
| [0006](adr/0006-arquetipos-e-contentsource.md) | `ContentSource` como única peça variável entre arquétipos; campos dormentes desde a v1 |
| [0007](adr/0007-spread-policy-obrigatoria.md) | Espalhamento obrigatório: `campaigns.spread_policy` é `NOT NULL` |
| [0008](adr/0008-recon-antes-da-campanha.md) | Campanha de engajamento exige recon: olhar o conteúdo antes de planejar |
| [0009](adr/0009-niveis-de-autonomia.md) | L0–L3 como propriedade da conta, com promoção por histórico; `gossip` nunca chega a L3 |
| [0010](adr/0010-llm-gateway-litellm.md) | LiteLLM como gateway de LLM, com prioridade de fila e overflow para API externa |
| [0011](adr/0011-polling-e-registro-de-nos.md) | Polling e registro de nós: sem webhook do orchestrator, a chave externa é `(node_id, external_task_id)` |
| [0012](adr/0012-servidor-com-varios-servicos.md) | Um servidor roda vários serviços: `servers → server_services`, e `devices` vira tabela de verdade |
| [0013](adr/0013-persona-separada-da-conta.md) | A persona é separada da conta: identidade, login e lugar onde ele roda são três coisas |
| [0014](adr/0014-absorver-a-maquina-de-influencers.md) | Absorver a máquina de influencers: a identidade visual vira coluna e asset, os motores de prompt são portados sem alteração semântica |

> A tabela completa, com resumo do trade-off de cada decisão, está em [adr/README.md](adr/README.md).

---

## Por onde começar

### 🛠 Dev novo no projeto

Objetivo: entender o modelo mental e conseguir rodar o loop em dry-run no primeiro dia.

| # | Leia | Por quê |
|---|---|---|
| 1 | [00-visao-geral.md](00-visao-geral.md) | A tese e os princípios. Sem isso as decisões do código parecem arbitrárias |
| 2 | [adr/0001-core-proprio.md](adr/0001-core-proprio.md) | Por que não forkamos ElizaOS/Letta/LangChain. Responde 80% dos "por que não usar X?" |
| 3 | [glossario.md](glossario.md) | O vocabulário. Volte aqui sempre que um termo não fechar |
| 4 | [01-arquitetura.md](01-arquitetura.md) | O mapa dos componentes e as fronteiras |
| 5 | [`db/migrations/0001_initial.sql`](../db/migrations/0001_initial.sql) | **Leia o schema inteiro, com os comentários.** É o documento mais denso do projeto |
| 6 | [05-loop-do-agente.md](05-loop-do-agente.md) + [02-modelo-de-dados.md](02-modelo-de-dados.md) | O loop e onde cada etapa escreve |
| 7 | [fases/fase-0-fundacao.md](fases/fase-0-fundacao.md) | O que existe hoje, o que está dormente e os critérios de saída da fase atual |
| 8 | [08-guardrails-e-autonomia.md](08-guardrails-e-autonomia.md) | O que o código nunca pode deixar acontecer |

**Depois:** `cp .env.example .env && make up && make seed`. Acompanhe pelo painel em http://localhost:3000 e confira o rastro em `events` filtrando pelo `trace_id`. O executor do loop por linha de comando chega na Fase 1 — ver [fases/fase-1-primeira-influencer.md](fases/fase-1-primeira-influencer.md).

> Antes do primeiro PR: [04-persona-card-spec.md](04-persona-card-spec.md) se for mexer em prompt de texto, [13-geracao-de-midia.md](13-geracao-de-midia.md) + [14-motores-de-prompt.md](14-motores-de-prompt.md) **se for mexer em qualquer coisa de imagem ou vídeo** (a consistência facial quebra em silêncio), [03-integracoes.md](03-integracoes.md) + [contratos/](contratos/) se for mexer em cliente externo, [10-painel.md](10-painel.md) se for mexer no painel.

### 🎛 Operador do dia a dia

Objetivo: aprovar, supervisionar e intervir sem quebrar conta.

| # | Leia | Por quê |
|---|---|---|
| 1 | [00-visao-geral.md §7](00-visao-geral.md) — princípios inegociáveis | O que jamais se contorna, nem sob pressa |
| 2 | [10-painel.md](10-painel.md) — telas 1, 2 e 3 | Sala de controle, ficha da conta e fila de aprovação: onde 90% do seu tempo acontece. Inclui os atalhos de teclado |
| 3 | [08-guardrails-e-autonomia.md](08-guardrails-e-autonomia.md) | O que cada nível permite e quando promover ou rebaixar uma conta |
| 4 | [glossario.md](glossario.md) — dry-run, task verificada, claim status, rights status, kill switch | Os termos que aparecem nos botões que você aperta |
| 5 | [06-campanhas.md](06-campanhas.md) — recon e espalhamento | O que você está aprovando quando aprova uma campanha |
| 6 | [runbooks/](runbooks/) | Leia **antes** de precisar |
| 7 | [11-observabilidade.md](11-observabilidade.md) | Como investigar "por que ela postou isso?" e o que os alertas significam |

**Regras que valem sempre:** faixa âmbar na tela significa dry-run — nada está sendo publicado de verdade. Rejeição sempre com motivo (ele realimenta o QA). Fofoca nunca em lote. Interação marcada como sensível nunca é auto-respondida — nem por você "só desta vez". Fila crescendo é sintoma de autonomia mal calibrada, não de operador lento.

### 🔌 Quem vai integrar um serviço novo

Objetivo: plugar um serviço externo sem furar a fronteira do core.

| # | Leia | Por quê |
|---|---|---|
| 1 | [adr/0001-core-proprio.md](adr/0001-core-proprio.md) | A regra-mãe: o core nunca fala com a plataforma; execução é assíncrona, remota e verificada |
| 2 | [03-integracoes.md](03-integracoes.md) | O padrão obrigatório: `ServiceClient` sobre httpx + tenacity, timeout, retry com backoff, `trace_id` e o shim de dry-run |
| 3 | [contratos/](contratos/) | O formato do documento que você precisa escrever **antes** de escrever o cliente |
| 4 | [02-modelo-de-dados.md](02-modelo-de-dados.md) | Onde o resultado do seu serviço aterrissa. Não crie tabela nova antes de checar se o campo já existe dormente |
| 5 | [09-llm-gateway.md](09-llm-gateway.md) | Se o serviço usa LLM, ele passa pelo gateway — não abre cliente próprio nem chave própria |
| 6 | [12-seguranca.md](12-seguranca.md) | Token, segredo e validação de webhook |
| 7 | [11-observabilidade.md](11-observabilidade.md) | Que eventos emitir e que métricas expor |

**Checklist de integração:**

- [ ] Contrato documentado em `docs/contratos/<servico>.md` com pelo menos um request e um response **reais** — não inventados
- [ ] Cliente em `olimpo/integrations/<servico>.py`, herdando de `ServiceClient`
- [ ] Toda chamada de escrita respeita `DISPATCH_ENABLED=false`, grava em `dryrun/<servico>/` e devolve resposta sintética derivada dos exemplos reais
- [ ] `trace_id` propagado em header e registrado nos eventos
- [ ] URL e token em `.env.example`, com valor vazio e comentário
- [ ] Toda decisão relevante emite linha em `events`
- [ ] Mock com `respx` em `tests/`, espelhando o contrato
- [ ] Se o serviço executa ação em conta real: o resultado só é confiado após verificação — nada de assumir sucesso por HTTP 200
- [ ] Sem stub vazio de serviço de fase futura (ver `fases/fase-0-fundacao.md §7`)

---

## Convenções da documentação

| Regra | Detalhe |
|---|---|
| Idioma | Prosa em português do Brasil; identificadores em inglês. Ver [glossário §3](glossario.md) |
| Numeração | `NN-nome.md` define ordem de leitura. `00`–`14` reservados; documentos sem número são transversais (glossário, este índice) |
| Schema | Nenhum documento redefine schema. Todos citam `db/migrations/0001_initial.sql` |
| Decisão | Escolha de arquitetura vira ADR, não parágrafo perdido em documento de subsistema |
| Fase | Todo recurso indica em que fase acorda. Campo dormente é marcado como tal |
| Tabelas | Preferir tabela markdown a lista quando houver mais de dois atributos por item |
| Sem placeholder | Documento incompleto é marcado com `> **Status:** rascunho` no topo, nunca com "TODO" solto no meio |
