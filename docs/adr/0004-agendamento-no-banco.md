# ADR-0004: O agendamento mora no banco; o Beat só dá o tick

- **Status:** Aceito
- **Data:** 2026-08-05
- **Decisores:** equipe Olimpo

## Contexto

O Olimpo é, no fundo, uma máquina de agendar coisas no futuro. Quase toda ação nasce com um horário:

- Um plano de dia gera intenções que viram tasks espalhadas pelas horas acordadas da conta (`accounts.active_hours`).
- Uma campanha sorteia um `slot_at` por participante dentro da janela, com jitter de 25 a 240 minutos (`CAMPAIGN_JITTER_MIN_MINUTES`/`MAX`) — ver ADR-0007.
- Um monitor tem `next_check_at`.
- Uma coleta de métricas é agendada para T+1h, T+6h, T+24h depois da publicação.

Três requisitos não-negociáveis moldam a decisão:

1. **Auditabilidade.** Precisamos responder, meses depois, "o que estava agendado para ontem às 19h, para quem, por qual motivo, e o que aconteceu com isso?". Isso inclui o que foi **cancelado** e o que foi **reagendado**, com quem fez e por quê.
2. **Cancelamento e reagendamento pelo painel.** Um humano vê a fila do dia, discorda de um post, cancela. Ou uma campanha é abortada e todas as tasks pendentes dela precisam morrer. Ou o kill switch de uma conta dispara e tudo dela para. Isso precisa ser um `UPDATE` e nada mais.
3. **Sobrevivência a restart.** Worker reiniciado, deploy no meio da tarde, container morto por OOM — nada disso pode perder ou duplicar um agendamento.

O caminho de menor esforço no ecossistema Celery seria usar `apply_async(eta=...)` ou `countdown=...`. É exatamente o caminho errado, pelas razões abaixo.

## Decisão

**O Postgres é a única fonte da verdade do agendamento: `tasks.scheduled_for` decide quando algo acontece. O Celery Beat não agenda nada de negócio — ele apenas emite um tick periódico que acorda o dispatcher, que consulta o banco e despacha o que está vencido.**

Detalhamento:

- **Escrita.** Planos, campanhas e monitores criam linhas em `tasks` com `scheduled_for` (e opcionalmente `expires_at`, `priority`, `campaign_id`, `monitor_id`). Ninguém agenda nada fora do banco.
- **Tick.** O Beat roda uma única task recorrente de infraestrutura — `dispatcher.tick`, a cada ~30 s — mais alguns jobs de manutenção (reconciliação de tasks in-flight, expiração, coleta de métricas atrasadas). O Beat não conhece contas, campanhas ou personas.
- **Leitura.** Cada tick executa essencialmente:
  ```sql
  SELECT ... FROM tasks
   WHERE status IN ('pending','scheduled')
     AND scheduled_for <= now()
     AND (expires_at IS NULL OR expires_at > now())
   ORDER BY priority, scheduled_for
   LIMIT :batch
     FOR UPDATE SKIP LOCKED;
  ```
  Servido pelo índice parcial `idx_tasks_due (scheduled_for, priority) WHERE status IN ('pending','scheduled')`.
- **`FOR UPDATE SKIP LOCKED`** garante que múltiplos workers/ticks concorrentes nunca peguem a mesma task, sem lock global e sem necessidade de lock distribuído no Redis para este caminho.
- **Guardrails no dispatch, não no planejamento.** Antes de despachar, o dispatcher aplica: kill switch global (`GLOBAL_KILL_SWITCH`) e por conta (`accounts.status`), janela acordada (`active_hours` no timezone da conta), teto de ações/hora (`ACCOUNT_MAX_ACTIONS_PER_HOUR`) e `DISPATCH_ENABLED`. Uma task que não passa é **reagendada** (`scheduled_for` empurrado) ou **expirada**, e o motivo vai para `events`.
- **Cancelar é `UPDATE tasks SET status='cancelled'`.** Reagendar é `UPDATE tasks SET scheduled_for=...`. Ambos geram linha em `events`. Nada precisa ser "retirado" de lugar nenhum.
- **Redis é transporte, não estado.** O broker carrega a execução de uma task já decidida. Se o Redis for zerado, nenhum agendamento é perdido — o próximo tick reencontra tudo no Postgres.

## Alternativas consideradas

| Alternativa | Prós | Contras | Por que não |
|---|---|---|---|
| **Celery `eta`/`countdown`** | Uma linha de código; sem dispatcher para escrever | A task fica **presa na memória de um worker** que a reservou (prefetch) até o horário; cancelar exige `revoke` best-effort com `terminate`, sem garantia; reagendar é revogar e recriar; restart do worker devolve a mensagem ao broker com semântica confusa; nada disso é auditável; visibilidade só via Flower | **Rejeitado com prejuízo.** Falha exatamente nos três requisitos: auditoria, cancelamento e sobrevivência a restart. Agendamentos nossos chegam a horas de distância — manter isso em RAM de worker é inaceitável. |
| **APScheduler com jobstore no Postgres** | Estado persistido; API de agendamento decente; `modify`/`remove` existem | O estado é um blob serializado (pickle/JSON) do job, não uma linha de negócio consultável; painel não faz join com `accounts`/`campaigns`; conviveria mal com Celery, que já está na stack para execução; scheduler ativo único exige eleição de líder | Persistir estado num formato que o painel não consegue consultar derrota o objetivo. Se o agendamento já vai para o Postgres, ele deve ir como **dado de primeira classe**. |
| **cron do sistema / `pg_cron`** | Simples, robusto, conhecido | Granularidade e modelo de recorrência fixa; nosso agendamento é por evento único e irregular (slot sorteado com jitter para uma conta específica), não recorrente; cancelar um "post das 19h32 da Luna" não é conceito que cron tenha | Modelo errado. `pg_cron` serve, no máximo, como fallback para emitir o tick. |
| **Fila de prioridade no Redis (sorted set por timestamp)** | Rápido; `ZRANGEBYSCORE` é natural para isso | Segunda fonte de verdade; Redis não é durável no nosso setup como banco primário; perde join com o resto; auditoria teria de ser espelhada no Postgres de qualquer forma | Se o Postgres precisa ter a linha de qualquer jeito para auditoria, o Redis vira cache redundante com risco de divergência. |
| **Banco como fonte + Beat como tick** (escolhida) | Auditável, cancelável, reagendável, sobrevive a tudo | Polling (latência de até um tick); dispatcher é código nosso; carga periódica no banco | Aceito. |

## Consequências

### Positivas
- **Auditoria completa e gratuita.** A fila do futuro é uma tabela. "O que está agendado para amanhã?" é `SELECT`. "O que foi cancelado na semana passada e por quem?" é `SELECT` em `tasks` + `events`. Nenhum sistema de agendamento externo entrega isso.
- **Painel poderoso sem esforço.** A tela "fila do dia" é uma listagem com join em `accounts` e `campaigns`. Cancelar, adiar, mudar prioridade, cancelar a campanha inteira: tudo `UPDATE`, tudo transacional, tudo com trilha.
- **Cancelamento em massa é trivial e confiável.** `UPDATE tasks SET status='cancelled' WHERE campaign_id = X AND status IN ('pending','scheduled')`. Com `eta` isso seria uma dança de `revoke` sem garantia.
- **Sobrevive a qualquer restart.** Worker, Beat, Redis, tudo pode morrer. O estado está no banco. O próximo tick continua de onde parou.
- **Guardrails aplicados no último momento possível.** Uma task planejada às 8h para as 20h só é avaliada contra o kill switch às 20h. Se a conta foi suspensa às 15h, a task não sai — mesmo tendo sido planejada antes.
- **Backpressure natural.** Se o farm está lento, as tasks simplesmente acumulam em `pending`. Nada estoura, nada se perde, e a fila é visível.

### Negativas / custos aceitos
- **Latência de até um tick.** Com tick de 30 s, uma task agendada para 19h00m01s sai em até 19h00m31s. Irrelevante para conteúdo (que tem jitter de minutos por design) e aceitável para o resto. Se o arquétipo gossip exigir mais, o tick da fila `realtime` cai para 5 s — e mesmo assim a latência do farm (ADR-0002) domina.
- **Polling constante.** O banco leva uma query a cada 30 s, para sempre. É barata (índice parcial, `LIMIT`), mas é carga que uma abordagem push não teria.
- **Escrevemos o dispatcher.** Lease, retry, backoff, expiração, reconciliação — tudo código nosso, com testes próprios. O Celery daria parte disso de graça se aceitássemos `eta`.
- **Beat continua sendo ponto único.** Duas instâncias de Beat = ticks duplicados. Mitigado pelo `SKIP LOCKED` (tick duplo é inofensivo, só desperdiça uma query), mas o compose mantém uma instância só.
- **Relógio importa.** `scheduled_for` é `timestamptz` e a comparação é com `now()` do banco — o relógio de referência é o do Postgres, não o dos workers. Isso é uma simplificação deliberada.

### Riscos e como mitigamos
- **Risco: tempestade de thundering herd** (mil tasks vencidas ao mesmo tempo depois de uma indisponibilidade). Mitigação: `LIMIT` no batch por tick, rate limit por conta aplicado no dispatch, e `expires_at` descartando o que perdeu validade. A vazante é sempre controlada.
- **Risco: task travada em `dispatched` para sempre** porque o callback do orchestrator se perdeu. Mitigação: job de reconciliação varre `idx_tasks_inflight` e consulta o orchestrator por `external_task_id`; timeout leva a `failed` com `last_error`, nunca a reenvio cego (ADR-0002).
- **Risco: Beat morre e ninguém percebe.** Mitigação: métrica `olimpo_dispatcher_last_tick_timestamp` exposta ao Prometheus, com alerta se ficar velha por mais de 2 minutos. Ver `docs/11-observabilidade.md`.
- **Risco: `tasks` cresce indefinidamente e o índice parcial degrada.** Mitigação: o índice é **parcial** (`WHERE status IN ('pending','scheduled')`), então só indexa a fila viva — tasks concluídas saem do índice sozinhas. Tabela histórica é particionada por mês quando passar de dezenas de milhões.
- **Risco: alguém usa `apply_async(eta=...)` por conveniência.** Mitigação: lint/revisão bloqueia `eta` e `countdown` fora de `olimpo.workers.infra`; documentado em `docs/05-loop-do-agente.md`.

## Revisitar quando

- Precisarmos de latência de agendamento consistentemente abaixo de ~1 s ponta a ponta — o que só faria sentido se o gargalo do farm (ADR-0002) desaparecesse.
- A carga de polling se tornar mensurável no Postgres (> 1% do tempo de CPU do banco), caso em que trocamos o polling por `LISTEN/NOTIFY` mantendo o banco como fonte da verdade — mudança de transporte, não de decisão.
- O número de tasks vivas simultaneamente passar de ~1M, tornando a varredura do índice parcial cara.
- Sairmos do Celery (ex.: para um runtime async puro), o que trocaria quem dá o tick sem tocar no resto.
