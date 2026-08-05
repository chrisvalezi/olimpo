# Runbooks — Olimpo

Procedimentos de plantão. Cada runbook segue a mesma estrutura:

> **Sintoma** → **Diagnóstico** (comandos concretos) → **Correção** (passo a passo) → **Prevenção** → **Escalação**

Um runbook existe para ser seguido às 3h da manhã por alguém que não escreveu o código. Se um passo exigir raciocínio original, o runbook está incompleto — corrija-o.

---

## Antes de qualquer coisa

Duas verdades operacionais que valem para todos os runbooks:

1. **Parar é barato. Não parar pode custar a operação.** Acionar `GLOBAL_KILL_SWITCH` custa horas de conteúdo. Deixar uma frota rodando durante um incidente pode custar contas, dinheiro ou um processo. Na dúvida, pare.
2. **Preserve antes de corrigir.** `pg_dump` e cópia dos logs vêm **antes** da correção. O estado pós-correção não serve de evidência nem de post-mortem.

```bash
# Estado geral, 15 segundos
docker compose ps
curl -fsS localhost:8000/health | jq .
docker compose exec worker celery -A olimpo.workers.app inspect ping
docker compose exec postgres psql -U olimpo -d olimpo -c \
  "SELECT status, count(*) FROM tasks GROUP BY status ORDER BY 2 DESC;"
```

---

## Índice por sintoma

| Sintoma observado | Alerta associado | Runbook |
|---|---|---|
| Conta suspensa, bloqueada ou banida na plataforma | `ContaPerdida` | [conta-banida.md](conta-banida.md) |
| Várias contas caíram na mesma janela | `MultiplasContasSuspensas` | [conta-banida.md](conta-banida.md) → depois [kill-switch.md](kill-switch.md) |
| Queda abrupta de seguidores / posts sem alcance | `QuedaDeSeguidores` | [conta-banida.md](conta-banida.md) (seção shadowban) |
| Conta ativa há > 48 h sem ação nenhuma | `ContaSilenciosa` | [fila-travada.md](fila-travada.md) |
| Preciso parar tudo, agora | — | [kill-switch.md](kill-switch.md) |
| Preciso parar **uma** conta | — | [kill-switch.md](kill-switch.md#4-parada-por-conta) |
| Como retomar depois de uma parada | — | [kill-switch.md](kill-switch.md#6-retomada) |
| Tasks empilhando, nada sai | `FilaTravada`, `FilaRealtimeAtrasada` | [fila-travada.md](fila-travada.md) |
| Task mais antiga pendente com idade crescente | `FilaTravada` | [fila-travada.md](fila-travada.md) |
| Tasks presas em `dispatched` sem verificação | `TasksPresasEmDispatched` | [orchestrator-fora-do-ar.md](orchestrator-fora-do-ar.md) |
| Muitas tasks expirando (`expired`) | `TasksExpirando` | [fila-travada.md](fila-travada.md) |
| android-orchestrator não responde / circuito aberto | `OrchestratorForaDoAr` | [orchestrator-fora-do-ar.md](orchestrator-fora-do-ar.md) |
| Webhook parou de chegar | `TasksPresasEmDispatched` | [orchestrator-fora-do-ar.md](orchestrator-fora-do-ar.md) |
| Mac minis lentos, LLM demorando | `LLMSaturada` | [llm-saturada.md](llm-saturada.md) |
| Um dos dois Mac minis parou de receber tráfego | `MacMiniMudo` | [llm-saturada.md](llm-saturada.md) |
| Custo de LLM externo disparando | `OrcamentoLLMEstourando` | [llm-saturada.md](llm-saturada.md#6-controle-de-custo) |
| Conteúdo impróprio, ofensivo ou ilegal foi publicado | `ConteudoImproprioPublicado` | [conteudo-improprio-publicado.md](conteudo-improprio-publicado.md) |
| Notificação de DMCA / direito de imagem | — | [conteudo-improprio-publicado.md](conteudo-improprio-publicado.md) |
| Agente escrevendo fora da persona | `PersonaDerivando` | [conteudo-improprio-publicado.md](conteudo-improprio-publicado.md#7-quando-não-é-incidente-mas-deriva) |
| Guardrail bloqueando tudo | `GuardrailBloqueandoDemais` | [conteudo-improprio-publicado.md](conteudo-improprio-publicado.md#5-correção-do-guardrail) |
| Postgres corrompido, perdido ou revertido | `PostgresIndisponivel` | [restaurar-backup.md](restaurar-backup.md) |
| Backup não rodou | `BackupNaoVerificado` | [restaurar-backup.md](restaurar-backup.md#7-prevenção) |
| Worker Celery morto | `WorkerMorto` | [fila-travada.md](fila-travada.md#31-worker-morto-ou-sem-consumir) |
| Vazamento de segredo / acesso indevido | — | [../12-seguranca.md](../12-seguranca.md#54-resposta-a-incidente) |

---

## Índice por runbook

| # | Runbook | Quando | Severidade típica |
|---|---|---|---|
| 1 | [conta-banida.md](conta-banida.md) | Conta suspensa ou perdida | **P1** |
| 2 | [kill-switch.md](kill-switch.md) | Parada global ou por conta | **P1** |
| 3 | [fila-travada.md](fila-travada.md) | Pipeline parado, tasks empilhando | **P1/P2** |
| 4 | [orchestrator-fora-do-ar.md](orchestrator-fora-do-ar.md) | Camada de execução indisponível | **P1** |
| 5 | [llm-saturada.md](llm-saturada.md) | Mac minis saturados ou lentos | **P2** |
| 6 | [conteudo-improprio-publicado.md](conteudo-improprio-publicado.md) | Algo ruim foi ao ar | **P1** |
| 7 | [restaurar-backup.md](restaurar-backup.md) | Recuperação do Postgres | **P1** |

---

## Comandos que aparecem em todos os runbooks

```bash
# Postgres
docker compose exec postgres psql -U olimpo -d olimpo          # ou: make psql
docker compose exec -T postgres psql -U olimpo -d olimpo -c "SELECT 1;"

# Redis / filas
docker compose exec redis redis-cli                             # ou: make redis
docker compose exec redis redis-cli LLEN dispatch

# Celery
docker compose exec worker celery -A olimpo.workers.app inspect ping
docker compose exec worker celery -A olimpo.workers.app inspect active
docker compose exec worker celery -A olimpo.workers.app inspect reserved

# Logs
docker compose logs -f --tail=200 worker                        # ou: make worker-logs
docker compose logs --since 1h --no-log-prefix api | jq 'select(.level=="error")'

# Serviços
docker compose ps
docker compose restart api worker beat                          # ou: make restart
curl -fsS localhost:8000/health | jq .
curl -fsS "${ORCHESTRATOR_URL}/health"
```

---

## Depois de todo incidente

- [ ] Timeline reconstruída via `events` por `trace_id` — ver [11-observabilidade.md §6](../11-observabilidade.md#6-rastreabilidade-reconstruindo-a-história-de-um-post)
- [ ] Evento de fechamento emitido: `type='incident.closed'` com `detail.runbook`, `detail.duration_min`, `detail.root_cause`
- [ ] Runbook atualizado se algum passo faltou, falhou ou exigiu improviso
- [ ] Se houve mudança de arquitetura, registrar em `docs/adr/`
- [ ] Se o alerta não disparou quando deveria, ou disparou tarde, ajustar o limiar em `ops/prometheus/rules/olimpo.yml`
