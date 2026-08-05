# Runbook — Restaurar o Postgres

**Severidade:** P1 · **Alertas:** `PostgresIndisponivel`, `BackupNaoVerificado`

> Restaurar o banco é a parte fácil. O problema é o que vem depois: **o Postgres volta ao passado, mas o Instagram não.** Entre o momento do backup e o momento do restore, contas publicaram, seguidores comentaram, tasks executaram. O banco restaurado *acha* que nada disso aconteceu.
>
> Reconciliar essa divergência (§6) é o trabalho de verdade. Restaurar e religar sem reconciliar publica conteúdo duplicado — que além de estragar o feed, é o comportamento não-humano que a plataforma detecta.

---

## 1. Sintoma

- Postgres não sobe; `docker compose ps postgres` mostra `Exited` ou `Restarting`.
- `/health` devolve `postgres != ok`.
- Erros de corrupção nos logs do Postgres.
- Perda de dados por `DELETE`/`UPDATE` acidental sem `WHERE`.
- Volume `pgdata` perdido, corrompido, ou `make reset` executado por engano.
- Migração que falhou no meio e deixou o schema inconsistente.

---

## 2. Antes de tudo — parar a frota

> Nenhuma restauração começa com a frota rodando. Escrita concorrente durante restore corrompe o resultado, e o estado inconsistente pode fazer o dispatcher republicar coisas.

```bash
cd /caminho/do/olimpo

sed -i.bak 's/^GLOBAL_KILL_SWITCH=.*/GLOBAL_KILL_SWITCH=true/' .env

docker compose stop worker beat api panel
docker compose ps
```

```sql
-- Se o banco ainda responde, registre o início. Se não responde, siga.
INSERT INTO events (type, detail, trace_id)
VALUES ('incident.opened',
        jsonb_build_object('service','postgres','runbook','restaurar-backup.md',
          'actor','<seu.nome>','human','Iniciando restauração do banco.'),
        'incident-db-' || to_char(now(),'YYYYMMDDHH24MI'));
```

---

## 3. Diagnóstico — restaurar é mesmo necessário?

Restauração é o último recurso. Elimine primeiro o que é mais barato.

### 3.1 O container sobe?

```bash
docker compose logs --tail=100 postgres
docker compose start postgres && sleep 10
docker compose exec -T postgres pg_isready -U olimpo -d olimpo
```

| Log indica | Ação |
|---|---|
| `database system was not properly shut down` + recovery ok | **Não restaure.** Recuperou sozinho |
| `could not open file ... No such file` | Corrupção de arquivo → restaurar |
| `invalid page in block` | Corrupção de página → §3.3 antes de decidir |
| `PANIC: could not locate a valid checkpoint record` | WAL corrompido → restaurar |
| `No space left on device` | **Não restaure.** Libere disco (§3.2) |
| Container reinicia em loop sem log claro | Volume corrompido → restaurar |

### 3.2 Disco cheio — a causa mais comum e a menos grave

```bash
df -h
docker system df
docker compose exec postgres df -h /var/lib/postgresql/data
```

```bash
# Liberar sem tocar em pgdata
docker system prune -f
docker image prune -a -f
```

Com espaço liberado, o Postgres costuma subir sozinho. Depois disso, investigue o que cresceu — provavelmente `events`, `llm_calls` ou `monitor_hits` ([11-observabilidade.md §7](../11-observabilidade.md#7-higiene-e-retenção)).

### 3.3 Integridade dos dados

Se o banco sobe mas há suspeita de corrupção:

```bash
docker compose exec postgres psql -U olimpo -d olimpo -c "
  SELECT datname, pg_size_pretty(pg_database_size(datname)) FROM pg_database;"

# Um dump completo bem-sucedido é um bom teste de leitura íntegra
docker compose exec -T postgres pg_dump -U olimpo -d olimpo -Fc > /tmp/teste-integridade.dump
echo "exit=$?"
pg_restore --list /tmp/teste-integridade.dump | wc -l
```

```sql
-- As 22 tabelas do 0001_initial.sql estão presentes?
SELECT count(*) FROM information_schema.tables
WHERE table_schema = 'public' AND table_type = 'BASE TABLE';

-- As extensões?
SELECT extname FROM pg_extension WHERE extname IN ('vector','pg_trgm','uuid-ossp');

-- Migrações aplicadas
SELECT version, applied_at FROM schema_migrations ORDER BY version;

-- Contagens de sanidade
SELECT 'accounts' t, count(*) FROM accounts
UNION ALL SELECT 'memories',   count(*) FROM memories
UNION ALL SELECT 'story_arcs', count(*) FROM story_arcs
UNION ALL SELECT 'events',     count(*) FROM events
UNION ALL SELECT 'tasks',      count(*) FROM tasks
UNION ALL SELECT 'posts',      count(*) FROM posts;
```

### 3.4 Perda parcial — restauração cirúrgica

Se só uma tabela foi afetada (ex.: `DELETE` sem `WHERE` em `memories`), **não restaure o banco inteiro**. Restaure a tabela num banco temporário e copie de volta (§5.4).

### 3.5 Escolher o backup

```bash
ls -lah /backup/ | tail -20

# Verificar que o candidato está íntegro ANTES de destruir qualquer coisa
age -d -i /etc/olimpo/backup.key /backup/olimpo-2026-03-12-0400.dump.age \
  > /tmp/restore.dump
pg_restore --list /tmp/restore.dump | head -30
pg_restore --list /tmp/restore.dump | wc -l
```

> Um dump que não passa no `pg_restore --list` está corrompido. Descubra isso **agora**, não depois de apagar o banco atual. Se falhar, vá para o backup anterior.

```bash
# Quanto tempo o backup tem?
BK=/backup/olimpo-2026-03-12-0400.dump.age
echo "backup de: $(date -r "$BK")"
echo "idade: $(( ( $(date +%s) - $(date -r "$BK" +%s) ) / 3600 )) horas"
```

**A idade do backup define o tamanho da reconciliação da §6.** Anote esse número — ele é usado repetidamente adiante.

---

## 4. Preservar o estado atual

> Mesmo corrompido, o banco atual contém dados posteriores ao backup. Descartá-lo é jogar fora a única evidência do que aconteceu no intervalo — inclusive `memories` e `story_arcs` gerados depois.

```bash
D="/backup/pre-restore-$(date +%F-%H%M)" && mkdir -p "$D"

# (a) Cópia física do volume — o mais completo
docker run --rm \
  -v olimpo_pgdata:/data:ro \
  -v "$D":/backup \
  alpine tar czf /backup/pgdata-volume.tar.gz -C /data .

# (b) Se o banco ainda responde, dump lógico também
docker compose exec -T postgres pg_dump -U olimpo -d olimpo -Fc \
  > "$D/pre-restore.dump" 2>"$D/pg_dump.err" || echo "dump falhou — ver err"

# (c) Se só algumas tabelas são legíveis, salve o que é irrecuperável
docker compose exec -T postgres pg_dump -U olimpo -d olimpo -Fc \
  -t memories -t story_arcs -t persona_versions -t accounts -t day_plans -t events \
  > "$D/alma-pre-restore.dump" 2>/dev/null

ls -lah "$D"
```

---

## 5. Restaurar

### 5.1 Preparar

```bash
docker compose stop worker beat api panel
docker compose ps        # só postgres e redis devem estar de pé

age -d -i /etc/olimpo/backup.key /backup/olimpo-2026-03-12-0400.dump.age \
  > /tmp/restore.dump
ls -lah /tmp/restore.dump
```

### 5.2 Restaurar num banco novo, não por cima

Restaurar sobre o banco existente mistura estados. Crie um banco limpo e faça a troca no fim.

```bash
# Banco de destino
docker compose exec -T postgres psql -U olimpo -d postgres -c \
  "DROP DATABASE IF EXISTS olimpo_restore;"
docker compose exec -T postgres psql -U olimpo -d postgres -c \
  "CREATE DATABASE olimpo_restore OWNER olimpo;"

# Extensões antes do restore
docker compose exec -T postgres psql -U olimpo -d olimpo_restore <<'SQL'
CREATE EXTENSION IF NOT EXISTS "uuid-ossp";
CREATE EXTENSION IF NOT EXISTS "vector";
CREATE EXTENSION IF NOT EXISTS "pg_trgm";
SQL

# Restaurar
docker compose exec -T postgres pg_restore \
  -U olimpo -d olimpo_restore --no-owner --no-privileges -j 4 -v \
  < /tmp/restore.dump 2>&1 | tail -40
```

Alguns avisos sobre extensões já existentes são normais. Erros sobre **tabelas** ou **dados** não são.

### 5.3 Verificar a integridade do restaurado

```sql
-- \c olimpo_restore

-- Estrutura
SELECT count(*) AS tabelas FROM information_schema.tables
WHERE table_schema='public' AND table_type='BASE TABLE';        -- esperado: 22

SELECT extname FROM pg_extension WHERE extname IN ('vector','pg_trgm','uuid-ossp');
SELECT version, applied_at FROM schema_migrations ORDER BY version;

-- O que importa: a alma da operação
SELECT
  (SELECT count(*) FROM accounts)                              AS contas,
  (SELECT count(*) FROM accounts WHERE status = 'active')      AS ativas,
  (SELECT count(*) FROM memories)                              AS memorias,
  (SELECT count(*) FROM memories WHERE embedding IS NOT NULL)  AS memorias_com_embedding,
  (SELECT count(*) FROM story_arcs WHERE active)               AS arcos_ativos,
  (SELECT count(*) FROM persona_versions)                      AS versoes_persona,
  (SELECT count(*) FROM day_plans)                             AS planos,
  (SELECT count(*) FROM events)                                AS eventos,
  (SELECT count(*) FROM posts)                                 AS posts;

-- Nenhuma conta pode ter perdido a persona
SELECT count(*) AS contas_sem_persona
FROM accounts WHERE persona_card = '{}'::jsonb OR persona_card IS NULL;

-- Todo arco ativo tem conta viva?
SELECT count(*) AS arcos_orfaos
FROM story_arcs sa LEFT JOIN accounts a ON a.id = sa.account_id
WHERE sa.active AND a.id IS NULL;

-- O índice HNSW sobreviveu? (sem ele, a recuperação de memória fica inviável)
SELECT indexname, indexdef FROM pg_indexes
WHERE tablename = 'memories' AND indexname = 'idx_memories_embedding';

-- Busca vetorial funciona de fato?
SELECT count(*) FROM (
  SELECT id FROM memories
  WHERE embedding IS NOT NULL
  ORDER BY embedding <=> (SELECT embedding FROM memories
                          WHERE embedding IS NOT NULL LIMIT 1)
  LIMIT 5
) s;

-- Continuidade narrativa: cada conta ativa mantém memória e arco?
SELECT a.handle, a.status,
       count(DISTINCT m.id)  AS memorias,
       count(DISTINCT sa.id) FILTER (WHERE sa.active) AS arcos_ativos,
       max(m.occurred_at)    AS memoria_mais_recente
FROM accounts a
LEFT JOIN memories   m  ON m.account_id  = a.id
LEFT JOIN story_arcs sa ON sa.account_id = a.id
WHERE a.status IN ('active','warming','paused')
GROUP BY a.handle, a.status
ORDER BY memorias;
```

> Conta ativa com `memorias = 0` ou sem arco ativo indica restauração parcial. **Não prossiga** — volte para outro backup.

```sql
-- Tasks que estavam em voo no momento do backup: reconciliar na §6.4
SELECT t.status, count(*), min(t.dispatched_at), max(t.dispatched_at)
FROM tasks t
WHERE t.status IN ('dispatched','running','scheduled','pending')
GROUP BY t.status;
```

Se os embeddings vieram nulos, recalcule antes de religar — memória sem embedding não é recuperável pelo loop:

```bash
docker compose run --rm api python -m olimpo.llm.reembed --missing-only
```

### 5.4 Restauração cirúrgica de uma tabela

Para perda parcial (§3.4), sem tocar no resto:

```bash
docker compose exec -T postgres psql -U olimpo -d olimpo <<'SQL'
CREATE SCHEMA IF NOT EXISTS restore_tmp;
SQL

# Restaura só as tabelas necessárias, num schema separado
docker compose exec -T postgres pg_restore -U olimpo -d olimpo \
  --no-owner --schema-only -t memories < /tmp/restore.dump 2>/dev/null || true
```

Caminho mais seguro: restaurar no `olimpo_restore` (§5.2) e copiar de lá:

```sql
-- Copiar apenas o que falta, sem sobrescrever o que existe
INSERT INTO memories
SELECT r.* FROM olimpo_restore.public.memories r
LEFT JOIN memories m ON m.id = r.id
WHERE m.id IS NULL;
```

> Postgres não faz consulta entre bancos diretamente. Use `dblink`/`postgres_fdw`, ou exporte para CSV a partir do `olimpo_restore` e importe com `\copy` no `olimpo`.

### 5.5 Promover o banco restaurado

```bash
docker compose exec -T postgres psql -U olimpo -d postgres <<'SQL'
SELECT pg_terminate_backend(pid) FROM pg_stat_activity
 WHERE datname IN ('olimpo','olimpo_restore') AND pid <> pg_backend_pid();

ALTER DATABASE olimpo RENAME TO olimpo_corrompido;
ALTER DATABASE olimpo_restore RENAME TO olimpo;
SQL

docker compose exec -T postgres psql -U olimpo -d olimpo -c "SELECT count(*) FROM accounts;"
```

Mantenha `olimpo_corrompido` por pelo menos 7 dias. Ele pode conter dados do intervalo que ainda serão úteis.

### 5.6 Reaplicar migrações pendentes

Se o backup é anterior a uma migração:

```bash
docker compose run --rm migrate
docker compose exec -T postgres psql -U olimpo -d olimpo -c \
  "SELECT version, applied_at FROM schema_migrations ORDER BY version;"
```

---

## 6. Reconciliação — o passo que não pode ser pulado

> O banco voltou para as 04:00. São 11:00. **Sete horas de realidade não estão no banco.** Contas publicaram, comentários chegaram, tasks executaram — e o banco restaurado não sabe de nada disso.
>
> Religar agora significa: republicar posts que já estão no ar, reagendar tasks já executadas, e responder de novo comentários já respondidos.

### 6.1 Dimensionar a lacuna

```sql
SELECT
  max(created_at)                                        AS ultimo_registro,
  now()                                                  AS agora,
  round(extract(epoch FROM (now() - max(created_at)))/60) AS lacuna_minutos
FROM events;
```

```sql
-- O que o banco restaurado acha que ainda vai acontecer
SELECT t.id, a.handle, t.type, t.status, t.scheduled_for, t.dispatched_at,
       t.external_task_id
FROM tasks t JOIN accounts a ON a.id = t.account_id
WHERE t.status IN ('pending','scheduled','dispatched','running')
  AND t.scheduled_for < now()
ORDER BY t.scheduled_for;
```

**Toda linha dessa consulta pode já ter sido executada de verdade.**

### 6.2 Reconciliar com o android-orchestrator

O orchestrator é a fonte da verdade sobre o que realmente executou.

```bash
PSQL="docker compose exec -T postgres psql -U olimpo -d olimpo -tA"

# (a) Tasks que o banco acha em voo: perguntar o desfecho
$PSQL -c "SELECT id || ' ' || external_task_id FROM tasks
          WHERE status IN ('dispatched','running') AND external_task_id IS NOT NULL;" \
| while read -r tid ext; do
    st=$(curl -fsS -m 10 -H "Authorization: Bearer ${ORCHESTRATOR_TOKEN}" \
          "${ORCHESTRATOR_URL}/tasks/${ext}" 2>/dev/null | jq -r '.status // "desconhecida"')
    echo "$tid $ext $st"
  done | tee /tmp/reconc-inflight.txt

# (b) O que o orchestrator executou DEPOIS do backup e o banco não sabe
curl -fsS -H "Authorization: Bearer ${ORCHESTRATOR_TOKEN}" \
  "${ORCHESTRATOR_URL}/tasks?since=2026-03-12T04:00:00Z&status=completed" \
  | jq -r '.tasks[] | [.id, .account_handle, .action, .completed_at] | @tsv' \
  | tee /tmp/executadas-na-lacuna.tsv

wc -l /tmp/executadas-na-lacuna.tsv
```

Aplicar os desfechos:

```sql
-- Confirmou execução → fechar a task, não reexecutar
UPDATE tasks
SET status = 'verified', verified_at = now(), external_status = 'completed',
    result = result || jsonb_build_object('reconciled_after_restore', true)
WHERE external_task_id = ANY(ARRAY['<ext1>','<ext2>']);

-- Não executou → reagendar COM espalhamento
UPDATE tasks
SET status = 'scheduled',
    scheduled_for = now() + interval '30 minutes' + (random() * interval '3 hours'),
    external_task_id = NULL, external_status = NULL,
    attempts = attempts + 1,
    last_error = 'reagendada apos restauracao de banco'
WHERE external_task_id = ANY(ARRAY['<ext3>'])
  AND attempts < max_attempts;
```

### 6.3 Reconciliar com a realidade das plataformas

O orchestrator pode não cobrir toda a lacuna. Confira os perfis reais.

```sql
-- O que o banco restaurado registra como publicado, por conta
SELECT a.handle, count(*) AS posts_no_banco, max(p.published_at) AS ultimo
FROM posts p JOIN accounts a ON a.id = p.account_id
WHERE p.published_at > now() - interval '3 days'
GROUP BY a.handle ORDER BY a.handle;
```

Para cada conta ativa, compare com o perfil real. O caminho automatizado é despachar uma coleta antes de retomar a publicação:

```sql
-- Cria uma coleta de métricas por conta ativa, com prioridade máxima e espaçada
INSERT INTO tasks (account_id, type, status, priority, origin, scheduled_for, payload)
SELECT a.id, 'collect_metrics', 'scheduled', 0, 'human',
       now() + (row_number() OVER (ORDER BY a.id) * interval '3 minutes'),
       jsonb_build_object('reason','reconciliacao_pos_restore',
                          'since', (now() - interval '<lacuna> hours'))
FROM accounts a
WHERE a.status IN ('active','warming');
```

Posts encontrados no perfil e ausentes do banco precisam ser inseridos, ou o sistema tentará publicar o mesmo conteúdo de novo:

```sql
INSERT INTO posts (account_id, content_item_id, task_id, platform,
                   external_id, permalink, published_at)
VALUES ('<account_id>', NULL, NULL, 'instagram',
        '<external_id>', '<permalink>', '<published_at>');

INSERT INTO events (account_id, type, detail, trace_id)
VALUES ('<account_id>','post.reconciled',
        jsonb_build_object('source','verificacao_manual_pos_restore',
          'permalink','<permalink>','actor','<seu.nome>',
          'human','Post existente na plataforma e ausente do banco após restore.'),
        'incident-db-...');
```

### 6.4 Cancelar o que ficou obsoleto

```sql
-- Publicação agendada para antes do restore: republicar é duplicar
UPDATE tasks
SET status = 'cancelled',
    last_error = 'cancelada na reconciliacao pos-restore (pode ja ter executado)'
WHERE status IN ('pending','scheduled')
  AND scheduled_for < now()
  AND type IN ('publish_post','publish_story','publish_reels',
               'comment','reply_comment','send_dm','follow','unfollow');

-- Coleta e leitura são seguras de reexecutar: só reagendar
UPDATE tasks
SET scheduled_for = now() + (random() * interval '2 hours')
WHERE status IN ('pending','scheduled')
  AND scheduled_for < now()
  AND type IN ('collect_metrics','collect_interactions','watch_reels',
               'view_stories','monitor_check');

-- Expirados continuam expirados
UPDATE tasks SET status = 'expired'
WHERE status IN ('pending','scheduled')
  AND expires_at IS NOT NULL AND expires_at < now();
```

### 6.5 Memória e narrativa da lacuna

O que aconteceu entre o backup e o restore não gerou memória. A persona tem um buraco na vivência.

```sql
-- Uma memória episódica que registra honestamente a lacuna, por conta ativa.
-- Sem isto, o REFLECT pode gerar narrativa incoerente com o feed real.
INSERT INTO memories (account_id, kind, content, importance, metadata, occurred_at)
SELECT a.id, 'episodic',
       'Houve um período de indisponibilidade do sistema entre '
        || to_char(now() - interval '<lacuna> hours', 'DD/MM HH24:MI')
        || ' e ' || to_char(now(), 'DD/MM HH24:MI')
        || '. Registros desse intervalo podem estar incompletos.',
       3.0,
       jsonb_build_object('origin','system','reason','restauracao_de_backup',
                          'gap_hours', <lacuna>),
       now()
FROM accounts a
WHERE a.status IN ('active','warming');
```

```sql
-- Arcos ativos: conferir se o `state` faz sentido com o feed real
SELECT a.handle, sa.title, sa.summary, jsonb_pretty(sa.state), sa.updated_at
FROM story_arcs sa JOIN accounts a ON a.id = sa.account_id
WHERE sa.active AND a.status IN ('active','warming')
ORDER BY a.handle;
```

Se o arco avançou durante a lacuna (a conta postou o clímax de uma história), ajuste `state` manualmente. Caso contrário a conta vai repetir um capítulo que a audiência já viu.

### 6.6 Interações da lacuna

```sql
-- Coletar as interações do intervalo, para não deixar seguidor sem resposta
INSERT INTO tasks (account_id, type, status, priority, origin, scheduled_for, payload)
SELECT a.id, 'collect_interactions', 'scheduled', 10, 'human',
       now() + (row_number() OVER (ORDER BY a.id) * interval '4 minutes'),
       jsonb_build_object('since', (now() - interval '<lacuna> hours'))
FROM accounts a WHERE a.status = 'active';
```

Risco de resposta duplicada: uma interação já respondida durante a lacuna não consta como respondida no banco restaurado. Revise antes de reativar auto-resposta:

```sql
SELECT a.handle, i.author_handle, left(i.body, 80) AS comentario, i.received_at
FROM interactions i JOIN accounts a ON a.id = i.account_id
WHERE i.replied_at IS NULL
  AND i.received_at > now() - interval '<lacuna+2> hours'
ORDER BY i.received_at DESC;
```

Recomendação: manter as contas em **L0** por 24 h após o restore, para que toda resposta passe por humano.

---

## 7. Religar

Só depois da §6 completa.

### 7.1 Checklist

- [ ] Integridade verificada (§5.3): 22 tabelas, extensões, memórias, arcos, índice HNSW
- [ ] Migrações reaplicadas; `schema_migrations` coerente
- [ ] Embeddings presentes (ou recalculados)
- [ ] Tasks em voo reconciliadas com o orchestrator (§6.2)
- [ ] Posts da lacuna conferidos nos perfis reais (§6.3)
- [ ] Tasks obsoletas canceladas (§6.4)
- [ ] Backlog espalhado — nenhuma conta acima de 12 ações/hora
- [ ] Memória da lacuna registrada; arcos conferidos (§6.5)
- [ ] Contas em L0 por 24 h
- [ ] `olimpo_corrompido` preservado
- [ ] Backup novo do banco **restaurado e reconciliado** feito
- [ ] Admin aprovou a retomada

### 7.2 Espalhar antes de ligar

```sql
WITH ordenadas AS (
  SELECT id, row_number() OVER (PARTITION BY account_id
                                ORDER BY priority, scheduled_for) AS n
  FROM tasks
  WHERE status IN ('pending','scheduled') AND scheduled_for <= now()
)
UPDATE tasks t
SET scheduled_for = now() + interval '20 minutes'
                          + (o.n * interval '20 minutes')
                          + (random() * interval '25 minutes')
FROM ordenadas o WHERE t.id = o.id;

-- Deve voltar vazio
SELECT a.handle, date_trunc('hour', t.scheduled_for) AS hora, count(*)
FROM tasks t JOIN accounts a ON a.id = t.account_id
WHERE t.status IN ('pending','scheduled')
  AND t.scheduled_for BETWEEN now() AND now() + interval '12 hours'
GROUP BY a.handle, hora HAVING count(*) > 12;
```

### 7.3 Subir em degraus

```bash
# Backup imediato do estado bom
docker compose exec -T postgres pg_dump -U olimpo -d olimpo -Fc \
  > "/backup/olimpo-pos-restore-$(date +%F-%H%M).dump"

# API e painel primeiro, sem worker
docker compose start api panel
curl -fsS localhost:8000/health | jq .

# Todas as contas em L0 por 24 h
docker compose exec -T postgres psql -U olimpo -d olimpo -c \
  "UPDATE accounts SET autonomy='L0' WHERE status IN ('active','warming');"

# Liberar o kill switch
sed -i 's/^GLOBAL_KILL_SWITCH=.*/GLOBAL_KILL_SWITCH=false/' .env
docker compose restart api

# Uma conta canária
docker compose exec -T postgres psql -U olimpo -d olimpo -c \
  "UPDATE accounts SET status='active' WHERE handle='<canaria>';"
docker compose start beat worker
```

Observar 30 minutos. Depois seguir os degraus da §6.4 de [kill-switch.md](kill-switch.md#64-religar).

### 7.4 Fechar

```sql
INSERT INTO events (type, detail, trace_id)
VALUES ('incident.closed',
        jsonb_build_object('service','postgres','runbook','restaurar-backup.md',
          'actor','<seu.nome>',
          'backup_used','olimpo-2026-03-12-0400.dump.age',
          'gap_hours', <n>, 'downtime_min', <n>,
          'tasks_cancelled', <n>, 'tasks_reconciled', <n>,
          'posts_reconciled', <n>,
          'root_cause','<causa>',
          'human','Banco restaurado e reconciliado com o estado real das contas.'),
        'incident-db-...');
```

---

## 8. Prevenção

| Medida | Detalhe |
|---|---|
| **Teste de restauração mensal** | Em ambiente descartável, cronometrado. Backup nunca restaurado é hipótese |
| **WAL archiving** | Reduz o RPO de 24 h para minutos — e a lacuna da §6 encolhe na mesma proporção |
| **Dump separado da "alma"** | `memories`, `story_arcs`, `persona_versions`, `accounts`, `day_plans` — pequeno, rápido, restaurável isoladamente ([12-seguranca.md §6.3](../12-seguranca.md#63-executar)) |
| **Backup fora do host** | Backup no mesmo disco do Postgres não sobrevive à perda do disco |
| **Métrica de backup** | `olimpo_backup_last_success_timestamp` + alerta `BackupNaoVerificado` |
| **Verificar o dump, não só criá-lo** | `pg_restore --list` em todo backup, automaticamente |
| **Alerta de disco** | Disco cheio é a causa mais comum de queda do Postgres. Alertar em 80% |
| **`make reset` bloqueado em produção** | Ele apaga volumes. Guarda de ambiente obrigatória |
| **Retenção ativa** | `events`, `llm_calls` e `monitor_hits` crescendo sem poda enchem o disco ([11 §7](../11-observabilidade.md#7-higiene-e-retenção)) |
| **Idempotência de publicação** | Chave por `content_item_id` no orchestrator: republicação duplicada recusada na origem — a melhor defesa contra o risco da §6 |

---

## 9. Escalação

| Quando | Para quem | Prazo |
|---|---|---|
| Postgres fora do ar | Admin do Olimpo | **Imediato** |
| Restauração necessária | Admin + responsável técnico | **Imediato** |
| Backup mais recente > 24 h | Admin — decidir sobre a perda | **Imediato** |
| Nenhum backup restaurável | **Crítico.** Admin + responsável técnico. Avaliar reconstrução parcial dos perfis públicos | **Imediato** |
| `memories` ou `story_arcs` perdidas sem backup | Admin — perda permanente de ativo | **Imediato** |
| Lacuna > 6 h | Admin — reconciliação manual de todas as contas | 1 h |
| Publicação duplicada detectada após religar | **Kill switch** + [conta-banida.md](conta-banida.md) — risco de ban elevado | **Imediato** |
| Contas de cliente (`client_id`) afetadas | Comercial | 4 h |
| Causa raiz é disco/infra | Responsável de infra | 24 h |

---

**Ver também:** [12-seguranca.md §6](../12-seguranca.md#6-backup-e-recuperação) · [kill-switch.md](kill-switch.md) · [orchestrator-fora-do-ar.md](orchestrator-fora-do-ar.md) · [11-observabilidade.md](../11-observabilidade.md)
