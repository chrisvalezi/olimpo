# 01 — Arquitetura

> Como as peças se encaixam, quais são as fronteiras, e por que elas estão onde estão.

---

## 1. As quatro camadas

```
┌─────────────────────────────────────────────────────────────────────┐
│  PAINEL                                    Next.js 15 · porta 3000  │
│  sala de controle · aprovações · campanhas · observabilidade        │
└───────────────────────────────┬─────────────────────────────────────┘
                                │ REST + SSE
┌───────────────────────────────▼─────────────────────────────────────┐
│  CORE (Olimpo)                            FastAPI + Celery · 8000   │
│                                                                     │
│  ┌──────────────┐  ┌──────────────┐  ┌──────────────┐               │
│  │   MANAGER    │  │   AGENTES    │  │  MONITORES   │               │
│  │  campanhas   │  │  1 por conta │  │  vigias 24/7 │               │
│  │  políticas   │  │  o loop      │  │  (Fase 4)    │               │
│  │  aprovações  │  │  memória     │  │              │               │
│  └──────────────┘  └──────────────┘  └──────────────┘               │
│  ┌────────────────────────────────────────────────────────────┐     │
│  │ QA · guardrails · dispatcher · reconciliador · trend engine │     │
│  └────────────────────────────────────────────────────────────┘     │
│                                                                     │
│  Postgres 16 + pgvector  ·  Redis (fila + locks + cache)            │
└───────────────┬──────────────────────────────┬──────────────────────┘
                │                              │
    ┌───────────▼───────────┐      ┌───────────▼────────────────────┐
    │  LLM GATEWAY          │      │  SERVIÇOS EXTERNOS (já existem)│
    │  LiteLLM · 4000       │      │  máquina de influencers        │
    │  ├─ Mac mini 1 M4     │      │  media-forge (Fase 5)          │
    │  ├─ Mac mini 2 M4     │      │  android-manager (leitura)     │
    │  └─ Anthropic/OpenAI  │      └────────────────────────────────┘
    └───────────────────────┘
                                   ┌────────────────────────────────┐
                                   │  ANDROID-ORCHESTRATOR          │
                                   │  executa + VERIFICA em device  │
                                   │         ▼                      │
                                   │  frota redroid (Android real)  │
                                   │  Instagram · WhatsApp · TikTok │
                                   └────────────────────────────────┘
```

---

## 2. A fronteira que define o projeto

**O core decide. O orchestrator executa.** Nenhuma linha do Olimpo fala com o Instagram.

Isso não é purismo arquitetural — é o que viabiliza três coisas:

1. **Dry-run total.** Com `DISPATCH_ENABLED=false`, o sistema inteiro roda de ponta a ponta gravando em disco. Dá para validar meses de comportamento narrativo sem tocar em uma conta real.
2. **Substituibilidade.** Plataforma nova (TikTok, Kwai, X) é trabalho no orchestrator, não no core. O core só ganha um valor novo no enum `platform` e os payloads correspondentes.
3. **Verificação real.** O orchestrator confirma que a ação *aconteceu no device*, não que a requisição retornou 200. É uma garantia que API oficial nenhuma dá.

O custo aceito: latência maior, dependência de um serviço externo, e a necessidade de um **reconciliador** para tasks que ficaram ambíguas. Ver [ADR-0002](adr/0002-separacao-core-execucao.md).

---

## 3. Módulos do core

```
src/olimpo/
├── core/          modelos, config, sessão de banco, QA, guardrails, eventos
├── agents/        o loop: planner, source/*, producer, recorder, reflector
├── campaigns/     manager, recon, planner, spread policy
├── monitors/      vigias 24/7, ingestão, dedup, regras de disparo  (Fase 4)
├── llm/           cliente do gateway, prompts, saída estruturada, contabilidade
├── integrations/  clientes dos serviços externos (com dry-run e circuit breaker)
├── workers/       Celery: dispatcher, reconciler, tick, tasks
├── api/           FastAPI: REST do painel, webhooks, SSE, métricas
└── db/            migrações, runner, seeds
```

**Regra de dependência:** `api` e `workers` dependem de `agents`/`campaigns`/`monitors`, que dependem de `core`/`llm`/`integrations`. Nunca o contrário. `core` não importa nada de camada superior.

---

## 4. Os dois relógios

O sistema tem dois ritmos, e confundi-los é a origem de quase todo bug de agendamento.

**Relógio do agente** — lento, diário. O planner roda uma vez por dia e produz intenções com horário sugerido. Trabalha em escala de horas.

**Relógio do dispatcher** — rápido, a cada 30s. Olha o que está vencido *agora* em `tasks.scheduled_for` e despacha. Não sabe nada sobre narrativa.

A **fonte da verdade do agendamento é o Postgres**, não a fila. O Celery Beat só dá o tick; ele não carrega tasks futuras. Isso permite que o painel cancele, reagende e audite qualquer ação futura — e que uma queda do Redis não perca o dia inteiro. Ver [ADR-0004](adr/0004-agendamento-no-banco.md).

---

## 5. Filas e prioridade

| Fila | Conteúdo | Prioridade |
|---|---|---|
| `realtime` | Webhooks, verificação, resposta a comentário, checagem de monitor quente | Máxima |
| `dispatch` | Envio de tasks ao orchestrator | Alta |
| `production` | Plano do dia, geração de mídia, caption, QA | Normal |
| `maintenance` | Reflexão, coleta de métricas, limpeza, reconciliação | Baixa |

Quando os Mac minis saturam, a degradação é intencional e nesta ordem: adia-se `maintenance`, depois `production`. **`realtime` nunca é adiada** — um seguidor esperando resposta é custo de reputação; um plano do dia atrasado 20 minutos não é.

---

## 6. Estado e consistência

**Postgres é a única fonte da verdade.** Redis é fila, lock e cache — nada que doa perder.

Pontos de consistência que exigem cuidado:

| Situação | Tratamento |
|---|---|
| Duas execuções do planner no mesmo dia | `UNIQUE (account_id, plan_date)` em `day_plans` |
| Dispatch duplicado da mesma task | Lock por task no Redis + chave de idempotência no orchestrator |
| Task despachada mas resposta perdida | Reconciliador consulta o estado externo; **na dúvida, não republica** |
| Duas campanhas mirando a mesma conta na mesma hora | Teto `ACCOUNT_MAX_ACTIONS_PER_HOUR` avaliado no dispatch, não no planejamento |
| Restore de backup com tasks em voo | Runbook de reconciliação — ver [runbooks/restaurar-backup.md](runbooks/restaurar-backup.md) |

---

## 7. Auditabilidade

Toda decisão do sistema grava em `events` com `trace_id`. A pergunta *"por que a conta X postou isso às 19h?"* precisa ser respondível meses depois, atravessando: plano do dia → intenção → item de conteúdo → raciocínio da LLM → relatório de QA → aprovação → task → verificação → post.

Isso é requisito de primeira classe, não logging. É o que permite depurar comportamento emergente — e o que responde a um questionamento externo sobre uma publicação.

---

## 8. O que roda onde

| Componente | Onde | Por quê |
|---|---|---|
| Core, banco, fila, painel | Docker, uma máquina | Simples de operar; Postgres e Redis não são gargalo nesta escala |
| LLM local | 2 Mac mini M4 (fora do Docker) | Ollama/MLX com aceleração nativa do Apple Silicon |
| Frota Android | android-manager (redroid) | Já existente, escala independente |
| Geração de mídia | máquina de influencers | Já existente |

**O primeiro gargalo previsível são os Mac minis**, não o banco. A mitigação é a política de roteamento do gateway e a prioridade de fila — e, se persistir, um terceiro node ou overflow controlado para API externa. Ver [LLM Gateway](09-llm-gateway.md).

---

## Referências

[Loop do agente](05-loop-do-agente.md) · [Modelo de dados](02-modelo-de-dados.md) · [Integrações](03-integracoes.md) · [ADRs](adr/)
