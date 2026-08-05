# ADR-0003: Um Postgres com pgvector para tudo

- **Status:** Aceito
- **Data:** 2026-08-05
- **Decisores:** equipe Olimpo

## Contexto

O Olimpo tem dois perfis de dado que normalmente moram em bancos diferentes:

1. **Relacional/transacional** — contas, tasks, campanhas, conteúdo, aprovações, métricas, eventos. Precisa de integridade referencial, transação e join.
2. **Vetorial** — o *memory stream* de cada agente. A recuperação do paper generative_agents pondera **relevância × recência × importância**: similaridade de cosseno sobre o embedding, decaimento sobre `occurred_at`, e o score `importance` atribuído pela LLM.

A leitura óbvia é "vetor pede banco vetorial": Qdrant, Weaviate, Pinecone, Milvus. Só que a consulta que realmente fazemos não é "top-k por similaridade" — é:

> recupere as memórias **desta conta**, dos tipos episódica e narrativa, ligadas ao arco ativo, ocorridas nos últimos N dias, ordenadas por `0.5·similaridade + 0.3·recência + 0.2·importância`

Ou seja: **filtro relacional pesado + ordenação híbrida**. Num banco vetorial dedicado isso vira ou um filtro de metadados limitado (com qualidade de recall degradada quando o filtro é seletivo), ou duas viagens e um join na aplicação. E ainda deixa duas fontes de verdade para sincronizar: escreveu memória no Postgres, precisa escrever no Qdrant; deletou conta, precisa deletar dos dois; restaurou backup, precisa restaurar consistente nos dois.

A escala também importa. Estimativa realista: uma conta gera de 20 a 100 memórias por dia (posts, interações, reflexões, eventos de campanha). Com 50 contas ativas: ~1,5k–5k memórias/dia, ~0,5–1,8M memórias/ano. Isso está confortavelmente dentro do que o pgvector com HNSW aguenta em uma máquina de porte médio.

## Decisão

**Um único Postgres 16 com as extensões `vector`, `pg_trgm` e `uuid-ossp` serve simultaneamente como banco relacional e como índice vetorial de memória. Não existe banco vetorial dedicado no Olimpo.**

Detalhamento:

- `memories.embedding vector(1024)` com índice `USING hnsw (embedding vector_cosine_ops)`.
- Dimensão 1024 fixada para acomodar os modelos de embedding candidatos rodando local nos Mac minis (família `bge-m3` / `mxbai-embed-large`). Trocar de modelo exige re-embedar a tabela — assumido.
- A consulta de recuperação é **uma query SQL só**, com o filtro relacional (`account_id`, `kind`, `arc_id`, janela temporal) no `WHERE` e o score híbrido no `ORDER BY`. Não há join na aplicação.
- Índice de apoio `idx_memories_account_kind (account_id, kind, occurred_at DESC)` para os cortes que não precisam de vetor (ex.: "as 20 memórias mais recentes desta conta").
- Escrita de memória e escrita do fato que a gerou (post, interação, reflexão) acontecem **na mesma transação**. Não existe janela em que o sistema lembra de algo que não aconteceu, nem o contrário.

## Alternativas consideradas

| Alternativa | Prós | Contras | Por que não |
|---|---|---|---|
| **Qdrant** | Excelente ANN, filtro por payload razoável, self-hosted, ótimo em bilhões de vetores | Segunda fonte de verdade; sincronização e backup duplos; filtro por payload não substitui join real; mais um serviço no compose e no on-call | Ganho de performance irrelevante na nossa escala; custo operacional imediato e permanente. |
| **Weaviate** | Busca híbrida (BM25 + vetor) embutida, schema forte | Mesmo problema de dupla verdade; modelo de dados próprio; footprint de memória alto | O híbrido texto+vetor conseguimos com `pg_trgm` + pgvector no mesmo banco. |
| **Pinecone (gerenciado)** | Zero operação, escala trivialmente | SaaS externo com dados de persona e conteúdo das contas; custo recorrente por vetor; latência de rede em cada recuperação; dependência de terceiro num sistema que já depende do orchestrator | Enviar o memory stream das contas para fora é risco desnecessário. Ver `docs/12-seguranca.md`. |
| **Letta como camada de memória** | Memória hierárquica sofisticada, self-editing | Estado fora do nosso Postgres; quebra a auditoria unificada; opinado sobre formato de agente | Rebaixado a spike na Fase 1 (ADR-0001). Default permanece pgvector. |
| **Sem vetor: só busca lexical + recência** | Simplicidade máxima, zero custo de embedding | Perde o "ela lembrou de algo relacionado, não igual" — que é o que faz a narrativa parecer viva | Barato demais em qualidade; o pgvector custa pouco mais e entrega o comportamento do paper. |
| **Postgres + pgvector** (escolhida) | Uma transação, um backup, um join, uma restauração | Teto de escala mais baixo que um vetorial dedicado; tuning de HNSW é responsabilidade nossa | Aceito — o teto está muito acima do horizonte de 2–3 anos. |

## Consequências

### Positivas
- **Uma transação.** "Postou → gravou memória → registrou evento" é atômico. Impossível ficar inconsistente.
- **Um backup, uma restauração, um ponto de recuperação.** `pg_dump` traz o sistema inteiro, incluindo a memória dos agentes.
- **Join livre.** Perguntas como "quais memórias desta conta foram usadas em planos que geraram posts com engajamento acima da média?" são uma query. Com banco separado seriam um pipeline.
- **Menos um serviço** no docker-compose, no monitoramento, no on-call e no orçamento.
- **`ON DELETE CASCADE` funciona.** Apagar uma conta apaga a memória dela — sem job de limpeza cross-database que sempre deixa órfãos.
- **Ferramental existente serve.** Adminer, `psql`, dashboards do Postgres no Grafana cobrem também a memória.

### Negativas / custos aceitos
- **Teto de escala.** Ver limites abaixo. Assumimos que ele não é atingido no horizonte deste projeto.
- **Construção de índice HNSW é cara.** Reindexar alguns milhões de vetores é operação de manutenção com janela, não algo que se faz num deploy qualquer. Mitigado por `maintenance_work_mem` alto e `CREATE INDEX CONCURRENTLY`.
- **Carga vetorial compete com a carga OLTP.** Uma varredura pesada de memória pode roubar buffer cache do caminho crítico de dispatch. Na prática, resolvido com read replica quando/se doer.
- **Trocar de modelo de embedding = re-embedar tudo.** Não há mágica; é um job de migração de dados. Dimensão fixa em 1024 no schema torna a troca por modelos de outra dimensão uma migração de coluna.
- **Tuning de `ef_search`/`m` é nosso.** Sem defaults auto-ajustados de um serviço gerenciado.

### Riscos e como mitigamos
- **Risco: recall ruim do HNSW com filtro seletivo por `account_id`.** É o modo de falha clássico do ANN filtrado — o índice caminha por vizinhos que o filtro descarta. Mitigação: como o filtro por conta é sempre presente e altamente seletivo, o plano ideal costuma ser **varredura da partição lógica da conta**, não o índice HNSW. Monitoramos os planos; se necessário, particionamos `memories` por `account_id` (hash) e mantemos HNSW por partição.
- **Risco: tabela `memories` cresce sem limite.** Mitigação: política de consolidação — o passo REFLECT sintetiza episódicas antigas em semânticas/narrativas, e episódicas com `importance` baixa e sem uso (`last_used_at` nulo) além de N meses são arquivadas para tabela fria. Memória humana também esquece.
- **Risco: `events` (bigserial, cresce rápido) degradar o banco compartilhado.** Mitigação: particionamento por mês e política de retenção; `events` é append-only e nunca participa do caminho quente.

## Limite de escala e caminho de saída

**Onde isso deixa de servir.** O ponto de inflexão prático do pgvector com HNSW numa instância única é a **ordem de milhões de vetores** — na faixa de 5–10M de linhas em `memories`, com um índice que já não cabe confortavelmente na RAM, a latência p95 da recuperação sai da casa das dezenas de milissegundos e a reindexação passa a exigir janela de manutenção significativa. Nos nossos números (~0,5–1,8M memórias/ano com 50 contas), isso corresponde a **algo entre 3 e 10 anos de operação**, ou a um salto de uma ordem de grandeza no número de contas.

**Gatilhos objetivos de alarme:**
- `memories` passa de **5M de linhas**;
- p95 da query de recuperação passa de **200 ms**;
- o índice HNSW de `memories` não cabe em `shared_buffers` + page cache da instância;
- `REINDEX` do HNSW passa de **1 hora**.

**Caminho de saída, em ordem de custo crescente:**
1. **Higiene primeiro.** Consolidação agressiva no REFLECT + arquivamento de episódicas frias. Costuma devolver metade da tabela.
2. **Particionar `memories` por `account_id`** (hash, ~16–64 partições) com índice HNSW por partição. Cada busca toca uma partição pequena. Resolve o problema de recall filtrado ao mesmo tempo. **Zero mudança de código de aplicação.**
3. **Read replica dedicada à leitura de memória.** Isola a carga vetorial do OLTP. Também sem mudança de aplicação além da string de conexão.
4. **Extrair para um vetorial dedicado (Qdrant).** Só neste ponto. A saída é barata **porque a preparamos**: toda recuperação de memória passa por um único módulo, `olimpo.memory.retriever`, com a interface `retrieve(account_id, query, kinds, k, weights) -> list[Memory]`. Trocar a implementação é reescrever esse módulo e manter o Postgres como fonte de verdade — o Qdrant vira índice derivado, reconstruível a partir do Postgres, nunca fonte de verdade. Isso mantém a propriedade que mais nos importa (uma transação, um backup) mesmo depois da migração.

## Revisitar quando

- Qualquer um dos quatro gatilhos objetivos acima disparar.
- Passarmos de ~200 contas ativas (mudança de ordem de grandeza na taxa de escrita).
- Precisarmos de busca vetorial **cross-account** frequente e de baixa latência — ex.: "algum agente da casa já comentou sobre este assunto?" varrendo a frota inteira. Esse padrão não tem filtro seletivo e é o pior caso do pgvector.
- O spike do Letta (Fase 1) provar valor, o que reabriria a discussão de onde a memória mora.
- Multi-tenancy (Fase 5, `clients`) exigir isolamento físico de dados por cliente.
