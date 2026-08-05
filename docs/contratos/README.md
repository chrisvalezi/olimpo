# Contratos de integração

O Olimpo é **o cérebro**: ele decide *o quê / quando / por quê*, mas não fala com nenhuma plataforma social. Toda ação sai daqui como chamada a um dos serviços abaixo.

Este diretório documenta **o contrato que o Olimpo assume** de cada serviço. Três deles já existem (repositórios privados do dono); um ainda será construído.

| Documento | Serviço | Estado | Papel no Olimpo | Variáveis |
|---|---|---|---|---|
| [android-orchestrator.md](android-orchestrator.md) | android-orchestrator | ✅ existe — **contrato a confirmar** | Executa e **verifica** toda ação em device real (IG, WhatsApp) | `ORCHESTRATOR_*` |
| [maquina-de-influencers.md](maquina-de-influencers.md) | máquina de influencers | ✅ existe (em dev) — **contrato a confirmar** | Persona, história, geração de foto/vídeo, brands | `PERSONA_ENGINE_*` |
| [android-manager.md](android-manager.md) | android-manager | ✅ existe — **contrato VALIDADO em 2026-08-05** | Frota redroid. Olimpo usa **somente leitura**, garantida por allowlist nossa | nenhuma: uma linha de `server_services` por instância |
| [media-forge.md](media-forge.md) | media-forge | ❌ **não existe** — este doc é a **especificação** | Corte/edição de vídeo (Fase 5) | `MEDIA_FORGE_*` |

> ⚠️ **Leia isto antes de implementar qualquer cliente.**
> Os três primeiros documentos foram escritos **sem acesso aos repositórios**. Eles descrevem o contrato que o Olimpo *assume* — não o contrato *verificado*. Cada ponto que depende de checar a API real está marcado com ❓ **A CONFIRMAR**, e cada documento termina com a seção *"Perguntas para validar com o serviço real"*.
>
> **Esses documentos são checklist de integração, não verdade.** O primeiro passo da Fase 0 é percorrer as perguntas, corrigir o doc e só então escrever o cliente. Um doc que ainda tem ❓ aberto significa cliente que ainda não pode sair de dry-run.

---

## Princípios de integração (valem para os 4 clientes, sem exceção)

Todo cliente vive em `src/olimpo/integrations/<servico>/` e obedece aos oito princípios abaixo. Eles não são estilo — são a defesa contra o risco *"acoplamento com os 3 serviços"* do [PLANO §9](../../PLANO.md).

### 1. Toda integração fica atrás de uma interface Python (`Protocol`)

O core **nunca** importa `httpx` nem conhece uma URL. Ele depende de um `Protocol`; a implementação HTTP é um detalhe injetado.

```python
# src/olimpo/integrations/orchestrator/protocol.py
from typing import Protocol

class OrchestratorClient(Protocol):
    async def dispatch(self, task: TaskRequest) -> DispatchResult: ...
    async def get_task(self, external_task_id: str) -> TaskState: ...
    async def cancel(self, external_task_id: str, reason: str) -> None: ...
    async def health(self) -> ServiceHealth: ...
```

Consequências práticas:

- Trocar o orchestrator por outro executor é trocar uma classe, não reescrever o agente.
- Teste de agente não sobe HTTP: injeta `FakeOrchestratorClient`.
- Se a API real divergir do que este doc assume, o dano fica contido no adaptador.

**Regra dura:** se um `import httpx` aparecer fora de `integrations/`, é bug de arquitetura.

### 2. Todo cliente tem modo dry-run

Controlado pelo `DISPATCH_ENABLED` (default `false`). Em dry-run o cliente **não faz chamada de rede**: serializa o que teria enviado, grava em disco e devolve uma resposta sintética plausível.

```
dryrun/
  2026-08-05/
    orchestrator/
      14-32-07_publish_reels_luna_a3f1.json    # request + response sintética
    persona_engine/
      14-30-55_generate_image_luna_9c02.json
```

A resposta sintética usa o **mesmo schema** da real (inclusive `external_task_id` fake, prefixado `dry_`). Isso é o que permite rodar o loop `plan→…→verify` inteiro sem tocar em conta nenhuma — critério de saída da Fase 0.

Cada arquivo de dry-run também vira uma linha em `events` (`type = "<servico>.dryrun"`), para que o painel mostre a mesma timeline em dry-run e em produção.

### 3. Retry com backoff exponencial via `tenacity`

```python
from tenacity import (
    retry, stop_after_attempt, wait_exponential_jitter,
    retry_if_exception_type,
)

@retry(
    stop=stop_after_attempt(4),
    wait=wait_exponential_jitter(initial=1, max=30, jitter=2),
    retry=retry_if_exception_type((TransientHTTPError, httpx.TimeoutException)),
    reraise=True,
)
async def _post(self, path: str, body: dict) -> dict: ...
```

| Classe de erro | HTTP | Retenta? |
|---|---|---|
| Timeout de conexão/leitura | — | ✅ sim |
| `429 Too Many Requests` | 429 | ✅ sim, respeitando `Retry-After` |
| `5xx` | 500–504 | ✅ sim |
| `409 Conflict` por idempotência | 409 | ❌ não — trata como sucesso, ver §5 |
| `4xx` de validação | 400, 422 | ❌ não — é bug nosso, falha e loga |
| `401 / 403` | 401, 403 | ❌ não — credencial errada, alerta imediato |
| Ação rejeitada pela plataforma | 200 c/ `status=failed` | ❌ não no cliente — decisão sobe pro agente |

**O jitter é obrigatório.** Sem ele, N tasks que falharam juntas retentam juntas — o mesmo padrão coordenado que a `spread_policy` existe para evitar.

### 4. Timeout explícito, sempre

Nenhuma chamada sem timeout. Nunca herdar o default da biblioteca.

| Operação | Timeout de conexão | Timeout total | Observação |
|---|---|---|---|
| `health` / listagem de devices | 2 s | 5 s | usado no caminho de decisão; tem que ser barato |
| Despacho de task | 5 s | 30 s | o despacho é *enfileirar*, não executar |
| Polling de status | 3 s | 10 s | roda em loop, não pode segurar worker |
| Upload de mídia | 10 s | 300 s | arquivo grande; timeout de leitura maior |
| Geração de imagem (síncrona) | 5 s | 120 s | ❓ se o serviço for síncrono — ver doc |
| Submissão de job assíncrono | 5 s | 30 s | media-forge e vídeo: sempre assíncrono |

O timeout de *execução da ação no device* **não é timeout de HTTP** — é um prazo de negócio, controlado pelo Olimpo via `tasks.expires_at`.

### 5. Idempotência por chave

Toda operação que cria algo carrega uma chave de idempotência derivada do nosso próprio ID — que já é UUID e já é único.

```
Idempotency-Key: olimpo:task:{tasks.id}
Idempotency-Key: olimpo:media:{content_items.id}:{slot}
Idempotency-Key: olimpo:job:{media_forge_job_ref}
```

Regra de interpretação da resposta:

| Resposta | Significado | Ação do cliente |
|---|---|---|
| `201 Created` | criou agora | grava `external_task_id` |
| `200 OK` + mesmo id | já existia, mesma chave | grava `external_task_id`, **não é erro** |
| `409 Conflict` + id no corpo | já existia | idem — extrai o id e segue |
| `409 Conflict` sem id | chave reusada com payload diferente | ❌ bug nosso: falha alto e loga |

Isso é o que torna o retry do §3 seguro: retentar um despacho nunca publica duas vezes.

❓ **A CONFIRMAR** em cada serviço: o header se chama `Idempotency-Key`? É honrado? Por quanto tempo a chave é lembrada?

### 6. Toda chamada é logada em `events`

Sem exceção, e **antes** de saber o resultado (`*.request`) e **depois** (`*.response` ou `*.error`). A tabela `events` existe desde o dia 1 justamente para isso.

```json
{
  "account_id": "9b1e…",
  "task_id": "4c77…",
  "type": "orchestrator.dispatch.response",
  "trace_id": "olimpo-2026-08-05-7f3a91",
  "detail": {
    "service": "android-orchestrator",
    "operation": "dispatch",
    "http_status": 201,
    "latency_ms": 412,
    "attempt": 1,
    "idempotency_key": "olimpo:task:4c77…",
    "external_task_id": "orc_01J8…",
    "dry_run": false,
    "request_digest": "sha256:1f9c…",
    "circuit": "closed"
  }
}
```

Regras:

- `trace_id` propaga do plano do dia até a verificação — é o que responde *"por que a conta X postou isso às 19h?"*.
- **Nunca** logar token, cookie ou credencial. O corpo do request entra como `request_digest` (hash) quando for grande ou sensível; o payload completo já está em `tasks.payload`.
- Erro loga a exceção **e** o corpo da resposta truncado em 2 KB.

### 7. Circuit breaker quando o serviço cai

Um serviço fora do ar não pode virar milhares de tasks em `failed`. Cada cliente tem seu próprio breaker, com estado no Redis (compartilhado entre workers).

| Parâmetro | Default | Significado |
|---|---|---|
| `failure_threshold` | 5 falhas consecutivas | abre o circuito |
| `error_rate_threshold` | 50% em janela de 60 s | abre mesmo sem sequência |
| `open_duration` | 60 s | tempo fechado antes de tentar de novo |
| `half_open_probes` | 1 | uma chamada de teste; sucesso fecha, falha reabre com backoff |
| `max_open_duration` | 15 min | teto do backoff do circuito |

Comportamento com o circuito **aberto**, por serviço:

| Serviço fora do ar | O que o Olimpo faz |
|---|---|
| android-orchestrator | Tasks ficam em `scheduled` (não viram `failed`). Se passar de `expires_at` → `expired`. Alerta no painel. |
| máquina de influencers | Produção do dia é adiada; o agente pode replanejar com conteúdo de banco. |
| android-manager | Assume "device desconhecido" e **não despacha** — nunca despacha às cegas. |
| media-forge | Job fica pendente; lote de clipagem atrasa (latência não é crítica). |

Abrir e fechar o circuito são eventos: `type = "<servico>.circuit.open" / ".close"`.

### 8. O core nunca vê o erro cru do serviço

Cada cliente traduz falhas para um vocabulário próprio do Olimpo:

| Exceção do Olimpo | Quando | O agente deve |
|---|---|---|
| `ServiceUnavailable` | circuito aberto / 5xx persistente | adiar, não desistir |
| `TransientHTTPError` | timeout, 429, 502 | já foi retentado; se chegou, adiar |
| `ContractViolation` | resposta não bate com o schema esperado | falhar alto — o doc está errado, corrigir |
| `ActionRejected` | executou, plataforma recusou (ex.: bloqueio de ação) | tratar como sinal de saúde da conta |
| `DeviceUnavailable` | nenhum device saudável com a conta logada | adiar e alertar curadoria humana |
| `AuthError` | 401/403 | parar o cliente e alertar — nunca retentar em loop |

`ContractViolation` é o mais importante do conjunto: é o mecanismo que transforma "o doc estava errado" em erro visível e rastreável, em vez de comportamento silencioso e torto.

---

## Anatomia de um cliente

```
src/olimpo/integrations/
├── base/
│   ├── http.py          # httpx.AsyncClient + timeouts + tenacity + trace_id
│   ├── breaker.py       # circuit breaker com estado no Redis
│   ├── dryrun.py        # writer de dryrun/ + resposta sintética
│   ├── idempotency.py   # construção da chave
│   └── errors.py        # ServiceUnavailable, ContractViolation, ...
├── orchestrator/
│   ├── protocol.py      # a interface (o que o core enxerga)
│   ├── client.py        # implementação HTTP real
│   ├── fake.py          # implementação em memória, para testes
│   ├── models.py        # pydantic: request/response
│   └── mapping.py       # status externo → tasks.status
├── persona_engine/
├── android_manager/
└── media_forge/
```

Todo cliente novo entrega, no mínimo: `protocol.py`, `client.py`, `fake.py`, `models.py` e testes contra o `fake`. **`models.py` em pydantic é o que gera `ContractViolation` de graça** — se a API real devolver campo diferente, o parse quebra em vez de propagar lixo pro banco.

---

## Ver também

- [../03-integracoes.md](../03-integracoes.md) — como as integrações se encaixam no loop do agente, com o fluxo ponta a ponta
- [../01-arquitetura.md](../01-arquitetura.md) — arquitetura macro
- [../02-modelo-de-dados.md](../02-modelo-de-dados.md) — `tasks`, `events`, `accounts.device_binding`
- [../08-guardrails-e-autonomia.md](../08-guardrails-e-autonomia.md) — o que pode ser despachado em cada nível
- [`.env.example`](../../.env.example) — as variáveis de cada serviço
