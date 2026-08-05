# ADR-0005: Migrações em SQL puro com runner mínimo

- **Status:** Aceito
- **Data:** 2026-08-05
- **Decisores:** equipe Olimpo

## Contexto

O schema do Olimpo é o documento mais importante do projeto. Ele carrega decisões que nenhum outro artefato carrega: os arquétipos como `ENUM`, os campos dormentes de fofoca e clipagem (ADR-0006), `campaigns.spread_policy NOT NULL` (ADR-0007), a máquina de estados de campanha com `recon_pending` (ADR-0008), os índices parciais que o dispatcher usa (ADR-0004), o índice HNSW da memória (ADR-0003). Ler `0001_initial.sql` é entender o sistema — os comentários no arquivo fazem parte da documentação.

A escolha default do ecossistema Python seria Alembic. Alembic é bom no que se propõe, mas o que ele se propõe é acoplar o schema aos modelos SQLAlchemy e gerar migrações por diff. Isso traz três atritos concretos para este projeto:

1. **Autogeração não entende metade do nosso schema.** `ENUM` do Postgres com valores adicionados depois, `int4range`, `vector(1024)`, índice HNSW com `vector_cosine_ops`, índices **parciais** com `WHERE`, `CREATE EXTENSION`, triggers de `updated_at` criadas em bloco `DO $$`, FKs adicionadas fora de ordem para resolver ciclos. Autogenerate produz diffs errados ou vazios para quase tudo isso — e um diff errado num schema de produção é pior que nenhum diff.
2. **A migração vira Python.** Um `op.create_index(..., postgresql_using='hnsw', postgresql_ops={...})` é menos legível que o `CREATE INDEX` correspondente, e não é revisável por alguém que sabe Postgres mas não sabe Alembic.
3. **O schema deixa de ser lido como documentação.** Passa a ser uma sequência de mutações Python cujo estado final só existe no banco ou num `--autogenerate` contra base vazia.

Do outro lado: sem Alembic perdemos autogeração e downgrade automático, e passamos a depender de disciplina para escrever DDL correto.

## Decisão

**Migrações são arquivos `.sql` puros, numerados e imutáveis, em `db/migrations/`, aplicados por um runner mínimo próprio (`python -m olimpo.db.migrate`) que registra o que já rodou em `schema_migrations`. Não usamos Alembic.**

Detalhamento:

- **Nomenclatura:** `NNNN_descricao_curta.sql`, com `NNNN` sequencial de 4 dígitos. Ordem lexicográfica = ordem de aplicação.
- **Controle:** a tabela `schema_migrations (version text PRIMARY KEY, applied_at timestamptz, checksum text)` — criada pela própria `0001` — é o estado.
- **Checksum.** O runner calcula o SHA-256 do arquivo e compara com o registrado. **Editar uma migração já aplicada é erro fatal**, não aviso. Isso é o que garante que dev, staging e produção tenham o mesmo schema.
- **Transacional.** Cada migração roda dentro de uma transação (Postgres tem DDL transacional). Falhou, reverteu inteira. Exceções que não podem rodar em transação (`CREATE INDEX CONCURRENTLY`, `ALTER TYPE ... ADD VALUE` em versões antigas) são marcadas com um comentário mágico `-- olimpo:no-transaction` na primeira linha e o runner respeita.
- **Lock.** O runner toma um `pg_advisory_lock` antes de aplicar, para que dois containers `migrate` subindo em paralelo não colidam. O compose já ordena via `service_completed_successfully`, mas o lock é a garantia real.
- **Comandos:** `migrate up` (aplica pendentes), `migrate status` (o que falta), `migrate verify` (confere checksums sem aplicar). **Não existe `migrate down`.**
- **SQLAlchemy é só mapeamento.** Os modelos em `olimpo.db.models` refletem o schema; eles não o definem. Não há `Base.metadata.create_all()` em lugar nenhum, nem em testes — testes aplicam as migrações reais, o que é o único jeito de testar contra o schema verdadeiro.

## Alternativas consideradas

| Alternativa | Prós | Contras | Por que não |
|---|---|---|---|
| **Alembic** | Padrão do ecossistema; autogeração; downgrade; branching/merge de revisões; muita documentação | Autogenerate falha ou erra em ENUM, `vector`, HNSW, índices parciais, `int4range`, extensões e triggers — a maior parte do nosso schema; migração vira Python menos legível; schema deixa de ser documentação; grafo de revisões é overhead para um repo de time único | O benefício principal (autogeração) não funciona no nosso schema, e o custo (opacidade) incide sobre o nosso artefato mais importante. |
| **Alembic com todas as migrações escritas à mão** (`op.execute("...")`) | Mantém o runner e o histórico do Alembic | Vira SQL puro dentro de wrapper Python — o pior dos dois mundos: nem legibilidade de `.sql`, nem autogeração | Cerimônia sem benefício. Se o conteúdo é SQL, o arquivo deve ser `.sql`. |
| **sqitch / Flyway / dbmate / golang-migrate** | Ferramentas maduras de migração em SQL puro; resolvem checksum e ordenação | Mais um binário/runtime no container Python; convenções próprias (Flyway é JVM; sqitch é Perl); dbmate/golang-migrate são Go — leves, mas adicionam toolchain | O runner que precisamos são ~120 linhas de `psycopg`. Preferimos zero dependência externa a uma dependência pequena com toolchain próprio. |
| **`schema.sql` único recriado a cada mudança** | Máxima legibilidade | Impossível evoluir produção com dados | Só serviria antes da primeira conta existir. |
| **ORM com `create_all()` / migração automática** | Nenhum esforço | Nenhum controle; impossível em produção | Fora de questão. |
| **SQL puro + runner próprio** (escolhida) | Transparência total; schema é documentação; revisável por qualquer DBA; zero dependência | Sem autogeração; sem downgrade; disciplina manual | Aceito conscientemente. |

## Consequências

### Positivas
- **Transparência total.** O que roda no banco é exatamente o que está no arquivo. Não há tradução, não há "o que o Alembic vai gerar disso?".
- **O schema é documentação.** `0001_initial.sql` tem comentários explicando por que cada campo `[DORMENTE]` existe, o que cada valor de `claim_status` significa e qual índice o dispatcher usa. Um novo integrante lê o arquivo e entende o sistema — isso não é um efeito colateral, é um objetivo.
- **Revisável por quem sabe Postgres.** Um DBA externo (ou uma consultoria, ou um auditor) revisa o SQL sem aprender Alembic nem ler Python.
- **Nada é impossível de expressar.** Extensões, ENUMs, ranges, HNSW, índices parciais, triggers em `DO $$`, `CHECK` constraints, FKs circulares resolvidas com `ALTER TABLE` posterior — tudo direto, sem escape hatch.
- **Diff de PR legível.** A revisão de uma mudança de schema é a leitura de DDL, o que faz o time discutir modelagem em vez de sintaxe de ferramenta.
- **Runner minúsculo.** ~120 linhas, testável, sem magia. O comportamento em caso de falha é óbvio.

### Negativas / custos aceitos
- **Sem autogeração.** Toda mudança de schema é escrita à mão e o modelo SQLAlchemy correspondente é atualizado à mão. Duas edições em vez de uma, com risco de divergirem.
- **Sem downgrade automático.** Não existe `migrate down`. Reverter é escrever uma migração nova para frente (`0007_revert_do_0006.sql`) ou restaurar backup. Assumido: em produção, downgrade automático de schema com dados quase nunca funciona de verdade, então perdemos pouco na prática — mas perdemos a conveniência em desenvolvimento, onde `make reset` é a saída.
- **Sem branching/merge de revisões.** Dois PRs criando `0007_` colidem. Resolvido por convenção humana (renumerar no rebase) — aceitável em time pequeno, doloroso em time grande.
- **Disciplina manual.** Nada impede alguém de escrever DDL que trava a tabela em produção. A ferramenta não protege; a revisão protege.
- **Sem `alembic stamp`** para adotar bancos preexistentes. Irrelevante aqui: o banco nasce com este schema.

### Riscos e como mitigamos
- **Risco: modelo SQLAlchemy divergir do schema real.** Mitigação: teste no CI que sobe um Postgres limpo, aplica todas as migrações e reflete o schema, comparando tabelas/colunas/tipos com os modelos declarados. Divergência quebra o build.
- **Risco: alguém editar uma migração já aplicada.** Mitigação: checksum em `schema_migrations`; o runner **falha** (não avisa) e o CI roda `migrate verify` contra um banco que já aplicou a série anterior.
- **Risco: DDL que trava tabela grande em produção.** Mitigação: checklist em `docs/runbooks/` — `ADD COLUMN` sempre nullable ou com default constante (Postgres 11+ não reescreve), `CREATE INDEX CONCURRENTLY` com `-- olimpo:no-transaction`, backfill em lote em migração separada da mudança de constraint.
- **Risco: colisão de numeração entre PRs.** Mitigação: `migrate status` no CI detecta duas versões iguais; convenção de renumerar no rebase; PRs de schema são priorizados para merge rápido.
- **Risco: o runner ter um bug e aplicar parcialmente.** Mitigação: transação por migração + advisory lock; `migrate status` sempre reflete o estado real, nunca um cache.

## Revisitar quando

- O time passar de ~4 pessoas escrevendo migrações em paralelo — colisão de numeração e ausência de merge de revisões deixam de ser aceitáveis.
- Precisarmos de migrações condicionais por tenant (Fase 5, `clients`) ou de schemas separados por cliente.
- Aparecer necessidade real de rollback automatizado com dados (ex.: exigência de compliance para reversão em janela).
- O runner próprio acumular funcionalidade a ponto de virar um mini-Alembic — sinal de que devíamos ter usado o de verdade, ou trocar por `dbmate`/`sqitch` que já resolvem checksum, lock e branching.
