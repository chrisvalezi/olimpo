# Runbook — LLM saturada

**Severidade:** P2 (P1 se `realtime` estiver atrasada) · **Alertas:** `LLMSaturada`, `MacMiniMudo`, `OrcamentoLLMEstourando`, `FilaRealtimeAtrasada`

> Dois Mac mini M4 são um recurso **finito e não elástico**. Não escalam sob demanda. Quando saturam, o Olimpo não pode "esperar passar" — precisa **escolher o que deixar de fazer**. Este runbook é sobre triagem, não sobre conserto: a decisão central é *o que sacrificar para que a interação em tempo real continue funcionando*.
>
> Ordem de prioridade, sempre: **interação > despacho > produção > reflexão**.

---

## 1. Sintoma

- `olimpo_llm_latency_seconds` p95 acima de 45 s nos backends `mac_mini_*`.
- Fila `realtime` com mensagens envelhecendo — respostas a comentários atrasadas.
- Worker com todos os slots ocupados por tasks de LLM ([fila travada](fila-travada.md) como sintoma secundário).
- Custo de API externa subindo — o LiteLLM está caindo em fallback silenciosamente.
- `llm_calls` com `success=false` e `error` de timeout se acumulando.
- Um dos dois Mac minis sem tráfego nenhum (`MacMiniMudo`).

---

## 2. Diagnóstico

### 2.1 Latência por backend — a query principal

```sql
SELECT backend,
       count(*)                                                       AS chamadas,
       round(avg(latency_ms))                                         AS media_ms,
       round(percentile_cont(0.50) WITHIN GROUP (ORDER BY latency_ms)) AS p50,
       round(percentile_cont(0.95) WITHIN GROUP (ORDER BY latency_ms)) AS p95,
       round(max(latency_ms))                                         AS pior,
       count(*) FILTER (WHERE NOT success)                            AS falhas,
       round(sum(cost_usd)::numeric, 4)                               AS custo_usd
FROM llm_calls
WHERE created_at > now() - interval '30 minutes'
GROUP BY backend
ORDER BY p95 DESC NULLS LAST;
```

| Leitura | Diagnóstico |
|---|---|
| Ambos os Macs com p95 alto | **Saturação real.** Vá para §3 |
| Só um Mac com p95 alto | Desbalanceamento ou hardware/modelo com problema. §3.4 |
| Um Mac com `chamadas = 0` | Está fora. §3.4 |
| p95 alto em `anthropic`/`openai` | Problema do provedor, não seu. Verificar status page |
| `falhas` alto com `p95` baixo | Não é saturação: erro de configuração ou contexto estourado. §2.5 |
| Latência normal e fila cheia | O gargalo não é LLM. Volte para [fila-travada.md](fila-travada.md) |

### 2.2 Evolução no tempo — desde quando

```sql
SELECT date_trunc('minute', created_at) AS minuto, backend,
       count(*) AS chamadas,
       round(avg(latency_ms)) AS media_ms
FROM llm_calls
WHERE created_at > now() - interval '2 hours'
GROUP BY minuto, backend
ORDER BY minuto DESC, backend
LIMIT 60;
```

Degradação gradual → carga crescente. Degradação abrupta → algo mudou (modelo trocado, campanha disparada, monitor novo).

### 2.3 Quem está consumindo

```sql
-- Por finalidade: onde o tempo e o dinheiro estão indo
SELECT purpose, backend, count(*) AS chamadas,
       round(avg(latency_ms))       AS media_ms,
       round(avg(prompt_tokens))    AS prompt_medio,
       round(avg(output_tokens))    AS output_medio,
       round(sum(cost_usd)::numeric, 4) AS custo
FROM llm_calls
WHERE created_at > now() - interval '1 hour'
GROUP BY purpose, backend
ORDER BY chamadas DESC;

-- Por conta: alguma conta sozinha está afogando a frota?
SELECT a.handle, a.archetype, count(*) AS chamadas,
       round(avg(l.latency_ms)) AS media_ms,
       round(sum(l.cost_usd)::numeric, 4) AS custo
FROM llm_calls l JOIN accounts a ON a.id = l.account_id
WHERE l.created_at > now() - interval '1 hour'
GROUP BY a.handle, a.archetype
ORDER BY chamadas DESC LIMIT 20;

-- Prompts muito grandes derrubam a latência mais que o volume de chamadas
SELECT purpose, round(avg(prompt_tokens)) AS prompt_medio,
       max(prompt_tokens) AS pior,
       round(corr(prompt_tokens, latency_ms)::numeric, 2) AS correlacao_com_latencia
FROM llm_calls
WHERE created_at > now() - interval '2 hours' AND prompt_tokens IS NOT NULL
GROUP BY purpose ORDER BY prompt_medio DESC;
```

> Correlação alta com `prompt_tokens` significa que o problema é **contexto inflado** — memórias demais recuperadas, Persona Card gigante — e não número de requisições. A correção é diferente (§4.5).

### 2.4 Saúde dos Mac minis

```bash
# Alcançáveis? (do container, para reproduzir o caminho real)
docker compose exec api sh -c "curl -sS -m 10 -o /dev/null -w 'mac1 %{http_code} %{time_total}s\n' ${MAC_MINI_1_URL}/api/tags"
docker compose exec api sh -c "curl -sS -m 10 -o /dev/null -w 'mac2 %{http_code} %{time_total}s\n' ${MAC_MINI_2_URL}/api/tags"

# O que cada um está processando agora
curl -fsS "${MAC_MINI_1_URL}/api/ps" | jq '.models[] | {name, size_vram, expires_at}'
curl -fsS "${MAC_MINI_2_URL}/api/ps" | jq '.models[] | {name, size_vram, expires_at}'

# Os modelos esperados estão carregados nos dois?
curl -fsS "${MAC_MINI_1_URL}/api/tags" | jq -r '.models[].name' | sort
curl -fsS "${MAC_MINI_2_URL}/api/tags" | jq -r '.models[].name' | sort

# Latência de baseline: uma geração trivial. Se isto demora, o Mac está no limite.
time curl -fsS "${MAC_MINI_1_URL}/api/generate" \
  -d '{"model":"qwen2.5:7b","prompt":"responda apenas: ok","stream":false}' \
  | jq -r '.response'
```

No próprio Mac mini (SSH):

```bash
uptime                                  # load average
sudo powermetrics --samples 1 -i 1000 | grep -iE 'GPU|thermal'
ps aux | grep -i ollama
```

> **Throttling térmico** é a causa mais subestimada. Um M4 quente entrega uma fração do throughput sem apresentar erro nenhum — só fica lento. Se a latência degrada ao longo do dia e melhora de madrugada, é temperatura.

### 2.5 Gateway LiteLLM

```bash
curl -fsS localhost:4000/health/liveliness
curl -fsS -H "Authorization: Bearer ${LITELLM_MASTER_KEY}" localhost:4000/health | jq .

docker compose logs --since 30m --no-log-prefix litellm \
  | grep -iE 'timeout|fallback|rate.?limit|cooldown|unhealthy' | tail -40
```

```sql
-- Erros por classe: distingue saturação de erro de configuração
SELECT backend, model, left(error, 80) AS erro, count(*)
FROM llm_calls
WHERE created_at > now() - interval '1 hour' AND NOT success
GROUP BY backend, model, erro
ORDER BY 4 DESC;
```

### 2.6 Impacto nas filas

```bash
for q in realtime dispatch production maintenance; do
  printf '%-12s %s\n' "$q" "$(docker compose exec -T redis redis-cli LLEN "$q")"
done

docker compose exec worker celery -A olimpo.workers.app inspect active \
  | grep -cE 'produce|plan_day|reflect|recon'
```

Fila `realtime` com backlog é **P1**: seguidores esperando resposta é dano de reputação imediato.

---

## 3. Correção — degradação graciosa

> Ordem obrigatória. Cada degrau libera capacidade sem tocar no que importa. Só desça um degrau se o anterior não bastou.

### 3.1 Degrau 1 — parar a produção não essencial

Suspende ideação e reflexão; interação e despacho continuam intactos.

```bash
# Nenhuma task nova de produção é consumida
docker compose exec worker celery -A olimpo.workers.app control \
  cancel_consumer production
```

```sql
-- Adia a reflexão do dia para a madrugada (ela não tem urgência nenhuma)
UPDATE tasks
SET scheduled_for = date_trunc('day', now()) + interval '1 day 3 hours'
                    + (random() * interval '2 hours')
WHERE status IN ('pending','scheduled')
  AND type = 'reflect'
  AND scheduled_for < now() + interval '6 hours';

-- Adia o planejamento de contas de baixa prioridade
UPDATE tasks t
SET scheduled_for = now() + interval '4 hours' + (random() * interval '2 hours')
FROM accounts a
WHERE a.id = t.account_id
  AND t.status IN ('pending','scheduled')
  AND t.type = 'plan_day'
  AND a.archetype = 'influencer'
  AND a.followers < 5000;
```

Reavaliar após 15 min com a query da §2.1.

### 3.2 Degrau 2 — garantir capacidade para `realtime`

```bash
# Worker dedicado, isolado da produção
docker compose run --rm -d worker \
  celery -A olimpo.workers.app worker --loglevel=INFO \
  --queues=realtime,dispatch --concurrency=4 --hostname=realtime@%h

# Reduz a concorrência do worker principal:
# menos requisições simultâneas ao Mac mini = menor latência por requisição
docker compose exec worker celery -A olimpo.workers.app control \
  pool_shrink 4
```

> Contra-intuitivo mas central: **reduzir a concorrência melhora a latência** quando o gargalo é o modelo local. 8 requisições simultâneas num M4 não são 8× mais rápidas — são 8× mais lentas cada uma, e todas estouram o timeout juntas.

### 3.3 Degrau 3 — pausar campanhas e monitores

```sql
-- Campanhas consomem LLM em rajada (adaptação por persona + recon)
UPDATE campaigns SET status = 'planning'
WHERE status = 'running' AND window_end > now();

UPDATE tasks
SET scheduled_for = now() + interval '3 hours' + (random() * interval '2 hours')
WHERE status IN ('pending','scheduled') AND campaign_id IS NOT NULL;

-- Monitores 24/7 (Fase 4): dobrar o intervalo de checagem
UPDATE monitors
SET interval_seconds = interval_seconds * 2,
    next_check_at = now() + (interval_seconds * 2 || ' seconds')::interval
WHERE status = 'active';
```

### 3.4 Se o problema é um Mac mini só

```bash
# Confirmar qual
curl -fsS -m 5 "${MAC_MINI_1_URL}/api/tags" >/dev/null && echo "mac1 ok" || echo "mac1 FORA"
curl -fsS -m 5 "${MAC_MINI_2_URL}/api/tags" >/dev/null && echo "mac2 ok" || echo "mac2 FORA"
```

O LiteLLM deveria tirá-lo do rodízio sozinho. Se não o fez, retire manualmente em `ops/litellm.config.yaml`, comentando o `model_list` correspondente:

```bash
${EDITOR:-vi} ops/litellm.config.yaml
docker compose restart litellm
sleep 20
curl -fsS -H "Authorization: Bearer ${LITELLM_MASTER_KEY}" localhost:4000/health | jq .
```

No Mac afetado:

```bash
# Reiniciar o Ollama e pré-carregar o modelo (o primeiro load é lento)
launchctl kickstart -k gui/$(id -u)/com.ollama.ollama   # ou: pkill ollama && ollama serve &
curl -fsS localhost:11434/api/generate \
  -d '{"model":"qwen2.5:7b","prompt":"ok","stream":false,"keep_alive":"2h"}' >/dev/null
```

**Com um Mac só, a capacidade cai pela metade.** Aplique os degraus 1 a 3 mesmo que a latência do sobrevivente pareça aceitável — ela vai degradar quando ele receber a carga inteira.

### 3.5 Degrau 4 — reduzir a demanda na raiz

```sql
-- Pausar as contas de menor valor até normalizar
UPDATE accounts SET status = 'paused'
WHERE status = 'active'
  AND archetype = 'influencer'
  AND followers < 1000;

-- Cortar a cadência à metade nas demais
UPDATE accounts
SET cadence = jsonb_set(
      jsonb_set(cadence, '{posts_per_day}',
                to_jsonb(greatest(1, (cadence->>'posts_per_day')::int / 2))),
      '{stories_per_day}',
      to_jsonb(greatest(1, (cadence->>'stories_per_day')::int / 2)))
WHERE status = 'active';
```

Registre para conseguir desfazer:

```sql
INSERT INTO events (type, detail, trace_id)
VALUES ('llm.degradation.engaged',
        jsonb_build_object('actor','<seu.nome>','degrau',4,
          'runbook','llm-saturada.md',
          'accounts_paused', <n>, 'cadence_halved', true,
          'human','Demanda reduzida por saturação dos Mac minis.'),
        'incident-llm-' || to_char(now(),'YYYYMMDDHH24MI'));
```

---

## 4. Fallback para API externa

### 4.1 Quando abrir

| Situação | Abrir fallback? |
|---|---|
| Fila `realtime` atrasada, seguidores esperando | **Sim, imediato** |
| Interação classificada como `sensitive` precisa de resposta | **Sim** — qualidade importa mais que custo |
| Furo de fofoca com `expires_at` próximo | **Sim** — a alternativa é perder o conteúdo |
| Recon de campanha bloqueando execução | Sim, se a janela da campanha estiver acabando |
| Ideação/`plan_day` do dia seguinte | **Não** — adiar é gratuito |
| `reflect` | **Não, nunca** — pode esperar dias |
| Produção de caption de rotina | Não — adie ou reduza a cadência |
| Um Mac mini fora e o outro saudável | Só para `realtime` |
| Os dois Macs fora | Sim, para tudo que for perecível, com teto de custo |

**Princípio:** pagar por API externa se justifica quando o custo de **não fazer agora** é maior que o custo do token. Interação humana esperando e furo perecendo qualificam. Reflexão noturna, não.

### 4.2 Como abrir

```bash
# Confirme que há chave configurada
grep -E '^(ANTHROPIC_API_KEY|OPENAI_API_KEY|LLM_DAILY_BUDGET_USD)=' .env \
  | sed 's/=.*/=<definida>/'

# Eleve o teto diário de forma deliberada e temporária
sed -i.bak 's/^LLM_DAILY_BUDGET_USD=.*/LLM_DAILY_BUDGET_USD=25.0/' .env
docker compose restart api worker litellm
```

Registrar sempre — é gasto de dinheiro real:

```sql
INSERT INTO events (type, detail, trace_id)
VALUES ('llm.fallback.opened',
        jsonb_build_object('actor','<seu.nome>',
          'from_budget', 5.0, 'to_budget', 25.0,
          'scope','realtime + furos perecíveis',
          'reason','saturação dos Mac minis',
          'revisar_em', (now() + interval '4 hours'),
          'runbook','llm-saturada.md'),
        'incident-llm-...');
```

> **Ponha um lembrete para fechar.** Fallback aberto e esquecido é a forma mais comum de queimar orçamento. Reverta `LLM_DAILY_BUDGET_USD` assim que a latência local normalizar.

### 4.3 Fallback seletivo

Preferível ao fallback global: abrir só para o que precisa. Em `ops/litellm.config.yaml`, os aliases lógicos permitem rotear por finalidade — `olimpo-fast` continua local, `olimpo-smart` cai para a API externa. Assim `realtime` ganha qualidade sem que `production` custe dinheiro.

---

## 5. Verificar a recuperação

```sql
-- Latência voltando ao normal? Rode a cada 10 min.
SELECT backend, count(*),
       round(percentile_cont(0.95) WITHIN GROUP (ORDER BY latency_ms)) AS p95
FROM llm_calls
WHERE created_at > now() - interval '10 minutes'
GROUP BY backend;

-- Gasto do dia contra o teto
SELECT round(sum(cost_usd)::numeric, 4) AS gasto_hoje,
       count(*) FILTER (WHERE backend IN ('anthropic','openai')) AS chamadas_externas
FROM llm_calls
WHERE created_at >= date_trunc('day', now());
```

```bash
for q in realtime dispatch production maintenance; do
  printf '%-12s %s\n' "$q" "$(docker compose exec -T redis redis-cli LLEN "$q")"
done
```

Reverter os degraus, na **ordem inversa** da aplicação:

```bash
# Reativar a produção
docker compose exec worker celery -A olimpo.workers.app control \
  add_consumer production

# Devolver a concorrência
docker compose exec worker celery -A olimpo.workers.app control pool_grow 4
```

```sql
-- Restaurar cadência e contas pausadas (uma a uma, conferindo)
UPDATE monitors SET interval_seconds = interval_seconds / 2 WHERE status = 'active';
UPDATE campaigns SET status = 'running' WHERE status = 'planning' AND window_end > now();
```

```bash
# Fechar o fallback
sed -i 's/^LLM_DAILY_BUDGET_USD=.*/LLM_DAILY_BUDGET_USD=5.0/' .env
docker compose restart api worker litellm
```

---

## 6. Controle de custo

### 6.1 Onde o dinheiro está indo

```sql
-- Custo por dia nos últimos 14 dias
SELECT date_trunc('day', created_at)::date AS dia,
       round(sum(cost_usd)::numeric, 2)    AS custo,
       count(*) FILTER (WHERE backend IN ('anthropic','openai')) AS externas,
       count(*) FILTER (WHERE backend LIKE 'mac_mini%')          AS locais
FROM llm_calls
WHERE created_at > now() - interval '14 days'
GROUP BY dia ORDER BY dia DESC;

-- Custo por finalidade — onde cortar dói menos
SELECT purpose, count(*), round(sum(cost_usd)::numeric, 4) AS custo,
       round((sum(cost_usd) / nullif(count(*),0))::numeric, 6) AS custo_por_chamada
FROM llm_calls
WHERE created_at > now() - interval '7 days' AND cost_usd > 0
GROUP BY purpose ORDER BY custo DESC;

-- Custo por conta — alguma conta é desproporcionalmente cara?
SELECT a.handle, a.archetype, count(*) AS chamadas,
       round(sum(l.cost_usd)::numeric, 4) AS custo
FROM llm_calls l JOIN accounts a ON a.id = l.account_id
WHERE l.created_at > now() - interval '7 days'
GROUP BY a.handle, a.archetype
ORDER BY custo DESC LIMIT 20;
```

### 6.2 Quando o teto é atingido

O gateway corta e passa a devolver erro claro. Comportamento esperado:

```sql
SELECT created_at, detail
FROM events WHERE type = 'llm.budget.exceeded'
ORDER BY created_at DESC LIMIT 10;
```

Três opções, nesta ordem de preferência:

1. **Aceitar** — operar só com LLM local pelo resto do dia. Degradar produção (§3.1).
2. **Elevar o teto** deliberadamente, com registro em `events` e revisão marcada.
3. **Investigar** — teto estourado com volume normal indica prompt inflado (§2.3) ou fallback aberto e esquecido.

### 6.3 Redução estrutural de custo

| Alavanca | Efeito |
|---|---|
| **Cortar contexto** | Recuperar 5 memórias em vez de 20. Corte linear no `prompt_tokens`, quase sem perda de qualidade |
| **Cache de prompt** | Persona Card é idêntico em toda chamada da mesma conta — grande economia em API externa |
| **Modelo menor por finalidade** | `plan_day` não precisa do mesmo modelo que a resposta a um comentário sensível |
| **Lote de reflexão** | Refletir uma vez por dia, não a cada ação |
| **Truncar o histórico de interações** | O prompt de resposta não precisa das últimas 50 mensagens |
| **Fallback seletivo, nunca global** | §4.3 |

---

## 7. Prevenção

| Medida | Detalhe |
|---|---|
| **Benchmark antes de crescer** | Tokens/s medidos por modelo (item 3 dos próximos passos do PLANO). Capacidade conhecida = frota dimensionável |
| **Teto de contas por Mac mini** | Derivado do benchmark. Adicionar a 30ª conta sem medir é como se chega aqui |
| **`realtime` sempre isolada** | Worker dedicado, permanente — não só durante incidente |
| **Timeout agressivo** | Chamada de LLM sem timeout ocupa slot do worker indefinidamente |
| **Concorrência calibrada** | `WORKER_CONCURRENCY=8` é default, não medida. Ajustar pela latência real |
| **`keep_alive` no Ollama** | Modelo descarregado da VRAM recarrega a cada chamada — latência artificial enorme |
| **Alerta de divergência entre backends** | `MacMiniMudo` pega o Mac que caiu antes de a latência do outro explodir |
| **Monitorar temperatura dos Macs** | Throttling térmico não gera erro, só lentidão |
| **Orçamento com folga** | `LLM_DAILY_BUDGET_USD` tão apertado que corta em qualquer incidente é falso controle |
| **Prompt sob orçamento de tokens** | Limite explícito por finalidade, medido em `llm_calls.prompt_tokens` |

---

## 8. Escalação

| Quando | Para quem | Prazo |
|---|---|---|
| Saturação resolvida com degrau 1 ou 2 | Registrar; sem escalação | — |
| Fila `realtime` atrasada > 15 min | Admin — autorizar fallback externo | Imediato |
| Um Mac mini fora | Responsável de infra dos Macs | 1 h |
| Os dois Macs fora | Admin + infra. Considerar `GLOBAL_KILL_SWITCH` se o fallback não cobrir | Imediato |
| Custo diário > 3× o normal | Admin — decidir entre cortar ou pagar | 1 h |
| `LLM_DAILY_BUDGET_USD` estourado 2 dias seguidos | Admin — o orçamento está errado ou o sistema está inflado | 24 h |
| Saturação recorrente (3ª vez na semana) | Responsável técnico — é dimensionamento, não plantão | 24 h |
| Degrau 4 necessário (pausar contas) | Admin — impacto direto na operação | Imediato |

---

**Ver também:** [fila-travada.md](fila-travada.md) · [09-llm-gateway.md](../09-llm-gateway.md) · [11-observabilidade.md](../11-observabilidade.md) · `ops/litellm.config.yaml`
