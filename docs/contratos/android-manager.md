# Contrato — android-manager

> **Estado:** o serviço **existe e está pronto** (repositório privado `chrisvalezi/android-manager`). Este documento descreve o **contrato que o Olimpo assume**, escrito sem acesso ao código. Pontos marcados ❓ **A CONFIRMAR** dependem de checar a API real.

| | |
|---|---|
| **Papel** | Painel de gerenciamento da frota de containers redroid |
| **Variáveis** | `ANDROID_MANAGER_URL`, `ANDROID_MANAGER_TOKEN` |
| **Cliente** | `src/olimpo/integrations/android_manager/` |
| **Acesso do Olimpo** | 🔒 **SOMENTE LEITURA** |
| **Fase** | 1 |

## Escopo: o que o Olimpo faz e o que não faz

> **O Olimpo NÃO gerencia a frota.** Ele consulta disponibilidade antes de despachar e registra o vínculo em `accounts.device_binding`. Nada mais.

| ✅ O Olimpo faz | ❌ O Olimpo nunca faz |
|---|---|
| Lista devices/containers | Cria, destrói ou reinicia container |
| Lê saúde de um device | Instala ou atualiza app |
| Consulta qual conta está logada onde | **Faz login de conta** — isso é trabalho de **curador humano** |
| Grava `device_binding` em `accounts` | Altera qualquer configuração de device |
| Verifica disponibilidade antes de despachar | Aloca ou reserva device (quem enfileira é o orchestrator) |

Login de conta é operação humana e assim permanece: é a etapa que exige julgamento, verificação de segurança, resolução de checkpoint. Automatizá-la é a forma mais rápida de queimar contas em lote.

**Fronteira com o android-orchestrator:** o orchestrator *usa* a frota (executa ações no device); o android-manager *descreve* a frota (estado, saúde, quem está logado). O Olimpo pergunta ao manager *"dá para agir agora?"* e manda o orchestrator agir. Se as duas fontes divergirem, **o orchestrator ganha** — quem executa sabe mais do que quem observa.

---

## 1. Autenticação

```http
GET /v1/devices HTTP/1.1
Authorization: Bearer ${ANDROID_MANAGER_TOKEN}
X-Trace-Id: olimpo-2026-08-05-7f3a91
```

❓ **A CONFIRMAR:** esquema, e se existe token com escopo **read-only**. O Olimpo deveria receber uma credencial que *tecnicamente não consegue* escrever — princípio do menor privilégio, e proteção contra um bug nosso mexer na frota.

---

## 2. Listar devices

```
GET {ANDROID_MANAGER_URL}/v1/devices
```

```json
{
  "devices": [
    {
      "device_id": "redroid-07",
      "container_id": "ctr_9f21a",
      "status": "running",
      "healthy": true,
      "host": "farm-node-02",
      "android_version": "13",
      "uptime_seconds": 421800,
      "resources": { "cpu_percent": 31.4, "memory_percent": 62.0 },
      "network": { "proxy_id": "prx_br_sp_18", "public_ip_country": "BR" },
      "accounts": [
        { "platform": "instagram", "handle": "luna.corre",
          "logged_in": true, "last_verified_at": "2026-08-05T09:12:00-03:00" }
      ],
      "busy": false,
      "last_seen_at": "2026-08-05T14:31:58-03:00"
    }
  ],
  "total": 24,
  "healthy": 22
}
```

| Campo | Uso no Olimpo | Marca |
|---|---|---|
| `device_id` / `container_id` | chave de `accounts.device_binding` | ❓ qual é a chave estável? |
| `status` / `healthy` | gate antes de despachar | ❓ quais valores existem? |
| `accounts[]` | qual conta vive onde | ❓ o manager sabe disso? |
| `logged_in` | **o gate mais importante** | ❓ é verificado de verdade ou é só o último estado conhecido? |
| `busy` | evita empilhar ação no mesmo device | ❓ existe? |
| `network.proxy_id` | identidade de rede estável por conta | ❓ existe? |
| `last_seen_at` | dado velho = dado inútil | ❓ qual a frequência de heartbeat? |

O par `logged_in` + `last_verified_at` é o que mais interessa. *"Estava logada às 9h"* às 22h não é garantia de nada — o Olimpo trata verificação com mais de N horas como desconhecida e despacha assim mesmo, deixando o orchestrator descobrir (§5).

---

## 3. Consultar um device / localizar uma conta

```
GET {ANDROID_MANAGER_URL}/v1/devices/{device_id}
GET {ANDROID_MANAGER_URL}/v1/accounts?handle=luna.corre&platform=instagram
```

```json
{
  "handle": "luna.corre",
  "platform": "instagram",
  "device_id": "redroid-07",
  "container_id": "ctr_9f21a",
  "logged_in": true,
  "last_verified_at": "2026-08-05T09:12:00-03:00",
  "device_healthy": true,
  "device_busy": false
}
```

❓ **A CONFIRMAR:** existe busca por handle, ou o Olimpo precisa varrer `/v1/devices` e filtrar? Com 50 contas, varrer a cada despacho é caro — nesse caso o cliente mantém cache de 60 s.

---

## 4. Saúde da frota

```
GET {ANDROID_MANAGER_URL}/v1/health
```

```json
{
  "status": "ok",
  "devices": { "total": 24, "running": 23, "healthy": 22, "busy": 9 },
  "hosts": [
    { "host": "farm-node-01", "devices": 12, "healthy": 12 },
    { "host": "farm-node-02", "devices": 12, "healthy": 10 }
  ],
  "updated_at": "2026-08-05T14:32:00-03:00"
}
```

Vai para o painel ([docs/10](../10-painel.md)) e para as métricas ([docs/11](../11-observabilidade.md)). Frota saudável abaixo de um limiar é alerta operacional: com metade dos devices fora, o espalhamento da campanha degrada e o padrão fica mais concentrado — anti-detecção piora junto com a infra.

---

## 5. Como o Olimpo usa isso no despacho

```
task pronta para despachar
        │
        ▼
consulta android-manager (cache 60 s)
        │
        ├── device saudável + conta logada ──▶ despacha ao orchestrator
        │
        ├── device unhealthy / offline ──────▶ reagenda +10 min (não é falha)
        │
        ├── conta não logada ────────────────▶ NÃO despacha
        │                                      conta → paused
        │                                      alerta de CURADORIA HUMANA
        │
        └── manager fora do ar (circuito) ───▶ NÃO despacha às cegas
                                               reagenda, alerta
```

Duas regras:

1. **Nunca despachar com o manager fora do ar.** É tentador tratar "não sei" como "provavelmente tudo bem". Mas despachar para device errado ou conta deslogada gera erro no orchestrator, tentativa de login inesperada e ruído na conta. Adiar é sempre mais barato.
2. **"Conta não logada" nunca é retry automático.** É alerta humano. Retentar não faz a sessão voltar.

### `accounts.device_binding`

```json
{
  "device_id": "redroid-07",
  "container_id": "ctr_9f21a",
  "host": "farm-node-02",
  "proxy_id": "prx_br_sp_18",
  "bound_at": "2026-07-14T11:02:00-03:00",
  "last_confirmed_at": "2026-08-05T14:31:58-03:00",
  "source": "android_manager"
}
```

**Estabilidade de device e de identidade de rede é mitigação de ban** ([PLANO §9](../../PLANO.md)). Uma conta que aparece de dez IPs e dez devices diferentes é sinalizada rápido. O Olimpo não *controla* o binding — mas **detecta** quando ele muda:

```
device_binding.device_id != resposta atual do manager
        │
        ▼
evento "account.device_binding.changed" (severity: warn)
+ atualiza o binding
+ 24 h de cadência reduzida para a conta (comportamento conservador após migração)
```

---

## 6. Falha e circuit breaker

| Situação | Reação |
|---|---|
| Timeout / `5xx` | retry (3×, backoff); depois circuito abre |
| Circuito aberto | despachos **suspensos**, tasks ficam em `scheduled`, alerta |
| `404` na conta | conta → `paused`, alerta de curadoria |
| `401 / 403` | para o cliente, alerta de credencial |
| Resposta fora do schema | `ContractViolation` — este doc está errado, corrigir |

O cache de 60 s dá tolerância natural: uma piscada do manager não derruba o despacho.

---

## 7. Perguntas para validar com o serviço real

| # | Pergunta | Impacto |
|---|---|---|
| 1 | **Existe API HTTP**, ou o android-manager é só um painel web? | 🔴 Se for só painel, o Olimpo não consulta nada e precisa confiar no orchestrator para resolver o device |
| 2 | Qual o endpoint de **listar devices** e o formato exato? | 🔴 |
| 3 | O manager sabe **qual conta está logada em qual container**? | 🔴 É o dado principal que o Olimpo quer daqui |
| 4 | `logged_in` é **verificado ativamente** ou é o último estado conhecido? Com que frequência? | 🔴 Define se dá para confiar no gate |
| 5 | Existe busca **por handle**, ou é preciso varrer a lista? | 🟡 Define cache e custo |
| 6 | Qual a chave estável de um device — `device_id`, `container_id`, nome? Muda ao reiniciar? | 🟡 `device_binding` depende disso |
| 7 | Existe flag **`busy`** (device executando task)? | 🟡 Evita empilhar ações |
| 8 | O manager expõe **proxy / identidade de rede** por device? | 🟡 Relevante para anti-ban |
| 9 | Existe endpoint de **health** da frota? | 🟡 Painel e alertas |
| 10 | Dá para emitir **token read-only**? | 🟡 Menor privilégio |
| 11 | Existe **webhook/evento** quando um device cai ou uma conta desloga? | 🟢 Substituiria polling por reação imediata |
| 12 | Qual a frequência de **heartbeat**? Quão velho pode estar o `last_seen_at`? | 🟢 |
| 13 | Existe OpenAPI/Swagger? *(**peça isto primeiro**)* | 🟢 |
| 14 | Quando um curador loga uma conta nova, **o Olimpo é notificado** ou precisa descobrir por polling? | 🟢 Afeta o onboarding de conta (Fase 6) |

---

## Ver também

- [README deste diretório](README.md) — princípios comuns aos clientes
- [android-orchestrator.md](android-orchestrator.md) — quem de fato usa a frota
- [../03-integracoes.md](../03-integracoes.md) — o fluxo ponta a ponta
- [../11-observabilidade.md](../11-observabilidade.md) — métricas de frota
