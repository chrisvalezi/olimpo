# Contrato — android-manager

> ✅ **VALIDADO EM 2026-08-05** contra o código real do serviço e contra um host de verdade (o mesmo que roda o orchestrator). O que está marcado ✅ foi conferido; o que continua ❓ está isolado na [§10](#10-o-que-continua-incerto).
>
> A versão anterior deste documento foi escrita **às cegas** e errava justamente onde o Olimpo dependia dela: prometia rotas `/v1/*`, token de API com escopo *read-only*, `busy` por device, `proxy_id` por conta e — o pior — **"qual conta está logada em qual container"**. Nada disso existe. O android-manager não conhece contas de Instagram; ele conhece containers e usuários do Android. A fonte da verdade sobre contas é o **orchestrator + o Olimpo** ([android-orchestrator.md](android-orchestrator.md)).

| | |
|---|---|
| **Papel** | Painel de gerenciamento da frota de containers redroid de **um** host |
| **Onde é configurado** | Não há URL global. Cada instância é uma linha de `server_services` com `kind = 'android_manager'` (migração [`0005`](../../db/migrations/0005_servers_and_services.sql)); credencial **por serviço** |
| **Cliente** | [`src/olimpo/integrations/android_manager.py`](../../src/olimpo/integrations/android_manager.py) — **é a referência executável deste contrato** |
| **Acesso do Olimpo** | 🔒 **SOMENTE LEITURA — garantida por allowlist nossa** (§3), não pelo servidor |
| **Fase** | 1 (observação da frota, inventário de devices, captura sob demanda) |

---

## 1. Topologia — um host, três portas

✅ **VALIDADO.** O android-manager não é um serviço; são dois processos e um vizinho, no **mesmo host** que já roda o android-orchestrator. Isso é o fato que quebrou o modelo antigo do Olimpo ("1 nó = 1 orchestrator") e originou a [ADR-0012](../adr/0012-servidor-com-varios-servicos.md).

```
                     um SERVIDOR (host)
   ┌──────────────────────────────────────────────────────────┐
   │  :8078  android-orchestrator (worker)   ── executa ações  │
   │                                                           │
   │  :4000  android-manager (backend)   ── a API, em HTTP     │
   │     ▲                                                     │
   │     │ proxy /api                                          │
   │  :443   nginx  ── serve a INTERFACE (/) e proxia /api     │
   │                                                           │
   │  :9000  gateway de tela  ── transmissão ao vivo           │
   └──────────────────────────────────────────────────────────┘
```

| Porta | O que é | O Olimpo fala com ela? |
|---|---|---|
| `:4000` | Backend do android-manager. Responde `/api/*` em **HTTP puro**. | Só quando não há nginx — ver [§9](#9-https-na-443-ou-4000-direto) |
| `:443` | nginx: interface web na raiz (`/`) e proxy de `/api` para a 4000, com TLS. | ✅ **É o endereço preferido** |
| `:9000` | Gateway de tela. **Serviço separado**, não faz parte do android-manager. | Só a interface, por iframe — nunca o core |
| `:8078` | android-orchestrator (worker). Contrato próprio. | ✅ outro serviço, outra credencial |

Consequências diretas no modelo de dados:

- `server_services.base_url` é onde a **API** responde; `server_services.web_url` é o que se **abre no navegador**. No android-manager os dois divergem (`https://host/api/...` vs `https://host/`), e é por isso que o campo existe.
- A saúde é **por serviço**, não por host: um android-manager fora do ar não impede o orchestrator do mesmo servidor de publicar.
- O gateway de tela é **outra linha** de `server_services` (`kind = 'screen_gateway'`), com URL própria — ver [§8](#8-transmissão-de-tela-por-que-o-olimpo-é-agnóstico-ao-transporte).

---

## 2. Autenticação

✅ **VALIDADO.** É **login com usuário e senha**, e nada além disso.

```http
POST /api/auth/login HTTP/1.1
Content-Type: application/json

{"username": "olimpo", "password": "..."}
```

```json
{"token": "..."}
```

```http
GET /api/devices HTTP/1.1
Authorization: Bearer <token>
```

| Fato | Consequência para o cliente |
|---|---|
| **Não existe chave de API nem service account** | `AndroidManagerClient` exige `username`/`password`. Sem eles, só as rotas públicas funcionam |
| O token **pode expirar** | Login preguiçoso: o cliente só autentica quando precisa, e em `401` refaz o login e repete a chamada **uma única vez** |
| O token também é aceito em `?token=` na query | Existe para `<img>` e WebSocket, que não mandam header. O cliente **não** usa essa forma: token em URL vaza em log de proxy |
| `GET /api/auth/status` é **público** | Devolve `{"authEnabled": true, "hasAdmin": true, "userCount": 1}`. É como a sondagem descobre se precisa de credencial antes de tentar |
| `GET /api/health` é **público** | A sondagem mais barata que existe. Serve para dizer "vivo" mesmo sem credencial cadastrada |

> ⚠️ **Se `authEnabled` for falso, o serviço responde TUDO como admin, sem credencial nenhuma.** Qualquer um na rede tem a frota inteira: shell nos containers, destruição de container, troca de usuário Android. A sondagem grava isso em `server_services.capabilities.auth_enabled` justamente para o painel poder gritar. **Não trate como detalhe de configuração — trate como incidente de segurança.**

Sem credencial num serviço com auth ligada, a sondagem não falha: ela reporta `alive: true, authenticated: false` e o aviso *"sem credencial: só a checagem de saúde está disponível"*. `401`/`403` viram `service_status = 'unauthorized'`, **não** `offline` — o operador conserta coisas diferentes em cada caso.

---

## 3. A allowlist — e por que ela mora do NOSSO lado

✅ **VALIDADO, e é o ponto mais importante deste documento.**

O android-manager tem controle de permissão **por seção**, mas ele **não cobre** `/api/devices`, `/api/system` e `/api/extproxy`. E é exatamente sob `/api/devices` que moram execução de shell arbitrário, injeção de digitação, escrita no sdcard e troca de usuário Android.

Ou seja: **não existe credencial "somente leitura" para pedir.** Um usuário criado para o Olimpo continua podendo destruir a frota. O `403` do servidor não é uma barreira em que se possa confiar.

Por isso a garantia é nossa e é **anterior à rede**: a lista em `olimpo/integrations/android_manager.py` é conferida **antes** de a requisição sair. Chamada fora dela levanta `NotAllowed` — que é erro de programação, não de rede, e nunca abre o circuit breaker.

| Forma | Conteúdo |
|---|---|
| Exata (`ALLOWLIST`) | `GET /api/health` · `GET /api/auth/status` · `POST /api/auth/login` · `GET /api/auth/me` · `GET /api/devices` · `GET /api/system` · `GET /api/config/groups` · `GET /api/watchdog/status` |
| Por prefixo+sufixo (`ALLOWLIST_PATTERNS`) | `GET /api/devices/:id/users` · `GET /api/devices/:id/network` · `GET /api/devices/:id/proxy/status` · `POST /api/devices/:id/screenshot` |

Três decisões embutidas aí:

1. **A captura de tela é `POST` no serviço, mas é leitura no efeito** — não muda nada no device. Entra na lista, com timeout curto ([§5](#5-captura-de-tela--e-a-armadilha-do-boot)).
2. **A validação por prefixo+sufixo** é deliberadamente burra: casa `/api/devices/<qualquer coisa>/users`, e nada mais. Um `id` que contenha `/` não expande o alcance porque o sufixo continua exigido.
3. **Ampliar a allowlist é decisão de arquitetura, não de conveniência.** O Olimpo é observador desta frota; quem cria, destrói, instala app e loga conta é o **curador humano**, pelo painel do próprio android-manager. Login de conta é a etapa que exige julgamento, verificação de segurança e resolução de checkpoint — automatizá-la é a forma mais rápida de queimar contas em lote.

---

## 4. Rotas que o Olimpo consome

✅ **VALIDADO** — este é o conjunto completo. Nada fora daqui é chamado.

| Método | Rota | O quê | Auth | Uso no Olimpo |
|---|---|---|---|---|
| `GET` | `/api/health` | vivo | ❌ pública | Sondagem barata; `server_services.status` |
| `GET` | `/api/auth/status` | `{authEnabled,hasAdmin,userCount}` | ❌ pública | Descobrir se precisa de credencial; alerta de auth desligada |
| `POST` | `/api/auth/login` | `{username,password}` → `{token}` | ❌ | Obter o Bearer |
| `GET` | `/api/devices` | containers Android vivos | ✅ | **Inventário**: popula a tabela `devices` |
| `GET` | `/api/system` | CPU/RAM/GPU **do host** | ✅ | Saturação do host degrada a frota inteira |
| `GET` | `/api/watchdog/status` | quais devices estão sob vigilância automática | ✅ | `devices.under_external_management` ([§7](#7-três-daemons-do-outro-lado--e-o-risco-de-dois-sistemas-no-mesmo-device)) |
| `GET` | `/api/config/groups` | agrupamento de devices configurado no serviço | ✅ | Rótulos de frota |
| `GET` | `/api/devices/:id/users` | usuários **do Android** naquele container | ✅ | O mais próximo de "quem está ativo" que este serviço oferece |
| `POST` | `/api/devices/:id/screenshot` | captura sob demanda | ✅ | Prova visual, curadoria ([§5](#5-captura-de-tela--e-a-armadilha-do-boot)) |

### 4.1 `GET /api/devices` — o inventário

**Cacheado por ~1 minuto no servidor.** Consultar com mais frequência que isso não traz nada de novo: é gasto de conexão para reler a mesma resposta. E o inverso também vale — um device que morreu há 40 s ainda aparece vivo aqui. **Para decidir despacho, quem sabe mais é o orchestrator**, que executa.

A resposta é aceita nas duas formas que já apareceram (`[...]` ou `{"devices": [...]}`), e ❓ **o nome exato de cada campo não é documentado pelo serviço e varia entre versões.** Em vez de quebrar, `normalize_device()` procura por sinônimos e guarda o objeto cru em `devices.metadata`, para não perder nada:

| Campo nosso | Sinônimos aceitos | Observação |
|---|---|---|
| `container` | `name`, `container`, `containerName`, `id` | Chave junto com `server_id` |
| `alive` | `running`, `alive`, `isRunning` | |
| `android_version` | `androidVersion`, `android`, `version` | |
| `adb_target` | `adbTarget`, `adb`, `address`, `ip` | Sem porta, assume `:5555` |
| `video_device` | `videoDevice`, `video`, `v4l2` | `/dev/videoN` do v4l2loopback |
| `screencast_capable` | derivado | `true` sse existe `video_device` |

### 4.2 `GET /api/system` — do HOST, não do device

CPU, RAM e GPU **da máquina**. ❓ O formato exato não está congelado; a sondagem lê as chaves `cpu`, `memory` e `gpu` quando presentes e ignora o resto.

Isto **não** é "recursos por container" — esse dado não existe em lugar nenhum ([§6](#6-o-que-não-existe)). O valor é outro: se o host satura, a frota inteira degrada ao mesmo tempo, e isso é invisível olhando device por device.

### 4.3 `GET /api/devices/:id/users`

Usuários **do sistema Android** dentro do container, e qual deles está em primeiro plano. Não são contas de rede social. Vale a mesma ressalva de `account_placements.foreground`: o Android mantém **vários usuários em execução ao mesmo tempo**, e "em execução" não é "está na tela".

---

## 5. Captura de tela — e a armadilha do boot

✅ **VALIDADO.**

```
POST /api/devices/{id}/screenshot
```

```json
{"image": "data:image/jpeg;base64,/9j/4AAQSk..."}
```

- É **`POST`**, não `GET`. Não tem cache: cada chamada é uma captura nova.
- Devolve **data-URL pronta para `<img>`** — não é URL de arquivo, não é binário. Nada a baixar depois.

> ⚠️ **Antes de capturar, o serviço espera o boot do device por ATÉ 60 SEGUNDOS.** Numa varredura de frota, um único device ruim segura a conexão por um minuto e **envenena o pool** — as capturas boas ficam esperando atrás dele.

Mitigação obrigatória no cliente, e ela é dupla:

1. **Timeout curto e explícito** (15 s no `screenshot()`, contra os 20 s padrão do cliente). Melhor perder a captura de um device doente do que atrasar as dos saudáveis.
2. **Concorrência baixa.** Captura de frota é operação em lote pequeno, nunca fan-out completo em paralelo.

---

## 6. O que NÃO existe

✅ **VALIDADO.** Cada linha abaixo era uma promessa da versão anterior deste documento.

| ❌ Não existe | O que o contrato antigo prometia | Onde o dado realmente vive |
|---|---|---|
| **"Qual conta está logada neste container"** | `devices[].accounts[]` com `handle`, `logged_in`, `last_verified_at` | O android-manager **não conhece contas de Instagram/WhatsApp**. Quem sabe é o orchestrator (`GET /accounts`), gravado por nós em `accounts` e `account_placements` |
| **Busca de device por handle** | `GET /v1/accounts?handle=...` | Não existe. A consulta é nossa, em `account_placements` |
| **CPU/RAM por device** | `devices[].resources.cpu_percent` | Só existe do **host**, em `/api/system` |
| **Chave de API / service account** | `ANDROID_MANAGER_TOKEN` | Só usuário e senha, com token de sessão |
| **Escopo read-only no servidor** | "token que tecnicamente não consegue escrever" | O controle por seção **não cobre** `/api/devices`. A garantia é a allowlist ([§3](#3-a-allowlist--e-por-que-ela-mora-do-nosso-lado)) |
| **Flag `busy` por device** | gate de despacho | Não existe. Quem serializa por device é o orchestrator |
| **Proxy / identidade de rede por conta** | `network.proxy_id` | Existe `/api/devices/:id/proxy/status` (na allowlist) e `/api/extproxy` (não usamos). ❓ Nenhum liga proxy a **conta** |
| **Webhook / evento de "device caiu"** | notificação | Não existe. É polling, como no orchestrator ([ADR-0011](../adr/0011-polling-e-registro-de-nos.md)) |

---

## 7. Três daemons do outro lado — e o risco de dois sistemas no mesmo device

✅ **VALIDADO.** O android-manager **não é passivo**. Ele roda, por conta própria e sem nos avisar:

| Daemon | O que faz sozinho | Por que nos afeta |
|---|---|---|
| **Watchdog** | Liga sozinho no boot do host e pode **reiniciar container** que julgar travado | Um restart no meio de uma publicação nossa quebra a ação — e uma sessão interrompida em `post-feed` pode custar a conta |
| **Scheduler de deploy** | Aplica mudanças em containers no horário dele | O device muda de estado sem que nada no Olimpo tenha pedido |
| **Health check de proxy externo** | Testa e pode reagir a proxy fora do ar | Troca de rota de rede durante uma ação é sinal forte de detecção |

**Mitigação.** A sondagem consulta `GET /api/watchdog/status` e marca `devices.under_external_management = true` para os containers sob vigilância. Isso é **sinal de exclusão**, e vale assim:

```
device com under_external_management = true
        │
        ├── painel: badge âmbar "sob vigilância automática do android-manager"
        │
        └── despacho: motivo para NÃO mandar ação nossa para este device
                      (preferir outro placement da mesma conta; se não houver,
                       reagendar — nunca competir com o watchdog)
```

`GET /api/watchdog/status` **exige permissão de seção** e pode responder `403` num usuário restrito. A ausência dele não é falha do serviço: a sondagem engole o erro, grava `capabilities.watchdog = false` e segue. ❓ O formato da resposta não é documentado; lemos `devices[]` procurando `containerId`/`container`/`id` e considerando ativos os que têm `enabled` ou `running`.

---

## 8. Transmissão de tela: por que o Olimpo é agnóstico ao transporte

Hoje o gateway (`:9000`) usa **WebSocket + H.264 + WebCodecs** — não WebRTC — com um cliente web autocontido que dá para embutir por `iframe`. **O dono do projeto vai implementar WebRTC.** Escrever o Olimpo contra o transporte atual seria escrever para ser reescrito.

Por isso o gateway entra no modelo como **mais um `server_services`**, do tipo `screen_gateway`, com URL própria; a interface monta o link e embute. Trocar WebSocket por WebRTC é trocar o que o serviço serve naquela URL — nada no core muda.

Requisitos que valem para **qualquer** transporte:

| Requisito | Por quê |
|---|---|
| O painel precisa estar em **HTTPS** | Decodificação de vídeo no browser exige contexto seguro; e página HTTPS não embute origem HTTP |
| O container precisa expor **dispositivo de vídeo** (`devices.video_device`) | Sem ele não há transmissão **contínua** — resta o modo alternativo, que derruba quem já estava assistindo. Inútil para uma grade |
| O device precisa ter **endereço ADB** (`devices.adb_target`) | É o alvo do gateway |
| O parâmetro que torna o espectador **passivo** é obrigatório | Não é detalhe de interface: ele garante **no servidor** que o Olimpo assistindo nunca roube o controle de um curador humano operando o device |
| Existe **teto explícito de telas simultâneas** | Cada tela aberta custa um processo de codificação no host. Sem teto, abrir a grade derruba o servidor que se queria observar |

O montador do link vive em [`panel/lib/servers.ts`](../../panel/lib/servers.ts) (`urlDaTela`), e devolve `null` quando falta `adb_target` ou `video_device` — o botão nasce desabilitado, com o motivo no `title`, em vez de abrir uma tela preta.

---

## 9. HTTPS na 443 ou 4000 direto?

**Recomendação: cadastrar o serviço na `:443`, atrás do nginx.** É o default sugerido pela API (`scheme=https`, `port=443`, `health=/api/health`, `web=/`).

| | `:443` (nginx) | `:4000` (backend direto) |
|---|---|---|
| Interface web | ✅ servida na raiz | ❌ não serve interface |
| `web_url` clicável no painel | ✅ | ❌ |
| Transmissão de tela por iframe | ✅ (painel em HTTPS não embute HTTP) | ❌ conteúdo misto bloqueado |
| Credencial na rede | protegida por TLS | **usuário e senha em texto claro** |
| Certificado | tipicamente **autoassinado** | — |

O certificado autoassinado é a regra em rede interna. Desligar a verificação é uma decisão **por serviço** (`server_services.verify_tls = false`), nunca global, e o painel mostra um cadeado aberto âmbar quando é o caso — a informação fica visível, não escondida no banco.

**A `:4000` é aceitável só como contorno temporário** (nginx ainda não configurado, depuração). Nesse caso, não há transmissão de tela nem link clicável, e a senha trafega em claro dentro da rede fechada.

---

## 10. O que continua incerto

Nenhum item abaixo bloqueia a Fase 1.

| # | ❓ | Impacto | Contorno hoje |
|---|---|---|---|
| 1 | **Nomes exatos dos campos de `/api/devices`** e estabilidade entre versões | Inventário | `normalize_device()` aceita sinônimos e guarda o objeto cru em `devices.metadata` |
| 2 | **Formato de `/api/system`** (chaves e unidades de `cpu`/`memory`/`gpu`) | Alerta de saturação do host | Lemos o que existe, exibimos o resto como bruto |
| 3 | **Formato de `/api/watchdog/status`** e quais permissões ele exige | `under_external_management` | Heurística `enabled`/`running`; ausência não é falha |
| 4 | **Formato de `/api/config/groups`** | Rótulos de frota | Guardado em `capabilities`, não é caminho crítico |
| 5 | **Validade do token de login** (TTL, renovação) | Frequência de re-login | Login preguiçoso + uma repetição em `401` |
| 6 | **O `id` de device usado nas rotas com parâmetro é o nome do container?** | Captura e `/users` | Usamos o valor que veio de `/api/devices`; ainda não houve divergência |
| 7 | **`/api/extproxy`** — o que expõe e se liga proxy a conta | Anti-ban | Fora da allowlist. Não consultamos |
| 8 | **Contrato do gateway de tela após o WebRTC** | Transmissão | Modelado como serviço opaco com URL própria ([§8](#8-transmissão-de-tela-por-que-o-olimpo-é-agnóstico-ao-transporte)) |

---

## Ver também

- [README deste diretório](README.md) — princípios comuns aos clientes
- [`src/olimpo/integrations/android_manager.py`](../../src/olimpo/integrations/android_manager.py) — a allowlist executável
- [android-orchestrator.md](android-orchestrator.md) — quem de fato **usa** a frota, e quem sabe de contas
- [ADR-0012](../adr/0012-servidor-com-varios-servicos.md) — por que servidor e serviço são coisas diferentes
- [`db/migrations/0005_servers_and_services.sql`](../../db/migrations/0005_servers_and_services.sql) — `servers`, `server_services`, `devices`
- [../02-modelo-de-dados.md](../02-modelo-de-dados.md) — as tabelas citadas aqui
- [../10-painel.md](../10-painel.md) — a tela de Servidores
