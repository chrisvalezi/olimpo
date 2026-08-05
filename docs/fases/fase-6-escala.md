# Fase 6 — Escala e negócio

**Duração estimada:** contínua (ciclos de 2 semanas) · **Depende de:** [Fase 5](fase-5-clipagem.md) · **Entrega para:** — (regime permanente)

---

## 1. Resumo

Fase sem data de término. O sistema deixa de ser construído e passa a ser **melhorado por si mesmo**: analytics por nicho/horário/formato, A/B testing conduzido pelos próprios agentes, onboarding de conta nova em menos de um dia, banco de conteúdo reaproveitável entre contas e as trilhas de monetização.

Diferente das anteriores, não tem "critério de saída" único — tem **metas de regime permanente** que precisam ser sustentadas, e cada ciclo de 2 semanas escolhe uma frente.

---

## 2. Pré-requisitos

| Item | Detalhe |
|---|---|
| Fase 5 completa | Três arquétipos em produção, multi-tenant funcionando |
| Volume histórico | ≥ 500 posts publicados com `post_metrics` coletadas em ≥ 4 janelas cada. Sem histórico, analytics é ficção |
| Agendador global de slots | Dívida nº 6 da Fase 5 — primeira entrega, antes de aumentar a frota |
| Orçamento de LLM revisto | `LLM_DAILY_BUDGET_USD` calibrado com o custo real por conta medido nas fases anteriores |
| Backup e restore testados | Antes de escalar, provar que o Postgres volta. Runbook exercitado, não só escrito |

---

## 3. Escopo

### 3.1 Fundação de escala (frente 0 — faça primeiro)

1. **`olimpo.core.scheduler`** — agendador global de slots: uma visão única do calendário da frota (todas as contas, todos os arquétipos, todas as campanhas). Aloca slots respeitando, em ordem, `GLOBAL_KILL_SWITCH` → limite global de frota → limite por device → `ACCOUNT_MAX_ACTIONS_PER_HOUR` → `accounts.cadence` → `active_hours` → jitter de campanha. Substitui o sorteio por campanha da Fase 3.
2. **`olimpo.core.fleet`** — rotação automática do pool de contas coletoras (dívida herdada das Fases 3 e 4): saúde por coletora, aposentadoria automática, reposição a partir de um pool de reserva.
3. **Particionamento de `events` e `post_metrics`** — partição mensal por `created_at`/`collected_at` e política de retenção (detalhe completo por 90 dias, agregado depois). `events` cresce mais rápido que tudo.
4. **Índices de leitura analítica** — views materializadas para os agregados mais consultados, atualizadas em `maintenance`.

### 3.2 Analytics

5. **`olimpo.analytics.warehouse`** — views materializadas cruzando `posts` × `post_metrics` × `content_items` × `accounts` por nicho, hora do dia, dia da semana, formato (`content_items.kind`), `source_strategy`, arco narrativo e trend adotada.
6. **`olimpo.analytics.benchmarks`** — baseline por nicho e por arquétipo. Uma conta só é "ruim" contra o baseline do seu nicho, não contra a média geral.
7. **Alertas de anomalia** — queda súbita de alcance (sinal de shadowban), engajamento zerado, taxa de verificação de task caindo, custo de LLM fora da curva. Tudo em `guardrail_violations` com `severity='warn'` e alerta no Grafana.
8. **Painel — aba Analytics** — comparação entre contas, evolução por arco, e o custo por post publicado (LLM + geração + infra).

### 3.3 A/B testing conduzido pelos agentes

9. **`olimpo.experiments.model`** — experimento como entidade: hipótese, variantes, métrica primária, tamanho mínimo de amostra, janela e critério de parada. Persistido em `campaigns` com `kind='mixed'` e `metadata` de experimento, ou em tabela própria via migração `0002_experiments.sql`.
10. **Atribuição de variante** — cada `content_item` participante carrega a variante em `metadata`; a alocação é feita no `PLAN`, não no momento da publicação, para não confundir horário com variante.
11. **Análise e decisão** — job em `maintenance` avalia o resultado quando o n mínimo é atingido e escreve a conclusão como `memories(kind='semantic')` de alta importância na conta (e no baseline do nicho, quando generalizável). É assim que o resultado volta para o `PLAN` — mesmo mecanismo dos insights da Fase 2.
12. **Disciplina experimental** — um experimento ativo por conta de cada vez; nunca A/B durante campanha coordenada (confunde tudo); resultado com n abaixo do mínimo é descartado, não "sugere tendência".
13. **O que se testa:** horário de publicação, formato, estilo de hook/primeira linha, presença de CTA, uso de trend, comprimento de caption, thumbnail/capa de reels.

### 3.4 Onboarding de conta nova em < 1 dia

14. **`olimpo.onboarding.wizard`** — fluxo no painel: escolher arquétipo → gerar rascunho de Persona Card com LLM a partir de nicho e referências → revisar → criar persona na máquina de influencers (ou linha editorial, ou curadoria) → vincular `device_binding` do android-manager → definir `cadence`, `active_hours`, `timezone` → criar o primeiro `story_arc` (ou os `monitors`, ou as `sources` longas) → agendar o plano de aquecimento.
15. **`olimpo.onboarding.warmup`** — plano de aquecimento como template versionado: N dias em `status='warming'` só consumindo (`watch_reels`, `view_stories`, `like` esparso), com rampa até a cadência-alvo. Diferente por arquétipo.
16. **Checklist de prontidão** — `GET /panel/accounts/{id}/readiness` devolve o que falta para a conta sair de `draft` (persona válida, device vinculado, guardrails configurados, aquecimento cumprido). Conta não vira `active` com item pendente.
17. **Templates por arquétipo** — pacotes prontos de Persona Card, `cadence`, `trigger_rules` e guardrails que o wizard usa como ponto de partida.

### 3.5 Banco de conteúdo reaproveitável

18. **`olimpo.content.library`** — catálogo de `content_items` publicados com performance, embedding do conteúdo e metadados de reuso (`metadata.reuse`): quantas vezes reaproveitado, por quais contas, com que resultado.
19. **Regras de reuso** — nunca a mesma conta em menos de 90 dias; nunca duas contas do panteão no mesmo intervalo de 30 dias; reuso sempre com nova caption gerada na voz da conta que republica (texto único continua sendo regra de ouro); `rights_status` reavaliado no momento do reuso, nunca herdado sem checagem.
20. **Sugestão no PLAN** — quando o estoque tem um item de alta performance elegível, o `PLAN` pode escolhê-lo em vez de produzir do zero, registrando a escolha em `day_plans.reasoning`.

### 3.6 Monetização

21. **Publipost com disclosure** — `campaigns` com `client_id` e `kind='content'`; disclosure obrigatório (`#publi`/marcação paga) verificado no QA, com guardrail bloqueante. Marca vetada por persona configurável no Persona Card.
22. **Afiliados** — link com rastreio em `content_items.metadata`, conversão importada e cruzada em `olimpo.analytics`.
23. **Clipagem como serviço** — pacote comercial sobre a Fase 5: escopo em `clients.contract`, relatório periódico automático para o papel `client`, SLA de volume medido contra o entregue.
24. **Custo por conta** — dashboard de margem: `llm_calls.cost_usd` + custo de geração de mídia + rateio de infra por conta e por cliente.

### 3.7 Expansão de plataforma

25. **TikTok** — `platform='tiktok'` já existe no enum; depende do orchestrator suportar as ações. O core muda pouco: novo mapeamento de `task_type` e trends por plataforma.
26. **WhatsApp** — `platform='whatsapp'` para distribuição e atendimento, com guardrails próprios (mensagem não solicitada é território minado). Só entra com política escrita antes.

---

## 4. Fora de escopo

- Reescrever o core. A arquitetura das Fases 0–5 é a arquitetura. Mudança estrutural exige ADR e justificativa por métrica.
- Novos arquétipos além dos três, até que os existentes estejam com margem positiva e operação estável.
- L3 para `gossip` (nunca) e para `clipper` (teto L2). Reconfirmado.
- Vender o Olimpo como produto SaaS de autoatendimento. Multi-tenant aqui é para operação assistida.
- Automação de qualquer etapa jurídica: aprovação de contrato, resposta a notificação extrajudicial, negociação de licença.
- Publicidade de nichos vedados (saúde, apostas, crédito, política) em publipost.
- Aumentar a frota antes do agendador global entregue e validado.

---

## 5. Critérios de saída

Não há saída — há **metas de regime permanente**, medidas por ciclo de 2 semanas. Um ciclo só é considerado bom se todos os invariantes seguem valendo e as metas da frente escolhida foram batidas.

**Invariantes permanentes (checados diariamente por job em `maintenance`, quebra = alerta)**

- [ ] `SELECT count(*) FROM accounts WHERE archetype='gossip' AND autonomy IN ('L2','L3')` = `0`.
- [ ] `SELECT count(*) FROM accounts WHERE archetype='clipper' AND autonomy='L3'` = `0`.
- [ ] `SELECT count(*) FROM content_items WHERE status='published' AND rights_status='unknown'` = `0`.
- [ ] `SELECT count(*) FROM content_items WHERE status='published' AND archetype_of(account_id)='gossip' AND claim_status='unverified'` = `0`.
- [ ] `SELECT count(*) FROM interactions WHERE sensitive AND replied_by='agent'` = `0`.
- [ ] Toda campanha em `running` tem `spread_policy` não-vazia e aprovação registrada.
- [ ] Zero conta com ações acima de `ACCOUNT_MAX_ACTIONS_PER_HOUR` na última janela.

**Metas de regime**

| Meta | Alvo | Medição |
|---|---|---|
| Onboarding de conta nova | < 1 dia útil, do wizard ao início do aquecimento | Tempo entre `accounts.created_at` e `warmup_started_at`, p95 |
| Contas perdidas | 0 por trimestre | `SELECT count(*) FROM accounts WHERE status='lost'` por janela |
| Taxa de verificação de task | ≥ 97% | `verified / (verified + failed)` em 30 dias |
| Disponibilidade da API | ≥ 99% mensal | Prometheus |
| Custo de LLM por post publicado | Estável ou em queda ciclo a ciclo | `llm_calls.cost_usd` / posts |
| Experimentos concluídos | ≥ 2 por ciclo, com decisão registrada | Experimentos com conclusão em `memories(kind='semantic')` |
| Reuso de conteúdo | ≥ 10% dos posts vindos da biblioteca, sem violar as regras de reuso | `metadata.reuse` |
| Incidentes jurídicos (fofoca) | 0 pedidos de remoção procedentes | Registro em `guardrail_violations` + runbook |
| DMCA (clipagem) | 0 strikes | Idem |
| Margem por conta | Positiva em todas as contas ativas há > 60 dias | Dashboard de custo |

**Portões para aumentar a frota** — só se pode passar de N para N+5 contas quando: agendador global ativo há ≥ 30 dias sem violação de limite; taxa de verificação ≥ 97%; zero conta perdida no trimestre; custo por post dentro do orçamento; runbooks de incidente exercitados nos últimos 90 dias.

---

## 6. Riscos da fase

| Risco | Impacto | Mitigação |
|---|---|---|
| **Escalar antes de estabilizar** | Perda de contas em bloco, dano difícil de reverter | Portões numéricos para aumentar a frota; agendador global obrigatório antes de qualquer expansão |
| A/B com n insuficiente virando "aprendizado" | O sistema passa a otimizar para ruído — e a memória semântica torna o erro permanente | Tamanho mínimo de amostra definido antes do experimento; resultado abaixo do n é descartado; conclusão sempre carrega o n em `metadata` |
| Experimento concorrendo com campanha | Confundimento total dos resultados | Um experimento ativo por conta; proibido durante campanha coordenada; verificado pelo agendador |
| Reuso de conteúdo criando padrão detectável | O mesmo criativo circulando entre contas da casa é assinatura de farm | Janelas mínimas de reuso (90 d por conta, 30 d entre contas); caption sempre nova; nunca duas contas do mesmo nicho |
| Monetização pressionando o guardrail | Cliente pede o que a política proíbe | Guardrail bloqueia antes da negociação; nicho vedado é lista, não caso a caso; disclosure é bloqueante no QA |
| `events` e `post_metrics` engolindo o banco | Degradação geral de performance | Particionamento mensal + retenção + agregados materializados, entregues na frente 0 |
| Complacência operacional | Runbook nunca exercitado falha quando é preciso | Simulado trimestral obrigatório: takedown, DMCA, conta banida, kill switch global |
| Dependência de um único fornecedor de LLM externo | Custo ou disponibilidade fora de controle | LiteLLM já abstrai; manter ≥ 2 provedores configurados e testados no fallback |
| Mudança de plataforma quebrando o orchestrator | Toda a operação para | O core não fala com a plataforma — o dano fica contido no orchestrator; manter dry-run funcional para validar mudanças sem risco |
| Perda de conhecimento (só uma pessoa sabe operar) | Risco de continuidade | Runbooks por incidente, ADRs por decisão, e este conjunto de documentos de fase mantidos vivos |

---

## 7. O que fica dormente

A esta altura, **todas as colunas e tipos de `db/migrations/0001_initial.sql` estão em uso**. O schema desenhado na Fase 0 chegou ao fim do roadmap sem uma migração dolorosa — que era exatamente a aposta registrada na seção 5 do `PLANO.md`.

O que segue deliberadamente adormecido:

| Artefato | Estado | Quando acorda |
|---|---|---|
| `autonomy_level='L3'` para `gossip` e `clipper` | Bloqueado por arquitetura | **Nunca** |
| `platform='whatsapp'` | Enum em uso apenas se a política de mensagens for escrita e aprovada | Sob demanda, com ADR |
| Arquétipos além dos três | `archetype` é enum — um quarto exige migração | Quando um caso de uso real justificar |
| Portal self-service de cliente | Não construído | Se a clipagem como serviço escalar além do atendimento assistido |
| Modelo treinado para detecção de momento | Heurística + LLM continua | Quando houver dados de retenção suficientes para treinar |
| Tiering de storage (frio) | Só retenção | Quando o custo de storage passar o de LLM |

---

## 8. Handoff — para o próximo ciclo, sempre

A Fase 6 não entrega para uma fase seguinte; ela entrega para **si mesma**, a cada ciclo. O handoff é o ritual de fechamento de ciclo.

**Estado do sistema em regime**

- Três arquétipos, N contas, com onboarding padronizado e aquecimento por template.
- Agendador global governando o calendário inteiro da frota — a peça que faltava para escalar sem virar padrão detectável.
- Ciclo fechado de aprendizado: métrica → experimento → conclusão → memória semântica → `PLAN`.
- Biblioteca de conteúdo com regras de reuso que não comprometem o anti-detecção.
- Custo e margem visíveis por conta e por cliente.

**Decisões estruturais que não se reabrem sem ADR**

| Decisão | Origem |
|---|---|
| O core nunca fala com a plataforma | ADR-0001 (Fase 0) |
| O agendamento é o Postgres | ADR-0004 (Fase 0) |
| O loop é o mesmo para todo arquétipo; só o `ContentSource` varia | `PLANO.md` §4, provado nas Fases 4 e 5 |
| Teto de autonomia por arquétipo | ADR-0012 (Fases 4 e 5) |
| `unknown`/`unverified` bloqueiam publicação | ADR-0012 e ADR-0014 |
| `spread_policy` obrigatória em toda campanha | `docs/06-campanhas.md` (Fase 3) |
| Conclusão de experimento vira memória semântica, não regra hard-coded | ADR-0009 (Fase 2), estendido nesta fase |

**Ritual de fechamento de ciclo (a cada 2 semanas)**

1. Rodar o checklist de invariantes permanentes. Qualquer quebra vira o item nº 1 do ciclo seguinte.
2. Revisar as metas de regime; registrar as que saíram do alvo e a causa.
3. Fechar os experimentos com n atingido; descartar os demais explicitamente.
4. Revisar `sources.reliability` (fofoca) e a whitelist de criadores (clipagem).
5. Revisar a lista de dívidas técnicas abertas — quitar ao menos uma por ciclo.
6. Atualizar `CHANGELOG.md` e este documento com o que mudou de fato.

**Dívidas técnicas ainda abertas ao entrar no regime permanente**

1. `sources.reliability` continua manual, sem aprendizado a partir do histórico de acertos e erros da fonte (herdada da Fase 4).
2. Não há resolução de entidade na correlação de histórias entre fontes (Fase 4).
3. `clients.contract` é jsonb sem schema validado (Fase 5).
4. Sem tiering de storage (Fase 5).
5. Detector de momentos sem modelo treinado (Fase 5) — o loop de retenção desta fase é o primeiro passo para quitá-la.
6. A similaridade de texto usada no anti-repetição é proxy do modelo local, não do critério da plataforma (Fase 3).

Cada ciclo deve quitar pelo menos uma. Dívida que sobrevive a seis ciclos vira ADR de "aceita permanentemente" ou entra no escopo do ciclo seguinte — não fica em limbo.
