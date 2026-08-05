# Fase 3 — Multi-influencer e campanhas coordenadas

**Duração estimada:** 4–6 semanas · **Depende de:** [Fase 2](fase-2-engajamento.md) · **Entrega para:** [Fase 4](fase-4-fofoca.md)

---

## 1. Resumo

De uma conta para um panteão: 3–5 influencers em nichos distintos, cada uma com motor narrativo próprio (arcos gerados, eventos aleatórios, humor dinâmico), coordenadas por um motor de campanhas com `spread_policy` obrigatória. Campanha de engajamento passa a exigir **RECON** — uma conta Android abre o conteúdo alvo, a LLM multimodal analisa, e só então o sistema decide quem participa e o que cada persona diz.

É a fase em que o risco muda de natureza: sai da qualidade individual e entra na **detecção de padrão coordenado**.

---

## 2. Pré-requisitos

| Item | Detalhe |
|---|---|
| Fase 2 completa | 7 dias em L2 sem incidente |
| Limitador global de frota | Dívida nº 2 da Fase 2 — **construir antes de subir a segunda conta** |
| 2–4 contas novas criadas e logadas | No android-manager, com `device_binding` distinto por conta |
| 2–4 personas na máquina de influencers | Nichos deliberadamente distintos (fitness, gamer, lifestyle…), seeds e face refs próprias |
| Aquecimento das contas novas | 7+ dias em `status='warming'` antes de qualquer publicação |
| Modelo multimodal disponível | Alias `olimpo-vision` no LiteLLM — pode ser fallback externo, o RECON não é de alto volume |
| `extract_content` no orchestrator | Abrir um post/perfil e devolver caption, autor, contadores, top comments e screenshot |

---

## 3. Escopo

### 3.1 Segurança de frota (pré-requisito interno, faça primeiro)

1. **`olimpo.core.ratelimit`** — limitador em Redis em três níveis: por conta (`ACCOUNT_MAX_ACTIONS_PER_HOUR`), por device (o `device_binding` pode ser compartilhado) e **global de frota** (ações/hora do sistema inteiro). O `dispatcher_tick` consulta os três antes de despachar.
2. **`olimpo.core.social_graph`** — teto de interações entre contas da casa: quantas contas do panteão podem curtir/comentar no post de outra, com que frequência, e nunca de forma recíproca imediata. Violação → task descartada com evento `social_graph.blocked`.

### 3.2 Motor narrativo completo

3. **`olimpo.agents.narrative.arcs`** — geração de arcos: dado o Persona Card, o histórico e o nicho, a LLM propõe um arco (`story_arcs.title`, `.summary`, `.starts_at`, `.ends_at`, `.state`) com beats semanais. Arco vencido é encerrado (`active=false`) e sucedido automaticamente; sobreposição máxima de 2 arcos ativos por conta.
4. **`olimpo.agents.narrative.events`** — eventos aleatórios com pesos por persona ("o voo atrasou", "achou uma cafeteria nova", "machucou o joelho"). Amostrados no PLAN, gravados como `memories(kind='narrative')` e ligados ao `arc_id`. Frequência e severidade parametrizadas no Persona Card.
5. **`olimpo.agents.narrative.mood`** — `accounts.mood` como estado com inércia: `{energy, valence, stress}` evoluindo por eventos, métricas do dia anterior e regressão à média da persona. O humor modula tom, cadência (dentro do teto de `cadence`) e `reply_rate`.
6. **Continuidade** — o PLAN passa a receber `memories(kind='narrative')` do arco ativo, para que a história tenha consequência: quem machucou o joelho não posta corrida na semana seguinte. Regra de consistência checada no QA.

### 3.3 Trend engine v1

7. **`olimpo.trends.collector`** — job em `maintenance` que emite `extract_content` para páginas de descoberta/áudios em alta por plataforma e nicho, populando `trends` (`platform`, `kind`, `ref`, `label`, `score`, `window_start/end`).
8. **`olimpo.trends.rank`** — decaimento do `score` e fechamento de janela (`window_end`); `idx_trends_live` serve as tendências ativas.
9. **Consumo** — o PLAN recebe as top-N trends do nicho da conta e **pode** adotá-las; adoção é opcional e registrada em `day_plans.reasoning`. Nunca todas as contas adotam a mesma trend — a elegibilidade passa pela `spread_policy`.

### 3.4 Motor de campanhas

10. **`olimpo.campaigns.model`** — criação de campanha via `POST /panel/campaigns` com `brief`, `kind` (`content|engagement|mixed`), `window_start/end`, `account_filter`, `brand_refs`, `audio_ref` e **`spread_policy` obrigatória** (a coluna é `NOT NULL` no schema; a API rejeita `{}`).
11. **`olimpo.campaigns.recon`** — para `kind IN ('engagement','mixed')` com `target_url`/`target_handle`:
    - cria `tasks(type='extract_content', origin='campaign')` atribuída a uma conta **coletora** de baixo valor (nunca a persona principal);
    - o retorno popula `campaign_recon.raw_capture`, `.media_refs`, `.top_comments`, `.captured_at`;
    - a LLM multimodal (`olimpo-vision`) preenche `.analysis`, `.summary`, `.sentiment`, `.safety_verdict`, `.safety_reasons`, `.suggested_angles`, `.analyzed_at`, `.llm_call_id`;
    - `safety_verdict='blocked'` → campanha vai para `status='aborted'`, nenhuma conta participa;
    - **máquina de estados obrigatória:** `draft → recon_pending → recon_done → planning → approval_pending → approved → running → completed`. Não existe caminho de `draft` para `planning` numa campanha de engajamento.
12. **`olimpo.campaigns.spread`** — implementa a `spread_policy`:
    - **participação parcial** — amostra contas elegíveis por relevância de nicho + aleatoriedade, teto em `CAMPAIGN_MAX_PARTICIPATION`; as não sorteadas viram `campaign_participants` com `skipped=true` e `skip_reason` (auditável);
    - **jitter temporal** — `slot_at` sorteado na janela respeitando `CAMPAIGN_JITTER_MIN_MINUTES`/`MAX`, sem dois slots na mesma faixa de 25 min, e sempre dentro da `active_hours` de cada conta;
    - **cadência natural** — a campanha não pode fazer a conta ultrapassar `accounts.cadence`; se já tem post do dia, ou a campanha substitui a intenção do PLAN ou a conta é pulada;
    - **texto sempre único** — cada `campaign_participants.local_adaptation` e cada `actions[].text` gerados individualmente na voz da persona; verificação de similaridade cruzada entre participantes (nenhum par > 0.85), com regeneração automática de quem colidir.
13. **`olimpo.campaigns.dispatch`** — materializa `campaign_participants.actions` em `tasks` (`origin='campaign'`, `campaign_id` preenchido) e, para campanhas de conteúdo, em `content_items` com `campaign_id`.
14. **Aprovação de campanha** — campanha inteira passa por `approvals` (`subject_type='campaign'`) independentemente do nível de autonomia das contas. Contas podem ser L2/L3; campanha é sempre revisada antes de disparar.
15. **Kill switch de campanha** — `POST /panel/campaigns/{id}/abort` cancela todas as tasks `pending`/`scheduled` da campanha e move o status para `aborted`.

### 3.5 Crossovers

16. **`olimpo.campaigns.crossover`** — interação deliberada entre personas da casa (uma comenta no post da outra, aparecem juntas em um arco). Passa obrigatoriamente pelo `social_graph`: teto de crossovers por semana, nunca recíproco no mesmo dia, nunca envolvendo todas as contas do panteão.

### 3.6 Escala operacional

17. **`GeneratedSource` assíncrono** — quitando a dívida das Fases 1–2: a máquina de influencers passa a chamar de volta (ou é consultada por job dedicado em `production`), liberando o worker durante a geração.
18. **`olimpo.panel_api.campaigns`** — CRUD de campanha, visualização do recon (screenshot + análise + ângulos sugeridos), tabela de participantes com slot e adaptação local, botão de abort.
19. **Painel** — visão de panteão (todas as contas, autonomia, saúde, próxima ação), timeline de campanha, mapa de espalhamento (slots no tempo) para inspeção visual do jitter.
20. **`docs/06-campanhas.md`** — documento vivo com o fluxo, a `spread_policy` e o contrato do recon.
21. **`docs/runbooks/campanha-abortada.md`** e **`docs/runbooks/conta-banida.md`**.

---

## 4. Fora de escopo

- Fofoca, monitores 24/7, ingestão, `CuratedSource`. **Fase 4.**
- Clipagem, `media-forge`, `DerivedSource`. **Fase 5.**
- Multi-tenant (`clients`, `client_id`). **Fase 5.**
- Mais de 5 contas. O objetivo é validar coordenação, não escala bruta. **Fase 6.**
- A/B testing formal conduzido pelos agentes. **Fase 6.**
- Onboarding automatizado de conta nova. Ainda é manual e documentado. **Fase 6.**
- Monetização, publipost com disclosure, afiliados. **Fase 6.**
- Campanha cross-plataforma. Instagram apenas nesta fase.

---

## 5. Critérios de saída

- [ ] `SELECT count(*) FROM accounts WHERE status='active' AND archetype='influencer'` entre 3 e 5, todas em L2 ou L3.
- [ ] Cada conta tem nicho e Persona Card distintos; nenhum par de `persona_card->'style'->>'voice'` com similaridade > 0.80.
- [ ] Limitador de frota: teste de carga enfileirando 200 tasks simultâneas respeita os 3 tetos; `SELECT count(*) FROM events WHERE type='ratelimit.blocked'` > 0 e nenhuma conta ultrapassa `ACCOUNT_MAX_ACTIONS_PER_HOUR`.
- [ ] Arcos gerados: `SELECT count(*) FROM story_arcs WHERE active` = número de contas ativas, todos com `state` não-vazio; ao menos 1 arco encerrado e sucedido automaticamente durante a fase.
- [ ] Eventos narrativos: `SELECT count(*) FROM memories WHERE kind='narrative'` ≥ 5 por conta em 4 semanas.
- [ ] Humor vivo: `SELECT mood FROM accounts` não-vazio em todas; o valor de `energy` mudou ≥ 10 vezes em 30 dias por conta (série auditável em `events`).
- [ ] Consequência narrativa validada à mão em ≥ 3 casos (evento na semana N aparece no conteúdo da semana N+1), registrado no handoff.
- [ ] `SELECT count(*) FROM trends WHERE window_end IS NULL` > 0; ao menos 3 `day_plans.reasoning` citam uma trend adotada, e ao menos 1 cita uma trend **rejeitada** com justificativa.
- [ ] `POST /panel/campaigns` com `spread_policy` ausente ou `{}` devolve `422`.
- [ ] Campanha de engajamento não avança sem recon: forçar `status='planning'` sem `campaign_recon` é rejeitado pela máquina de estados (teste automatizado em `make test`).
- [ ] Recon real executado: `SELECT summary, safety_verdict, jsonb_array_length(suggested_angles) FROM campaign_recon` devolve resumo coerente com o post alvo, veredito preenchido e ≥ 3 ângulos.
- [ ] Recon bloqueante: alvo com conteúdo sensível → `safety_verdict='blocked'` e `campaigns.status='aborted'`, zero tasks criadas.
- [ ] Espalhamento verificável em uma campanha com ≥ 4 participantes:
  - nenhum par de `campaign_participants.slot_at` com diferença < `CAMPAIGN_JITTER_MIN_MINUTES`;
  - `count(*) FILTER (WHERE NOT skipped) / count(*)` ≤ `CAMPAIGN_MAX_PARTICIPATION`;
  - todo participante pulado tem `skip_reason` preenchido;
  - nenhum par de textos de ação com similaridade > 0.85 (script `scripts/check_spread.py <campaign_id>` sai `0`).
- [ ] Cadência respeitada: nenhuma conta ultrapassa `cadence->>'posts_per_day'` em dia de campanha (query de verificação no runbook).
- [ ] Aprovação de campanha obrigatória: campanha em `approved` só depois de linha em `approvals` com `decision='approved'`, mesmo com todas as contas em L3.
- [ ] Abort funciona: `POST /panel/campaigns/{id}/abort` durante `running` cancela as tasks restantes em ≤ 1 tick; `SELECT count(*) FROM tasks WHERE campaign_id=<id> AND status IN ('pending','scheduled')` = 0.
- [ ] Crossover controlado: `SELECT count(*) FROM events WHERE type='crossover.executed' AND created_at > now()-interval '7 days'` dentro do teto configurado; zero pares recíprocos no mesmo dia.
- [ ] **4 semanas** com o panteão operando: ≥ 3 campanhas executadas, zero conta perdida (`status='lost'`), zero dia sem publicação por conta ativa, sem intervenção diária.
- [ ] `GeneratedSource` assíncrono: com 5 contas produzindo em paralelo, a profundidade da fila `production` fica < 30 no p95 e nenhum worker fica bloqueado > 60 s numa chamada de geração.
- [ ] `make test` verde, cobertura ≥ 75% em `olimpo/campaigns`.

---

## 6. Riscos da fase

| Risco | Impacto | Mitigação |
|---|---|---|
| **Detecção de farm por padrão coordenado** | Perda simultânea de várias contas — o risco existencial da fase | `spread_policy` obrigatória por schema; jitter ≥ 25 min; participação parcial ≤ 60%; texto único verificado por similaridade; `social_graph` limitando interação interna; mapa de espalhamento inspecionado à mão nas 3 primeiras campanhas |
| Grafo de interação interna óbvio | Cluster fácil de identificar | Teto semanal de crossovers, sem reciprocidade imediata, nunca todas as contas; crossover é exceção, não rotina |
| Recon pela conta principal queima a persona | Conta de valor exposta em coleta | Pool de contas coletoras dedicadas e descartáveis; `campaign_recon.collector_account_id` sempre de fora do pool de personas |
| LLM multimodal alucina no recon e libera alvo perigoso | Panteão comenta em conteúdo tóxico | `safety_verdict` conservador (dúvida = `risky`); `risky` exige aprovação humana explícita; aprovação de campanha é sempre obrigatória |
| Personas convergindo para a mesma voz | Todas soam iguais; sinal de geração comum | Checagem de similaridade entre Persona Cards e entre captions cruzadas; nichos deliberadamente distintos; temperatura por persona |
| Motor narrativo produzindo arcos incoerentes | Persona perde credibilidade | Beats semanais revisados no primeiro arco de cada conta; QA de consistência contra `memories(kind='narrative')` |
| Mac minis saturando com 5 contas | PLAN e respostas atrasam | Prioridade de fila no LiteLLM; PLAN de madrugada escalonado por conta; overflow para API externa com teto de `LLM_DAILY_BUDGET_USD` |
| Campanha dobrando a cadência da conta | Padrão anômalo por conta | Regra de cadência no `spread`; a campanha substitui a intenção do dia em vez de somar |
| Complexidade do motor de campanhas travando a fase | Estouro de prazo | Entregar `kind='content'` primeiro (sem recon), depois `engagement`. Recon é a segunda metade da fase |
| Custo de LLM crescendo linear com contas | Estouro de orçamento | `llm_calls` por conta no Grafana; alerta em 80% de `LLM_DAILY_BUDGET_USD`; ideação no local, só recon/QA crítico no externo |

---

## 7. O que fica dormente

| Artefato | Estado nesta fase | Acorda em |
|---|---|---|
| `sources`, `ingested_items` | Vazias | Fase 4 |
| `monitors`, `monitor_hits`, `monitor_status` | Vazias | Fase 4 |
| `task_type='monitor_check'` | Não emitido | Fase 4 |
| `content_items.claim_status` | Sempre `'unverified'`, sem efeito | Fase 4 (vira bloqueante) |
| `content_items.source_refs` | `[]` | Fase 4 (curated) e Fase 5 (derived) |
| `content_items.attribution` | Nulo | Fase 5 |
| `content_items.rights_status` | Sempre `'own'` | Fase 5 |
| `sources.reliability` | Sem leitores | Fase 4 |
| `tasks.expires_at` | Nulo (influencer não tem furo) | Fase 4 |
| `task_status='expired'` | Nunca atingido | Fase 4 |
| `clients`, `accounts.client_id`, `campaigns.client_id` | Vazio/nulo | Fase 5 |
| `MEDIA_FORGE_URL` | Vazio | Fase 5 |
| `archetype IN ('gossip','clipper')` | Enum existe, nenhuma conta usa | Fases 4 e 5 |
| `platform='tiktok'`, `'whatsapp'` | Enum existe, só Instagram em uso | Fase 6 |
| `olimpo.agents.sources.curated` / `.derived` | **Não criar** | Fases 4 e 5 |

---

## 8. Handoff para a Fase 4

**Estado do sistema entregue**

- Panteão de 3–5 influencers em L2/L3, cada uma com arco, humor e voz próprios, rodando sem intervenção diária.
- Motor de campanhas com máquina de estados, recon obrigatório para engajamento e `spread_policy` aplicada e verificável.
- Limitador de frota em três níveis e grafo social contido.
- Trend engine v1 alimentando o PLAN.
- `GeneratedSource` assíncrono; o worker aguenta produção paralela.

**Decisões tomadas**

| Decisão | Registro |
|---|---|
| Campanha de engajamento sem recon é proibida por máquina de estados | `docs/adr/0010-recon-obrigatorio.md` |
| `spread_policy` é `NOT NULL` no schema e validada na API | `docs/06-campanhas.md` |
| Coleta/recon nunca usa conta-persona; existe pool de coletoras | ADR-0010 |
| Aprovação de campanha independe do nível de autonomia das contas | `docs/08-guardrails-e-autonomia.md` |
| Crossover é limitado pelo `social_graph`, não pela vontade do agente | `docs/adr/0011-grafo-social.md` |

**Dívidas técnicas aceitas conscientemente**

1. O `spread` sorteia slots por campanha, sem visão global do calendário da frota. Duas campanhas concorrentes podem concentrar ações na mesma faixa horária. Mitigado hoje pelo limitador global; a solução correta (agendador de slots global) fica para a Fase 6.
2. A similaridade de texto usa embeddings do modelo local; nada garante que seja um bom proxy do que a plataforma considera duplicata.
3. `trends` é coletada por scraping via `extract_content`, sujeita a mudança de UI da plataforma. Sem alerta de quebra estrutural — a Fase 4 constrói isso para os monitores e retroalimenta aqui.
4. O motor narrativo não tem revisão editorial automática de coerência de longo prazo (meses). Só a checagem por arco.
5. O pool de contas coletoras é gerido à mão. Rotação automática fica para a Fase 4, que consome coletoras de forma muito mais intensa nos monitores.
6. Painel ainda com token único, sem usuários. A Fase 4 traz uma fila de revisão jurídica que provavelmente força a resolver isso.

**A primeira coisa que a Fase 4 faz:** decidir a lista negra de alvos e o texto padrão de cada `claim_status` **antes** de escrever qualquer coletor. Na fofoca, o guardrail é o produto — não um filtro colado no fim.
