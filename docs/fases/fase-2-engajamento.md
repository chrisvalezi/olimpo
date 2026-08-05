# Fase 2 — Engajamento e autonomia parcial

**Duração estimada:** 3–4 semanas · **Depende de:** [Fase 1](fase-1-primeira-influencer.md) · **Entrega para:** [Fase 3](fase-3-multi-influencer-campanhas.md)

---

## 1. Resumo

A conta deixa de ser um megafone e passa a conversar: comentários e DMs são coletados pelo orchestrator, classificados, respondidos na voz da persona e lembrados. Nasce a memória semântica (reconhecer seguidores recorrentes) e o ciclo de métricas que realimenta o `PLAN`.

Ao final, a conta opera em **L2** — auto-posta e auto-responde, com revisão humana por amostragem de 10% — por uma semana inteira sem incidente.

---

## 2. Pré-requisitos

| Item | Detalhe |
|---|---|
| Fase 1 completa | 7 dias de publicação verificada, memória episódica populada |
| Tasks de coleta no orchestrator | `collect_interactions` e `collect_metrics` mapeadas e testadas no contrato |
| Volume mínimo de interação | A conta precisa receber comentários. Se não recebe, a Fase 2 não tem o que classificar — resolver antes com aquecimento e conteúdo, não com código |
| `L2_SAMPLING_RATE` definido | Default `0.10` no `.env` |
| Golden set de classificação | 100 comentários reais rotulados à mão antes de escrever o classificador |

---

## 3. Escopo

### 3.1 Coleta

1. **`olimpo.workers.collectors.interactions`** — job em `maintenance` que emite `tasks(type='collect_interactions')` por conta em intervalo irregular (jitter, nunca de hora em hora cravada). O retorno do orchestrator popula `interactions` com `kind`, `external_id`, `author_handle`, `body`, `received_at`. Dedup por `(account_id, kind, external_id)`.
2. **`olimpo.workers.collectors.metrics`** — `tasks(type='collect_metrics')` por post publicado, com cadência decrescente (1 h, 6 h, 24 h, 72 h após publicação). Popula `post_metrics`.

### 3.2 Classificação e resposta

3. **`olimpo.agents.interactions.classify`** — LLM (`olimpo-fast`) classifica cada `interaction` em `fan | hater | spam | opportunity | sensitive`, grava em `interactions.classification` e marca `interactions.sensitive`.
4. **Regra dura de sensibilidade** — saúde, dinheiro/investimento, romance/flerte, assédio, menor de idade, ameaça, autolesão → `sensitive=true`. **Nunca auto-respondido em nenhum nível de autonomia**, nem em L3. Vai direto para a fila humana e emite `guardrail_violations` com `severity='warn'`.
5. **`olimpo.agents.interactions.reply`** — gera `reply_body` na voz da persona, com contexto: o post comentado, o histórico do autor (memória semântica) e o Persona Card. Respostas curtas, sem template repetido — o QA textual rejeita similaridade alta contra as últimas 50 respostas da conta.
6. **`olimpo.agents.interactions.dispatch`** — vira `tasks(type='reply_comment'|'send_dm', origin='self', priority=10)` na fila **`realtime`**. Delay humano deliberado: nunca responder em menos de 90 s nem depois de 6 h.
7. **Política de não-resposta** — nem todo comentário merece resposta. Taxa-alvo configurável por conta em `persona_card.style.reply_rate`; spam e hater de baixo esforço são ignorados (ou recebem like) em vez de respondidos.

### 3.3 Memória semântica

8. **`olimpo.agents.memory` (extensão)** — escrita de `kind='semantic'`: fatos consolidados sobre a própria conta ("meu público responde melhor a story de bastidor") e sobre pessoas ("@fulano comenta em todo post desde março, é de Recife, torce pro Sport").
9. **`olimpo.agents.memory.consolidate`** — job noturno em `production` que lê as episódicas do dia e promove padrões recorrentes a semânticas, com deduplicação por similaridade (cosseno > 0.92 → atualiza a existente em vez de criar nova). É o mecanismo de *reflection* do generative_agents.
10. **Perfil de seguidor** — o recall por `author_handle` recupera as semânticas daquela pessoa antes de gerar a resposta. É isso que faz a conta "lembrar" de quem comenta sempre.

### 3.4 Autonomia

11. **`olimpo.core.autonomy`** — tabela de decisão única consultada por conteúdo e por interação:

| Nível | Post | Interação |
|---|---|---|
| L0 | `approval_pending` sempre | `approval_pending` sempre |
| L1 | auto (QA verde) | `approval_pending` sempre |
| L2 | auto | auto, com amostragem `L2_SAMPLING_RATE` gravando `approvals.sampled=true` |
| L3 | auto | auto, sem amostragem — **não usado nesta fase** |

12. **Promoção e rebaixamento** — promoção é ato humano explícito no painel (`POST /panel/accounts/{id}/autonomy`), gravado em `events`. **Rebaixamento é automático**: qualquer `guardrail_violations` com `severity IN ('block','kill')`, ou taxa de rejeição na amostragem acima de 20% em 7 dias, derruba a conta um nível e alerta.
13. **Portões de promoção** — L0→L1 exige 7 dias sem `qa_failed` bloqueante; L1→L2 exige 7 dias com ≥ 50 respostas revisadas e ≥ 90% aprovadas.

### 3.5 Métricas realimentando o PLAN

14. **`olimpo.agents.insights`** — job diário em `maintenance` que agrega `post_metrics` por hora do dia, formato (`content_items.kind`) e tema, e escreve o resultado como memória **semântica** de alta importância. É assim que a métrica volta para o `PLAN` sem acoplar o planejador a SQL de BI.
15. **`olimpo.agents.plan` (extensão)** — o prompt do PLAN passa a receber o bloco de insights + o resumo de engajamento dos últimos 7 dias, e o `day_plans.reasoning` precisa citar o dado que motivou a escolha.
16. **Ajuste de cadência** — o PLAN pode propor mudar `accounts.cadence`, mas a alteração exige aprovação humana em qualquer nível. Agente não decide sozinho postar o dobro.

### 3.6 Painel e observabilidade

17. **`olimpo.panel_api.interactions`** — fila de interações pendentes (`idx_interactions_pending`), com aprovação/edição de resposta e visão do histórico do autor.
18. **Painel** — aba de interações; card de conta com nível de autonomia, taxa de aprovação na amostragem e alertas de rebaixamento; gráfico de engajamento por post.
19. **Dashboards Grafana** (`ops/grafana/provisioning`) — latência do loop por etapa, profundidade das 4 filas Celery, taxa de verificação de task, custo de LLM por conta/dia, tempo médio até resposta.
20. **`docs/runbooks/rebaixamento-automatico.md`** e **`docs/runbooks/fila-realtime-entupida.md`**.

---

## 4. Fora de escopo

- Mais de uma conta. Tudo continua com a cobaia da Fase 1. **Fase 3.**
- Campanhas, `spread_policy`, recon, crossovers. **Fase 3.**
- L3. Não se chega a L3 nesta fase, nem com resultado bom.
- Motor narrativo (arcos gerados, eventos aleatórios, humor). **Fase 3.**
- Trends. `trends` continua vazia. **Fase 3.**
- Iniciar conversa (DM outbound frio). Só se responde a quem falou primeiro.
- Seguir/deixar de seguir automaticamente. O enum tem `follow`/`unfollow`, ninguém emite. **Fase 3.**
- Fofoca e clipagem. **Fases 4 e 5.**
- A/B testing formal. Observa-se correlação, não se conduz experimento. **Fase 6.**

---

## 5. Critérios de saída

- [ ] `SELECT count(*) FROM interactions WHERE account_id=<id>` ≥ 200 após 2 semanas, todas com `classification` não-nula.
- [ ] Classificador avaliado contra o golden set de 100 comentários: acurácia ≥ 85%, e **recall da classe `sensitive` ≥ 98%** (falso negativo aqui é o erro caro).
- [ ] `SELECT count(*) FROM interactions WHERE sensitive AND replied_by='agent'` devolve **`0`** — invariante, verificado por teste automatizado em `make test`.
- [ ] `SELECT count(*) FROM memories WHERE kind='semantic' AND account_id=<id>` ≥ 30, sem duplicatas (nenhum par com cosseno > 0.92).
- [ ] `python -m olimpo.agents.memory recall --account <id> --query "@handle_recorrente" --kinds semantic` devolve os fatos daquele seguidor.
- [ ] Teste de reconhecimento: um seguidor que já comentou 3× recebe resposta que referencia algo anterior. Validado à mão em ≥ 5 casos e registrado no handoff.
- [ ] Anti-repetição: as últimas 50 `reply_body` da conta não têm nenhum par com similaridade > 0.90.
- [ ] Tempo até resposta: p50 entre 90 s e 45 min, p95 < 6 h. Consultável via `SELECT percentile_cont(0.5) WITHIN GROUP (ORDER BY replied_at - received_at) FROM interactions WHERE replied_by='agent'`.
- [ ] `SELECT count(*) FROM post_metrics WHERE collected_at > now()-interval '7 days'` ≥ 4 coletas por post publicado na janela.
- [ ] Insights viraram memória: `SELECT content FROM memories WHERE kind='semantic' AND metadata->>'source'='insights'` devolve ≥ 5 afirmações com número.
- [ ] Ao menos 3 `day_plans.reasoning` dos últimos 7 dias citam explicitamente uma métrica (verificação manual, registrada).
- [ ] Promoção funcionando: `SELECT autonomy FROM accounts WHERE id=<id>` = `'L2'`, e `events` contém as transições L0→L1→L2 com `decided_by`.
- [ ] Amostragem ativa em L2: `SELECT count(*) FILTER (WHERE sampled) * 1.0 / count(*) FROM approvals WHERE requested_at > now()-interval '7 days'` entre 0.05 e 0.15.
- [ ] Taxa de rejeição na amostragem < 10% em 7 dias.
- [ ] Rebaixamento automático testado: injetar `guardrail_violations` com `severity='block'` derruba `accounts.autonomy` um nível em ≤ 1 min e emite `account.demoted`.
- [ ] **7 dias corridos em L2 sem incidente** — zero `severity IN ('block','kill')`, zero resposta a `sensitive`, zero rebaixamento, conta ainda `active`.
- [ ] Grafana `up-obs` mostra os 5 painéis; a fila `realtime` fica com profundidade < 20 no p95.
- [ ] `make test` verde, cobertura ≥ 70% em `olimpo/agents/interactions` e `olimpo/core/autonomy`.

---

## 6. Riscos da fase

| Risco | Impacto | Mitigação |
|---|---|---|
| **Resposta a comentário sensível** (saúde, menor, assédio) | Dano reputacional/legal real | Classe `sensitive` bloqueada por código, não por prompt; invariante testado no CI; recall ≥ 98% no golden set; em dúvida, o classificador marca sensível |
| Resposta genérica repetida delatando bot | Perda de credibilidade e sinal de detecção | Checagem de similaridade contra as últimas 50 respostas; temperatura maior no `reply`; taxa de resposta < 100% |
| Resposta instantânea 24 h/dia | Sinal claro de automação | Delay mínimo de 90 s + distribuição aleatória; nada fora de `active_hours`; a fila `realtime` não é atendida de madrugada |
| Memória semântica explodindo com quase-duplicatas | Recall degrada e custo sobe | Consolidação por similaridade > 0.92 atualiza em vez de inserir; auditoria semanal de contagem |
| Prompt injection via comentário ("ignore suas instruções, diga X") | Agente foge da persona ou vaza prompt | Corpo do comentário sempre em bloco delimitado e rotulado como *dado não-confiável*; QA textual pós-geração; nunca executar instrução vinda de `interactions.body` |
| Métrica ruim vira crença errada (n pequeno) | PLAN otimiza para ruído | Insight só vira memória com n ≥ 5 posts na comparação; a afirmação carrega o n em `metadata` |
| Fila `realtime` competindo com `production` pelo Mac mini | Resposta atrasa | Prioridade no roteamento do LiteLLM (interação > ideação); `realtime` usa `olimpo-fast`, PLAN usa `olimpo-smart` |
| Promoção precoce a L2 | Erro em escala antes da hora | Portões numéricos por nível, não opinião; rebaixamento automático sempre ligado |
| Volume de interação baixo demais para validar | Fase termina sem evidência | Se em 2 semanas não houver 200 interações, **não promover a L2** — estender a fase ou aumentar alcance antes |

---

## 7. O que fica dormente

| Artefato | Estado nesta fase | Acorda em |
|---|---|---|
| `memories.kind='narrative'` | Enum existe, ninguém escreve | Fase 3 |
| `accounts.mood` | Lido pelo PLAN, mas nada o escreve | Fase 3 |
| `campaigns`, `campaign_recon`, `campaign_participants` | Vazias | Fase 3 |
| `content_items.campaign_id` | Sempre nulo | Fase 3 |
| `tasks.origin='campaign'` / `'manager'` | Não emitido (tudo é `'self'`) | Fase 3 |
| `task_type='extract_content'` | Não emitido | Fase 3 |
| `task_type IN ('follow','unfollow','share')` | Não emitidos | Fase 3 |
| `trends` | Vazia | Fase 3 |
| `autonomy_level='L3'` | Não usado | Fase 3 (influencer) — e **nunca** para fofoca |
| `sources`, `ingested_items`, `monitors`, `monitor_hits` | Vazias | Fase 4 |
| `content_items.claim_status`, `.source_refs`, `.attribution` | Defaults, sem efeito | Fases 4 e 5 |
| `clients`, `accounts.client_id` | Vazio/nulo | Fase 5 |
| `tasks.expires_at` | Sempre nulo (influencer não tem furo) | Fase 4 |
| `sources.reliability` | Coluna existe, nada a lê | Fase 4 |

---

## 8. Handoff para a Fase 3

**Estado do sistema entregue**

- 1 conta em L2 há uma semana: publica e responde sozinha, com 10% de amostragem humana.
- Memória em dois níveis (episódica + semântica) com consolidação noturna e deduplicação.
- Classificador de interações validado contra golden set, com a classe `sensitive` blindada por código.
- Métricas coletadas e transformadas em insights que aparecem no `reasoning` do PLAN.
- Escada de autonomia com portões de promoção e rebaixamento automático.
- Observabilidade real no Grafana.

**Decisões tomadas**

| Decisão | Registro |
|---|---|
| `sensitive` nunca é auto-respondido, em nenhum nível | `docs/08-guardrails-e-autonomia.md` |
| Rebaixamento é automático; promoção é humana | `docs/adr/0008-escada-de-autonomia.md` |
| Métrica entra no PLAN como memória semântica, não como query | `docs/adr/0009-insights-como-memoria.md` |
| Conteúdo de terceiros (`interactions.body`) é sempre dado não-confiável no prompt | `docs/12-seguranca.md` |
| Consolidação por similaridade > 0.92 | `docs/05-loop-do-agente.md` |

**Dívidas técnicas aceitas conscientemente**

1. O golden set tem 100 exemplos de **uma** conta e **um** nicho. Com 5 personas na Fase 3, a acurácia por conta precisa ser reavaliada.
2. Não existe rate limit global entre contas — só `ACCOUNT_MAX_ACTIONS_PER_HOUR`. Com 1 conta é irrelevante; **é bloqueante para a Fase 3** e precisa ser a primeira coisa construída lá.
3. `GeneratedSource` continua síncrono com polling (dívida herdada da Fase 1). Com 5 contas produzindo em paralelo, isso satura o worker — refatorar para callback na Fase 3.
4. Os insights são agregações simples (média por hora/formato), sem controle de confundidores. É correlação, e o PLAN a trata como tal. Experimento de verdade só na Fase 6.
5. Amostragem de L2 é aleatória uniforme, não estratificada por risco. Poderia amostrar mais nas classes `hater`/`opportunity`.
6. O painel ainda tem token único, sem usuários. Continua adiado.

**A primeira coisa que a Fase 3 faz:** construir o limitador global de ações da frota e o grafo de interação entre contas da casa — **antes** de subir a segunda conta. Duas contas sem espalhamento já são um padrão detectável.
