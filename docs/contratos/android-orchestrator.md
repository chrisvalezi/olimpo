# Contrato — android-orchestrator

> ✅ **VALIDADO EM 2026-08-05** contra o código real do serviço (`/mnt/android-builds/android-orchestrator`: `API.md`, `INTEGRATION.md`, `KNOWN_ISSUES.md`, `api/orchestrator_api/`, `worker/orchestrator_worker/`, `droid/`).
>
> A versão anterior deste documento foi escrita **às cegas** e estava errada nos pontos que mais importavam: assumia webhook com HMAC, `Idempotency-Key`, endpoint `/v1/tasks`, id global de task, screenshot sob demanda e extração de comentários. **Nada disso existe.** O texto abaixo substitui aquele desenho.
>
> O que está marcado ✅ foi conferido no código. O que continua ❓ está isolado na [§16](#16-o-que-continua-incerto) e **não** está no caminho crítico da Fase 1.

| | |
|---|---|
| **Papel** | Camada de execução. Única coisa no sistema que toca o Instagram/WhatsApp. |
| **Variáveis** | Nenhuma URL única. A lista de nós vive na tabela `orchestrator_nodes` (migração [`0002`](../../db/migrations/0002_orchestrator_nodes.sql)); credencial Basic **por nó**. |
| **Cliente** | [`src/olimpo/integrations/orchestrator.py`](../../src/olimpo/integrations/orchestrator.py) |
| **Fase** | 0 (dry-run) → 1 (despacho real com polling e verificação) |
| **Criticidade** | **Máxima.** Se este contrato estiver errado, nada publica. |

---

## 1. Topologia — são DOIS serviços, e uma frota de nós

✅ **VALIDADO.** Cada máquina nossa é um **nó independente**. Dentro de um nó existem dois processos HTTP:

```
   Olimpo ──HTTP──▶ [ worker :8078 ]   ◀── ÚNICA porta pública do nó
                          │  HTTP + Bearer (por dentro, loopback)
                          ▼
                    [ executor :8077 ]  ──docker exec──▶ redroids (Instagram, WhatsApp)
```

| Processo | Porta | O que é | O Olimpo fala com ele? |
|---|---|---|---|
| **worker** | `:8078` | Cérebro do nó: fila, agendador, retry, painel HTML. SQLite próprio. | ✅ **Sim — só com ele.** |
| **executor** | `:8077` | Roda `docker exec` nos redroids. **Monta o `docker.sock`.** SQLite próprio de jobs. | ❌ **Nunca.** |

> ⚠️ **Quem fala com o executor tem root no host.** Por isso ele escuta **só em loopback** e não é alcançável de fora. Não expor, não configurar, não "otimizar" pulando o worker. `orchestrator_nodes.base_url` **sempre** aponta para a porta 8078.

### 1.1 Um nó controla apenas o próprio host

✅ **VALIDADO.** Uma instância descobre containers por `docker ps` **local** e executa por `docker exec` **local**. Consequências que moldam o Olimpo inteiro:

- **Não existe índice de frota.** Nenhum endpoint devolve "a lista dos nós". O **dono da lista de nós é o Olimpo** (`orchestrator_nodes`).
- **A visão de frota se monta por fan-out**: bater `/accounts`, `/devices` e `/actions` em cada nó e agregar.
- **Ids de task são inteiros autoincrement POR NÓ.** A task `42` do nó A não tem relação com a `42` do nó B. **A chave real é o par `(node_id, external_task_id)`** — é exatamente o índice `idx_tasks_external_key` da migração 0002.
- **Cada nó tem SQLite próprio, fila própria e auth própria.** Podem inclusive rodar versões diferentes do código — por isso o catálogo de ações se valida contra o `/actions` **daquele nó**, nunca contra uma lista fixa no nosso código.

---

## 2. Autenticação

✅ **VALIDADO.**

| Alvo | Auth |
|---|---|
| worker (`:8078`), quase tudo | **HTTP Basic** (`ORCH_PANEL_USER` / `ORCH_PANEL_PASS`) — **opt-in** |
| `GET /healthz` | sempre aberto |
| `GET /uploads/<nome>` | **sempre aberto, sem auth** — ver [§13](#13-armadilhas) |
| executor (`:8077`) | Bearer — irrelevante para nós, é interno |

```http
GET /accounts HTTP/1.1
Host: 100.64.0.7:8078
Authorization: Basic b2xpbXBvOnNlbmhh
```

**Auth é opt-in de propósito** (os nós vivem em Tailscale/rede fechada): **sem `ORCH_PANEL_PASS` definido, o worker responde tudo aberto**. Definir a senha liga o Basic.

Consequência para o cliente: **credencial opcional e diferente por nó** — vai existir nó aberto e nó com senha na mesma frota. É por isso que `orchestrator_nodes.basic_user` e `.basic_password` são anuláveis, e por isso `OrchestratorClient.__init__` aceita `basic_user=None`.

Sem credencial num nó protegido: **401** com `WWW-Authenticate: Basic`. Reação do Olimpo: **não retenta**, marca o nó `degraded` e abre alerta — é erro de configuração, não falha transitória.

> ❌ **Não existe Bearer de serviço, não existe rotação, não existe escopo por conta.** Tudo o que a versão anterior deste doc dizia sobre isso está morto.

---

## 3. Endpoints reais do worker

✅ **VALIDADO** — catálogo completo, com o que o Olimpo usa marcado.

| Método | Rota | O quê | Olimpo usa |
|---|---|---|---|
| `GET` | `/healthz` | vivo (sem auth) | ✅ breaker / `last_seen_at` |
| `GET` | `/accounts[?refresh=1]` | contas logadas em todos os redroids do nó | ✅ fan-out de inventário |
| `GET` | `/devices[?refresh=1]` | devices (redroids) vivos | ✅ fan-out de inventário |
| `GET` | `/actions` | catálogo de ações e params **deste nó** | ✅ validação de params |
| `POST` | `/tasks` (= `/plans`) | cria task → **201** com o objeto da task | ✅ despacho |
| `GET` | `/tasks` | lista tasks | painel |
| `GET` | `/tasks/{id}` | estado da task + `sessions[]` | ✅ **polling** |
| `GET` | `/tasks/{id}/log[?format=text]` | log de **todas** as execuções da task | ✅ diagnóstico de falha |
| `GET` | `/jobs/{job_id}` | **resultado cru** da execução (`ok`, `data`, `error`) | ✅ evidência |
| `GET` | `/jobs[?status=&container=&action=&since=&limit=]` | lista de jobs | painel |
| `GET` | `/jobs/{job_id}/live[?after=N]` | output ao vivo (buffer efêmero) | painel |
| `GET` | `/jobs/{job_id}/log[?format=text]` | log completo de UMA execução | diagnóstico |
| `POST` | `/tasks/{id}/cancel` \| `requeue` \| `delete` | controle — **responde 303 com HTML** | ✅ kill switch |
| `POST` | `/containers/{c}/pause` \| `resume` | pausa a raia de um device — **303** | manutenção |
| `POST` | `/upload?name=<orig>` | sobe mídia crua → URL servível | ✅ quando a mídia é local |
| `GET` | `/uploads/{nome}` | baixa a mídia — **público, sem auth** | o device |
| `GET` | `/logs?format=json&source=&level=&q=&since=&limit=` | log de serviço com traceback | diagnóstico |
| `GET` | `/debug[?container=]` | caixas-pretas: print/árvore/logcat **de cada falha** | diagnóstico |
| `GET` | `/debug/{cont}/{caixa}/{arquivo}` | o `screen.png`, `tree.txt`, `logcat.txt` | diagnóstico |
| `GET` | `/storage` | uploads + banco + caixas-pretas, em bytes | alerta de disco |
| `POST` | `/cleanup/debug` \| `uploads` \| `joblogs` | **APAGA** em lote | manutenção |

Além disso o worker serve páginas HTML (`/`, `/live`, `/midias`, `/playground`, `/logs`, `/api`). Não são API — mas `/playground` é a forma mais barata de fazer o smoke test da [§15](#15-aviso-quase-nada-foi-executado-no-device).

### 3.1 Inventário — exemplos reais

**`GET /devices`**

```json
{"devices": [{"container": "google-pixel-9-pro-1", "alive": true}], "queues": {}}
```

**`GET /accounts`** — todas as plataformas numa varredura só:

```json
{"accounts": [
  {"platform": "instagram", "container": "google-pixel-9-pro-1",
   "username": "eliane.mendes.oficial", "id": "32822973107", "user": 10,
   "active": true, "logged_in": true, "followers": 35, "following": 37, "media": 8},
  {"platform": "whatsapp", "container": "google-pixel-9-pro-1",
   "username": "Chris No Beat", "id": "554399374896", "jid": "554399374896",
   "user": 10, "active": true}
 ],
 "by_platform": {"instagram": ["…"], "whatsapp": ["…"]},
 "by_device": {"google-pixel-9-pro-1": ["eliane.mendes.oficial", "Chris No Beat"]},
 "refreshed_at": 1785817633.7, "cached": true}
```

Isto alimenta `orchestrator_nodes.accounts_cache` e `account_placements`. `user` é o **user Android**; no WhatsApp ele **é** a conta (o worker preenche `params.user` sozinho a partir do `account`), no Instagram várias contas convivem no mesmo user e a seleção é por `params.account`.

**`GET /actions`** — `{"actions": {<nome>: {...}}}`:

```json
{"whatsapp.send": {"params": {"to": {"type": "str", "required": true},
                              "text": {"type": "str", "required": true},
                              "account": {"type": "str", "required": false},
                              "user": {"type": "int", "required": false}},
                   "timeout": 240, "retryable": false, "publishes": true,
                   "doc": "envia texto (emoji e Unicode passam pelo clipboard)"}}
```

Três campos que o Olimpo consome direto:

- **`timeout`** — quanto a ação pode demorar. É o que define o `next_poll_at` e o prazo de expiração do lado de cá.
- **`retryable`** — se é seguro reexecutar depois de um timeout. **Cruza com o nosso `NON_RETRYABLE`**, mas não substitui: o worker do serviço *não* olha esse flag no retry dele (ver [§12](#12-erros-retry-e-idempotência)).
- **`publishes`** — a ação **age de verdade** (posta, curte, comenta, segue, ENVIA mensagem). Irreversível. No Olimpo, `publishes: true` implica aprovação por nível de autonomia e proibição de retry cego.

> ✅ `/actions` é **gerado do código** do serviço, com guarda automática de build. É a fonte da verdade; o `API.md` deles (e este doc) atrasam. **Valide params contra o `/actions` DO NÓ.**

---

## 4. O modelo de estado — `task` e `job` são coisas diferentes

✅ **VALIDADO.** Duas camadas, dois bancos, e confundi-las é a origem do bug "a task diz done e nada aconteceu".

```
POST /tasks  ──▶  {"id": 42}          (o token — inteiro, POR NÓ)
                      │
                      ├── GET /tasks/42        ──▶ estado da TASK
                      │                            (fila, agendamento, prioridade, tentativas)
                      │                              └── sessions[].job_id
                      └── GET /jobs/<job_id>   ──▶ RESULTADO CRU da execução
                                                   (ok, data, error, exit_code, stderr)
```

| | `task` | `job` |
|---|---|---|
| Onde vive | SQLite do **worker** | SQLite do **executor** (chega por passthrough) |
| O que é | o que **você pediu** | **uma execução** daquilo no device |
| Tem | fila, `scheduled_at`, `priority`, `attempts`, `repeat` | `ok`, `data`, `error`, `exit_code`, `stderr`, `log_bytes` |
| Id | inteiro autoincrement **por nó** | hash hex (`"8d50577aa39843c4"`) |

**Uma task pode gerar vários jobs** (um por tentativa). `sessions[]` no detalhe da task lista todos, do mais recente para o mais antigo. `last_job_id` é o atalho para o mais recente.

No Olimpo: `tasks.external_task_id` guarda o id da task, `tasks.external_job_id` guarda o `last_job_id`, `tasks.node_id` fecha a chave.

### 4.1 Estados da task

```
pending ──▶ running ──▶ done | failed | canceled
   ▲                        │
   └──── retry (≤3) ────────┘
```

| Estado externo | Significa | `tasks.status` no Olimpo |
|---|---|---|
| `pending` | na fila. Esperando **a hora**, **a raia** (device ocupado), **a conta logar** ou **o device voltar** | `dispatched` |
| `running` | job disparado no device | `running` |
| `done` | executou e voltou ok | `verified` **se** a evidência confirmar — ver [§7](#7-verificação-e-níveis-de-evidência) |
| `failed` | executou, falhou e **esgotou as tentativas** | `failed` |
| `canceled` | cancelada. Não retenta | `cancelled` |

> ⚠️ **`pending` longo pode ser normal.** Device desligado não gasta tentativa: a conta some do inventário, a task nem é disparada e fica `pending` até o device voltar. O painel do Olimpo precisa mostrar *por que* está parada (device vivo? conta logada? hora chegou?) em vez de tratar como travada — **mas** ver a armadilha em [§13.1](#131-a-armadilha-grave-a-task-fantasma-pending-para-sempre).

### 4.2 O estado `unknown` (nível do **job**)

✅ **VALIDADO.** O executor marca `unknown` quando o job **estoura o timeout** ou o serviço **reinicia no meio** da execução. Ele **nunca** é reenfileirado sozinho.

`unknown` quer dizer **"vá verificar"**, não "deu errado". Um `post-reel --publish` que estourou o tempo *depois* do toque em Compartilhar **pode ter publicado**.

**Regra dura do Olimpo:** `unknown` em ação com `publishes: true` **nunca** vira retry automático. Vai para verificação humana. É exatamente o que `to_task_status()` faz mapeando `unknown → running` (mantém em voo, não fecha como falha) e o que `evidence_level = 'unknown'` sinaliza no banco.

---

## 5. `POST /tasks` — o despacho

✅ **VALIDADO.** Aceita JSON (`Content-Type: application/json`) ou form. **`/plans` é sinônimo de `/tasks`** — mesma rota, nome histórico.

```bash
curl -X POST $BASE/tasks -H 'Content-Type: application/json' -d '{
  "action": "instagram.post-reel",
  "account": "eliane.mendes.oficial",
  "scheduled_at": "2026-08-05 19:00",
  "priority": 7,
  "params": {
    "account": "eliane.mendes.oficial",
    "video": "https://100.64.0.7:8078/uploads/04c9…_video.mp4",
    "caption": "a parte que ninguém mostra é essa aqui",
    "audio_mode": "original",
    "publish": true
  }}'
```

Resposta **201** com o objeto da task — **o `id` é o token**:

```json
{"id": 42, "platform": "instagram", "account": "eliane.mendes.oficial",
 "action": "instagram.post-reel",
 "status": "pending", "scheduled_at": 1785936000.0,
 "scheduled_brt": "2026-08-05 19:00 BRT", "priority": 7, "attempts": 0,
 "params": {"…": "…"}, "container": null, "repeat": 0,
 "last_job_id": null, "last_status": null, "created_at": 1785900000.01}
```

### Campos

| Campo | Obrigatório | Notas |
|---|---|---|
| `action` | ✅ sim | **QUALIFICADA**: `"instagram.post-feed"`, `"whatsapp.send"`. Sem o prefixo da plataforma o despacho falha — ver [§13.1](#131-a-armadilha-grave-a-task-fantasma-pending-para-sempre). Use o nome **canônico**, nunca alias. |
| `account` | ✅ sim | **É a unidade de roteamento.** Pode vir na raiz ou dentro de `params`. O nó acha os redroids onde a conta está logada e dispara no **primeiro livre**. Você não diz — nem precisa saber — em qual device a conta vive. Sem ele: **400**. |
| `params` | ✅ sim, e **não pode ser vazio** | Objeto aninhado, vai inteiro para o job. Os nomes são os do `/actions`. O cliente do Olimpo sempre injeta `{"account": …}` como piso. |
| `scheduled_at` (ou `at`) | não | Hora de **Brasília, UTC-3 FIXO**: `"HH:MM"` (próxima ocorrência) ou `"YYYY-MM-DD HH:MM"`. Vazio = agora. **Não existe timezone no protocolo.** |
| `priority` | não | **0–10**. Maior entra na frente quando um device fica livre — inclusive na frente de um retry. Nunca interrompe task em execução. |
| `repeat` | não | `1` = recorrente: ao concluir, reagenda em `+interval_min`. |
| `interval_min` | não | minutos entre repetições (default 60). |
| `container` | não | **só dica de debug.** Se apontar um device vivo, mira ele; se estiver morto/ausente, o nó **ignora** e roteia pela conta. |

Erros do despacho: **400** `{"error": "…"}` (sem `account`, horário inválido, params inválidos).

> ❌ **Não existe `client_task_id`, `callback`, `constraints`, `metadata`, `expires_at` nem `require_verification`.** O envelope inventado na versão anterior deste doc não existe. Tudo o que é *nosso* (trace, expiração, idempotência, guardrail) fica **do lado do Olimpo**.

### 5.1 Códigos de resposta

| HTTP | Significado ✅ | Reação do Olimpo |
|---|---|---|
| `201` | task criada | `tasks.status = 'dispatched'`, grava `(node_id, external_task_id)` |
| `400` | payload inválido (sem `account`, hora inválida, param desconhecido) | `failed` **sem retry** — `ContractViolation`, é bug nosso |
| `401` | nó protegido e credencial ausente/errada | para o cliente daquele nó, alerta, nó → `degraded` |
| `403` | ação não exposta no SPEC | `failed` sem retry |
| `404` | rota inexistente | `ContractViolation` |
| `502` | worker não conseguiu falar com o executor | transitório: retry com jitter + breaker |
| `5xx` | serviço | idem |

> ❌ **Não existem `409`, `423`, `429` nem `Retry-After`.** Conta ocupada não é erro: a task simplesmente fica `pending` até a raia liberar.

---

## 6. O padrão de polling — **não existe webhook**

✅ **VALIDADO.** *"Não existe webhook/callback. O modelo é polling."* — `INTEGRATION.md` §1, literal. Não há campo `callback_url` em rota nenhuma, não há assinatura HMAC, não há eventos `accepted/started/completed`. **Todo o desenho anterior de "webhook primário com polling de fallback" está morto.**

O padrão correto:

```
POST /tasks
   │
   ▼
guarda (node_id, external_task_id)          ← a chave é o PAR
   │
   ▼
┌───────────────────────────────────────────────────────────────┐
│ POLL: GET /tasks/{id}                                          │
│   status ∈ {pending, running}  → reagenda next_poll_at         │
│   status ∈ {done, failed, canceled} → sai do laço              │
└───────────────────────────────────┬───────────────────────────┘
                                    ▼
                      GET /jobs/{last_job_id}
                      ← o resultado REAL: ok, data, error, exit_code
                                    │
                                    ▼
                      read_evidence(job) → evidence_level
```

### 6.1 Pseudocódigo

```python
# 1. DESPACHO — a resposta 201 já traz o token
task = await client.create_task(
    account="luna.corre",
    action="instagram.post-reel",          # QUALIFICADA, sempre
    params={"video": url, "caption": cap, "publish": True},   # nunca vazio
    priority=7,
)
db.tasks.update(id=olimpo_task_id,
                node_id=node.id,
                external_task_id=task["id"],          # inteiro, POR NÓ
                external_action="instagram.post-reel",
                status="dispatched",
                next_poll_at=now() + 15)

# 2. POLL — um tick usa idx_tasks_awaiting_poll
async def poll_tick():
    for t in db.tasks.awaiting_poll():                # status ∈ dispatched|running
        client = client_for_node(t.node_id)           # credencial POR NÓ
        state = await client.get_task(t.external_task_id)

        t.external_job_id = state.get("last_job_id")
        t.status = to_task_status(state["status"])
        t.poll_count += 1

        if state["status"] in ("pending", "running"):
            # backoff com jitter: 15s → 30s → 60s → teto de 120s.
            # O teto vem do `timeout` da ação no /actions daquele nó.
            t.next_poll_at = now() + backoff(t.poll_count)
            if now() > t.dispatched_at + action_timeout + GRACE:
                alert("orchestrator.task_overdue", task=t)   # ver §13.1
            continue

        # 3. TERMINOU — o veredito está no JOB, não na task
        if not t.external_job_id:
            t.status, t.last_error = "failed", "task terminou sem job"
            continue
        job = await client.get_job(t.external_job_id)
        t.evidence_level, t.evidence = read_evidence(job)
        t.status = resolve_status(t.evidence_level)   # ver §7
```

### 6.2 Cadência

| Situação | `next_poll_at` |
|---|---|
| Ação curta (`like`, `follow`, `profile-stats` — `timeout` ≤ 300 s) | 10 s → 20 s → 40 s, teto 60 s |
| Publicação (`post-*`, `timeout` 900 s) | 30 s, teto 60 s |
| Sessão longa (`watch-reels`, `timeout` 7200 s) | 60 s, teto 300 s |
| `pending` há mais de `timeout + 2×tick` **sem nenhum job** | ⚠️ alerta de task fantasma — [§13.1](#131-a-armadilha-grave-a-task-fantasma-pending-para-sempre) |

> ❌ **Não existe endpoint de status em lote.** `GET /tasks` lista tudo do nó — é o que se usa quando há muitas tasks em voo no mesmo nó, em vez de N chamadas a `/tasks/{id}`. Com 50 contas espalhadas em poucos nós, um `GET /tasks` por nó por tick é mais barato que polling item a item.
>
> ❌ **Não dá para consultar por id nosso.** Não existe `client_task_id`. Se o `POST /tasks` der timeout de rede, **a task pode ter sido criada e nós não saberemos o id**. A recuperação é: listar `GET /tasks` do nó e casar por `(account, action, created_at)` dentro de uma janela curta. É frágil de propósito — é o motivo de a deduplicação ser responsabilidade nossa ([§12.3](#123-idempotência-não-existe)).

---

## 7. Verificação e níveis de evidência

Este é o mecanismo que sustenta `tasks.status = 'verified'` — e a parte onde a versão anterior deste doc era mais otimista.

### 7.1 `ok: true` significa exit 0, NÃO que a ação teve efeito

✅ **VALIDADO.** O envelope de um job é:

```json
{"id": "8d50577aa39843c4", "container": "google-pixel-9-pro-1",
 "action": "instagram.post-reel", "status": "done", "ok": true, "exit_code": 0,
 "created_at": 1784210065.5, "started_at": 1784210065.8, "ended_at": 1784210230.1,
 "data": {"published": true, "verified": true, "upload": {"signal": "upload_cleared"}},
 "error": null, "stderr": null, "argv": ["post-reel", "--video", "…"]}
```

`ok` é literalmente `exit == 0`. **`data: null` não é falha** — é "sem payload". Quem diz se a ação **teve efeito** é o `data`, e só algumas ações o populam.

### 7.2 Quem dá recibo e quem não dá

✅ **VALIDADO** ação por ação contra o `API.md` §5.

| Ação | Campo de prova em `data` | Nível máximo alcançável |
|---|---|---|
| `instagram.post-feed` | `{medias, selected, route, caption_ok, published, verified}` | **verified** |
| `instagram.post-reel` | `{draft:{…}, name, published, verified}` | **verified** |
| `instagram.post-story` | `published` / `verified` | **verified** |
| `instagram.dm` | `{target, sent, thread_with, verified_echo, text, reason}` | **verified** |
| `instagram.save-post` | `{target, saved, already, reason}` | **verified** |
| `instagram.follow` / `unfollow` | `{target, ok, state, already, reason?}` — `state` é o texto do botão | **verified** |
| `instagram.reel-action` | `{action_do, author, result}` — `like` **confirma por pixel** | **verified** |
| `instagram.reply-story` | `{target, author, sent, text}` | **verified** |
| `instagram.watch-reels` | `{watched:[{author, fingerprint, is_ad, …}], liked_count, …}` | **reported** (dado rico, mas é auto-relato) |
| `instagram.watch-stories` | `{watched:[{author, watched_s, liked}], …}` | **reported** |
| `instagram.reel-download` | metadados completos — ver [§14](#14-recon--a-limitação-crítica) | **reported** (é leitura) |
| `instagram.profile-stats` / `followers-list` / `post-insights` | contadores | **reported** (é leitura) |
| **`instagram.like`** | ❌ **`data: null`** | **reported** — não há recibo |
| **`instagram.comment`** | ❌ **`data: null`** | **reported** |
| **`instagram.comments`** | ❌ **`data: null`** | **reported** |
| **`instagram.reply`** | ❌ **`data: null`** | **reported** |
| **`instagram.engage`** | ❌ **`data: null`** | **reported** |
| **`instagram.profile`** | ❌ **`data: null`** | **reported** |
| **`instagram.switch`** | ❌ **`data: null`** | **reported** |

> ⚠️ **`like` e `comment` não devolvem recibo nenhum.** O detalhe (`liked`, `already_liked`, `posted`) sai como **log no stdout**, não como payload. Se você precisa de prova de curtida, use **`instagram.reel-action` com `do: ["like"]`** — que confirma por pixel e devolve `result`.
>
> Isso não é um detalhe de conveniência: o `KNOWN_ISSUES.md` do serviço registra um bug real dessa família — *"comentário do IG dizendo 'postado' sem postar"*, porque um toque disparado durante uma animação é aceito e descartado. **`ok` não é confirmação.**

### 7.3 Os quatro níveis (`tasks.evidence_level`)

Implementado em `read_evidence()`:

| Nível | Quando | `tasks.status` |
|---|---|---|
| `verified` | o nó confirmou **pelo efeito na tela** (`data.verified` / `published` / `sent` / `saved` / `state` / `result`) | `verified` |
| `reported` | `ok: true`, mas a ação não produz prova | `verified` **só se a ação for de leitura ou aquecimento**; para publicação, `verified` exige `verified` |
| `unknown` | timeout ou restart no meio — **pode ter acontecido** | fica em voo → **verificação humana**. Nunca retry automático em ação `publishes` |
| `none` | `ok: false` | `failed` |

**Regra dura, mantida da versão anterior e agora com base real:** uma task de publicação que termina em `reported` **não** vira `verified`. Se `data.published` e `data.verified` não vierem `true`, o Olimpo trata como "provavelmente publicou" — e "provavelmente" contamina `post_metrics`, `memories` e todo o passo `REFLECT`. O caminho é o mesmo do `unknown`: verificação.

### 7.4 O permalink do post publicado

❓ **INCERTO — e é a lacuna mais cara que sobrou.** `post-feed` devolve `{medias, selected, route, caption_ok, published, verified}` e `post-reel` devolve `{draft, name, published, verified}`. **Nenhum dos dois devolve o shortcode/permalink do conteúdo criado** de forma documentada.

Consequências e contorno:

- `posts.external_id` não é preenchido pelo próprio despacho.
- Para obter o shortcode: despachar `instagram.reel-download` ou `instagram.post-insights` sobre o próprio perfil logo depois — ou raspar do log da task (`GET /tasks/{id}/log`). **Ambos são contorno, não contrato.**
- `instagram.post-insights` **só funciona em post próprio de conta profissional** e é a única fonte de métrica de post que existe; ele devolve `{shortcode, url, account, metric_count, tabs, metrics}` com os valores **como texto do app** (`"156"`, `"90,9%"`), não como número.

**Ação:** smoke test de `post-feed --publish` e leitura do `data` completo, antes de a Fase 1 depender de `posts.external_id`.

---

## 8. Mapeamento `task_type` (Olimpo) → ação real

✅ **VALIDADO** contra `ACTION_MAP` em [`orchestrator.py`](../../src/olimpo/integrations/orchestrator.py) e contra o `/actions` do serviço.

| `task_type` (Olimpo) | Ação real | `publishes` | Evidência | Observação |
|---|---|---|---|---|
| `publish_post` | `instagram.post-feed` | ✅ | `verified` | **Um vídeo sozinho vira Reel** — o campo `data.route` diz `"feed"` ou `"reel"`. Máx. 20 mídias. |
| `publish_story` | `instagram.post-story` | ✅ | `verified` | Suporta `poll`, `question`, `audio-search`, `close-friends`. |
| `publish_reels` | `instagram.post-reel` | ✅ | `verified` | `audio_mode`: `original` \| `musica` \| `musica_muda`. |
| `like` | `instagram.like` | ✅ | ❌ **sem recibo** | Toggle: **retry DESCURTE**. Prefira `reel-action do=["like"]`. |
| `comment` | `instagram.comment` | ✅ | ❌ **sem recibo** | Retry **duplica** o comentário. |
| `follow` | `instagram.follow` | ✅ | `verified` (`data.state`) | Idempotente: já seguindo → `already: true`. |
| `unfollow` | `instagram.unfollow` | ✅ | `verified` (`data.state`) | Passa pela sheet "Deixar de seguir". |
| `save` | `instagram.save-post` | ✅ | `verified` (`data.saved`) | Bookmark **privado**, não notifica o autor. Idempotente. |
| `watch_reels` | `instagram.watch-reels` | ✅ (se taxas > 0) | `reported` (rico) | `timeout` **7200 s**. Devolve `watched[]` com autor + `fingerprint`. |
| `view_stories` | `instagram.watch-stories` | ✅ (se `like_pct` > 0) | `reported` | Devolve `watched[]` com autor. |
| `send_dm` | `instagram.dm` | ✅ | `verified` (`data.sent`) | **Não retentável** (duplica). `thread_with` protege de mandar para o contato errado. |
| `reply_comment` | `instagram.reply` | ✅ | ❌ **sem recibo** | Retry duplica. |
| `extract_content` | `instagram.reel-download` | ❌ | `reported` | ⚠️ **Só REELS.** Ver [§14](#14-recon--a-limitação-crítica). Usar `no_video: true`. |
| `collect_metrics` | `instagram.profile-stats` | ❌ | `reported` | Perfil, não post. Para post próprio existe `post-insights` (conta profissional). |

### 8.1 O que **NÃO existe** (`UNSUPPORTED`)

✅ **VALIDADO** — pedir estes tipos é **erro de programação**, não falha de runtime. Falhe cedo, no planejamento.

| Pedido | Realidade |
|---|---|
| `unlike` | ❌ não existe. `like` é toggle — "descurtir" é chamar `like` de novo, o que é exatamente o acidente que queremos impedir. |
| `unsave` | ❌ não existe. `save-post` **não desfaz**. |
| `delete_post` / `edit_post` | ❌ não existem. **Isto derruba a "política de remoção rápida" da Fase 4** — a remoção é manual, por humano, no device. |
| `delete_comment` | ❌ não existe. |
| `share` (post de feed) | ❌ não existe compartilhar post de feed. **Só reels**, via `instagram.reel-action do=[share\|story\|repost]` (o `share` é **DM a terceiros**) ou `instagram.repost-reel`. |
| `collect_interactions` | ❌ não existe leitura de comentários recebidos nem de DMs recebidas. `profile-stats` traz contadores, não conteúdo. |
| extrair o **texto** de comentários | ❌ não existe. `reel-download` traz o **contador**, não os comentários. |
| ler DMs recebidas (Instagram) | ❌ não existe. (No WhatsApp existe `whatsapp.inbox`/`messages` — plataforma diferente.) |
| **screenshot sob demanda** | ❌ não existe. Prints só aparecem nas **caixas-pretas de FALHA** (`GET /debug/…/screen.png`). Não há como pedir "me dá a tela deste post". |
| listar quem **eu sigo** | ❌ não existe. `followers-list` lista **seguidores**, não seguidos. |
| selecionar áudio em alta por **id** | ❌ não existe seleção por id. Existe `audio_search`/`audio_title` (**busca por texto** na biblioteca do IG). O `campaigns.audio_ref` precisa virar termo de busca, não identificador. |

> `plan_day`, `produce_content` e `reflect` **nunca** vão ao orchestrator — rodam no core e não tocam device.

---

## 9. As 29 ações Instagram reais

✅ **VALIDADO ao vivo** via `GET /actions` (o serviço expõe 62 ações no total: 29 Instagram + 33 WhatsApp).

```
comment · comments · dm · doctor · engage · follow · followers-list ·
follow-followers · like · list-accounts · post-feed · post-insights ·
post-reel · post-story · profile · profile-stats · reel-action ·
reel-download · remove-account · reply · reply-story · repost-reel ·
save-post · switch · unfollow · upload-quality · watch-reels ·
watch-stories · whoami
```

**Esta lista é referência, não fonte.** A fonte é o `/actions` **do nó** — nós podem rodar versões diferentes. É o que `orchestrator_nodes.actions_cache` guarda e o que `OrchestratorClient.actions()` busca.

Ações **destrutivas** existem na CLI mas estão **deliberadamente fora do HTTP** (`whatsapp.block`, `clear`, `delete-chat`, `delete`, `group-remove`, `group-leave`). Não estão no `/actions` e não são despacháveis.

---

## 10. Agendamento — quem agenda?

O nó **sabe agendar** (`scheduled_at`, `priority`, `repeat`, `interval_min`). O Olimpo **também tem** agendador (`tasks.scheduled_for` + tick). A decisão de arquitetura da versão anterior **continua valendo**, e agora com uma razão a mais.

### ✅ Recomendação (inalterada): **Olimpo agenda; o nó só executa**

Despachar sem `scheduled_at` (ou com `scheduled_at` = agora) e deixar o tick do Olimpo decidir a hora.

1. **O agendamento é a inteligência do produto.** Jitter anti-detecção, participação parcial, `active_hours` e `spread_policy` são decisões do cérebro. Delegá-las quebra a separação que o [PLANO §2](../../PLANO.md#2-decisão-de-arquitetura) define como ativo estratégico.
2. **Kill switch tem que ser instantâneo.** Com o agendamento local, `GLOBAL_KILL_SWITCH=true` para de despachar e pronto. Com agendamento remoto, existe uma fila em **N nós** que continua rodando, e cancelar exige varrer nó por nó — com uma rota (`POST /tasks/{id}/cancel`) que **responde 303 e engole exceções** ([§13.4](#134-as-rotas-de-controle-mentem)).
3. **Estado duplicado é o inimigo.** Um único `tasks.scheduled_for` mantém o sistema auditável.
4. **✅ Razão nova:** o `scheduled_at` do serviço é **hora de Brasília, UTC-3 fixo, sem timezone**. Delegar agendamento significa converter tudo para uma string ingênua e torcer para o relógio do nó estar certo. Manter em `timestamptz` do nosso lado é estritamente melhor.

**Granularidade do agendamento nativo:** ✅ minuto (`"HH:MM"` ou `"YYYY-MM-DD HH:MM"`) — serve para anti-detecção, se um dia for preciso.

**Exceções em que delegar é aceito:** sessões longas de aquecimento (`watch-reels`) e lotes noturnos de coleta. **Regra dura: nada que publique conteúdo delega o agendamento.**

**`repeat` / `interval_min`: não usar.** Uma task recorrente no nó é um agendador paralelo invisível ao kill switch e ao guardrail. Recorrência mora no Olimpo.

---

## 11. Multi-nó — como o Olimpo monta a frota

✅ **VALIDADO.** Ver a migração [`0002_orchestrator_nodes.sql`](../../db/migrations/0002_orchestrator_nodes.sql).

```
                       ┌──────────── OLIMPO ────────────┐
                       │  orchestrator_nodes            │
                       │  (a lista é NOSSA)             │
                       └───────┬──────────┬─────────────┘
             fan-out           │          │
       /accounts /devices /actions        │
                    ┌──────────┴───┐  ┌───┴──────────┐
                    │ nó "i9"      │  │ nó "fd-1"    │
                    │ :8078 aberto │  │ :8078 +Basic │
                    │ 4 redroids   │  │ 2 redroids   │
                    └──────────────┘  └──────────────┘
```

| Regra | Por quê |
|---|---|
| **A chave de uma task externa é `(node_id, external_task_id)`** | ids são autoincrement por nó — colidem |
| **Credencial por nó, opcional** | auth é opt-in; haverá nó aberto e nó com senha |
| **Inventário por fan-out, com cache** | `?refresh=1` custa ~90 s; o serviço já cacheia 60 s do lado dele |
| **Catálogo de ações por nó** | nós podem rodar versões diferentes do código |
| **Uploads são por nó** | um vídeo enviado ao nó A **não existe** no nó B |
| **A mesma conta pode viver em vários nós** | `account_placements` mapeia isso; dá paralelismo real |
| **Serializar por CONTA é obrigação do Olimpo** | o nó serializa por **device**, não por conta — lock no Redis antes do dispatch |

### 11.1 Concorrência: device é a raia

✅ **VALIDADO.** `am switch-user` é global no Android: **todas as contas de um mesmo redroid dividem uma única raia**. O agendador do nó nunca dispara 2 sessões no mesmo device ao mesmo tempo.

**Paralelismo se compra com devices, não com contas.** 10 tasks para 10 contas do mesmo redroid rodam **em série**. `orchestrator_nodes.max_concurrent` é medido em devices, não em tasks. O painel do Olimpo deve mostrar a fila — prometer "disparar tudo agora" é mentira.

### 11.2 Upload de mídia

```bash
curl -X POST "$BASE/upload?name=video.mp4" --data-binary @video.mp4
# → 201 {"name": "04c9…_video.mp4",
#         "url": "http://100.64.0.7:8078/uploads/04c9…_video.mp4",
#         "bytes": 8123456}
```

- O corpo é o **arquivo cru** (`--data-binary`), **não multipart**. Nome original no `?name=`.
- Erros: **400** corpo vazio, **413** acima do teto (300 MB, configurável).
- **Mande a URL de volta como veio.** Ela é escolhida para ser alcançável **de dentro do device** (`ORCH_MEDIA_BASE_URL` → IP do Tailscale → header `Host`). Device atrás de proxy perde a rota para a rede docker local. **Reescrever o host quebra o download.**
- ✅ Ações de postagem também aceitam **URL http(s)** direta em `video`/`media` — **o device é quem baixa**. Se a nossa mídia já estiver num storage alcançável pelo device, o upload é dispensável. Se não estiver, sobe-se para o nó (e **para cada nó** que for executar).
- ⚠️ `GET /uploads/{nome}` é **público, sem auth** — tem que ser, porque quem baixa é o redroid, que não manda credencial. Os nomes têm UUID e não são enumeráveis, **mas trate como link público**: quem tiver a URL, baixa. Nunca subir mídia que não possa vazar.

---

## 12. Erros, retry e idempotência

### 12.1 Retry acontece em DUAS camadas, e nenhuma delas é nossa

✅ **VALIDADO.**

| Camada | Onde | Quantas | Espera | Olha `retryable`? |
|---|---|---|---|---|
| **executor** | `api/orchestrator_api/worker.py` | até **3** (`ORCH_MAX_ATTEMPTS`) | backoff **5 s / 20 s / 45 s** | ✅ sim — e classifica por `data.preflight.reasons` |
| **worker** | `worker/orchestrator_worker/scheduler.py` | até **3** (`ORCH_TASK_MAX_ATTEMPTS`) | **120 s** (`ORCH_TASK_RETRY_DELAY`) | ❌ **não** |

> ⚠️ **Pior caso: 3 × 3 = 9 execuções da mesma ação.** O worker do serviço recoloca a task em `pending` **sem olhar se a ação é retentável**. Para `instagram.comment`, isso são até 9 comentários idênticos. Para `instagram.like`, são 9 toggles — resultado final imprevisível.

**✅ Recomendação operacional: `ORCH_TASK_MAX_ATTEMPTS=1` em todos os nós.** Retry de execução é decisão do agente, com nova avaliação de guardrail, e mora no Olimpo (`tasks.attempts`).

A classificação do **executor** é boa e vale conhecer:

| Classe | `data.preflight.reasons` | Comportamento |
|---|---|---|
| transitório | `not_booted`, `am_down`, `ui_unresponsive`, `no_internet` | nada rodou ainda → **sempre** retenta, mesmo ação não-retryable |
| humano | `not_logged_in`, `account_not_found` | falha na hora, **sem** retry — exige curador |
| meio da execução | sem `reasons` | respeita o `retryable` do catálogo |
| timeout | — | → `unknown`, **nunca** retenta |

### 12.2 Taxonomia de erro real

✅ **VALIDADO** — o `error` do job é **texto em português**, não um código enumerado. O cliente casa por substring.

| `error` contém | Causa | Retenta? | Efeito no Olimpo |
|---|---|---|---|
| `o autodroid trunca o texto no token` | texto com palavra `--…` | ❌ | `ContractViolation` — bug nosso, ver [§13.2](#132-texto-com----é-recusado) |
| `mídia não encontrada:` | caminho local inexistente | ❌ | `ContractViolation` |
| `não consegui baixar` / `download vazio` | URL de mídia ruim | ❌ | `ContractViolation`; checar se a URL é alcançável **do device** |
| `recusado pelo IG (…)` | conta privada recusou marcação/colaborador | ❌ | falha de curadoria; a mensagem do IG está entre parênteses |
| `não está logada` | conta deslogada / leftover | ❌ | conta → `paused`, **alerta de curadoria humana** |
| `passo '…' não avançou` | UI travou ou mudou | ⚠️ 1× | se repetir, é **regressão de UI** do serviço → abrir bug |
| `o álbum … mostra N mídia(s)` | MediaScanner não indexou | ✅ 1× | — |
| `seleção saiu errada` | ordem do carrossel divergiu | ❌ | abrir bug |
| `preflight` com `no_internet` / `not_booted` | device não pronto | ✅ | consulta android-manager antes de reagendar |

**Toda falha de UI grava uma caixa-preta** com print, árvore de acessibilidade e logcat: `GET /debug[?container=]` lista, `GET /debug/{cont}/{caixa}/{arquivo}` baixa. **É a única fonte de screenshot que existe** — e só em falha. O Olimpo deve anexar o link da caixa-preta ao alerta; sem isso o suporte vira "me manda o print".

### 12.3 Idempotência **não existe**

✅ **VALIDADO.** ❌ Nenhuma rota aceita `Idempotency-Key`. Não existe `client_task_id`. Não existe deduplicação do lado do serviço.

**Deduplicar é responsabilidade integral do Olimpo:**

1. **Antes de despachar**, um lock no Redis por `(account, action, target)` com TTL — nenhuma segunda task idêntica entra em voo.
2. **`POST /tasks` que dá timeout de rede não é retentado às cegas.** Primeiro se lista `GET /tasks` do nó e se procura uma task compatível criada nos últimos segundos; só se não houver, despacha de novo.
3. **Nunca retentar `unknown` em ação `publishes: true`.** Ver [§4.2](#42-o-estado-unknown-nível-do-job).
4. **Publicação que falha com timeout ou evidência fraca** passa por verificação antes de qualquer retry:

```
publish_* terminou com evidence_level ∈ {unknown, reported}
        │
        ▼
  NÃO retenta. Abre verificação:
        ├─ instagram.post-insights / reel-download no próprio perfil
        │      └── conteúdo está lá  ──▶ tasks.status = verified (grava o shortcode)
        └─ na dúvida ──▶ fila de revisão humana
```

Custo: uma task extra ou um humano olhando. Benefício: nunca postar duas vezes a mesma coisa — que, além de constrangedor, é assinatura óbvia de automação.

### 12.4 Escalada (decisão nossa, inalterada)

| Condição | Ação automática |
|---|---|
| 3 falhas consecutivas na mesma conta | conta → `paused`, alerta |
| `não está logada` em qualquer conta | conta → `paused`, **alerta de curadoria** (só humano resolve: login manual no app) |
| Erro de UI (`passo '…' não avançou`) em ≥ 3 contas na mesma hora | **suspeita de regressão de UI do serviço** → pausa campanhas, alerta crítico |
| Nó com `consecutive_errors ≥ 5` | nó → `unreachable`, tasks reroteadas para outro nó com a mesma conta |
| Tasks `pending` sem job há mais de `timeout` da ação | ⚠️ **task fantasma** — [§13.1](#131-a-armadilha-grave-a-task-fantasma-pending-para-sempre) |

---

## 13. Armadilhas

### 13.1 A armadilha GRAVE: a task fantasma (`pending` para sempre)

✅ **VALIDADO no código.** Se `action` vier **sem o prefixo da plataforma** OU `params` vier **vazio**, o despacho falha com **400 no executor** — mas o worker apenas faz:

```python
except apiclient.ApiError as exc:
    log.warning("falha ao disparar task %s: %s", plan["id"], exc)
```

A task **não** muda de estado. Ela fica `pending` **para sempre**, retentando a cada tick do agendador, **sem nenhum sinal de erro visível na API**. `GET /tasks/{id}` continua dizendo `pending` e `attempts: 0`. Nenhum job é criado.

Isso é ainda mais traiçoeiro porque `pending` longo **também é normal** (device desligado, conta deslogada, hora não chegou) — os dois casos são indistinguíveis pelo estado.

**Três defesas, todas obrigatórias:**

1. **Ação sempre qualificada.** O cliente levanta `ValueError` se `"." not in action`, antes de qualquer requisição.
2. **`params` nunca vazio.** O cliente injeta `{"account": …}` como piso em toda task.
3. **Alarme de task fantasma.** Task em `dispatched` há mais de `timeout(ação) + margem` **sem nenhum `last_job_id`** → evento `orchestrator.task_stuck_pending` com severidade `warn`, e o painel mostra o diagnóstico (device vivo? conta logada? hora chegou?).

### 13.2 Texto com `--` é recusado

✅ **VALIDADO.** Legenda, comentário e resposta são entregues ao `autodroid` como argumento posicional, e ele **trunca o texto no primeiro token começando com `--`** (qualquer um, inclusive `--` sozinho). **Não há escape.** O droid **recusa** o texto na entrada (`exit 2` em comentários, `exit 1` em post), e a API devolve **400**.

Um hífen simples (`-x`, `traço-simples`) passa normalmente.

**Isso morde exatamente onde dói:** captions e comentários vêm de LLM, e travessão/traço duplo é comum em português. `_sanitize()` no cliente remove tokens iniciados por `--` **antes** de qualquer despacho. Todo texto gerado passa por lá — sem exceção.

### 13.3 `publish` é opt-in

✅ **VALIDADO.** Toda a família `post-*` só toca em *Compartilhar* com `publish: true`. **Sem ele, o comando monta tudo e PARA na tela de legenda** — e devolve `ok: true`.

Ou seja: uma task de publicação sem `publish` termina `done`, com `ok: true`, e **nada foi publicado**. É o modo seguro de testar — e é uma armadilha silenciosa se esquecido em produção. `build_params()` põe `publish: True` por default e deixa o dry-run explícito.

### 13.4 As rotas de controle mentem

✅ **VALIDADO.** `POST /tasks/{id}/cancel|requeue|delete` e `POST /containers/{c}/pause|resume` são **botões do painel HTML**: respondem **303** com `Location: /`, e o handler é um `try/except Exception` que **engole a exceção** e redireciona igual.

**Um cancelamento que falhou é indistinguível de um que funcionou.** Sempre confirmar relendo `GET /tasks/{id}`. `3xx` significa "o botão foi clicado", não "deu certo".

(O mesmo padrão já causou um bug no serviço: uma coluna `NOT NULL` sem valor fazia **nenhuma task ser criada**, e o painel devolvia 303 como se tivesse funcionado.)

### 13.5 Outras

| Armadilha | Detalhe |
|---|---|
| **`?refresh=1` custa ~90 s** | Fura o cache do executor. **Nunca em laço.** Só por ação explícita de humano ("acabei de logar uma conta"). O serviço já cacheia 60 s. |
| **Timeouts longos** | `watch-reels` **7200 s** · `follow-followers` 3600 s · `watch-stories` 3600 s · `followers-list` 1800 s · toda a família `post-*` **900 s** · `reel-action` e `reel-download` 300 s · `dm`/`reply` 420 s · `like`/`comment` 180 s. O timeout do **cliente HTTP** é de 45 s e vale só para a chamada de controle — a execução é assíncrona. |
| **Um device = uma raia** | Tasks do mesmo container rodam **em série**. Ver [§11.1](#111-concorrência-device-é-a-raia). |
| **Uploads são por nó** | Um vídeo no nó A não existe no nó B. Se a mesma campanha usa dois nós, sobe duas vezes. |
| **`GET /uploads/` é público** | Sem auth, por necessidade. Trate como link público. |
| **`--account` troca o user Android do device inteiro** | Sempre passar `account`. O cliente injeta em `params` mesmo quando já está na raiz. |
| **Conta privada muda o comportamento do IG** | Recusa marcar pessoas que não te seguem e recusa colaboradores. O droid detecta o modal, reporta e **aborta**. É `error`, não bug. |
| **Aliases de ação existem, mas não use** | `post`, `story`, `reels`, `reel-camera`, `post-camera`, `reel-repost` ainda resolvem — mas `/actions` só lista os canônicos. Construir sobre alias é dívida. |

---

## 14. RECON — a limitação crítica

Este é o ponto em que a realidade mais diverge do que o Olimpo tinha desenhado. **A versão anterior deste doc descrevia uma ação `extract_content` que devolvia screenshot, caption, autor, contadores e top comments. Ela não existe.**

✅ **A única primitiva de extração de conteúdo é `instagram.reel-download`.**

### 14.1 O que ela faz

```
reel-download <shortcode|url> [--account X] [--no-video] [--cover] [--out DIR]
```

Abrir o reel faz o próprio app buscar e cachear o JSON público da mídia; o droid lê esse cache. É **read-only** (não curte, não comenta), roda em qualquer conta e é **`retryable`** (idempotente). `timeout` 300 s.

`data`:

```json
{"shortcode": "C9aB3XyPqrs", "pk": "…", "url": "https://www.instagram.com/reel/C9aB3XyPqrs/",
 "kind": "…", "taken_at": "…",
 "author": {"username": "criador.alvo", "pk": "…", "full_name": "…", "is_verified": true},
 "caption": "ninguém fala do dia 40, todo mundo fala do dia 1.",
 "mentions": ["…"], "hashtags": ["#disciplina", "#rotina"],
 "tagged_people": [{"username": "…", "x": 0.4, "y": 0.6}],
 "collaborators": ["…"],
 "audio": {"title": "…", "artist": "…", "id": "…"},
 "counts": {"likes": 24310, "comments": 918, "plays": 612400, "shares": 12},
 "video_duration": 31, "video_url": "https://cdn…", "cover_url": "…",
 "location": "…", "carousel": "…",
 "downloaded": {"video": "…", "cover": "…", "metadata": "…"}}
```

### 14.2 As quatro limitações que mudam o desenho do recon

| # | Limitação | ✅ Evidência |
|---|---|---|
| **(a)** | **Só funciona com REELS.** O código força a URL `/reel/<shortcode>/` porque `/p/` cai no viewer de feed, que **não cacheia o media info**. Post de feed, carrossel e foto **não são extraíveis**. | `droid/instagram/reel_dl.py:282-284` |
| **(b)** | **Não extrai o texto dos comentários** — só o **contador** (`counts.comments`). Não existe nenhuma ação que leia comentários de um post. | `API.md` §5 |
| **(c)** | **Baixa a mídia para o DEVICE**, em `/sdcard/Download/android-orchestrator/`. **Não há rota HTTP para buscar esse arquivo** — o serviço recupera com `docker cp`, o que é operação local do host. Para recon puro, usar **`no_video: true`** e ler o `data` do job. | `API.md` §5 |
| **(d)** | **Não há screenshot sob demanda.** Prints existem **só nas caixas-pretas de FALHA** (`GET /debug/…/screen.png`). Não há como pedir a tela de um post. | `API.md` §6, `INTEGRATION.md` §8 |

**Reel privado ou indisponível** (o app não cacheia o info) → erro claro. Não há como coletar conteúdo de perfil privado que a conta coletora não segue.

### 14.3 Alternativas próximas

| Ação | Serve para | `data` |
|---|---|---|
| **`instagram.profile-stats`** | **recon de perfil**: contadores + se **você** já segue. Read-only, não toca em nada. | `{target, ok, you_follow, follow_state, posts, posts_raw, followers, followers_raw, following, following_raw}` — os `*_raw` são o texto do app (`"1,2 mi"`); os sem sufixo já vêm parseados (podem vir `null`). |
| **`instagram.watch-reels` com `dry_run: true`** | **descoberta em escala**: navega o feed/explore/busca e devolve o que viu, sem engajar. | `watched: [{index, author, fingerprint, is_ad, sponsored, watched_s, actions, excluded}]` + `{source, query, segment, segments, watched_count, duration_s, stopped_reason}` |
| **`instagram.followers-list`** | listar seguidores de um alvo (até `limit`, default 200). Read-only, `retryable`. Serve para **diferença entre execuções** (quem entrou/saiu). | `{target, followers: [...], count, stopped_reason}` |
| **`instagram.post-insights`** | métricas do **nosso próprio** post — **só conta profissional**. | `{shortcode, url, account, metric_count, tabs, metrics}` — valores como **texto do app**. |

> **Consequência para as campanhas:** o recon deixa de ser multimodal e passa a ser **baseado em metadados estruturados + análise de texto**. Conteúdo que não é reel **não é analisável hoje** e deve ser **bloqueado por padrão**. Ver [`06-campanhas.md` §3](../06-campanhas.md#3-recon--olhar-antes-de-agir), reescrita para refletir isto.

---

## 15. AVISO: quase nada foi executado no device

✅ **VALIDADO** contra o `KNOWN_ISSUES.md` do próprio serviço. **Este é o maior risco do projeto Olimpo hoje — maior que qualquer detalhe de contrato acima.**

**Validado ponta a ponta no device** (rodou, com efeito conferido na fonte):

| Camada | Ações Instagram |
|---|---|
| droid direto | `whoami` · `list-accounts` · `like` · `comment` · `dm` |
| **serviço inteiro** (`POST /tasks` → agendador → executor → device → recibo) | **`instagram.whoami`** — e só. |

**NÃO testado no device** — importam e compilam, e a tradução da unificação foi de nomes e não de lógica, *mas "importa" está longe de "funciona"*:

```
post-feed · post-story · post-reel · repost-reel · watch-reels · watch-stories ·
reel-action · reel-download · post-insights · follow · unfollow ·
follow-followers · followers-list · profile-stats · save-post · reply ·
reply-story · switch · upload-quality · toda a família de rascunhos
```

Ou seja: **toda a superfície de publicação, todo o aquecimento, todo o crescimento e TODO o recon estão na coluna "nunca executado"**.

O `KNOWN_ISSUES.md` é explícito sobre o *tipo* de bug que essa família produz — todos encontrados **executando**, nunca por leitura:

- **`ok` do daemon tratado como confirmação** — comentário do IG dizendo "postado" **sem postar**; mensagem do WhatsApp virando rascunho com a task em `done`. Um toque disparado durante uma animação é aceito e descartado.
- **Recibo apontando outra coisa** — "a última mídia enviada" já apontou o arquivo de um teste anterior.
- **Nó achado por substring** — a verificação de envio aprovando tudo.
- **Coluna `NOT NULL` sem valor** — **nenhuma task era criada** e o painel devolvia 303 como se tivesse dado certo.

### Como o Olimpo trata isso

**Postura oficial: "contrato escrito, comportamento não verificado".**

1. **Smoke test obrigatório por ação, antes de qualquer dependência.** O caminho mais barato é o `/playground` do nó: escolher app, conta, disparar uma de cada — o resultado por passo já sai no envelope.
2. **Uma ação só sai de dry-run no Olimpo depois de um smoke test verde registrado** (com `job_id` e `data` anexados ao evento). Enquanto isso, `DISPATCH_ENABLED=false` para aquele `task_type`.
3. **Ordem sugerida do smoke test**, por dependência do roadmap: `profile-stats` → `reel-download --no-video` → `follow`/`unfollow` → `save-post` → `watch-reels --dry-run` → `post-feed` sem `publish` → `post-feed --publish` → `post-reel --publish` → `post-story --publish`.
4. **`ok: true` nunca é aceito como confirmação** de ação de publicação. Ver [§7](#7-verificação-e-níveis-de-evidência).

---

## 16. O que continua incerto

Reduzido ao que realmente sobrou. **Nenhum destes bloqueia a Fase 1** — todos têm contorno conhecido.

| # | ❓ | Impacto | Contorno hoje |
|---|---|---|---|
| 1 | **`post-feed`/`post-reel` devolvem o shortcode/permalink do conteúdo criado?** O `data` documentado não o traz. | `posts.external_id` e toda a métrica de post | Buscar depois via `post-insights`/`reel-download` no próprio perfil, ou raspar do `GET /tasks/{id}/log`. **Resolver no smoke test.** |
| 2 | **`audio_search` acerta a faixa certa de forma determinística?** A seleção é por **busca textual** na biblioteca do IG, sem id. | `campaigns.audio_ref` e o trend engine (Fase 3) | Tratar `audio_ref` como termo de busca + `audio_title` como filtro; validar com `data.ui.audio`. |
| 3 | **Quanto tempo as caixas-pretas de falha ficam disponíveis?** Existe `POST /cleanup/debug` com `older_than_days`, mas a política de cada nó é dele. | anexar print de falha ao alerta | Copiar o `screen.png` para storage nosso **no momento do alerta**, não depois. |
| 4 | **`watch-reels` com `source: "search"` + `query` respeita nicho de forma útil?** O comportamento existe; a qualidade do direcionamento nunca foi medida. | descoberta e aquecimento direcionado | Medir no smoke test com `dry_run: true` e ler `watched[].author`. |
| 5 | **Perfil privado / conta não seguida em `profile-stats` e `reel-download`** — qual erro exato retorna? | `safety_verdict = blocked` automático no recon | Tratar qualquer `ok: false` de recon como `blocked`. |
| 6 | **TikTok** — o `/actions` hoje lista só `instagram` e `whatsapp`. Estado do suporte: desconhecido. | Fase 5+ | Fora de escopo. |

---

## Ver também

- [README deste diretório](README.md) — princípios comuns a todos os clientes
- [`src/olimpo/integrations/orchestrator.py`](../../src/olimpo/integrations/orchestrator.py) — **o cliente é a referência executável deste contrato**
- [`db/migrations/0002_orchestrator_nodes.sql`](../../db/migrations/0002_orchestrator_nodes.sql) — `orchestrator_nodes`, `account_placements`, `tasks.node_id/evidence_level`
- [../03-integracoes.md](../03-integracoes.md) — o fluxo ponta a ponta, com polling
- [../06-campanhas.md](../06-campanhas.md) — recon de metadados e espalhamento
- [../07-monitores.md](../07-monitores.md) — coleta contínua com as primitivas que existem
- [../08-guardrails-e-autonomia.md](../08-guardrails-e-autonomia.md) — o que pode ser despachado em cada nível
