# Runbook — Conta suspensa ou banida

**Severidade:** P1 · **Alerta:** `ContaPerdida`, `MultiplasContasSuspensas`, `QuedaDeSeguidores`

> Uma conta perdida é um ativo perdido. Mas o risco maior não é a conta que caiu — são **as outras**. Se o Instagram detectou padrão, ele detectou o padrão da *frota*, não da conta. Os primeiros 15 minutos deste runbook servem para impedir que uma perda vire cinco.

---

## 1. Sintoma

Um ou mais destes:

- Login falha no container redroid; o android-orchestrator devolve `login_required` ou `account_disabled`.
- A conta aparece como "suspensa" / "sua conta foi desativada" ao abrir o app.
- Ações passam a falhar sistematicamente com `action_blocked`.
- Alcance despenca para quase zero enquanto os posts continuam publicando normalmente (**shadowban** — ver §7).
- Queda abrupta de `accounts.followers` (> 5% em 24 h).
- Tasks daquela conta acumulando `failed` com `last_error` repetido.

---

## 2. Diagnóstico

### 2.1 Confirmar o estado da conta

```bash
docker compose exec postgres psql -U olimpo -d olimpo
```

```sql
-- Identificar a conta e seu estado atual
SELECT id, handle, platform, archetype, status, autonomy, followers,
       warmup_started_at, last_action_at,
       device_binding->>'container_id' AS container,
       device_binding->>'device_id'    AS device
FROM accounts
WHERE handle = 'luna.corre';
```

Guarde o `id` — todo o resto do runbook usa. Nos comandos abaixo, `:acc` é esse uuid.

### 2.2 É banimento ou é falha de infraestrutura?

Nem toda falha de login é ban. Antes de declarar perda, elimine as causas triviais:

```bash
# O device/container está vivo? (android-manager, somente leitura)
curl -fsS -H "Authorization: Bearer ${ANDROID_MANAGER_TOKEN}" \
  "${ANDROID_MANAGER_URL}/devices/<device_id>" | jq .

# O orchestrator está saudável de modo geral?
curl -fsS -H "Authorization: Bearer ${ORCHESTRATOR_TOKEN}" \
  "${ORCHESTRATOR_URL}/health" | jq .
```

```sql
-- Outras contas NO MESMO device estão falhando também?
-- Se sim, é infra. Se só esta falha, é ban.
SELECT a.handle, a.status,
       count(*) FILTER (WHERE t.status = 'failed')   AS falhas_48h,
       count(*) FILTER (WHERE t.status = 'verified') AS ok_48h
FROM accounts a
LEFT JOIN tasks t ON t.account_id = a.id AND t.created_at > now() - interval '48 hours'
WHERE a.device_binding->>'device_id' = (
        SELECT device_binding->>'device_id' FROM accounts WHERE id = :'acc')
GROUP BY a.handle, a.status;
```

```sql
-- Assinatura do erro: o que o orchestrator vem dizendo
SELECT t.last_error, count(*), max(t.updated_at) AS mais_recente
FROM tasks t
WHERE t.account_id = :'acc'
  AND t.status = 'failed'
  AND t.updated_at > now() - interval '48 hours'
GROUP BY t.last_error
ORDER BY 2 DESC;
```

| `last_error` contém | Leitura |
|---|---|
| `account_disabled`, `account_suspended` | **Ban confirmado** |
| `login_required`, `checkpoint_required` | Challenge — recuperável, ver §4.4 |
| `action_blocked`, `rate_limited` | Limitação temporária — **precursor de ban**, tratar como alerta |
| `device_offline`, `container_not_found` | Infra, não ban → [orchestrator-fora-do-ar.md](orchestrator-fora-do-ar.md) |
| `timeout` | Inconclusivo — repetir o diagnóstico |

### 2.3 Isto é isolado ou é a frota?

**A pergunta mais importante do runbook.**

```sql
-- Contas perdidas ou suspensas nas últimas 72 h
SELECT a.handle, a.status, a.archetype, a.autonomy,
       a.device_binding->>'device_id' AS device,
       e.created_at AS quando, e.detail->>'reason' AS motivo
FROM events e
JOIN accounts a ON a.id = e.account_id
WHERE e.type = 'account.status.changed'
  AND e.detail->>'to' IN ('suspended','lost')
  AND e.created_at > now() - interval '72 hours'
ORDER BY e.created_at DESC;
```

**Mais de uma conta em 72 h → pare a frota agora** ([kill-switch.md](kill-switch.md)) e só depois continue. Duas perdas na mesma janela quase nunca são coincidência: é detecção de padrão coordenado.

### 2.4 Quais contas compartilham o destino desta

Três formas de contágio, em ordem de gravidade:

```sql
-- (a) MESMO DEVICE — a mais grave. Ban de device derruba todas juntas.
SELECT id, handle, status, autonomy, followers
FROM accounts
WHERE device_binding->>'device_id' = (
        SELECT device_binding->>'device_id' FROM accounts WHERE id = :'acc')
  AND id <> :'acc';

-- (b) MESMAS CAMPANHAS nos últimos 7 dias — padrão coordenado compartilhado
SELECT DISTINCT a.id, a.handle, a.status, c.name AS campanha, cp.slot_at
FROM campaign_participants cp
JOIN accounts  a ON a.id = cp.account_id
JOIN campaigns c ON c.id = cp.campaign_id
WHERE cp.campaign_id IN (
        SELECT campaign_id FROM campaign_participants
        WHERE account_id = :'acc' AND slot_at > now() - interval '7 days')
  AND a.id <> :'acc'
ORDER BY cp.slot_at;

-- (c) MESMO PADRÃO TEMPORAL — contas agindo nas mesmas horas.
-- Se a distribuição horária desta conta é quase idêntica à de outra,
-- as duas parecem a mesma operação para a plataforma.
SELECT a.handle,
       date_trunc('hour', t.dispatched_at) AS hora,
       count(*) AS acoes
FROM tasks t
JOIN accounts a ON a.id = t.account_id
WHERE t.dispatched_at > now() - interval '7 days'
  AND a.status = 'active'
GROUP BY a.handle, hora
ORDER BY hora, a.handle;
```

### 2.5 Alvo do post-mortem: o que ela fez nas últimas 48 h

Este é o material que explica **por que** caiu.

```sql
-- Linha do tempo completa de ações
SELECT t.dispatched_at, t.type, t.status, t.origin, t.priority,
       t.attempts, t.external_task_id, left(t.last_error, 60) AS erro,
       c.name AS campanha
FROM tasks t
LEFT JOIN campaigns c ON c.id = t.campaign_id
WHERE t.account_id = :'acc'
  AND t.created_at > now() - interval '48 hours'
ORDER BY coalesce(t.dispatched_at, t.scheduled_for);
```

```sql
-- Densidade de ações por hora — o teto é ACCOUNT_MAX_ACTIONS_PER_HOUR (12).
-- Estourou? A resposta pode estar aqui.
SELECT date_trunc('hour', dispatched_at) AS hora,
       count(*) AS acoes,
       string_agg(DISTINCT type::text, ', ') AS tipos
FROM tasks
WHERE account_id = :'acc'
  AND dispatched_at > now() - interval '48 hours'
GROUP BY hora
ORDER BY hora;
```

```sql
-- Todos os eventos da conta, com o "porquê" legível
SELECT e.created_at, e.type,
       coalesce(e.detail->>'human', e.detail->>'reason',
                left(e.detail::text, 100)) AS o_que,
       e.trace_id
FROM events e
WHERE e.account_id = :'acc'
  AND e.created_at > now() - interval '48 hours'
ORDER BY e.created_at;
```

```sql
-- O que ela publicou, e o que o QA disse sobre isso
SELECT p.published_at, p.permalink, ci.kind,
       left(ci.caption, 100) AS caption,
       ci.claim_status, ci.rights_status,
       ci.qa_report->>'verdict' AS qa
FROM posts p
LEFT JOIN content_items ci ON ci.id = p.content_item_id
WHERE p.account_id = :'acc'
  AND p.published_at > now() - interval '48 hours'
ORDER BY p.published_at;

-- Guardrails que dispararam (mesmo os apenas sinalizados)
SELECT created_at, rule, severity, action_taken, detail
FROM guardrail_violations
WHERE account_id = :'acc'
  AND created_at > now() - interval '48 hours'
ORDER BY created_at;
```

### 2.6 Hipóteses, em ordem de frequência

| Hipótese | Como confirmar |
|---|---|
| Excesso de ações/hora | §2.5, densidade > `ACCOUNT_MAX_ACTIONS_PER_HOUR` |
| Aquecimento insuficiente | `warmup_started_at` recente + volume alto desde o início |
| Padrão coordenado de campanha | §2.4(b) + `slot_at` agrupados (jitter falhou) |
| Ação fora de `active_hours` | Tasks com `dispatched_at` fora do `int4range` da conta |
| Conteúdo denunciado | `guardrail_violations` + comentários hostis em `interactions` |
| Excesso de follow/unfollow | `count(*) FILTER (WHERE type IN ('follow','unfollow'))` alto |
| Interação interna demais | Ações da frota mirando contas da própria frota |
| Device/IP queimado | Outras contas do mesmo device também caindo (§2.2) |

---

## 3. Correção — contenção imediata

> **Faça os passos 3.1 a 3.4 antes de qualquer análise adicional.** O relógio importa.

### 3.1 Parar a conta afetada

```sql
-- Congela a conta. O dispatcher ignora contas que não estão 'active'.
UPDATE accounts SET status = 'suspended' WHERE id = :'acc';

-- Cancela tudo que ainda não saiu
UPDATE tasks
SET status = 'cancelled',
    last_error = 'cancelado: conta suspensa (runbook conta-banida)'
WHERE account_id = :'acc'
  AND status IN ('pending','scheduled');

-- Registra o que foi feito, para a auditoria e o post-mortem
INSERT INTO events (account_id, type, detail, trace_id)
VALUES (:'acc', 'account.status.changed',
        jsonb_build_object(
          'from','active','to','suspended',
          'reason','suspeita_de_ban',
          'actor','<seu.nome>',
          'runbook','conta-banida.md',
          'human','Conta suspensa preventivamente após falhas de login.'),
        'incident-' || to_char(now(),'YYYYMMDDHH24MI'));
```

Tasks já em `dispatched`/`running` **não** são canceladas aqui — elas estão na mão do orchestrator. Cancele-as lá:

```bash
curl -fsS -X POST -H "Authorization: Bearer ${ORCHESTRATOR_TOKEN}" \
  "${ORCHESTRATOR_URL}/tasks/<external_task_id>/cancel"
```

### 3.2 Decidir se a frota inteira para

| Situação | Ação |
|---|---|
| 1 conta, causa isolada e identificada (ex.: conteúdo denunciado) | Segue só esta conta suspensa |
| 1 conta, causa **não** identificada | Pausar as contas do mesmo device (§3.3) |
| ≥ 2 contas em 72 h | **`GLOBAL_KILL_SWITCH=true`** — [kill-switch.md](kill-switch.md) |
| ≥ 2 contas da mesma campanha | Kill switch global **e** abortar a campanha (§3.4) |

### 3.3 Pausar as contas de risco

```sql
-- Contas do mesmo device → 'paused' (reversível, diferente de 'suspended')
UPDATE accounts SET status = 'paused'
WHERE device_binding->>'device_id' = (
        SELECT device_binding->>'device_id' FROM accounts WHERE id = :'acc')
  AND id <> :'acc'
  AND status = 'active';

-- Rebaixar autonomia das contas que participaram das mesmas campanhas:
-- voltam a exigir humano no loop até entendermos o que aconteceu.
UPDATE accounts SET autonomy = 'L0'
WHERE id IN (
    SELECT DISTINCT cp.account_id
    FROM campaign_participants cp
    WHERE cp.campaign_id IN (
        SELECT campaign_id FROM campaign_participants
        WHERE account_id = :'acc' AND slot_at > now() - interval '7 days')
      AND cp.account_id <> :'acc')
  AND autonomy <> 'L0';
```

### 3.4 Abortar campanhas em voo

```sql
UPDATE campaigns SET status = 'aborted'
WHERE status IN ('running','approved','planning')
  AND id IN (SELECT campaign_id FROM campaign_participants
             WHERE account_id = :'acc');

UPDATE tasks SET status = 'cancelled',
                 last_error = 'campanha abortada: incidente de conta'
WHERE campaign_id IN (SELECT id FROM campaigns WHERE status = 'aborted')
  AND status IN ('pending','scheduled');
```

### 3.5 Preservar o que é irrecuperável

> Faça isto **antes** de qualquer limpeza. `memories` e `story_arcs` não existem em lugar nenhum além do nosso banco. A conta pode voltar; a vivência dela, não.

```bash
mkdir -p "/backup/contas-perdidas/luna.corre-$(date +%F)"
cd "/backup/contas-perdidas/luna.corre-$(date +%F)"

ACC='a3f9b21c-0000-4000-8000-000000000001'
PSQL="docker compose exec -T postgres psql -U olimpo -d olimpo"

# A alma da conta — persona, memórias, arcos, planos
$PSQL -c "\copy (SELECT * FROM accounts        WHERE id = '$ACC')         TO 'account.csv'        CSV HEADER"
$PSQL -c "\copy (SELECT * FROM persona_versions WHERE account_id = '$ACC') TO 'persona_versions.csv' CSV HEADER"
$PSQL -c "\copy (SELECT * FROM story_arcs      WHERE account_id = '$ACC') TO 'story_arcs.csv'     CSV HEADER"
$PSQL -c "\copy (SELECT id, account_id, kind, content, importance, metadata, arc_id,
                        occurred_at, last_used_at, created_at
                 FROM memories WHERE account_id = '$ACC')                 TO 'memories.csv'       CSV HEADER"
$PSQL -c "\copy (SELECT * FROM day_plans       WHERE account_id = '$ACC') TO 'day_plans.csv'      CSV HEADER"

# O corpo — conteúdo, posts, métricas
$PSQL -c "\copy (SELECT * FROM content_items   WHERE account_id = '$ACC') TO 'content_items.csv'  CSV HEADER"
$PSQL -c "\copy (SELECT * FROM posts           WHERE account_id = '$ACC') TO 'posts.csv'          CSV HEADER"

# A evidência — para o post-mortem
$PSQL -c "\copy (SELECT * FROM tasks  WHERE account_id = '$ACC' AND created_at > now() - interval '30 days') TO 'tasks_30d.csv' CSV HEADER"
$PSQL -c "\copy (SELECT * FROM events WHERE account_id = '$ACC' AND created_at > now() - interval '30 days') TO 'events_30d.csv' CSV HEADER"
$PSQL -c "\copy (SELECT * FROM guardrail_violations WHERE account_id = '$ACC') TO 'violations.csv' CSV HEADER"

ls -la
```

> O `embedding` de `memories` é omitido de propósito: são recalculáveis a partir de `content` e inflam o arquivo. O `content` é o que importa.

Não exporte `interactions` com corpo íntegro para fora do banco — são dados pessoais de terceiros ([12-seguranca.md §4](../12-seguranca.md#4-dados-de-terceiros)). Se necessário para o post-mortem, exporte apenas `classification`, `sensitive` e datas.

### 3.6 Marcar a perda

Só quando o ban estiver **confirmado** (não recuperável — ver §4.4):

```sql
UPDATE accounts
SET status = 'lost',
    notes  = coalesce(notes || E'\n', '') ||
             'Perdida em ' || to_char(now(),'DD/MM/YYYY') ||
             '. Backup: /backup/contas-perdidas/luna.corre-' || to_char(now(),'YYYY-MM-DD') ||
             '. Causa provável: <preencher>.'
WHERE id = :'acc';

INSERT INTO events (account_id, type, detail, trace_id)
VALUES (:'acc', 'account.status.changed',
        jsonb_build_object('from','suspended','to','lost',
          'actor','<seu.nome>',
          'backup_path','/backup/contas-perdidas/...',
          'causa_provavel','<preencher>',
          'human','Ban confirmado; conta marcada como perdida.'),
        'incident-...');
```

**Nunca `DELETE FROM accounts`.** O `ON DELETE CASCADE` levaria junto `memories`, `story_arcs`, `persona_versions` e `day_plans` — exatamente o que não se recupera. `status='lost'` preserva tudo.

---

## 4. Post-mortem e recuperação

### 4.1 Estabelecer a causa

Com o material de §2.5, responda por escrito:

1. Qual foi a **última ação bem-sucedida** e a **primeira que falhou**?
2. Nas 48 h anteriores, a conta ultrapassou `ACCOUNT_MAX_ACTIONS_PER_HOUR` em alguma hora?
3. Alguma ação saiu fora de `active_hours`?
4. Ela participou de campanha? Os `slot_at` respeitaram `CAMPAIGN_JITTER_MIN_MINUTES`?
5. Algum `guardrail_violations` com `severity IN ('warn','block')` foi ignorado?
6. Havia mudança recente de `persona_card`, de device ou de `autonomy`?
7. A conta agiu sobre outras contas da casa? Quantas vezes?

### 4.2 O documento

Um arquivo em `/backup/contas-perdidas/<handle>-<data>/POSTMORTEM.md`:

```markdown
# Post-mortem — @<handle>

- **Perdida em:** <data/hora>
- **Idade da conta:** <dias desde warmup_started_at>
- **Autonomia no momento:** <L0..L3>
- **Seguidores perdidos:** <n>
- **Arquétipo:** <influencer|gossip|clipper>

## Linha do tempo
<da query §2.5, resumida>

## Causa provável
<uma frase>

## Evidência
<queries e números que sustentam a causa>

## O que teria evitado
<mudança concreta em .env, spread_policy, guardrail ou código>

## Contas em risco pelo mesmo motivo
<lista da §2.4 e o que foi feito com cada uma>

## Ações tomadas
- [ ] ...
```

### 4.3 Recriação da persona

A conta se perde; a persona não precisa. O que o backup de §3.5 permite recriar:

| Artefato | Como reaproveitar |
|---|---|
| `persona_card` | Importar íntegro na conta nova. **Mudar o handle e ajustar a estética** — repetir handle e visual em conta nova é o caminho mais rápido para o segundo ban |
| `memories` | Reimportar as de `kind='semantic'` e as `episodic` de `importance >= 7`. Recalcular embeddings. As memórias triviais morrem com a conta |
| `story_arcs` | Reimportar os `active=true`, com `state` ajustado — a narrativa continua, o palco muda |
| `persona_engine_id` | **Reutilizar.** A face/estética já está treinada na máquina de influencers |
| `cadence`, `active_hours`, `mood` | Reimportar, mas **reiniciar o aquecimento do zero** |
| `device_binding` | **Nunca reutilizar** o device da conta banida |
| `content_items` publicados | Fonte de repostagem gradual para reconstruir o feed |

Regras para a conta nova:

- `status='draft'` → `warming`, com `warmup_started_at = now()`. Sem atalho.
- `autonomy='L0'`, no mínimo por 2 semanas, independentemente do nível anterior.
- Device diferente, idealmente IP diferente.
- Fora de qualquer campanha por 30 dias.
- `notes` registrando que é sucessora de qual conta e por quê.

### 4.4 Se for challenge, não ban

`checkpoint_required` costuma ser recuperável, mas é o momento mais delicado:

1. Manter `status='suspended'` — **nenhuma automação** durante a recuperação.
2. Recuperação é **manual**, pelo android-manager, com curadoria humana no container.
3. Após recuperar: `status='warming'`, `autonomy='L0'`, aquecimento reduzido (metade da cadência) por 7 dias.
4. Só voltar a `active` após 7 dias sem novo challenge.
5. Nunca colocar em campanha nos 30 dias seguintes.

> Automatizar a resposta a um challenge é a forma mais confiável de transformar um challenge em ban permanente.

---

## 5. Prevenção

| Medida | Onde | Verificação |
|---|---|---|
| Teto de ações/hora respeitado | `ACCOUNT_MAX_ACTIONS_PER_HOUR=12` | Dashboard `Olimpo / Frota de Contas`, painel de ações/hora |
| Aquecimento gradual real | `status='warming'` por ≥ 14 dias, cadência crescente | `SELECT handle, now()-warmup_started_at FROM accounts WHERE status='warming'` |
| Jitter de campanha efetivo | `CAMPAIGN_JITTER_MIN_MINUTES=25` | Timeline de `slot_at` no dashboard `Olimpo / Campanhas` |
| Participação parcial | `CAMPAIGN_MAX_PARTICIPATION=0.6` | Nunca a frota inteira na mesma campanha |
| Respeito a `active_hours` | Dispatcher | Tasks despachadas fora do range devem ser zero |
| Devices distintos | `device_binding` | Nenhum device com mais contas que o combinado |
| Grafo interno limitado | Guardrail | Ações da frota mirando a frota, por semana |
| Autonomia conquistada | L0→L1→L2 progressivo | Nenhuma conta salta níveis |
| Guardrail `warn` não é ignorado | Revisão semanal | `guardrail_violations` com `severity='warn'` sem `action_taken` |
| Heatmap hora × conta revisado | Semanal | Listras verticais alinhadas = padrão detectável |

**Ritual semanal** (15 min, uma pessoa):

```sql
-- Contas que chegaram perto do teto na semana
SELECT a.handle, date_trunc('hour', t.dispatched_at) AS hora, count(*) AS acoes
FROM tasks t JOIN accounts a ON a.id = t.account_id
WHERE t.dispatched_at > now() - interval '7 days'
GROUP BY a.handle, hora
HAVING count(*) >= 10          -- 83% de ACCOUNT_MAX_ACTIONS_PER_HOUR
ORDER BY acoes DESC;

-- Ações fora da janela "acordada" da conta — nunca deveria haver nenhuma
SELECT a.handle, t.dispatched_at, t.type, a.active_hours
FROM tasks t JOIN accounts a ON a.id = t.account_id
WHERE t.dispatched_at > now() - interval '7 days'
  AND NOT (extract(hour FROM t.dispatched_at AT TIME ZONE a.timezone)::int
           <@ a.active_hours);

-- Campanhas cujo espalhamento foi apertado demais
SELECT c.name, count(*) AS participantes,
       extract(epoch FROM (max(cp.slot_at) - min(cp.slot_at)))/60 AS janela_min,
       round((extract(epoch FROM (max(cp.slot_at) - min(cp.slot_at)))/60
              / greatest(count(*) - 1, 1))::numeric, 1) AS min_entre_contas
FROM campaigns c
JOIN campaign_participants cp ON cp.campaign_id = c.id AND NOT cp.skipped
WHERE c.window_start > now() - interval '7 days'
GROUP BY c.name
HAVING (extract(epoch FROM (max(cp.slot_at) - min(cp.slot_at)))/60
        / greatest(count(*) - 1, 1)) < 25;   -- < CAMPAIGN_JITTER_MIN_MINUTES
```

---

## 6. Escalação

| Quando | Para quem | Prazo |
|---|---|---|
| 1 conta perdida, causa identificada, isolada | Registrar post-mortem; comunicar no canal de operação | 24 h |
| 1 conta perdida, causa **não** identificada | Admin do Olimpo — decidir se pausa a frota | 1 h |
| ≥ 2 contas em 72 h | **Kill switch global imediato** + admin | Imediato |
| ≥ 2 contas da mesma campanha | Kill switch + revisão da `spread_policy` antes de qualquer retomada | Imediato |
| Perda de conta com cliente vinculado (`client_id`) | Responsável comercial | 4 h |
| Suspeita de comprometimento de credencial | [12-seguranca.md §5.4](../12-seguranca.md#54-resposta-a-incidente) | Imediato |
| Perda de conta com > 10k seguidores | Admin + decisão explícita sobre recriação | 24 h |

---

## 7. Anexo — Shadowban

Não é ban. É pior de diagnosticar: tudo publica normalmente e nada alcança ninguém.

**Sinais:**

```sql
-- Alcance despencou mas as publicações continuam sendo verificadas
SELECT date_trunc('day', p.published_at) AS dia,
       count(*) AS posts,
       round(avg(pm.reach))  AS alcance_medio,
       round(avg(pm.likes))  AS likes_medio
FROM posts p
JOIN LATERAL (
  SELECT * FROM post_metrics m
  WHERE m.post_id = p.id ORDER BY m.collected_at DESC LIMIT 1
) pm ON true
WHERE p.account_id = :'acc'
  AND p.published_at > now() - interval '30 days'
GROUP BY dia ORDER BY dia;
```

Queda de alcance > 70% sustentada por 3+ dias, com `tasks.status='verified'` normal, é shadowban.

**Conduta:**

1. `status='paused'` por 7–14 dias. Nada de posts, nada de campanhas.
2. Revisar hashtags dos últimos posts — hashtag banida contamina o alcance da conta.
3. Revisar `guardrail_violations` recentes: houve conteúdo no limite?
4. Retomar com metade da cadência e sem campanha por 30 dias.
5. Não apagar os posts antigos em pânico — remoção em massa é sinal adicional.

Não há confirmação da plataforma. A recuperação se mede pelo alcance voltando, e leva semanas.

---

**Ver também:** [kill-switch.md](kill-switch.md) · [conteudo-improprio-publicado.md](conteudo-improprio-publicado.md) · [11-observabilidade.md](../11-observabilidade.md) · [08-guardrails-e-autonomia.md](../08-guardrails-e-autonomia.md)
