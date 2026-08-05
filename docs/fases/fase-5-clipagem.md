# Fase 5 — Clipagem

**Duração estimada:** 3–4 semanas · **Depende de:** [Fase 4](fase-4-fofoca.md) · **Entrega para:** [Fase 6](fase-6-escala.md)

---

## 1. Resumo

Terceiro e último arquétipo: `clipper`. Nasce o **media-forge**, o microserviço que faltava — ingere vídeo longo (live, podcast, YouTube), transcreve com Whisper, detecta os momentos que viram clipe, corta, reenquadra para vertical, queima legenda e adiciona trilha. O `DerivedSource` transforma cada clipe em `content_item`.

Junto vem a primeira dimensão comercial: clipagem é tipicamente **serviço para terceiros**, então `clients` e `accounts.client_id` acordam, e com eles o isolamento multi-tenant e a identidade de usuário no painel.

---

## 2. Pré-requisitos

| Item | Detalhe |
|---|---|
| Fase 4 completa | Ingestão validada, guardrails jurídicos executáveis |
| **Identidade de usuário no painel** | Dívida nº 5 da Fase 4 — primeira entrega desta fase, antes do multi-tenant |
| GPU ou máquina dedicada para ASR | Whisper em CPU não sustenta 1 fonte longa/dia com folga; medir antes de prometer SLA |
| Storage de vídeo dimensionado | Volume `media:` do compose; vídeo longo + intermediários consomem ordem de grandeza acima de foto |
| **Whitelist de criadores** | Lista de quem autorizou explicitamente. Documento humano, antes do código |
| Conta de clipagem criada e aquecida | `archetype='clipper'`, ≥ 7 dias em `warming` |
| Contrato do cliente piloto (se houver) | Escopo, criadores cobertos, direitos cedidos — vira `clients.contract` |

---

## 3. Escopo

### 3.1 Identidade e multi-tenant

1. **`olimpo.api.auth`** — usuários com papéis (`admin`, `editor`, `reviewer`, `client`), sessão no painel, `API_TOKEN` mantido só para serviço. Fim do token compartilhado.
2. **Rastro de decisão real** — `approvals.decided_by`, `guardrail_violations.detail.actor` e `events.detail.actor` passam a receber o id do usuário autenticado. Quita a dívida nº 5 da Fase 4.
3. **`olimpo.core.tenancy`** — escopo por `client_id` em todas as queries do painel e da API: usuário com papel `client` só enxerga `accounts`, `content_items`, `posts`, `post_metrics` e `campaigns` do próprio `clients.id`. Aplicado em camada de repositório, com teste de vazamento no CI.
4. **`clients`** — CRUD via `POST /panel/clients`, com `contract` jsonb guardando escopo, criadores cobertos, limites de volume e regras de atribuição.

### 3.2 media-forge (microserviço novo)

5. **Serviço `media-forge`** — repositório/serviço próprio, adicionado ao `docker-compose.yml`, exposto por `MEDIA_FORGE_URL` e autenticado por `MEDIA_FORGE_TOKEN`. API assíncrona (submete job → callback), porque processamento de vídeo dura minutos.
6. **`POST /ingest`** — recebe URL de vídeo longo ou arquivo, baixa, normaliza (container, fps, áudio), devolve `job_id` e a duração.
7. **`POST /transcribe`** — Whisper/ASR com timestamps por palavra e diarização de locutor quando possível. Devolve transcrição estruturada.
8. **`POST /detect-moments`** — combina sinais para propor cortes: densidade de fala, mudança de locutor, picos de energia no áudio, marcadores de retenção quando disponíveis (chat da live, replays) e uma passada de LLM sobre a transcrição pontuando "isso vira clipe" com justificativa. Devolve candidatos com `start`, `end`, `score`, `reason`, `hook_text`.
9. **`POST /render`** — corta, reenquadra para 9:16 com rastreamento de rosto/falante, queima legenda com o estilo do cliente, aplica trilha e devolve o asset final + thumbnail.
10. **`GET /jobs/{id}`** e callback — estados `queued → downloading → transcribing → detecting → rendering → done|failed`, com progresso.
11. **`olimpo.integrations.media_forge`** — cliente no padrão da Fase 0 (`ServiceClient`, retry, `trace_id`, dry-run gravando em `media/dryrun/media-forge/`).

### 3.3 DerivedSource

12. **Monitores de fonte longa** — `sources` com `kind IN ('youtube_channel','live_stream')` e `monitors` vigiando novos vídeos/lives. Reaproveita 100% da Fase 4: `monitor_check` → `ingested_items` com `dedup_hash` do id do vídeo.
13. **`olimpo.agents.sources.derived`** — implementa `ContentSource.propose()` para `archetype='clipper'`:
    - pega o `ingested_item` do vídeo longo;
    - orquestra o pipeline no media-forge;
    - para cada momento aprovado, cria um `content_item` (`kind='publish_reels'`, `source_strategy='derived'`) com `media_refs` do clipe, `source_refs` (URL, canal, timestamps do trecho), `attribution` preenchida e `rights_status` derivado da whitelist;
    - a caption/hook é escrita na voz da persona da conta de clipagem, com o `hook_text` do detector como matéria-prima.
14. **Modo lote** — clipagem processa em lote (`production`), não em tempo real. Uma fonte longa gera N clipes que são **espalhados ao longo de dias**, respeitando `accounts.cadence` — não se despejam 10 reels em uma tarde.
15. **Banco de clipes** — clipes aprovados e não publicados ficam em `content_items.status='approved'` com `scheduled_for` futuro, formando estoque. É o embrião do banco de conteúdo reaproveitável da Fase 6.

### 3.4 Direitos e atribuição

16. **`olimpo.core.guardrails.rights`** — regras executáveis, avaliadas no QA e revalidadas no dispatch:
    - `rights_status='unknown'` **bloqueia publicação**, em qualquer nível;
    - `'licensed'` exige o criador na whitelist (com referência ao registro da autorização em `clients.contract` ou no cadastro de criadores);
    - `'fair_use'` exige transformação mínima verificável: duração do clipe dentro do teto configurado e proporção do original abaixo do limite, mais legenda/reframe aplicados;
    - `attribution` **obrigatória e não-vazia** para `licensed` e `fair_use`; o QA verifica que o crédito aparece de fato na caption ou no vídeo renderizado;
    - lista de criadores vetados (strike/DMCA conhecido, oposição pública a clipes) → bloqueio duro.
17. **Registro de strike** — `guardrail_violations` com `rule='rights.dmca_strike'` ao detectar notificação; o criador entra automaticamente na lista de vetados e todo `content_item` pendente daquele criador é cancelado.
18. **Teto de autonomia** — `clipper` tem teto **L2**. Volume alto justifica automação, mas direito autoral não admite full auto sem amostragem.
19. **`docs/runbooks/dmca-recebido.md`** — remoção, veto do criador, cancelamento do estoque pendente, comunicação ao cliente, preservação da auditoria.

### 3.5 Painel e operação

20. **`olimpo.panel_api.clips`** — fila de revisão de clipes: player do trecho, transcrição do momento, `score` e `reason` do detector, `rights_status`, `attribution`, e o link para o ponto exato do original.
21. **Painel — aba Clipagem** — revisão em lote (aprovar vários), visão do estoque agendado e visão por cliente.
22. **`olimpo.panel_api.client_view`** — visão somente-leitura para o papel `client`: seus clipes, agendamento e métricas. Sem acesso a nada de outro tenant.
23. **Métricas de retenção** — `post_metrics.views` e `raw` passam a guardar retenção quando a plataforma expõe; alimenta o `score` do detector de momentos via `olimpo.agents.insights`.
24. **`docs/runbooks/media-forge-travado.md`** — job preso, storage cheio, ASR falhando.

---

## 4. Fora de escopo

- Clipagem de conteúdo sem criador identificável. Se não dá para atribuir, não se clipa.
- Legendagem em outros idiomas / tradução. Fase 6, se houver demanda.
- Edição criativa avançada (b-roll, zoom dinâmico, memes sobrepostos). O render é: corte + reframe + legenda + trilha.
- Geração de thumbnail com a máquina de influencers. O thumbnail é frame do próprio clipe.
- L3 para `clipper`. Teto L2.
- Portal de autoatendimento para o cliente submeter fontes. O onboarding é operado por nós. **Fase 6.**
- Faturamento, cobrança, contrato eletrônico. `clients.contract` guarda o escopo, não gera nota.
- Clipagem para TikTok/YouTube Shorts. Instagram Reels apenas nesta fase.
- Mais de um cliente em produção. Um piloto, validado.

---

## 5. Critérios de saída

- [ ] Login por usuário no painel funciona; `SELECT DISTINCT decided_by FROM approvals WHERE decided_at > now()-interval '7 days'` devolve ids de usuário reais, não `'human'`.
- [ ] Isolamento de tenant: usuário `client` do cliente A recebe `404` ao acessar `content_item` do cliente B. Teste de vazamento no CI cobrindo os 5 endpoints de leitura.
- [ ] `docker compose ps media-forge` saudável; `curl -fsS $MEDIA_FORGE_URL/health` devolve `200`.
- [ ] Pipeline completo: `POST /ingest` de um vídeo de 60 min → `GET /jobs/{id}` chega a `done` com ≥ 5 momentos candidatos e ≥ 3 clipes renderizados.
- [ ] Tempo de processamento de 60 min de vídeo ≤ 20 min ponta a ponta (medido 3×, registrado no handoff).
- [ ] Qualidade do render, verificada à mão em 10 clipes: 9:16 correto, falante enquadrado no eixo em ≥ 90% dos frames, legenda sincronizada (desvio < 300 ms), áudio sem clipping.
- [ ] Detecção avaliada: 20 momentos propostos julgados à mão — ≥ 60% considerados publicáveis sem edição adicional. Baseline registrado para comparação na Fase 6.
- [ ] `SELECT count(*) FROM content_items WHERE source_strategy='derived'` ≥ 30 após 2 semanas, **todos** com `attribution` não-vazia e `source_refs` contendo URL + timestamps.
- [ ] `SELECT count(*) FROM content_items WHERE source_strategy='derived' AND rights_status='unknown' AND status='published'` devolve **`0`** — invariante testado no CI.
- [ ] Bloqueio por direitos: submeter fonte de criador fora da whitelist e fora do fair use → `qa_failed` com `guardrail_violations.rule='rights.unknown'`, zero tasks.
- [ ] Crédito verificado: em 10 posts publicados, o `attribution` aparece de fato na caption ou no vídeo. Checagem automática presente no QA.
- [ ] Veto por strike: marcar um criador como vetado cancela todos os `content_items` pendentes dele (`status='rejected'`) em ≤ 1 min e emite `rights.creator_vetoed`.
- [ ] `archetype='clipper'` não passa de L2: `POST /panel/accounts/{id}/autonomy` com `L3` devolve `409`.
- [ ] Lote e espalhamento: uma fonte longa gerando 8 clipes produz `scheduled_for` distribuídos em ≥ 3 dias, sem violar `cadence->>'posts_per_day'` em nenhum dia.
- [ ] Estoque: `SELECT count(*) FROM content_items WHERE status='approved' AND scheduled_for > now()` ≥ 10 (o banco de clipes existe).
- [ ] **14 dias** com a conta de clipagem processando ≥ 1 fonte longa/dia em lote e publicando dentro da cadência, zero DMCA, zero clipe sem atribuição.
- [ ] Resiliência: matar o container do media-forge no meio de um job deixa o `content_item` em `draft` e o job é retomado ou marcado `failed` com alerta — nunca fica pendurado silenciosamente.
- [ ] Storage sob controle: política de retenção apagando o vídeo longo original após o render; `du -sh` do volume `media` estável ao longo de 7 dias.
- [ ] `make test` verde, cobertura ≥ 80% em `olimpo/core/guardrails/rights` e ≥ 70% em `olimpo/integrations/media_forge`.

---

## 6. Riscos da fase

| Risco | Impacto | Mitigação |
|---|---|---|
| **DMCA / strike de copyright** | Perda da conta, exposição do cliente | `rights_status='unknown'` bloqueia; whitelist explícita; `attribution` obrigatória e verificada no output; lista de vetados; teto L2 com amostragem; runbook de DMCA testado |
| Fair use interpretado com otimismo | Defesa frágil se questionado | Transformação mínima verificável por número (duração e proporção do original), não por opinião; reframe + legenda sempre aplicados; na dúvida, pedir autorização em vez de invocar fair use |
| Clipe descontextualizado difama o criador | Dano ao criador e ao cliente; reputação | QA de contexto: LLM lê a transcrição ao redor do corte e reprova quando o trecho inverte o sentido do original; janela de contexto mínima no corte |
| media-forge vira gargalo e come a fase | Estouro de prazo | Entregar o pipeline em fatias verificáveis (ingest → transcribe → detect → render), cada uma testável isolada; render é a última |
| ASR em CPU inviabilizando o SLA | Lote não fecha o dia | Medir tokens de áudio/min **antes** de prometer volume; GPU dedicada ou serviço externo de ASR como fallback declarado |
| Storage estourando | Sistema para por disco cheio | Retenção agressiva do original pós-render; alerta em 80% do volume; intermediários apagados por job |
| Vazamento entre tenants | Quebra de contrato, perda do cliente | Escopo por `client_id` em camada de repositório, não em cada endpoint; teste de vazamento no CI; papel `client` sem acesso a `events` |
| Detecção de momento medíocre | Produto sem valor percebido | Baseline medido nesta fase (≥ 60% publicáveis); retenção real realimentando o score; humano no loop em L2 |
| Volume alto de clipes com cadência de influencer | Ou entope a conta, ou o estoque nunca sai | Lote com espalhamento por dias e estoque agendado; `cadence` da conta de clipagem calibrada separadamente (3–10/dia, conforme o PLANO) |
| Cliente pedindo criador fora do escopo | Risco jurídico assumido por conta do cliente sem base | `clients.contract` define os criadores cobertos; pedido fora do contrato é bloqueado pelo guardrail, não negociado no chat |

---

## 7. O que fica dormente

| Artefato | Estado nesta fase | Acorda em |
|---|---|---|
| `autonomy_level='L3'` para `clipper` | **Nunca acorda** (teto L2) | — |
| `autonomy_level='L3'` para `gossip` | **Nunca acorda** | — |
| `platform IN ('tiktok','whatsapp')` | Enum existe, só Instagram em uso | Fase 6 |
| Banco de conteúdo cruzado entre contas | Existe estoque **por conta**, não reaproveitamento entre contas | Fase 6 |
| `trends` para clipagem | Coletado, não usado no detector de momentos | Fase 6 |
| A/B de hook/thumbnail | Não conduzido | Fase 6 |
| Onboarding self-service de cliente | Manual | Fase 6 |
| Faturamento a partir de `clients.contract` | `contract` guarda escopo, sem cobrança | Fase 6 |
| `sources.reliability` para fontes longas | Preenchido, sem uso no ranking de clipes | Fase 6 |
| Legendas multilíngue | Não implementado | Fase 6 |

---

## 8. Handoff para a Fase 6

**Estado do sistema entregue**

- Os **três arquétipos** do PLANO em produção sobre o mesmo loop: `GeneratedSource`, `CuratedSource` e `DerivedSource`. A tese de arquitetura está provada em código, não em slide.
- `media-forge` operante: vídeo longo entra, clipe vertical legendado sai, com callback e estados observáveis.
- Direitos autorais tratados como guardrail executável, não como boa intenção.
- Multi-tenant com isolamento por `client_id` e identidade de usuário real no painel — as duas últimas colunas dormentes do `0001_initial.sql` acordaram.
- Estoque de conteúdo agendado, a base do reaproveitamento.

**Decisões tomadas**

| Decisão | Registro |
|---|---|
| `rights_status='unknown'` bloqueia publicação em qualquer nível | `docs/adr/0014-direitos-e-atribuicao.md` |
| Fair use exige transformação medida por número, não por juízo | ADR-0014 |
| `clipper` tem teto L2 | `docs/adr/0012-teto-de-autonomia-por-arquetipo.md` (atualizado) |
| media-forge é serviço externo com API assíncrona, não biblioteca no core | `docs/adr/0015-media-forge-servico.md` |
| Isolamento de tenant em camada de repositório, não por endpoint | `docs/12-seguranca.md` |
| O vídeo longo original é descartado após o render | ADR-0015 |

**Dívidas técnicas aceitas conscientemente**

1. O detector de momentos é heurística + LLM, sem modelo treinado e sem feedback loop fechado com retenção real. A Fase 6 fecha esse loop.
2. O reframe segue rosto/falante, mas falha em cena com múltiplos falantes simultâneos — degrada para enquadramento central. Casos ruins são pegos na revisão L2.
3. Storage tem retenção, não tem tiering (nada vai para armazenamento frio). Com vários clientes isso vira custo relevante.
4. `clients.contract` é jsonb livre, sem schema validado. Funciona com um cliente; com dez, vira fonte de erro.
5. O papel `client` é somente-leitura e não recebe notificação de nada. Comunicação com o cliente é manual.
6. Herdada e ainda aberta: o agendador de slots é por campanha, sem visão global do calendário da frota (dívida nº 1 da Fase 3). Com três arquétipos disputando janelas, isso passa a ser o item mais urgente.
7. Herdada e ainda aberta: rotação automática do pool de coletoras (Fases 3 e 4).

**A primeira coisa que a Fase 6 faz:** o agendador global de slots. Com influencer, fofoca e clipagem competindo pelas mesmas contas, devices e janelas, o espalhamento por campanha deixou de ser suficiente — e é justamente onde mora o risco de detecção.
