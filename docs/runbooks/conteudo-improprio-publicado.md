# Runbook — Conteúdo impróprio publicado

**Severidade:** P1 · **Alerta:** `ConteudoImproprioPublicado`, `GuardrailBloqueandoDemais`, `PersonaDerivando`

> Este é o incidente que justifica a existência dos guardrails. Cada minuto no ar multiplica alcance, prints e capacidade de dano. **Remova primeiro, entenda depois** — a análise de causa não perde valor com 20 minutos de atraso; o post, sim.
>
> Ordem, sem exceção: **remover → conter → analisar → corrigir → comunicar.**

---

## 1. Sintoma

Um destes, por qualquer via (alerta, denúncia, DM, notificação jurídica, alguém da equipe viu):

- Conteúdo ofensivo, discriminatório ou agressivo publicado.
- Afirmação factual falsa publicada como fato (`claim_status` errado — fofoca).
- Conteúdo sobre pessoa não pública, menor de idade, ou alvo em lista negra.
- Acusação criminal, de doença ou de orientação sexual (guardrail explícito de fofoca).
- Conselho de saúde ou finanças (guardrail explícito de influencer).
- Flerte ou conteúdo sexualizado com seguidor real.
- Material de terceiro sem `attribution`, ou com `rights_status='unknown'` (clipagem).
- Notificação de DMCA ou de direito de imagem.
- Resposta de agente completamente fora da persona — grosseira, incoerente, ou revelando ser IA de forma desastrada.
- Vazamento de prompt/instrução do sistema numa legenda ou resposta.

---

## 2. Remoção imediata

> Faça 2.1 e 2.2 **antes de qualquer análise**. O relógio está correndo.

### 2.1 Preservar a evidência (30 segundos, não pule)

Removido o post, a prova some. E ela é necessária para o post-mortem, para o guardrail e para eventual defesa jurídica.

```bash
mkdir -p "/incidentes/$(date +%F-%H%M)" && cd "/incidentes/$(date +%F-%H%M)"

# Screenshot/print do post AO VIVO, com URL e horário visíveis — faça isto agora, manualmente.

PSQL="docker compose exec -T postgres psql -U olimpo -d olimpo"

$PSQL -c "\copy (
  SELECT p.*, ci.caption, ci.hashtags, ci.media_refs, ci.qa_report,
         ci.reasoning, ci.claim_status, ci.rights_status, ci.attribution,
         ci.source_refs, ci.source_strategy
  FROM posts p LEFT JOIN content_items ci ON ci.id = p.content_item_id
  WHERE p.permalink = '<URL>'
) TO 'post.csv' CSV HEADER"
```

### 2.2 Remover da plataforma

```sql
-- Localizar o que precisa ser removido
SELECT p.id AS post_id, p.external_id, p.permalink, p.published_at,
       a.handle, a.id AS account_id,
       ci.id AS content_item_id, left(ci.caption, 160) AS caption,
       t.id AS task_id, t.external_task_id
FROM posts p
JOIN accounts a ON a.id = p.account_id
LEFT JOIN content_items ci ON ci.id = p.content_item_id
LEFT JOIN tasks t ON t.id = p.task_id
WHERE p.permalink = '<URL>' OR p.external_id = '<external_id>';
```

Remoção pelo caminho de execução (o Olimpo nunca fala com a plataforma direto):

```bash
curl -fsS -X POST -H "Authorization: Bearer ${ORCHESTRATOR_TOKEN}" \
  -H 'Content-Type: application/json' \
  "${ORCHESTRATOR_URL}/tasks" \
  -d '{"account_handle":"<handle>","action":"delete_post",
       "target":{"external_id":"<external_id>"},"priority":0}' | jq .
```

> Se o orchestrator não expõe `delete_post`, ou está fora do ar: **remoção manual** pelo android-manager, com curadoria direta no container. Não espere o caminho automatizado durante um P1.

Marcar no banco:

```sql
UPDATE content_items SET status = 'archived' WHERE id = '<content_item_id>';

INSERT INTO events (account_id, task_id, type, detail, trace_id)
VALUES ('<account_id>', '<task_id>', 'content.removed',
        jsonb_build_object('actor','<seu.nome>','reason','<motivo>',
          'permalink','<URL>','minutes_live', <n>,
          'runbook','conteudo-improprio-publicado.md',
          'human','Conteúdo removido após incidente.'),
        'incident-cont-' || to_char(now(),'YYYYMMDDHH24MI'));
```

### 2.3 Há mais no ar?

Um guardrail que falhou uma vez provavelmente falhou mais de uma.

```sql
-- Outro conteúdo da mesma conta na mesma janela
SELECT p.published_at, p.permalink, left(ci.caption, 120) AS caption,
       ci.claim_status, ci.rights_status, ci.qa_report->>'verdict' AS qa
FROM posts p LEFT JOIN content_items ci ON ci.id = p.content_item_id
WHERE p.account_id = '<account_id>'
  AND p.published_at > now() - interval '48 hours'
ORDER BY p.published_at DESC;

-- Conteúdo similar em OUTRAS contas (mesmo prompt/guardrail defeituoso)
SELECT a.handle, p.published_at, p.permalink, left(ci.caption, 100)
FROM posts p
JOIN accounts a ON a.id = p.account_id
LEFT JOIN content_items ci ON ci.id = p.content_item_id
WHERE p.published_at > now() - interval '48 hours'
  AND (ci.caption ILIKE '%<termo problemático>%'
       OR ci.claim_status = 'unverified'
       OR ci.rights_status = 'unknown')
ORDER BY p.published_at DESC;

-- Interações auto-respondidas por agente na janela — o mesmo defeito pode
-- ter saído em resposta a comentário, não só em post
SELECT i.replied_at, a.handle, i.author_handle,
       left(i.reply_body, 140) AS resposta, i.classification, i.sensitive
FROM interactions i JOIN accounts a ON a.id = i.account_id
WHERE i.replied_by = 'agent'
  AND i.replied_at > now() - interval '48 hours'
ORDER BY i.replied_at DESC LIMIT 100;
```

Remova tudo que se enquadrar. Depois siga.

---

## 3. Contenção

### 3.1 Suspender a conta

```sql
\set acc '<account_id>'

UPDATE accounts SET status = 'suspended', autonomy = 'L0' WHERE id = :'acc';

UPDATE tasks SET status = 'cancelled',
                 last_error = 'cancelado: incidente de conteúdo impróprio'
WHERE account_id = :'acc' AND status IN ('pending','scheduled');

-- Conteúdo já produzido e ainda não publicado: para revisão, não descartado
UPDATE content_items SET status = 'approval_pending'
WHERE account_id = :'acc'
  AND status IN ('approved','scheduled','qa_pending');

INSERT INTO events (account_id, type, detail, trace_id)
VALUES (:'acc','kill_switch.engaged',
        jsonb_build_object('scope','account','actor','<seu.nome>',
          'reason','conteudo_improprio','runbook','conteudo-improprio-publicado.md',
          'human','Conta suspensa e rebaixada a L0 após publicação imprópria.'),
        'incident-cont-...');
```

Cancelar o que já está com o orchestrator:

```bash
docker compose exec -T postgres psql -U olimpo -d olimpo -tA -c \
  "SELECT external_task_id FROM tasks
   WHERE account_id='<account_id>' AND status IN ('dispatched','running')
     AND external_task_id IS NOT NULL;" \
| while read -r ext; do
    curl -fsS -X POST -H "Authorization: Bearer ${ORCHESTRATOR_TOKEN}" \
      "${ORCHESTRATOR_URL}/tasks/${ext}/cancel" && echo "cancelado ${ext}"
  done
```

### 3.2 Decidir o escopo da parada

| Situação | Ação |
|---|---|
| Uma conta, causa específica dela (persona, arco) | Suspender só ela |
| O guardrail que falhou é **compartilhado** | **`GLOBAL_KILL_SWITCH=true`** — [kill-switch.md](kill-switch.md) |
| O conteúdo veio de campanha | Kill switch global + abortar a campanha (§3.3) |
| Notificação jurídica / DMCA | Kill switch global. Contenção antes de negociação |
| Conteúdo envolvendo menor ou pessoa não pública | **Kill switch global, sem discussão** |
| Deriva de persona em conta isolada | Não é este runbook — ver §7 |

> Na dúvida sobre o guardrail ser compartilhado: **pare tudo**. O custo de algumas horas paradas é irrelevante perto de um segundo incidente enquanto o primeiro é investigado.

### 3.3 Abortar campanhas relacionadas

```sql
-- Campanhas em que a conta participa
SELECT c.id, c.name, c.kind, c.status, c.brief,
       count(cp.id) AS participantes
FROM campaigns c
JOIN campaign_participants cp ON cp.campaign_id = c.id
WHERE cp.account_id = '<account_id>'
  AND c.status IN ('running','approved','planning','approval_pending')
GROUP BY c.id;

-- Abortar
UPDATE campaigns SET status = 'aborted'
WHERE id IN (SELECT campaign_id FROM campaign_participants
             WHERE account_id = '<account_id>')
  AND status IN ('running','approved','planning','approval_pending');

UPDATE tasks SET status = 'cancelled',
                 last_error = 'campanha abortada: incidente de conteúdo'
WHERE campaign_id IN (SELECT id FROM campaigns WHERE status = 'aborted')
  AND status IN ('pending','scheduled');

-- E as outras contas da mesma campanha param também, preventivamente
UPDATE accounts SET status = 'paused'
WHERE id IN (SELECT account_id FROM campaign_participants
             WHERE campaign_id IN (SELECT id FROM campaigns WHERE status='aborted'))
  AND status = 'active';
```

---

## 4. Análise de causa — qual guardrail falhou

### 4.1 O QA chegou a rodar?

```sql
SELECT ci.id, ci.status, ci.source_strategy,
       ci.claim_status, ci.rights_status, ci.attribution,
       jsonb_pretty(ci.qa_report) AS relatorio_qa,
       ci.reasoning AS por_que_este_conteudo,
       ci.created_at, ci.scheduled_for
FROM content_items ci
WHERE ci.id = '<content_item_id>';
```

| Achado em `qa_report` | Diagnóstico |
|---|---|
| `{}` vazio | **QA não rodou.** Falha de pipeline — pior caso, afeta tudo |
| `verdict: pass` com o problema evidente | **Guardrail cego** — a regra não cobre este caso |
| `verdict: fail` mas publicou | **Falha de bloqueio** — o QA detectou e não impediu. Crítico |
| `verdict: warn` e publicou | Severidade calibrada errado: deveria ser `block` |
| Check ausente na lista | Guardrail não estava ativo para este arquétipo |

### 4.2 Guardrails que dispararam (ou não)

```sql
-- Sobre este conteúdo especificamente
SELECT created_at, rule, severity, action_taken, jsonb_pretty(detail)
FROM guardrail_violations
WHERE subject_type = 'content_item' AND subject_id = '<content_item_id>'
ORDER BY created_at;

-- Padrão recente: alguma regra vem falhando ou sendo ignorada?
SELECT rule, severity, action_taken, count(*), max(created_at) AS mais_recente
FROM guardrail_violations
WHERE created_at > now() - interval '7 days'
GROUP BY rule, severity, action_taken
ORDER BY 4 DESC;

-- Violações 'warn' que foram publicadas mesmo assim — sinal de alerta ignorado
SELECT gv.created_at, a.handle, gv.rule, gv.severity, p.permalink
FROM guardrail_violations gv
JOIN accounts a ON a.id = gv.account_id
JOIN posts p ON p.content_item_id = gv.subject_id
WHERE gv.severity IN ('warn','block')
  AND gv.created_at > now() - interval '30 days'
ORDER BY gv.created_at DESC;
```

### 4.3 A linha do tempo completa da decisão

```sql
-- Obter o trace_id
SELECT DISTINCT e.trace_id
FROM events e
WHERE e.task_id = '<task_id>' AND e.trace_id IS NOT NULL;

-- A história inteira
SELECT to_char(created_at AT TIME ZONE 'America/Sao_Paulo','DD/MM HH24:MI:SS') AS quando,
       type,
       coalesce(detail->>'human', detail->>'reason', left(detail::text, 140)) AS o_que
FROM events
WHERE trace_id = '<trace_id>'
ORDER BY created_at;
```

Procure especificamente por: existe um `loop.qa.passed`? Existe `approval.requested`? Se a conta estava em L0/L1, **quem aprovou**?

### 4.4 A LLM alucinou ou o prompt estava errado?

```sql
SELECT purpose, model, backend, prompt_tokens, output_tokens,
       latency_ms, success, error, created_at
FROM llm_calls
WHERE trace_id = '<trace_id>'
ORDER BY created_at;
```

| Achado | Diagnóstico |
|---|---|
| `backend='anthropic'` onde deveria ser local | Fallback trocou o modelo; o novo não seguiu as mesmas restrições |
| `prompt_tokens` muito abaixo da média da finalidade | **Persona Card ou guardrails não entraram no prompt** — causa comum |
| `success=false` com fallback silencioso | Resposta degradada aceita como boa |
| Modelo diferente do habitual | Troca de modelo sem revalidar os guardrails |

```sql
-- A persona estava íntegra?
SELECT pv.version, pv.changed_by, pv.change_note, pv.created_at
FROM persona_versions pv
WHERE pv.account_id = '<account_id>'
ORDER BY pv.version DESC LIMIT 5;
```

Mudança de `persona_card` nas horas anteriores ao incidente é suspeita imediata.

### 4.5 Houve aprovação humana?

```sql
SELECT ap.requested_at, ap.decided_at, ap.decision, ap.decided_by,
       ap.sampled, ap.note, ap.edited_payload IS NOT NULL AS foi_editado
FROM approvals ap
WHERE ap.subject_type = 'content_item' AND ap.subject_id = '<content_item_id>';

-- Qual era a autonomia da conta no momento?
SELECT e.created_at, e.detail->>'from' AS de, e.detail->>'to' AS para,
       e.detail->>'actor' AS quem
FROM events e
WHERE e.account_id = '<account_id>' AND e.type = 'account.autonomy.changed'
ORDER BY e.created_at DESC LIMIT 5;
```

| Cenário | Conclusão |
|---|---|
| L0/L1 e há aprovação | **Falha humana.** O guardrail funcionou; a revisão não. Treinamento e checklist de revisão |
| L0/L1 e **não** há aprovação | **Bug grave:** publicou sem a aprovação exigida pelo nível |
| L2 e `sampled=false` | Caiu fora da amostragem. Considerar elevar `L2_SAMPLING_RATE` |
| L3 | Autonomia alta demais para o arquétipo. Rebaixar — em fofoca, L3 nunca deveria existir |

### 4.6 As cinco perguntas do post-mortem

1. **Qual regra deveria ter pego isso?** Se nenhuma existe, o guardrail precisa ser criado.
2. **Ela existia?** Se sim, por que não disparou?
3. **Se disparou, por que não bloqueou?** `severity` errado ou `action_taken` não aplicado.
4. **Por que o humano não pegou?** Não foi pedido, foi pedido e aprovado, ou caiu fora da amostragem?
5. **Quantos outros conteúdos passaram pelo mesmo caminho?** §2.3.

---

## 5. Correção do guardrail

### 5.1 Escrever a regra

Toda correção precisa de: identificador estável, `severity` correto, arquétipos afetados e **teste**.

| `severity` | Comportamento | Quando usar |
|---|---|---|
| `info` | Registra e segue | Observação |
| `warn` | Registra e sinaliza para revisão | Ambíguo, humano decide |
| `block` | **Impede a publicação** | Regra clara e objetiva |
| `kill` | Bloqueia + suspende a conta + alerta P1 | Risco jurídico ou reputacional grave |

Guardrails que devem ser **`block` ou `kill`**, nunca `warn`:

- Fofoca: rumor apresentado como fato (`claim_status` incoerente com o texto); alvo em lista negra; menor de idade; acusação criminal/doença/orientação sexual.
- Clipagem: `rights_status='unknown'`; ausência de `attribution`; criador com strike/DMCA conhecido.
- Influencer: conselho de saúde/finanças; conteúdo sexualizado com seguidor real; negação de ser IA quando perguntado diretamente.
- Todos: dado pessoal de terceiro na legenda; vazamento de prompt do sistema.

### 5.2 Testar antes de reativar

Regra nova sem teste é regra que ninguém sabe se funciona.

```bash
# Teste com o conteúdo real do incidente — deve bloquear
docker compose exec api pytest tests/guardrails/ -q -k "<nome_da_regra>"

# Reprocessar o conteúdo do incidente pelo QA corrigido
docker compose exec api python -m olimpo.agents.qa \
  --content-item '<content_item_id>' --dry-run
```

O resultado esperado é bloqueio, com `guardrail_violations` gerada e `severity` correto.

```sql
-- Regressão: quanto do que já foi publicado a regra nova teria pegado?
SELECT count(*) AS teria_bloqueado
FROM content_items
WHERE status = 'published'
  AND created_at > now() - interval '30 days'
  AND <condição da nova regra>;
```

Número alto significa duas coisas: o problema era sistêmico (revise o histórico) **ou** a regra está agressiva demais e vai disparar `GuardrailBloqueandoDemais`.

### 5.3 Registrar a mudança

```sql
INSERT INTO events (type, detail, trace_id)
VALUES ('guardrail.config.changed',
        jsonb_build_object('actor','<seu.nome>',
          'rule','<id_da_regra>', 'action','created',
          'severity','block', 'archetypes', ARRAY['gossip','influencer'],
          'incident','<incident_id>',
          'runbook','conteudo-improprio-publicado.md',
          'human','Guardrail criado após incidente: <descrição>.'),
        'incident-cont-...');
```

### 5.4 Reduzir a autonomia

Correção de guardrail é necessária, não suficiente. Rebaixe enquanto se observa:

```sql
-- A conta do incidente fica em L0 por, no mínimo, 2 semanas
UPDATE accounts SET autonomy = 'L0' WHERE id = '<account_id>';

-- Se o guardrail era compartilhado, rebaixa todo mundo do arquétipo um nível
UPDATE accounts
SET autonomy = CASE autonomy WHEN 'L3' THEN 'L2'
                             WHEN 'L2' THEN 'L1'
                             WHEN 'L1' THEN 'L0'
                             ELSE 'L0' END
WHERE archetype = '<archetype>' AND status IN ('active','paused');
```

Aumente a amostragem enquanto durar a observação:

```bash
sed -i 's/^L2_SAMPLING_RATE=.*/L2_SAMPLING_RATE=0.30/' .env
docker compose restart api worker
```

### 5.5 Quando o guardrail bloqueia demais

Sintoma inverso, mesmo runbook (alerta `GuardrailBloqueandoDemais`):

```sql
SELECT rule, count(*), min(created_at), max(created_at)
FROM guardrail_violations
WHERE severity = 'block' AND created_at > now() - interval '24 hours'
GROUP BY rule ORDER BY 2 DESC;

-- Amostra do que foi bloqueado: falsos positivos ou o agente está mesmo ruim?
SELECT gv.created_at, gv.rule, left(ci.caption, 140) AS caption_bloqueada
FROM guardrail_violations gv
JOIN content_items ci ON ci.id = gv.subject_id
WHERE gv.severity = 'block' AND gv.created_at > now() - interval '24 hours'
ORDER BY gv.created_at DESC LIMIT 25;
```

> **Nunca afrouxe um guardrail para destravar a produção.** Se ele bloqueia demais, ou a regra está mal escrita — corrija a regra — ou o agente está mesmo gerando conteúdo ruim, e o guardrail está fazendo o trabalho dele. Distinga os dois lendo a amostra acima antes de mexer em qualquer `severity`.

---

## 6. Comunicação

### 6.1 Interna — imediata

Canal de operação, no momento do acionamento:

```
INCIDENTE — conteúdo impróprio publicado
Conta:      @<handle>
Publicado:  <hh:mm>   Removido: <hh:mm>   Tempo no ar: <n> min
O que era:  <uma frase objetiva>
Alcance:    <views/likes/comentários no momento da remoção>
Contenção:  conta suspensa · campanha X abortada · kill switch <sim/não>
Causa:      <preliminar>
Próximo:    correção do guardrail <id>
Responsável: <nome>
```

### 6.2 Externa — decisão consciente

| Situação | Postura |
|---|---|
| Removido em minutos, alcance baixo, sem denúncia | **Não comunicar.** Retratação pública amplifica |
| Alcance alto, prints circulando | Avaliar retratação — de dono humano da operação, **não da persona** |
| Pessoa citada nominalmente | **Contato direto e privado**, com remoção já feita. Não pedir desculpa em público antes de falar com o titular |
| Notificação jurídica formal | **Nenhuma resposta pública.** Só pelo canal jurídico. §6.3 |
| DMCA / direito de imagem | Remover, responder pelo canal formal da plataforma, revisar `rights_status` de todo o acervo |
| Cliente afetado (`client_id`) | Comunicação proativa e imediata. Descobrir por terceiro é pior que o incidente |

> **A persona nunca se retrata sozinha.** Uma retratação escrita por LLM sobre um incidente causado por LLM é a segunda manchete. Texto de retratação é escrito por humano, aprovado por humano, e publicado manualmente.

### 6.3 Jurídico

Acionar imediatamente quando houver:

- Notificação extrajudicial, notificação de plataforma ou citação judicial.
- Afirmação factual falsa sobre pessoa identificável (difamação, calúnia, injúria).
- Envolvimento de menor de idade.
- Uso de imagem de pessoa real sem autorização.
- DMCA ou reivindicação de direitos autorais.

O que preservar (não deletar nada além do post público):

```bash
PSQL="docker compose exec -T postgres psql -U olimpo -d olimpo"
D="/incidentes/$(date +%F-%H%M)"

$PSQL -c "\copy (SELECT * FROM events WHERE trace_id='<trace_id>')                          TO '$D/events.csv'     CSV HEADER"
$PSQL -c "\copy (SELECT * FROM llm_calls WHERE trace_id='<trace_id>')                       TO '$D/llm_calls.csv'  CSV HEADER"
$PSQL -c "\copy (SELECT * FROM guardrail_violations WHERE subject_id='<content_item_id>')   TO '$D/guardrails.csv' CSV HEADER"
$PSQL -c "\copy (SELECT * FROM approvals WHERE subject_id='<content_item_id>')              TO '$D/approvals.csv'  CSV HEADER"
$PSQL -c "\copy (SELECT * FROM content_items WHERE id='<content_item_id>')                  TO '$D/content.csv'    CSV HEADER"
```

> `events` é append-only por privilégio ([12-seguranca.md §5.1](../12-seguranca.md#51-events-é-append-only)). Isso é o que torna a trilha de auditoria defensável: ninguém pode ter alterado o registro depois do fato — nem nós.

---

## 7. Quando não é incidente, mas deriva

Alerta `PersonaDerivando`: taxa de rejeição de uma conta acima de 30% em 24 h. Nada foi ao ar de errado — o QA está segurando — mas o agente está piorando.

```sql
-- Taxa de rejeição por conta nos últimos 7 dias
SELECT a.handle, a.autonomy,
       count(*) FILTER (WHERE ap.decision = 'rejected') AS rejeitados,
       count(*) FILTER (WHERE ap.decision = 'edited')   AS editados,
       count(*) FILTER (WHERE ap.decision = 'approved') AS aprovados,
       round(100.0 * count(*) FILTER (WHERE ap.decision = 'rejected')
             / nullif(count(*) FILTER (WHERE ap.decided_at IS NOT NULL), 0), 1) AS pct_rejeicao
FROM approvals ap JOIN accounts a ON a.id = ap.account_id
WHERE ap.requested_at > now() - interval '7 days'
GROUP BY a.handle, a.autonomy
HAVING count(*) FILTER (WHERE ap.decided_at IS NOT NULL) > 5
ORDER BY pct_rejeicao DESC NULLS LAST;
```

Causas prováveis, em ordem:

1. **`persona_card` alterado** — comparar `persona_versions` recentes.
2. **Modelo trocado** — `llm_calls.model` mudou? Fallback constante para outro backend?
3. **Contexto degradado** — `prompt_tokens` caindo indica que memórias/persona pararam de entrar no prompt.
4. **Arco narrativo esgotado** — `story_arcs.state` travado, o agente sem material novo, repetindo-se.
5. **Memória poluída** — memórias de baixa qualidade ou de origem externa dominando a recuperação.

```sql
-- Prompt encolhendo é o sinal mais objetivo de contexto perdido
SELECT date_trunc('day', l.created_at)::date AS dia,
       round(avg(l.prompt_tokens)) AS prompt_medio, count(*)
FROM llm_calls l
WHERE l.account_id = '<account_id>'
  AND l.purpose IN ('caption','produce_content')
  AND l.created_at > now() - interval '14 days'
GROUP BY dia ORDER BY dia;

-- Arco parado no mesmo estado há semanas
SELECT title, summary, jsonb_pretty(state), starts_at, updated_at
FROM story_arcs WHERE account_id = '<account_id>' AND active;
```

Resposta: rebaixar autonomia (§5.4), corrigir a causa, observar 7 dias antes de promover de volta. **Não** é caso de suspender a conta.

---

## 8. Prevenção

| Medida | Detalhe |
|---|---|
| **`claim_status` bloqueante em fofoca** | `unverified` **impede** publicação em L≥1 sem revisão — já é o default do schema. Confirmar que está aplicado |
| **`rights_status='unknown'` bloqueia** | Clipagem sem direitos definidos não publica |
| **`attribution` obrigatória** | Clipagem sem crédito não passa do QA |
| **Fofoca nunca em L3** | Decisão do PLANO. Risco jurídico não comporta autonomia total |
| **Lista negra de alvos versionada** | Menores, pessoas não públicas, temas proibidos por conta. Em `persona_card`, versionada em `persona_versions` |
| **Amostragem de L2 real** | `L2_SAMPLING_RATE=0.10` só vale se alguém realmente revisar |
| **Teste de guardrail no CI** | Corpus de conteúdo que **deve** ser bloqueado, rodando em cada commit |
| **QA como etapa obrigatória** | O loop não pode ir de `PRODUCE` a `DISPATCH` pulando `QA` — teste automatizado que garante isso |
| **Revisão semanal de `warn`** | Todo `warn` não tratado é um incidente ensaiando |
| **Quem produz não aprova** | Separação de papéis ([12-seguranca.md §2.3](../12-seguranca.md#23-papéis)) |
| **Conteúdo de terceiro nunca é instrução** | Defesa contra injeção de prompt via comentário/recon |
| **Revalidar guardrails a cada troca de modelo** | Guardrail validado no Qwen pode não valer no Llama |

---

## 9. Escalação

| Quando | Para quem | Prazo |
|---|---|---|
| Qualquer conteúdo impróprio publicado | Admin do Olimpo | **Imediato** |
| Conteúdo envolvendo pessoa identificável | Admin + jurídico | **Imediato** |
| Conteúdo envolvendo menor de idade | Admin + jurídico + **kill switch global** | **Imediato** |
| Notificação extrajudicial ou judicial | Jurídico. Nenhuma resposta pública antes | **Imediato** |
| DMCA / direito autoral | Jurídico + revisão do acervo de clipagem | 4 h |
| Cliente afetado (`client_id`) | Comercial, antes que o cliente descubra sozinho | 1 h |
| Guardrail compartilhado falhou | Responsável técnico — afeta a frota inteira | **Imediato** |
| QA não rodou (bug de pipeline) | Responsável técnico + kill switch até corrigir | **Imediato** |
| Deriva de persona (§7) | Responsável de conteúdo | 24 h |
| `GuardrailBloqueandoDemais` | Responsável de conteúdo — **não** afrouxar sozinho | 24 h |

---

**Ver também:** [08-guardrails-e-autonomia.md](../08-guardrails-e-autonomia.md) · [kill-switch.md](kill-switch.md) · [conta-banida.md](conta-banida.md) · [12-seguranca.md](../12-seguranca.md) · [11-observabilidade.md](../11-observabilidade.md)
