# 03 — Integrações

> Como os serviços externos se encaixam no loop do agente, qual o padrão comum de cliente, e o caminho completo de uma publicação — da decisão até a verificação.
>
> Os contratos detalhados de cada serviço estão em [`docs/contratos/`](contratos/).

---

## 1. Por que o Olimpo depende de serviços externos

O Olimpo é o cérebro. Ele **não** fala com o Instagram, **não** renderiza pixel e **não** roda ffmpeg. Ele decide *o quê / quando / por quê*, e delega:

| Precisa de | Serviço | Estado |
|---|---|---|
| Agir na plataforma | [android-orchestrator](contratos/android-orchestrator.md) | existe |
| Criar mídia com um rosto consistente | [máquina de influencers](contratos/maquina-de-influencers.md) | existe |
| Saber se o device está de pé | [android-manager](contratos/android-manager.md) | existe |
| Cortar e editar vídeo | [media-forge](contratos/media-forge.md) | **a construir (Fase 5)** |

Essa separação não é acidente de organização — é [a decisão de arquitetura do projeto](../PLANO.md#2-decisão-de-arquitetura). Todo framework de agente social assume que **ele** fala com a plataforma. Aqui, quem fala é uma frota de Android real. É o que permite fazer coisas que uma API oficial não permite, e é o motivo de o core ser próprio e fino.

O preço dessa escolha é acoplamento com quatro serviços. O resto deste documento é sobre como pagar esse preço de forma controlada.

---

## 2. Onde cada integração entra no loop

O loop é o mesmo para influencer, fofoca e clipagem. Só o passo `SOURCE` muda.

| # | Passo | Roda onde | Integrações | Escreve |
|---|---|---|---|---|
| 1 | **PLAN** | core (LLM) | — | `day_plans` |
| 2 | **SOURCE** | core + serviço | ▸ influencer → **máquina de influencers**<br>▸ fofoca → ingestão + **orchestrator** (`extract_content`)<br>▸ clipagem → **media-forge** | `content_items` |
| 3 | **PRODUCE** | core (LLM) | — | `content_items.caption` |
| 4 | **QA** | core | — (usa a mídia já baixada) | `content_items.qa_report` |
| 5 | **DISPATCH** | core → serviços | **android-manager** (gate) → **android-orchestrator** | `tasks` |
| 6 | **VERIFY** | serviço → core | **android-orchestrator** (webhook/polling) | `tasks.status`, `posts` |
| 7 | **RECORD** | core + serviço | **orchestrator** (`collect_metrics`) | `post_metrics`, `memories` |
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
                    │  🤖 android-orchestrator (executa)       │
                    │                                          │
      VERIFY ◀──────│  🤖 webhook de conclusão + reconciliador │
      RECORD ──────▶│  🤖 collect_metrics                      │
      REFLECT ─────▶│  memória e história do dia (LLM local)   │
                    └──────────────────────────────────────────┘
```

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
| **Idempotência** | `Idempotency-Key: olimpo:<tipo>:<uuid>` derivada do nosso id | `integrations/base/idempotency.py` |
| **Log** | Toda chamada em `events`, antes e depois, com `trace_id` | `integrations/base/http.py` |
| **Circuit breaker** | Estado no Redis, compartilhado entre workers | `integrations/base/breaker.py` |
| **Erro traduzido** | O core nunca vê exceção HTTP crua | `integrations/base/errors.py` |

### A interface, na prática

```python
# src/olimpo/integrations/orchestrator/protocol.py
from typing import Protocol

class OrchestratorClient(Protocol):
    async def dispatch(self, task: TaskRequest) -> DispatchResult: ...
    async def get_task(self, external_task_id: str) -> TaskState: ...
    async def cancel(self, external_task_id: str, reason: str) -> None: ...
    async def health(self) -> ServiceHealth: ...
```

O agente recebe `OrchestratorClient` por injeção. Em produção chega `HttpOrchestratorClient`; em dry-run, o mesmo cliente com o writer ligado; em teste, `FakeOrchestratorClient` em memória.

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
| android-orchestrator | nada publica | tasks ficam em `scheduled`; `expired` só ao passar de `expires_at` |
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
 │        │                                                                    │
 │        ├─ 🖥️  android-manager: GET /v1/accounts?handle=luna.corre           │
 │        │      ◀── { device_id: redroid-07, logged_in: true, healthy: true } │
 │        │                                                                    │
 │        └─ 🤖 orchestrator: POST /v1/tasks                                   │
 │              Idempotency-Key: olimpo:task:4c77b0e2-…                        │
 │              { action: publish_reels, schedule: {mode: now},                │
 │                constraints: { expires_at, require_verification: true } }    │
 │              ◀── 201 { task_id: orc_01J8XK…, status: queued }               │
 │        ▼ tasks.status = dispatched · external_task_id gravado               │
 └─────────────────────────────────────────────────────────────────────────────┘
                                    │
 ┌─────────────────────────────────────────────────────────────────────────────┐
 │ 19:04  ⋯ o device age ⋯                       [🤖 orchestrator → redroid]   │
 │        abre o app · navega · sobe o vídeo · cola a caption · publica        │
 │                                                                             │
 │        webhook: started    ──▶ tasks.status = running                       │
 └─────────────────────────────────────────────────────────────────────────────┘
                                    │
 ┌─────────────────────────────────────────────────────────────────────────────┐
 │ 19:07  VERIFY                                            [🤖 → webhook]     │
 │        POST /webhooks/orchestrator   X-Olimpo-Signature: sha256=…           │
 │        { event: completed, verified: true,                                  │
 │          verification: { method: ui_check, evidence: [screenshot] },        │
 │          result: { external_id: C9aB3XyPqrs, permalink: … } }               │
 │                                                                             │
 │        valida HMAC · anti-replay · dedup por X-Olimpo-Delivery              │
 │        ▼ tasks.status = verified · verified_at                              │
 │        ▼ posts(external_id, permalink, published_at)                        │
 │                                                                             │
 │   ⚠️  verified != true  ──▶ tasks.status = failed, NUNCA verified           │
 │   ⚠️  webhook não chegou ──▶ reconciliador (60 s) faz GET /v1/tasks/{id}    │
 └─────────────────────────────────────────────────────────────────────────────┘
                                    │
 ┌─────────────────────────────────────────────────────────────────────────────┐
 │ 22:00  RECORD                                       [🤖 collect_metrics]    │
 │        task collect_metrics sobre posts recentes                            │
 │        ▼ post_metrics(likes, comments, saves, reach, views)                 │
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
| 2 | Webhook perdido | task presa em `running` para sempre | **reconciliador** a cada 60 s, com polling de resgate |
| 3 | Timeout no despacho | publicou? não publicou? | `Idempotency-Key` + retry seguro + checagem via `extract_content` no próprio perfil |
| 4 | `completed` sem `verified` | métrica e memória contaminadas | regra dura: vira `failed`, nunca `verified` |

O ponto 3 é o mais traiçoeiro: sem idempotência, o retry de um despacho que na verdade funcionou publica o mesmo reels duas vezes. Além de constrangedor, é assinatura óbvia de automação.

---

## 5. Dry-run: o loop inteiro sem tocar em conta nenhuma

`DISPATCH_ENABLED=false` é o **default** e assim permanece até a Fase 1 estar validada.

```
DISPATCH_ENABLED=false
        │
        ├─ orchestrator.dispatch()   ──▶ dryrun/2026-08-05/orchestrator/19-04-31_publish_reels.json
        │                                 devolve { task_id: "dry_01J8…", status: "queued" }
        ├─ persona.generate_video()  ──▶ dryrun/…/persona_engine/06-10-02_generate_video.json
        │                                 devolve URL de mídia placeholder
        ├─ manager.get_account()     ──▶ devolve device sintético saudável
        └─ media_forge.submit()      ──▶ devolve job sintético concluído
```

Propriedades que fazem isso funcionar:

- A resposta sintética usa **o mesmo schema pydantic** da real. Se o modelo mudar, o fake muda junto.
- `external_task_id` sintético é prefixado `dry_` — impossível confundir no banco.
- Cada chamada vira linha em `events` (`type = "<servico>.dryrun"`), então o painel mostra a **mesma timeline** em dry-run e em produção.
- O webhook de conclusão é **simulado** por um worker que lê `dryrun/` e dispara o callback local — é o que faz `VERIFY` acontecer de verdade sem serviço nenhum de pé.

O critério de saída da Fase 0 é literalmente isto: *"task `criar_post` roda ponta a ponta em dry-run"*. Todos os oito passos, todos os quatro clientes, zero requisições de rede.

---

## 6. Estado dos contratos

Três dos quatro documentos foram escritos **sem acesso aos repositórios**. Eles descrevem o contrato que o Olimpo *assume*, com cada ponto incerto marcado ❓ **A CONFIRMAR** e uma lista de perguntas ao final.

| Serviço | Doc | ❓ bloqueantes | Próximo passo |
|---|---|---|---|
| android-orchestrator | [contrato](contratos/android-orchestrator.md#10-perguntas-para-validar-com-o-serviço-real) | 8 | validar as 8 perguntas 🔴 antes de escrever o cliente |
| máquina de influencers | [contrato](contratos/maquina-de-influencers.md#10-perguntas-para-validar-com-o-serviço-real) | 8 | idem — a nº 1 (consistência facial) é a mais importante |
| android-manager | [contrato](contratos/android-manager.md#7-perguntas-para-validar-com-o-serviço-real) | 4 | confirmar se existe API HTTP |
| media-forge | [especificação](contratos/media-forge.md) | — | não existe; a spec **é** o contrato |

**Regra de trabalho:** documento com ❓ aberto no caminho crítico significa cliente que **não sai de dry-run**. Validar os contratos é o [Próximo passo nº 2 do PLANO](../PLANO.md#10-próximos-passos-concretos) e o primeiro trabalho real da Fase 0.

> Atalho que economiza semanas: **peça o OpenAPI/Swagger dos três serviços antes de qualquer coisa.** Metade das perguntas se responde sozinha, e o resto vira conversa curta.

---

## Ver também

- [contratos/](contratos/) — os quatro contratos, com princípios comuns no [README](contratos/README.md)
- [01-arquitetura.md](01-arquitetura.md) — arquitetura macro
- [02-modelo-de-dados.md](02-modelo-de-dados.md) — `tasks`, `events`, `posts`, `device_binding`
- [05-loop-do-agente.md](05-loop-do-agente.md) — o loop em detalhe
- [06-campanhas.md](06-campanhas.md) — recon e espalhamento anti-detecção
- [08-guardrails-e-autonomia.md](08-guardrails-e-autonomia.md) — o que pode ser despachado em cada nível
- [11-observabilidade.md](11-observabilidade.md) — métricas e alertas das integrações
