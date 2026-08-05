# 03 — Integrações

> Como os serviços externos se encaixam no loop do agente, qual o padrão comum de cliente, e o caminho completo de uma publicação — da decisão até a verificação.
>
> Os contratos detalhados de cada serviço estão em [`docs/contratos/`](contratos/).

---

## 1. Por que o Olimpo depende de serviços externos

O Olimpo é o cérebro. Ele **não** fala com o Instagram, **não** renderiza pixel e **não** roda ffmpeg. Ele decide *o quê / quando / por quê*, e delega:

| Precisa de | Serviço | Estado |
|---|---|---|
| Agir na plataforma | [android-orchestrator](contratos/android-orchestrator.md) | existe — ✅ contrato **validado contra o código em 2026-08-05** |
| Criar mídia com um rosto consistente | [máquina de influencers](contratos/maquina-de-influencers.md) | existe |
| Saber se o device está de pé | [android-manager](contratos/android-manager.md) | existe |
| Cortar e editar vídeo | [media-forge](contratos/media-forge.md) | **a construir (Fase 5)** |

Essa separação não é acidente de organização — é [a decisão de arquitetura do projeto](../PLANO.md#2-decisão-de-arquitetura). Todo framework de agente social assume que **ele** fala com a plataforma. Aqui, quem fala é uma frota de Android real. É o que permite fazer coisas que uma API oficial não permite, e é o motivo de o core ser próprio e fino.

O preço dessa escolha é acoplamento com quatro serviços. O resto deste documento é sobre como pagar esse preço de forma controlada.

### 1.1 O orchestrator não é um serviço — é uma **frota de nós**

✅ **VALIDADO em 2026-08-05.** Isto muda o desenho de tudo o que vem depois:

- Cada máquina é um **nó independente**, com dois processos: **worker `:8078`** (o único com que falamos) e **executor `:8077`** (monta o `docker.sock`, escuta só em loopback — nunca tocar).
- Um nó controla **apenas os containers redroid do próprio host**: descobre por `docker ps` local, executa por `docker exec` local. **Não existe índice de frota.**
- **O dono da lista de nós é o Olimpo** (tabela `orchestrator_nodes`). A visão de frota se monta por **fan-out**: bater `/accounts`, `/devices` e `/actions` em cada nó e agregar.
- **Não existe webhook.** O modelo é **polling** — ver [§4](#4-o-fluxo-de-uma-publicação-ponta-a-ponta).
- Ids de task são inteiros autoincrement **por nó**. A chave real é o par **`(node_id, external_task_id)`**.

Detalhes completos no [contrato](contratos/android-orchestrator.md) e na migração [`0002_orchestrator_nodes.sql`](../db/migrations/0002_orchestrator_nodes.sql).

---

## 2. Onde cada integração entra no loop

O loop é o mesmo para influencer, fofoca e clipagem. Só o passo `SOURCE` muda.

| # | Passo | Roda onde | Integrações | Escreve |
|---|---|---|---|---|
| 1 | **PLAN** | core (LLM) | — | `day_plans` |
| 2 | **SOURCE** | core + serviço | ▸ influencer → **máquina de influencers**<br>▸ fofoca → ingestão + **orchestrator** (`instagram.reel-download`, `watch-reels` dry-run, `profile-stats`)<br>▸ clipagem → **media-forge** | `content_items` |
| 3 | **PRODUCE** | core (LLM) | — | `content_items.caption` |
| 4 | **QA** | core | — (usa a mídia já baixada) | `content_items.qa_report` |
| 5 | **DISPATCH** | core → serviços | **android-manager** (gate) → **orchestrator: escolhe o NÓ** → `POST /tasks` | `tasks` (com `node_id`) |
| 6 | **VERIFY** | core → serviço | **orchestrator: POLLING** `GET /tasks/{id}` → `GET /jobs/{id}` | `tasks.status`, `evidence_level`, `posts` |
| 7 | **RECORD** | core + serviço | **orchestrator** (`instagram.profile-stats` / `post-insights`) | `post_metrics`, `memories` |
| 8 | **REFLECT** | core (LLM) | — | `memories`, `story_arcs` |

```
                    ┌──────────── O CORE (Olimpo) ────────────┐
                    │                                          │
      PLAN ────────▶│  decide o dia                            │
                    │                                          │
      SOURCE ──────▶│  ┌────────────────────────────────────┐  │
                    │  │ GeneratedSource ──▶ 🎨 persona eng.│  │
                    │  │ CuratedSource   ──▶ 🤖 orchestrator│  │
                    │  │ DerivedSource   ──▶ ✂️  media-forge│  │
                    │  └────────────────────────────────────┘  │
                    │                                          │
      PRODUCE ─────▶│  caption na voz da persona (LLM local)   │
      QA ──────────▶│  facial · textual · brand · legal        │
                    │                                          │
      DISPATCH ────▶│  🖥️  android-manager (device de pé?)     │
                    │  🧭 escolhe o NÓ (account_placements)    │
                    │  🤖 POST /tasks no worker daquele nó     │
                    │                                          │
      VERIFY ──────▶│  🔁 POLLING: GET /tasks/{id}             │
                    │     └─▶ GET /jobs/{id}  → evidence_level │
      RECORD ──────▶│  🤖 profile-stats · post-insights        │
      REFLECT ─────▶│  memória e história do dia (LLM local)   │
                    └──────────────────────────────────────────┘

                 ┌─────────── A CAMADA DE NÓS ───────────┐
                 │ orchestrator_nodes (a lista é NOSSA)  │
                 │        fan-out de inventário          │
                 │  GET /accounts · /devices · /actions  │
                 └──────┬─────────────────────┬──────────┘
                   ┌────┴──────┐        ┌─────┴───────┐
                   │ nó "i9"   │        │ nó "fd-1"   │
                   │ :8078     │        │ :8078 +Basic│
                   │ 4 redroids│        │ 2 redroids  │
                   └───────────┘        └─────────────┘
```

Note as setas do `VERIFY`: **elas saem do core**. Não há nada empurrando estado para dentro do Olimpo — quem pergunta somos nós.

**Observação que vale por si:** três dos oito passos não tocam serviço nenhum, e o passo mais caro do sistema (`PRODUCE`, `REFLECT`) roda em LLM local. As integrações são pontuais e bem delimitadas — foi assim que o loop foi desenhado.

---

## 3. O padrão comum de cliente

Os quatro clientes seguem os mesmos oito princípios, detalhados em [contratos/README.md](contratos/README.md#princípios-de-integração-valem-para-os-4-clientes-sem-exceção). Resumo operacional:

| Princípio | Regra | Onde vive |
|---|---|---|
| **Interface** | Todo cliente atrás de um `Protocol`. O core nunca importa `httpx` | `integrations/*/protocol.py` |
| **Dry-run** | `DISPATCH_ENABLED=false` → grava em `dryrun/`, devolve resposta sintética | `integrations/base/dryrun.py` |
| **Retry** | `tenacity`, backoff exponencial **com jitter**, só em erro transitório | `integrations/base/http.py` |
| **Timeout** | Explícito por operação. Nunca herdar default de biblioteca | idem |
| **Idempotência** | `Idempotency-Key: olimpo:<tipo>:<uuid>` derivada do nosso id — ⚠️ **o android-orchestrator NÃO honra nenhuma chave de idempotência**: lá a deduplicação é 100% nossa, por lock no Redis antes do dispatch | `integrations/base/idempotency.py` |
| **Log** | Toda chamada em `events`, antes e depois, com `trace_id` | `integrations/base/http.py` |
| **Circuit breaker** | Estado no Redis, compartilhado entre workers | `integrations/base/breaker.py` |
| **Erro traduzido** | O core nunca vê exceção HTTP crua | `integrations/base/errors.py` |

### A interface, na prática

```python
# src/olimpo/integrations/orchestrator.py
from typing import Any, Protocol

class ExecutionBackend(Protocol):
    """UMA instância por NÓ — a credencial Basic é opcional e difere por nó."""
    async def create_task(self, *, account: str, action: str,
                          params: dict[str, Any], scheduled_at: str | None,
                          priority: int) -> dict[str, Any]: ...
    async def get_task(self, task_id: int | str) -> dict[str, Any]: ...    # polling
    async def cancel_task(self, task_id: int | str) -> dict[str, Any]: ...
```

O agente recebe o backend por injeção — **nunca uma URL**. Em produção chega um `OrchestratorClient` por nó ativo, resolvido a partir de `orchestrator_nodes`; em dry-run, o mesmo cliente com o writer ligado; em teste, um fake em memória.

Duas particularidades deste cliente que **não** valem para os outros três:

- **Ele fala com um nó, não com um serviço.** Quem escolhe o nó é o dispatcher, consultando `account_placements` — a mesma conta pode viver em nós diferentes.
- **O resultado real não está na resposta da task.** `GET /tasks/{id}` diz que terminou; `GET /jobs/{last_job_id}` diz se **teve efeito**. São dois bancos diferentes, em dois processos diferentes.

```python
class Agent:
    def __init__(self, orchestrator: OrchestratorClient, persona: PersonaEngineClient, ...):
        self._orchestrator = orchestrator   # nunca uma URL, nunca um httpx.Client
```

Isso é o que contém o risco *"acoplamento com os 3 serviços"* do [PLANO §9](../PLANO.md): quando a API real divergir do que os docs assumem — e vai divergir, porque foram escritos sem acesso ao código — o dano fica no adaptador.

### Jitter no retry não é detalhe

Backoff sem jitter faz N tasks que falharam juntas retentarem juntas. Isso recria exatamente o padrão coordenado que a `spread_policy` existe para destruir. **Todo retry do Olimpo tem jitter**, inclusive o de infraestrutura.

### Circuit breaker: o que cada serviço caindo significa

| Serviço fora | Efeito | Reação |
|---|---|---|
| **um nó** do orchestrator | as contas **daquele host** param | nó → `unreachable`; tasks reroteadas para outro nó que tenha a mesma conta (`account_placements`); sem alternativa, ficam em `scheduled` |
| **todos os nós** | nada publica | tasks ficam em `scheduled`; `expired` só ao passar do nosso `expires_at` |
| máquina de influencers | não há mídia nova | produção adiada; usa banco de conteúdo aprovado |
| android-manager | não se sabe o estado da frota | **não despacha às cegas**; reagenda |
| media-forge | lote de clipagem atrasa | job pendente; latência tolerável |

Regra comum: **nenhum serviço fora do ar vira `tasks.failed` em massa.** Falha de infraestrutura é adiamento, não fracasso de task. O que separa os dois é `expires_at` — e ele existe justamente para que "adiar" tenha um limite.

---

## 4. O fluxo de uma publicação, ponta a ponta

Caso concreto: a influencer `@luna.corre` publica um reels às 19h.

```
 ┌─────────────────────────────────────────────────────────────────────────────┐
 │ 06:00  PLAN                                                    [core, LLM]  │
 │        lê memories + story_arcs + métricas + campanhas do manager           │
 │        decide: "hoje: reels da corrida do amanhecer, tom cansado-satisfeito"│
 │        ▼ grava day_plans                                                    │
 └─────────────────────────────────────────────────────────────────────────────┘
                                    │
 ┌─────────────────────────────────────────────────────────────────────────────┐
 │ 06:10  SOURCE — GeneratedSource                        [🎨 persona engine]  │
 │        POST /v1/personas/{id}/videos                                        │
 │        consistency: { face_reference_id, seed: 884213771 }                  │
 │        ◀── 202 { job_id, estimated_seconds: 300 }                           │
 │        polling ... 4 min ... ◀── completed { url, sha256 }                  │
 │        ▼ Olimpo BAIXA a mídia para storage próprio                          │
 └─────────────────────────────────────────────────────────────────────────────┘
                                    │
 ┌─────────────────────────────────────────────────────────────────────────────┐
 │ 06:15  PRODUCE                                                 [core, LLM]  │
 │        caption na voz da persona + hashtags + escolha de áudio (trends)     │
 │        ▼ content_items (status: draft)                                      │
 └─────────────────────────────────────────────────────────────────────────────┘
                                    │
 ┌─────────────────────────────────────────────────────────────────────────────┐
 │ 06:18  QA                                                          [core]   │
 │        ├─ facial: embedding vs. canônico ──▶ 0.94  ✅  (gate: 0.90)         │
 │        ├─ textual: persona · tabus · brand safety  ✅                       │
 │        └─ legal: rights_status=own · claim_status n/a  ✅                    │
 │        ▼ status: approved   (em L0 seria approval_pending → painel)         │
 └─────────────────────────────────────────────────────────────────────────────┘
                                    │
 ┌─────────────────────────────────────────────────────────────────────────────┐
 │ 06:20  AGENDAMENTO                                                 [core]   │
 │        respeita active_hours [8,23) · cadence · jitter da spread_policy     │
 │        ▼ tasks(publish_reels, scheduled_for=19:04:31, expires_at=23:00)     │
 │          status: pending            ⏳ dorme no banco até a janela          │
 └─────────────────────────────────────────────────────────────────────────────┘
                                    │
 ┌─────────────────────────────────────────────────────────────────────────────┐
 │ 19:04  DISPATCH                                                             │
 │        tick do dispatcher (idx_tasks_due) seleciona a task                  │
 │        ├─ GLOBAL_KILL_SWITCH?  ──▶ não                                      │
 │        ├─ conta ativa? guardrail? teto de ações/hora?  ──▶ ok               │
 │        ├─ lock no Redis por (account, action) — dedup é NOSSA               │
 │        │                                                                    │
 │        ├─ 🖥️  android-manager: device de pé?  ──▶ ok                        │
 │        │                                                                    │
 │        ├─ 🧭 ESCOLHE O NÓ: account_placements(luna.corre)                   │
 │        │      ◀── nó "i9" (priority 100, 2 raias livres)                    │
 │        │                                                                    │
 │        └─ 🤖 POST http://100.64.0.7:8078/tasks   (Basic, se o nó exigir)    │
 │              { action: "instagram.post-reel",        ← QUALIFICADA          │
 │                account: "luna.corre",                ← roteamento           │
 │                params: { video: …, caption: …,       ← NUNCA vazio          │
 │                          publish: true },            ← senão só monta       │
 │                priority: 7 }                                                │
 │              ◀── 201 { id: 42, status: "pending", … }                       │
 │        ▼ tasks: node_id · external_task_id=42 · status=dispatched           │
 │        ▼ next_poll_at = agora + 30s                                         │
 └─────────────────────────────────────────────────────────────────────────────┘
                                    │
 ┌─────────────────────────────────────────────────────────────────────────────┐
 │ 19:04  ⋯ o nó age ⋯                        [worker → executor → redroid]    │
 │        o nó acha o redroid onde a conta está logada e dispara no 1º LIVRE   │
 │        (um device = uma raia: as outras contas daquele container esperam)   │
 │        abre o app · navega · sobe o vídeo · cola a caption · publica        │
 └─────────────────────────────────────────────────────────────────────────────┘
                                    │
 ┌─────────────────────────────────────────────────────────────────────────────┐
 │ 19:05  POLL  (não existe webhook — quem pergunta somos nós)                 │
 │        GET /tasks/42  ◀── { status: "running", last_job_id: "8d50577a…" }   │
 │        ▼ tasks.status = running · next_poll_at = +30s                       │
 └─────────────────────────────────────────────────────────────────────────────┘
                                    │
 ┌─────────────────────────────────────────────────────────────────────────────┐
 │ 19:07  VERIFY                                                               │
 │        GET /tasks/42  ◀── { status: "done", last_job_id: "8d50577a…" }      │
 │                                                                             │
 │        ⚠️ a TASK diz que terminou. O JOB diz se TEVE EFEITO:                │
 │        GET /jobs/8d50577a…                                                  │
 │          ◀── { ok: true, exit_code: 0, status: "done",                      │
 │                data: { published: true, verified: true, upload: {…} } }     │
 │                                                                             │
 │        read_evidence(job) ──▶ ("verified", { published, verified })         │
 │        ▼ tasks.status = verified · evidence_level = 'verified'              │
 │        ▼ posts(published_at)   ❓ permalink: ver contrato §7.4              │
 │                                                                             │
 │   ⚠️  ok:true SEM data.verified ──▶ evidence_level='reported'.              │
 │       Para publicação isso NÃO é sucesso: vai para verificação.             │
 │   ⚠️  status 'unknown' ──▶ timeout/restart. PODE ter publicado.             │
 │       NUNCA retentar. Fila de revisão humana.                               │
 │   ⚠️  'pending' sem job após o timeout da ação ──▶ TASK FANTASMA (alerta)   │
 └─────────────────────────────────────────────────────────────────────────────┘
                                    │
 ┌─────────────────────────────────────────────────────────────────────────────┐
 │ 22:00  RECORD                                                               │
 │        instagram.profile-stats (perfil) · instagram.post-insights (post     │
 │        próprio, SÓ conta profissional) — cada um é uma task nova, com o     │
 │        mesmo ciclo de polling acima                                         │
 │        ▼ post_metrics · accounts.followers                                  │
 └─────────────────────────────────────────────────────────────────────────────┘
                                    │
 ┌─────────────────────────────────────────────────────────────────────────────┐
 │ 23:30  REFLECT                                                 [core, LLM]  │
 │        "o reels do amanhecer foi o melhor da semana; o povo respondeu ao    │
 │         tom cansado, não ao treino em si"                                   │
 │        ▼ memories(episodic) · story_arcs.state · realimenta o PLAN de amanhã│
 └─────────────────────────────────────────────────────────────────────────────┘

 Ao longo de TODO o fluxo:  events(trace_id="olimpo-2026-08-05-7f3a91")
 ─ é o que responde "por que a conta X postou isso às 19h?"
```

### Os quatro pontos onde este fluxo quebra

| # | Ponto | Sintoma | Defesa |
|---|---|---|---|
| 1 | Geração lenta ou falha | 19h chega sem mídia | produção às 6h, **12 h de folga**; banco de conteúdo aprovado em reserva |
| 2 | **Task fantasma** — ação não qualificada ou `params` vazio | task fica `pending` **para sempre**, sem job e **sem nenhum erro na API** (o nó só faz `log.warning`) | cliente recusa ação sem `.`; injeta `{"account": …}` como piso; **alerta** de `pending` sem job após o `timeout` da ação |
| 3 | Timeout de rede no `POST /tasks` | publicou? não publicou? **e não temos o id** | ❌ não há `Idempotency-Key` nem consulta por id nosso → lock no Redis **antes**, e recuperação por `GET /tasks` do nó casando `(account, action, created_at)` |
| 4 | `ok: true` sem prova (`evidence_level = reported`) | métrica e memória contaminadas | regra dura: publicação só vira `verified` com `data.verified`/`published`. Caso contrário, verificação |
| 5 | Job em `unknown` (timeout / restart) | **pode** ter publicado | nunca retry automático em ação `publishes: true` → revisão humana |
| 6 | Retry em cascata do próprio nó | até **3 × 3 = 9** execuções da mesma ação — 9 comentários idênticos | `ORCH_TASK_MAX_ATTEMPTS=1` nos nós; retry mora no Olimpo, com reavaliação de guardrail |

Os pontos 3 e 6 são os mais traiçoeiros, e são a **mesma** falha vista de dois lados: **não existe idempotência em lugar nenhum do orchestrator**. Um despacho retentado às cegas — por nós ou pelo nó — publica o mesmo reels duas vezes. Além de constrangedor, é assinatura óbvia de automação.

---

## 5. Dry-run: o loop inteiro sem tocar em conta nenhuma

`DISPATCH_ENABLED=false` é o **default** e assim permanece até a Fase 1 estar validada.

```
DISPATCH_ENABLED=false
        │
        ├─ orchestrator.create_task() ─▶ dryrun/2026-08-05/orchestrator/19-04-31_post-reel.json
        │                                 devolve { id: -1042, status: "pending" }
        ├─ persona.generate_video()  ──▶ dryrun/…/persona_engine/06-10-02_generate_video.json
        │                                 devolve URL de mídia placeholder
        ├─ manager.get_account()     ──▶ devolve device sintético saudável
        └─ media_forge.submit()      ──▶ devolve job sintético concluído
```

Propriedades que fazem isso funcionar:

- A resposta sintética usa **o mesmo schema pydantic** da real. Se o modelo mudar, o fake muda junto.
- `external_task_id` sintético é **negativo** — o serviço real só emite inteiros positivos, então é impossível confundir no banco. (O prefixo `dry_` de antes não serve: o id é um inteiro.)
- Cada chamada vira linha em `events` (`type = "<servico>.dryrun"`), então o painel mostra a **mesma timeline** em dry-run e em produção.
- **O polling é simulado**, não o webhook: um fake responde `GET /tasks/{id}` com `pending → running → done` ao longo de alguns ticks, e `GET /jobs/{id}` com um `data` plausível **por ação** — inclusive `data: null` para `like`/`comment`, que é o comportamento real. É isso que faz `VERIFY` e `read_evidence()` acontecerem de verdade sem serviço nenhum de pé.
- O fake também reproduz os modos de falha que importam: `unknown`, `ok: true` sem `data.verified`, e a **task fantasma** (`pending` eterno sem job). Se o Olimpo não sobrevive a eles em dry-run, não sobrevive em produção.

O critério de saída da Fase 0 é literalmente isto: *"task `criar_post` roda ponta a ponta em dry-run"*. Todos os oito passos, todos os quatro clientes, zero requisições de rede.

---

## 6. Estado dos contratos

| Serviço | Doc | Estado | Próximo passo |
|---|---|---|---|
| android-orchestrator | [contrato](contratos/android-orchestrator.md) | ✅ **VALIDADO em 2026-08-05** contra o código. 6 ❓ residuais, nenhum bloqueante | ⚠️ **smoke test por ação** — ver abaixo |
| máquina de influencers | [contrato](contratos/maquina-de-influencers.md#10-perguntas-para-validar-com-o-serviço-real) | escrito às cegas — 8 ❓ bloqueantes | a nº 1 (consistência facial) é a mais importante |
| android-manager | [contrato](contratos/android-manager.md#10-o-que-continua-incerto) | escrito às cegas — 4 ❓ | confirmar se existe API HTTP |
| media-forge | [especificação](contratos/media-forge.md) | não existe; a spec **é** o contrato | — |

**Regra de trabalho:** documento com ❓ aberto no caminho crítico significa cliente que **não sai de dry-run**.

### ⚠️ O orchestrator trocou de risco, não saiu dele

O contrato agora está certo. O que **não** está garantido é o comportamento: segundo o `KNOWN_ISSUES.md` do próprio serviço, **quase toda a superfície Instagram nunca foi executada no device** depois da unificação do código. Validado ponta a ponta: apenas `whoami`, `list-accounts`, `like`, `comment`, `dm` — e, pelo serviço inteiro (`POST /tasks` → device → recibo), apenas `instagram.whoami`.

**Não testado:** `post-feed`, `post-story`, `post-reel`, `watch-reels`, `reel-download`, `follow`, `unfollow`, `profile-stats`, `save-post`, `reply`, e mais.

Postura: **"contrato escrito, comportamento não verificado"**. Cada `task_type` só sai de dry-run depois de um **smoke test verde registrado** (com `job_id` e `data` anexados ao evento). Detalhes e ordem sugerida em [contratos/android-orchestrator.md §15](contratos/android-orchestrator.md#15-aviso-quase-nada-foi-executado-no-device).

---

## Ver também

- [contratos/](contratos/) — os quatro contratos, com princípios comuns no [README](contratos/README.md)
- [01-arquitetura.md](01-arquitetura.md) — arquitetura macro
- [02-modelo-de-dados.md](02-modelo-de-dados.md) — `tasks`, `events`, `posts`, `device_binding`
- [05-loop-do-agente.md](05-loop-do-agente.md) — o loop em detalhe
- [06-campanhas.md](06-campanhas.md) — recon e espalhamento anti-detecção
- [08-guardrails-e-autonomia.md](08-guardrails-e-autonomia.md) — o que pode ser despachado em cada nível
- [11-observabilidade.md](11-observabilidade.md) — métricas e alertas das integrações
