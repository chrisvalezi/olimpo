# Contrato — android-orchestrator

> **Estado:** o serviço **existe e está pronto** (repositório privado `chrisvalezi/android-orchestrator`). Este documento descreve o **contrato que o Olimpo assume**, escrito sem acesso ao código.
>
> Cada ponto que depende de checar a API real está marcado ❓ **A CONFIRMAR**. A seção final lista as perguntas objetivas. **Este doc é checklist de integração** — enquanto houver ❓ aberto no caminho crítico, o cliente correspondente não sai de dry-run.

| | |
|---|---|
| **Papel** | Camada de execução. Única coisa no sistema que toca o Instagram/WhatsApp. |
| **Variáveis** | `ORCHESTRATOR_URL`, `ORCHESTRATOR_TOKEN`, `ORCHESTRATOR_WEBHOOK_URL`, `ORCHESTRATOR_WEBHOOK_SECRET` |
| **Cliente** | `src/olimpo/integrations/orchestrator/` |
| **Fase** | 0 (dry-run) → 1 (despacho real com verificação) |
| **Criticidade** | **Máxima.** Se este contrato estiver errado, nada publica. |

## Capacidades declaradas pelo dono

Essas são as capacidades afirmadas do serviço — a base de tudo o que o Olimpo assume:

1. **Localiza qualquer conta logada em instantes** — dado um handle, encontra em qual container/device ela está.
2. **Recebe automações com verificação de task concluída** — não é "fire and forget": o orchestrator confirma que a ação aconteceu de fato.
3. **Agenda task por hora e por conta** — *"às 19h a conta X assiste 10 reels do segmento Y"*.
4. **Cobre tudo que existe no Instagram** — postar, story, reels, DM, comentar, curtir, seguir, assistir.
5. **Também WhatsApp.** TikTok planejado.

O item 2 é o que torna o Olimpo possível. Sem verificação real, `tasks.status = 'verified'` seria uma mentira e todo o loop `RECORD → REFLECT` estaria construído sobre dados falsos.

---

## 1. Autenticação

O Olimpo assume **bearer token estático de serviço**, enviado em toda requisição:

```http
POST /v1/tasks HTTP/1.1
Host: orchestrator.local:9000
Authorization: Bearer ${ORCHESTRATOR_TOKEN}
Content-Type: application/json
Idempotency-Key: olimpo:task:4c77b0e2-1f3a-4c8b-9d21-77aa1e5f0c33
X-Trace-Id: olimpo-2026-08-05-7f3a91
X-Client: olimpo/0.1.0
```

| Item | Suposição do Olimpo | Marca |
|---|---|---|
| Esquema | `Authorization: Bearer <token>` | ❓ **A CONFIRMAR** — pode ser `X-API-Key` |
| Rotação | Token estático, sem refresh | ❓ **A CONFIRMAR** |
| Escopo | Token único com acesso a todas as contas | ❓ **A CONFIRMAR** — se houver escopo por conta/cliente, muda a config |
| Transporte | HTTP na LAN; TLS se sair da LAN | ❓ **A CONFIRMAR** |
| `401 / 403` | Cliente para, abre alerta, **não** retenta | decisão nossa |

**Direção inversa (webhook):** o orchestrator autentica-se no Olimpo com HMAC-SHA256 sobre o corpo bruto, chave `ORCHESTRATOR_WEBHOOK_SECRET` — ver §5.

---

## 2. Despacho de task

### Endpoint

```
POST {ORCHESTRATOR_URL}/v1/tasks
```

❓ **A CONFIRMAR:** path e versionamento (`/v1/tasks`? `/api/tasks`? `/dispatch`?).

### Envelope comum

Todo despacho tem o **mesmo envelope**; o que muda é o `payload`. Isso é deliberado: o cliente do Olimpo serializa `tasks.payload` direto, sem um `if` por tipo de ação.

```json
{
  "client_task_id": "4c77b0e2-1f3a-4c8b-9d21-77aa1e5f0c33",
  "account": { "platform": "instagram", "handle": "luna.corre" },
  "action": "publish_reels",
  "payload": { "...": "específico por ação" },
  "schedule": { "mode": "now" },
  "constraints": {
    "expires_at": "2026-08-05T23:30:00-03:00",
    "max_duration_seconds": 900,
    "require_verification": true
  },
  "callback": {
    "url": "http://olimpo.local:8000/webhooks/orchestrator",
    "events": ["accepted", "started", "completed", "failed"]
  },
  "metadata": {
    "olimpo_account_id": "9b1e7c44-…",
    "olimpo_campaign_id": null,
    "trace_id": "olimpo-2026-08-05-7f3a91",
    "origin": "self"
  }
}
```

| Campo | Tipo | Obrigatório | Notas |
|---|---|---|---|
| `client_task_id` | uuid | sim | É o `tasks.id` do Olimpo. Ancora a idempotência. ❓ o serviço aceita id externo? |
| `account.handle` | string | sim | Como o Olimpo identifica a conta. ❓ ou o orchestrator exige um id interno dele? |
| `action` | enum | sim | Ver §3 |
| `payload` | object | sim | Específico por ação |
| `schedule.mode` | `now` \| `at` \| `window` | sim | Ver §4 |
| `constraints.expires_at` | ISO8601 | sim | Depois disso, **não executar** — furo vencido é pior que não postar |
| `constraints.require_verification` | bool | sim | Só considerar concluído com verificação real |
| `callback` | object | sim | Webhook. Ver §5 |
| `metadata` | object | não | Opaco ao orchestrator; volta no callback |

### Resposta esperada

```json
{
  "task_id": "orc_01J8XK9F2QW7T3M4",
  "client_task_id": "4c77b0e2-1f3a-4c8b-9d21-77aa1e5f0c33",
  "status": "queued",
  "account": {
    "handle": "luna.corre",
    "device_id": "redroid-07",
    "container_id": "ctr_9f21a",
    "resolved_in_ms": 340
  },
  "scheduled_for": "2026-08-05T19:00:00-03:00",
  "estimated_start": "2026-08-05T19:00:12-03:00",
  "created_at": "2026-08-05T14:32:07-03:00"
}
```

O Olimpo grava `task_id` em `tasks.external_task_id` e `status` bruto em `tasks.external_status`. O bloco `account` alimenta `accounts.device_binding` — é como o Olimpo sabe, sem gerenciar a frota, em qual device a conta vive.

### Erros no despacho

| HTTP | Significado assumido | Reação do Olimpo |
|---|---|---|
| `201` | aceita | `tasks.status = 'dispatched'` |
| `200` | mesma `Idempotency-Key`, já existia | idem, sem duplicar |
| `202` | aceita e já agendada | idem |
| `400 / 422` | payload inválido | `failed` sem retry — **bug nosso**, `ContractViolation` |
| `401 / 403` | credencial | para o cliente, alerta |
| `404` | conta não encontrada / não logada | `failed`, alerta de curadoria (`DeviceUnavailable`) |
| `409` | conflito de idempotência | extrai id existente; se ausente, falha alto |
| `423` / `409` | conta ocupada com outra task | ❓ **A CONFIRMAR** — reagenda com jitter, não é falha |
| `429` | rate limit do orchestrator | retry com `Retry-After` |
| `5xx` | serviço | retry + circuit breaker |

❓ **A CONFIRMAR:** existe erro específico para *"conta existe mas não está logada"* vs. *"device offline"*? A distinção importa: a primeira exige curador humano, a segunda só exige esperar.

---

## 3. Payload por tipo de ação

Os tipos abaixo espelham 1:1 o enum `task_type` da migração `0001_initial.sql`. ❓ **A CONFIRMAR o nome exato de cada ação no serviço real** — o mapeamento vive em `integrations/orchestrator/mapping.py` e é o único lugar a mudar.

### Tabela de referência

| `task_type` (Olimpo) | `action` (assumido) | Mídia? | Verificação esperada | Fase |
|---|---|---|---|---|
| `publish_post` | `publish_post` | sim | post existe no perfil + `external_id` | 1 |
| `publish_story` | `publish_story` | sim | story ativo no perfil | 1 |
| `publish_reels` | `publish_reels` | sim | reels existe + `permalink` | 1 |
| `like` | `like` | não | ícone em estado curtido | 3 |
| `comment` | `comment` | não | comentário visível + id | 3 |
| `follow` / `unfollow` | `follow` / `unfollow` | não | estado do botão | 3 |
| `save` / `share` | `save` / `share` | não | estado do ícone | 3 |
| `watch_reels` | `watch_reels` | não | N reels assistidos, tempo total | 1 (aquecimento) |
| `view_stories` | `view_stories` | não | N stories vistos | 1 (aquecimento) |
| `send_dm` | `send_dm` | opcional | mensagem na thread | 2 |
| `reply_comment` | `reply_comment` | não | resposta visível + id | 2 |
| `extract_content` | `extract_content` | — | **retorna dados** (ver §7) | 3 (recon) |
| `collect_metrics` | `collect_metrics` | — | **retorna dados** | 1 |
| `collect_interactions` | `collect_interactions` | — | **retorna dados** | 2 |
| `monitor_check` | `extract_content` (variante) | — | **retorna dados** | 4 |

> `plan_day`, `produce_content` e `reflect` **nunca** vão ao orchestrator — rodam no core e não tocam device.

### 3.1 `publish_post`

```json
{
  "action": "publish_post",
  "payload": {
    "media": [
      { "type": "image", "url": "https://olimpo.local/media/9c02f1.jpg",
        "sha256": "e3b0c44298fc1c14…", "width": 1080, "height": 1350 }
    ],
    "caption": "seis da manhã e a cidade ainda dormindo. 12km. 🏃‍♀️",
    "location": null,
    "first_comment": "#corrida #maratona2026",
    "disable_comments": false,
    "alt_text": "mulher correndo em avenida vazia ao amanhecer"
  }
}
```

❓ **A CONFIRMAR — decisão de arquitetura pendente:** a mídia vai por **URL** (orchestrator baixa) ou por **upload prévio** (`POST /v1/media` → recebe `media_id` → referencia no payload)?
O Olimpo **prefere URL**: mantém o storage no nosso lado, torna o payload pequeno e o retry barato. Se o serviço exigir upload, o cliente ganha um passo `ensure_uploaded()` com cache por `sha256` — a mesma mídia nunca sobe duas vezes.

### 3.2 `publish_story`

```json
{
  "action": "publish_story",
  "payload": {
    "media": [{ "type": "image", "url": "https://olimpo.local/media/a71b3.jpg", "sha256": "…" }],
    "stickers": [
      { "kind": "poll", "question": "corro amanhã de novo?", "options": ["sim", "descansa"],
        "x": 0.5, "y": 0.72, "scale": 1.0 },
      { "kind": "mention", "handle": "marca.energetico", "x": 0.2, "y": 0.9 }
    ],
    "link": null,
    "close_friends_only": false
  }
}
```

❓ **A CONFIRMAR:** quais stickers são suportados (`poll`, `question`, `music`, `mention`, `location`, `link`, `countdown`)? O sistema de coordenadas é normalizado 0..1?

### 3.3 `publish_reels`

```json
{
  "action": "publish_reels",
  "payload": {
    "media": [{ "type": "video", "url": "https://olimpo.local/media/reels_1188.mp4",
                "sha256": "…", "duration_seconds": 27, "width": 1080, "height": 1920 }],
    "caption": "a parte que ninguém mostra é essa aqui",
    "cover": { "mode": "frame", "at_seconds": 3.5 },
    "audio": { "mode": "trending", "audio_ref": "ig_audio_882913", "volume_original": 0.15 },
    "share_to_feed": true,
    "hashtags": ["#corrida", "#rotina", "#disciplina"]
  }
}
```

❓ **A CONFIRMAR (importante):** dá para **selecionar um áudio em alta da plataforma** por id no momento do post? Isso é pré-requisito do trend engine (Fase 3) e do `campaigns.audio_ref`. Se o orchestrator não souber selecionar áudio nativo, a trilha tem que ser embutida no arquivo pelo `media-forge` — o que muda a spec dele.

❓ `cover.mode`: aceita `frame` (timestamp) e `image` (arquivo próprio)?

### 3.4 `like`, `follow`, `unfollow`, `save`, `share`

Mesma forma, alvo por URL ou por handle:

```json
{
  "action": "like",
  "payload": {
    "target": { "kind": "post", "url": "https://www.instagram.com/p/C8xY2LqPabc/" },
    "human_behavior": { "scroll_before": true, "dwell_seconds": [3, 9] }
  }
}
```

```json
{
  "action": "follow",
  "payload": {
    "target": { "kind": "profile", "handle": "criador.alvo" },
    "human_behavior": { "open_profile_first": true, "dwell_seconds": [4, 12] }
  }
}
```

❓ **A CONFIRMAR — o mais relevante deste bloco:** o `human_behavior` existe? Se o orchestrator **já** insere comportamento humano (scroll, dwell, hesitação) internamente, o Olimpo não deve tentar controlar isso e o campo sai do payload. Se **não** insere, o Olimpo precisa modelar micro-comportamento — decisão de escopo relevante para o risco de detecção.

### 3.5 `comment` e `reply_comment`

```json
{
  "action": "comment",
  "payload": {
    "target": { "kind": "post", "url": "https://www.instagram.com/p/C8xY2LqPabc/" },
    "text": "esse take sobre ritmo tá certíssimo, ninguém fala disso",
    "human_behavior": { "read_caption": true, "dwell_seconds": [6, 15] }
  }
}
```

```json
{
  "action": "reply_comment",
  "payload": {
    "target": { "kind": "comment", "post_url": "https://www.instagram.com/p/C8x…/",
                "comment_id": "17924…", "author_handle": "seguidor.fiel" },
    "text": "obrigada! amanhã tem mais 😄"
  }
}
```

Verificação esperada: o comentário publicado **retorna id e permalink**, para o Olimpo ligar a `interactions.external_id` e conseguir remover depois se um guardrail exigir.

❓ **A CONFIRMAR:** existe `delete_comment` / `delete_post`? É **requisito jurídico** da Fase 4 (política de remoção rápida em fofoca) e do risco de DMCA na Fase 5.

### 3.6 `watch_reels` e `view_stories`

São as tasks de **aquecimento e comportamento humano** — as mais usadas por volume, e o exemplo literal do dono: *"às 19h a conta X assiste 10 reels do segmento Y"*.

```json
{
  "action": "watch_reels",
  "payload": {
    "source": { "kind": "hashtag", "ref": "#corridaderua" },
    "count": 10,
    "watch_seconds_per_item": [8, 25],
    "interactions": { "like_probability": 0.15, "save_probability": 0.03,
                      "follow_probability": 0.01, "comment_probability": 0.0 },
    "total_budget_seconds": 420
  }
}
```

| Campo | Uso |
|---|---|
| `source.kind` | `hashtag` \| `explore` \| `following` \| `profile` \| `audio` ❓ quais existem? |
| `count` | quantos itens consumir |
| `watch_seconds_per_item` | faixa `[min, max]`, sorteado por item |
| `interactions.*_probability` | engajamento orgânico durante o consumo |
| `total_budget_seconds` | teto duro de sessão |

```json
{
  "action": "view_stories",
  "payload": {
    "source": { "kind": "following" },
    "count": 20,
    "reply_probability": 0.0,
    "skip_ads": true
  }
}
```

❓ **A CONFIRMAR:** o retorno traz **o que foi assistido** (lista de autores/permalinks)? Se sim, isso é ouro: vira memória episódica da conta (*"hoje ela viu 10 reels de corrida, três da Ana"*), que é exatamente o insumo do passo `REFLECT`. Se não traz, o Olimpo registra só o agregado.

### 3.7 `send_dm`

```json
{
  "action": "send_dm",
  "payload": {
    "target": { "kind": "profile", "handle": "seguidor.novo" },
    "messages": [
      { "kind": "text", "text": "oi! vi que você também corre no parque 👀" }
    ],
    "typing_simulation": true,
    "only_if_thread_exists": false,
    "abort_if_blocked": true
  }
}
```

⚠️ DM é a ação de maior risco de ban e de maior risco reputacional. Guardrail do Olimpo (independente do orchestrator): nunca em L0/L1 sem aprovação; nunca em massa; nunca com link em primeira mensagem; nunca conteúdo sensível ([docs/08](../08-guardrails-e-autonomia.md)).

❓ **A CONFIRMAR:** WhatsApp usa a mesma `action` com `platform: "whatsapp"` ou um conjunto separado de ações?

### 3.8 `collect_metrics`

```json
{
  "action": "collect_metrics",
  "payload": {
    "targets": [
      { "kind": "post", "external_id": "C8xY2LqPabc" },
      { "kind": "post", "external_id": "C8zA9LmQdef" }
    ],
    "include_profile": true
  }
}
```

Resultado esperado (chega pelo webhook ou pelo `GET`):

```json
{
  "result": {
    "profile": { "followers": 12480, "following": 311, "posts": 87 },
    "posts": [
      { "external_id": "C8xY2LqPabc", "likes": 842, "comments": 37,
        "saves": 61, "shares": 12, "views": 19022, "reach": 15310,
        "collected_at": "2026-08-05T21:04:11-03:00" }
    ]
  }
}
```

Alimenta `post_metrics` e `accounts.followers`. ❓ **A CONFIRMAR:** métricas vêm da tela de insights (só contas profissionais) ou raspadas da UI pública? Isso define quais campos existem de fato — `reach` e `saves` normalmente só existem em conta profissional.

---

## 4. Agendamento — quem agenda, o Olimpo ou o orchestrator?

O orchestrator **sabe agendar** por hora e por conta. O Olimpo **também tem** agendador (APScheduler + `tasks.scheduled_for`). Decidir onde o agendamento mora é a decisão de arquitetura deste documento.

### Modo A — Olimpo agenda, orchestrator só executa (`schedule.mode = "now"`)

O Olimpo guarda `tasks.scheduled_for`, um tick a cada 30–60 s seleciona o que venceu (`idx_tasks_due`) e despacha para execução imediata.

```json
{ "schedule": { "mode": "now" } }
```

| | |
|---|---|
| ✅ | Uma única fonte de verdade temporal. `spread_policy`, jitter, `active_hours` e `ACCOUNT_MAX_ACTIONS_PER_HOUR` já vivem aqui. |
| ✅ | Cancelar/reagendar é `UPDATE` local — não depende de o orchestrator expor cancelamento. |
| ✅ | Decisão de última hora: o agente pode reavaliar o mundo **no instante** do despacho (métrica nova, kill switch, notícia). Crítico para fofoca. |
| ✅ | Dry-run é trivial: nada sai do processo. |
| ❌ | Se o Olimpo estiver fora do ar às 19h, a task não sai. |
| ❌ | Mais chamadas HTTP, distribuídas ao longo do dia. |

### Modo B — Olimpo delega o agendamento (`schedule.mode = "at"` / `"window"`)

```json
{ "schedule": { "mode": "at", "at": "2026-08-05T19:00:00-03:00", "timezone": "America/Sao_Paulo" } }
```

```json
{ "schedule": { "mode": "window",
                "not_before": "2026-08-05T18:30:00-03:00",
                "not_after":  "2026-08-05T20:30:00-03:00",
                "jitter_seconds": 900 } }
```

| | |
|---|---|
| ✅ | Sobrevive a queda do Olimpo. |
| ✅ | O orchestrator pode otimizar a fila do device (evita duas contas do mesmo container ao mesmo tempo). |
| ❌ | **Estado duplicado.** `tasks.scheduled_for` e o agendamento remoto podem divergir — a pior classe de bug de sistema distribuído. |
| ❌ | Cancelar depende de o orchestrator expor cancelamento confiável ❓. |
| ❌ | O kill switch global vira um problema: seria preciso varrer e cancelar tudo o que já foi delegado. |
| ❌ | Perde-se a decisão de última hora. |

### ✅ Recomendação: **Modo A como padrão; Modo B como exceção declarada**

**O Olimpo agenda no próprio banco e despacha na hora.** Justificativa:

1. O agendamento **é a inteligência do produto**, não um detalhe de execução. Jitter anti-detecção, participação parcial, respeito à cadência natural e `active_hours` são decisões do cérebro. Delegá-las quebra a separação que o [PLANO §2](../../PLANO.md) define como ativo estratégico.
2. **Kill switch tem que ser instantâneo.** `GLOBAL_KILL_SWITCH=true` precisa parar tudo em segundos. Com Modo A, para de despachar — pronto. Com Modo B, existe uma fila remota que continua rodando.
3. **Estado duplicado é o inimigo.** Um único `tasks.scheduled_for` mantém o sistema auditável.

**Exceções em que o Modo B é aceito** (sempre com `window`, nunca `at` seco):

| Caso | Por quê |
|---|---|
| Sessões longas de aquecimento (`watch_reels` de 30–60 min) | Baixo risco, alto volume, tolera fila remota |
| Lotes noturnos de `collect_metrics` | Latência irrelevante; sobrevive a manutenção do Olimpo |

Regra dura: **nada que publique conteúdo usa Modo B.** Publicação sempre passa pelo dispatcher do Olimpo, para que o guardrail e o kill switch sejam avaliados no último segundo.

❓ **A CONFIRMAR:** o orchestrator suporta `window` com jitter interno? Suporta cancelar uma task agendada? Qual a granularidade (minuto? hora cheia?) — se o agendamento nativo for por *hora cheia*, ele é inutilizável para anti-detecção e o Modo A deixa de ser preferência e vira obrigação.

---

## 5. Verificação de conclusão — webhook com fallback de polling

Este é o mecanismo que sustenta `tasks.status = 'verified'`.

### ✅ Recomendação: **webhook como caminho primário, polling como rede de segurança**

Webhook sozinho é frágil (entrega perdida = task presa para sempre). Polling sozinho é caro e lento (dezenas de contas × milhares de tasks). A combinação é o padrão certo:

```
DISPATCH ──▶ orchestrator enfileira
                  │
                  ├──▶ webhook: accepted ──▶ tasks.status = dispatched
                  ├──▶ webhook: started  ──▶ tasks.status = running
                  └──▶ webhook: completed ─▶ tasks.status = verified   ◀── caminho feliz
                  
   ┌──────────────────────────────────────────────────────────┐
   │  RECONCILIADOR (a cada 60 s)                             │
   │  tasks em dispatched/running há mais de N minutos        │
   │  → GET /v1/tasks/{id}  (polling de resgate)              │
   │  → sem resposta e passou de expires_at → failed/expired  │
   └──────────────────────────────────────────────────────────┘
```

O reconciliador não é opcional: sem ele, todo webhook perdido é uma task zumbi. Ele usa exatamente o índice `idx_tasks_inflight` da migração.

| Estado | Prazo antes do polling de resgate |
|---|---|
| `dispatched` sem `accepted` | 2 min |
| `running` sem `completed` | `max_duration_seconds` + 2 min |
| qualquer um | passou de `expires_at` → `expired` |

### Webhook: contrato de entrada

```
POST {ORCHESTRATOR_WEBHOOK_URL}
X-Olimpo-Signature: sha256=8f2b1c…
X-Olimpo-Delivery: dlv_01J8XM…
X-Olimpo-Timestamp: 1785936121
```

```json
{
  "event": "completed",
  "task_id": "orc_01J8XK9F2QW7T3M4",
  "client_task_id": "4c77b0e2-1f3a-4c8b-9d21-77aa1e5f0c33",
  "action": "publish_reels",
  "account": { "handle": "luna.corre", "device_id": "redroid-07" },
  "status": "completed",
  "verified": true,
  "verification": {
    "method": "ui_check",
    "evidence": [
      { "kind": "screenshot", "url": "http://orchestrator.local:9000/artifacts/…/post.png" },
      { "kind": "permalink",  "value": "https://www.instagram.com/reel/C9aB3XyPqrs/" }
    ],
    "checked_at": "2026-08-05T19:04:38-03:00"
  },
  "result": {
    "external_id": "C9aB3XyPqrs",
    "permalink": "https://www.instagram.com/reel/C9aB3XyPqrs/",
    "published_at": "2026-08-05T19:04:22-03:00"
  },
  "timings": { "queued_at": "…T19:00:03", "started_at": "…T19:03:41", "finished_at": "…T19:04:38" },
  "metadata": { "trace_id": "olimpo-2026-08-05-7f3a91", "olimpo_account_id": "9b1e7c44-…" }
}
```

Falha:

```json
{
  "event": "failed",
  "task_id": "orc_01J8XK9F2QW7T3M4",
  "client_task_id": "4c77b0e2-…",
  "status": "failed",
  "verified": false,
  "error": {
    "code": "action_blocked",
    "message": "Instagram exibiu 'Tente novamente mais tarde'",
    "retryable": true,
    "retry_after_seconds": 7200,
    "screenshot_url": "http://orchestrator.local:9000/artifacts/…/error.png"
  },
  "attempts": 2
}
```

### Requisitos do endpoint no Olimpo

| Requisito | Implementação |
|---|---|
| **Assinatura HMAC** | `HMAC_SHA256(ORCHESTRATOR_WEBHOOK_SECRET, raw_body)`, comparação em tempo constante. Assinatura inválida → `401` e evento `orchestrator.webhook.invalid_signature` |
| **Anti-replay** | Rejeitar `X-Olimpo-Timestamp` com mais de 5 min de desvio |
| **Idempotente** | Mesma `X-Olimpo-Delivery` duas vezes = processa uma vez. Reentrega é normal |
| **Fora de ordem** | `completed` pode chegar antes de `started`. Transição só avança, nunca regride |
| **Rápido** | Valida, grava em `events`, enfileira no Redis e responde `200` em < 200 ms. Nada de processar na request |
| **Desconhecido** | `client_task_id` que não existe → `200` + log. Nunca `5xx` (evita retry infinito do outro lado) |

❓ **A CONFIRMAR (bloco crítico):**
1. O header de assinatura existe e qual o nome/algoritmo?
2. Há retry de entrega? Quantas vezes, com qual backoff?
3. Os eventos são `accepted/started/completed/failed` ou outro conjunto?
4. `verification.evidence` existe — screenshot é servido por URL, base64 ou não existe?
5. O `permalink`/`external_id` do post publicado **volta**? Sem ele, `posts.external_id` fica vazio e `collect_metrics` não tem alvo. **Este é o item mais importante do documento.**

### Polling: contrato de resgate

```
GET {ORCHESTRATOR_URL}/v1/tasks/{task_id}
GET {ORCHESTRATOR_URL}/v1/tasks?client_task_id={uuid}
```

Mesmo corpo do webhook. ❓ **A CONFIRMAR:** dá para consultar pelo nosso `client_task_id`? Se não der, o Olimpo depende 100% de ter gravado o `external_task_id` — e o caminho "despacho respondeu timeout mas foi aceito" fica sem resgate. Nesse caso, `GET /v1/tasks?idempotency_key=…` seria a saída.

❓ Existe endpoint de lote (`POST /v1/tasks/status` com N ids)? Com 50 contas, polling item a item fica caro.

---

## 6. Mapeamento de status

`tasks.status` (Olimpo) × status externo (assumido). O mapeamento é o único ponto que conhece os nomes do outro lado — `integrations/orchestrator/mapping.py`.

| `tasks.status` | Status externo assumido | Quem escreve | Semântica no Olimpo |
|---|---|---|---|
| `pending` | — | Olimpo | criada, fora da janela |
| `scheduled` | — | Olimpo | na janela, aguardando o tick |
| `dispatched` | `queued`, `accepted`, `pending` | resposta do POST / webhook `accepted` | orchestrator recebeu |
| `running` | `running`, `executing`, `in_progress` | webhook `started` | device agindo |
| `verified` | `completed` **com** `verified: true` | webhook `completed` | ✅ único estado de sucesso |
| `failed` | `failed`, `error`, `blocked` | webhook `failed` / polling | esgotou tentativas |
| `cancelled` | `cancelled` | Olimpo (kill switch, guardrail) | abortada de propósito |
| `expired` | `expired`, `timeout` | reconciliador | passou de `expires_at` |

**Regra dura: `completed` sem `verified: true` NÃO vira `verified`.**

```python
if ext.status == "completed" and not ext.verified:
    # o orchestrator diz que rodou, mas não provou.
    task.status = TaskStatus.failed
    task.last_error = "completed_without_verification"
    emit_event("orchestrator.completed_unverified", severity="warn")
```

Isso é deliberado e caro de propósito. Toda a memória do agente (`REFLECT`) e todas as métricas se apoiam em `verified`. Aceitar "provavelmente publicou" contamina a base de forma irreversível. Se a API não devolver um flag de verificação explícito, esta regra vira: só é `verified` quando houver `result.external_id` — evidência concreta de que o objeto existe.

❓ **A CONFIRMAR:** os nomes de status reais e se existe flag booleano de verificação.

---

## 7. Caso especial: `extract_content` (RECON de campanha)

Uma campanha de engajamento **não é planejada às cegas** ([README](../../README.md), [docs/06](../06-campanhas.md)). Antes de decidir quem participa e o que cada persona comenta, uma conta Android **abre o conteúdo e olha**. `extract_content` é essa ação — e é o único tipo de task cujo *valor* está no dado retornado, não no efeito colateral.

O retorno preenche `campaign_recon.raw_capture`, `.media_refs` e `.top_comments`; a LLM multimodal preenche `.analysis`, `.summary`, `.safety_verdict` e `.suggested_angles`.

### Request

```json
{
  "client_task_id": "8f21a0c3-…",
  "account": { "platform": "instagram", "handle": "conta.coletora.03" },
  "action": "extract_content",
  "payload": {
    "target": { "kind": "post", "url": "https://www.instagram.com/reel/C9aB3XyPqrs/" },
    "capture": {
      "screenshot": true,
      "screenshot_mode": "full_page",
      "download_media": true,
      "caption": true,
      "author": true,
      "counters": true,
      "top_comments": 20,
      "comment_sort": "top",
      "audio": true
    },
    "human_behavior": { "dwell_seconds": [10, 25], "scroll_comments": true },
    "no_interaction": true
  },
  "schedule": { "mode": "now" },
  "constraints": { "expires_at": "2026-08-05T16:00:00-03:00", "require_verification": true },
  "callback": { "url": "http://olimpo.local:8000/webhooks/orchestrator",
                "events": ["completed", "failed"] },
  "metadata": { "olimpo_campaign_id": "c1d4…", "trace_id": "olimpo-…" }
}
```

`no_interaction: true` é **obrigatório**: a conta coletora não pode curtir, seguir nem comentar por acidente. Recon que deixa rastro estraga a campanha antes de ela começar.

### Response (via webhook `completed`)

```json
{
  "event": "completed",
  "task_id": "orc_01J8XN…",
  "client_task_id": "8f21a0c3-…",
  "action": "extract_content",
  "status": "completed",
  "verified": true,
  "result": {
    "target": {
      "url": "https://www.instagram.com/reel/C9aB3XyPqrs/",
      "external_id": "C9aB3XyPqrs",
      "kind": "reels",
      "posted_at": "2026-08-04T18:12:00-03:00"
    },
    "author": {
      "handle": "criador.alvo",
      "display_name": "Criador Alvo",
      "followers": 184200,
      "verified": true,
      "bio": "treino · rotina · sem atalho",
      "avatar_url": "http://orchestrator.local:9000/artifacts/…/avatar.jpg"
    },
    "caption": "ninguém fala do dia 40, todo mundo fala do dia 1.",
    "hashtags": ["#disciplina", "#rotina"],
    "counters": { "likes": 24310, "comments": 918, "views": 612400,
                  "shares": null, "saves": null },
    "audio": { "ref": "ig_audio_882913", "title": "original audio - criador.alvo",
               "is_original": true },
    "media": [
      { "kind": "screenshot", "url": "http://orchestrator.local:9000/artifacts/…/full.png",
        "width": 1080, "height": 2400, "sha256": "…" },
      { "kind": "video", "url": "http://orchestrator.local:9000/artifacts/…/reel.mp4",
        "duration_seconds": 31, "sha256": "…" }
    ],
    "top_comments": [
      { "author_handle": "fulano.treina", "text": "o dia 40 é onde a maioria some",
        "likes": 1840, "replies": 22, "posted_at": "2026-08-04T18:41:00-03:00",
        "is_author_reply": false },
      { "author_handle": "criador.alvo", "text": "exatamente isso 👊",
        "likes": 402, "replies": 3, "posted_at": "2026-08-04T19:02:00-03:00",
        "is_author_reply": true }
    ],
    "captured_at": "2026-08-05T14:41:09-03:00"
  }
}
```

### Campos mínimos exigidos pelo Olimpo

| Campo | Uso | Sem ele |
|---|---|---|
| `media[kind=screenshot]` | entrada da LLM multimodal | ❌ **bloqueia o recon** — a análise é visual |
| `caption` | tema, tom, gancho | análise fica pobre |
| `author.handle` + `followers` | proporcionalidade e risco | não dá para calibrar quem participa |
| `counters` | o post já bombou? vale entrar? | perde-se o critério de oportunidade |
| `top_comments` | **o que a plateia já está dizendo** | comentários das personas soam genéricos e destoam — o sinal de farm |
| `posted_at` | frescor: comentar em post de 3 dias é rastro | perde-se |
| `audio.ref` | reaproveitar o áudio na campanha de conteúdo | perde-se |

`top_comments` é o campo mais subestimado da lista. É dele que sai o *registro* da conversa — as personas precisam soar como quem está naquele comentário, não como quem leu a legenda. Sem ele, N contas comentando "amei!" é exatamente o padrão detectável que a `spread_policy` existe para evitar.

❓ **A CONFIRMAR (bloco crítico do recon):**
1. `extract_content` existe como ação, ou é preciso compô-la de primitivas menores?
2. Screenshot volta por URL, base64 ou não volta?
3. Quantos comentários dá para extrair, e dá para ordenar por "mais relevantes"?
4. Funciona em perfil privado / conta que a coletora não segue? (provavelmente não — o Olimpo precisa saber para tratar `safety_verdict = blocked`)
5. Consegue extrair de **perfil** (bio, últimos N posts), não só de post único? Isso é pré-requisito dos monitores da Fase 4.
6. Os artefatos (screenshot/vídeo) ficam disponíveis por quanto tempo? O Olimpo precisa copiar para storage próprio antes de expirarem.

---

## 8. Falha e retry

Dois níveis de retry, com dono diferente. Confundi-los é a fonte clássica de post duplicado.

| Nível | Dono | O que retenta |
|---|---|---|
| **Transporte** | cliente (`tenacity`) | a chamada HTTP não chegou/não respondeu |
| **Execução** | Olimpo (`tasks.attempts`) | a ação chegou, foi tentada e falhou no device |

O retry de transporte é seguro **porque** a `Idempotency-Key` existe. O retry de execução é decisão do agente, com nova avaliação de guardrail.

### Taxonomia de erro de execução

| `error.code` (assumido) | Causa | Retenta? | Backoff | Efeito colateral |
|---|---|---|---|---|
| `device_unavailable` | container caiu/ocupado | ✅ | 5 min | consulta android-manager antes |
| `account_not_logged_in` | sessão caiu | ❌ | — | alerta de **curadoria humana**; conta → `paused` |
| `action_blocked` | "tente novamente mais tarde" | ⚠️ tardio | ≥ `retry_after`, mín. 2 h | **sinal de saúde**: reduz cadência da conta |
| `checkpoint_required` | verificação de segurança do IG | ❌ | — | conta → `suspended`, alerta imediato |
| `rate_limited` | limite da plataforma | ⚠️ | 1 h + jitter | reduz cadência do dia |
| `target_not_found` | post apagado / perfil privado | ❌ | — | task `failed`, campanha reavalia alvo |
| `media_invalid` | formato/resolução rejeitados | ❌ | — | `ContractViolation`, volta pra produção |
| `timeout` | ação passou de `max_duration` | ✅ 1× | 10 min | verificar antes se não publicou (§8.1) |
| `verification_failed` | executou, não conseguiu provar | ⚠️ **nunca cego** | — | ver §8.1 |

### 8.1 A regra que evita post duplicado

Ação de publicação que falha com `timeout` ou `verification_failed` **nunca é retentada às cegas.** O device pode ter publicado e falhado só ao confirmar.

```
publish_* falhou com timeout|verification_failed
        │
        ▼
  despacha extract_content no PRÓPRIO perfil (últimos 3 posts)
        │
        ├── conteúdo está lá  ──▶ tasks.status = verified
        │                          (grava external_id colhido)
        └── não está          ──▶ retry permitido (attempts+1)
```

Custo: uma task extra. Benefício: nunca postar duas vezes a mesma coisa — o que, além de queimar credibilidade, é assinatura óbvia de automação.

### Escalada

| Condição | Ação automática |
|---|---|
| 3 falhas consecutivas na mesma conta | conta → `paused`, alerta |
| `checkpoint_required` em qualquer conta | conta → `suspended`, alerta imediato |
| `action_blocked` em ≥ 3 contas na mesma hora | **suspeita de detecção de padrão** → pausa campanhas ativas, alerta crítico |
| Circuito aberto > 15 min | alerta crítico; tasks seguram até `expires_at` |

O terceiro caso é o mais importante do sistema: bloqueio simultâneo em várias contas quase nunca é coincidência — é a plataforma reconhecendo o padrão. A reação certa é parar, não retentar.

---

## 9. Health check

```
GET {ORCHESTRATOR_URL}/v1/health
```

```json
{
  "status": "ok",
  "version": "1.4.2",
  "devices": { "total": 24, "healthy": 22, "busy": 9 },
  "queue": { "pending": 41, "running": 7 },
  "uptime_seconds": 918233
}
```

Consultado pelo painel e pelo breaker em half-open. ❓ **A CONFIRMAR:** existe? Traz visão de fila? Se trouxer `queue.pending`, o Olimpo pode aplicar backpressure e adiar despachos não urgentes.

---

## 10. Perguntas para validar com o serviço real

Ordenadas por impacto. **As 8 primeiras bloqueiam a Fase 1.**

### 🔴 Bloqueiam a Fase 1

| # | Pergunta | Por que bloqueia |
|---|---|---|
| 1 | **Qual o endpoint de despacho, o método e o corpo exato?** | Sem isso não há cliente |
| 2 | **A resposta de conclusão traz `external_id`/`permalink` do conteúdo publicado?** | Sem isso `posts.external_id` fica vazio, `collect_metrics` não tem alvo e não há métrica nenhuma |
| 3 | **Existe flag explícito de verificação (`verified: true`) ou só `status: completed`?** | Define se `tasks.status = verified` é confiável — todo o `REFLECT` depende disso |
| 4 | **Existe webhook de conclusão? Qual o payload, quais eventos, qual assinatura?** | Sem webhook, tudo vira polling — funciona, mas muda a arquitetura do worker |
| 5 | **Dá para consultar status pelo nosso `client_task_id`?** | Sem isso, timeout no despacho vira task órfã irrecuperável |
| 6 | **Mídia vai por URL ou exige upload prévio?** | Muda o cliente e o storage |
| 7 | **Como a conta é identificada — handle, id interno, ou par (device, conta)?** | Define `accounts.device_binding` |
| 8 | **`Idempotency-Key` é honrado? Por quanto tempo?** | Sem isso, retry seguro é impossível |

### 🟡 Bloqueiam Fases 2–3

| # | Pergunta |
|---|---|
| 9 | Quais os **nomes exatos** de cada ação? (`publish_reels`? `post_reel`? `instagram.reels.publish`?) |
| 10 | A lista de ações cobre tudo do §3, ou faltam algumas (`save`, `share`, `unfollow`)? |
| 11 | Existe **`delete_comment` / `delete_post`**? (requisito jurídico da Fase 4) |
| 12 | O orchestrator **já** insere comportamento humano (scroll, dwell, hesitação), ou o Olimpo deve modelar isso no payload? |
| 13 | Em `watch_reels`, dá para direcionar por hashtag/nicho e retornar **o que foi assistido**? |
| 14 | Dá para **selecionar áudio em alta da plataforma** por id ao publicar reels? |
| 15 | Quais **stickers de story** são suportados, e o sistema de coordenadas é 0..1? |
| 16 | Existe **cancelamento** de task já aceita/agendada? (kill switch) |
| 17 | Suporte a `schedule.window` com jitter interno? Qual a granularidade mínima? |
| 18 | Existe endpoint de **status em lote**? |
| 19 | Existe limite de **tasks concorrentes por conta/por device**? O que acontece ao estourar? |
| 20 | `collect_metrics` lê insights (conta profissional) ou raspa a UI pública? Quais campos existem de fato? |

### 🟢 Bloqueiam a Fase 4+ (recon e monitores)

| # | Pergunta |
|---|---|
| 21 | **`extract_content` existe?** Retorna screenshot, caption, autor, contadores e top comments em uma só task? |
| 22 | Screenshot volta como URL, base64, ou não volta? |
| 23 | Quantos comentários dá para extrair? Dá para ordenar por relevância? |
| 24 | Dá para extrair de **perfil** (bio + últimos N posts), não só de post único? |
| 25 | Por quanto tempo os artefatos (screenshot/vídeo) ficam disponíveis? |
| 26 | Funciona em perfil privado ou conta não seguida? Qual erro retorna? |
| 27 | WhatsApp usa as mesmas ações com `platform` diferente, ou um conjunto próprio? |
| 28 | Qual o estado real do suporte a **TikTok**? |

### ⚪ Operacionais

| # | Pergunta |
|---|---|
| 29 | Autenticação: `Bearer`? Token estático? Escopo por conta? |
| 30 | Existe `/health`? Expõe fila e devices saudáveis? |
| 31 | Rate limit do próprio orchestrator? Retorna `Retry-After`? |
| 32 | Existe OpenAPI/Swagger? *(Se sim, metade destas perguntas se responde sozinha — **peça isto primeiro**.)* |
| 33 | Como distinguir "conta não logada" de "device offline"? |
| 34 | Há logs/artefatos por task acessíveis para debug (vídeo da sessão, trace de UI)? |

---

## Ver também

- [README deste diretório](README.md) — princípios comuns a todos os clientes
- [../03-integracoes.md](../03-integracoes.md) — o fluxo ponta a ponta
- [../06-campanhas.md](../06-campanhas.md) — recon e espalhamento
- [../08-guardrails-e-autonomia.md](../08-guardrails-e-autonomia.md) — o que pode ser despachado em cada nível
- [`db/migrations/0001_initial.sql`](../../db/migrations/0001_initial.sql) — `tasks`, `task_status`, `campaign_recon`
