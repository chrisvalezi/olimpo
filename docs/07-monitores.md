# 07 — Monitores 24/7

> Vigias persistentes que observam alvos específicos sem parar, detectam novidade e disparam produção de conteúdo. É o que faz uma página de fofoca existir: **a pauta não nasce dentro de casa, ela chega de fora — e chega a qualquer hora.**

**Fase:** 4 (fofoca). O schema e os conceitos nascem agora; o subsistema é implementado depois de os influencers estarem estáveis.

---

## 1. Por que monitor é diferente de tudo o mais

Um influencer é **proativo**: acorda, olha a própria vida fictícia, decide o que postar. O plano do dia é gerado uma vez e executado.

Uma página de fofoca é **reativa**: ela não tem o que dizer até que algo aconteça no mundo. E quando acontece, o valor decai em minutos — quem posta o furo em 8 minutos ganha o alcance; quem posta em 3 horas está repetindo o que todo mundo já viu.

Isso quebra três premissas do loop normal:

| Premissa do influencer | Realidade da fofoca |
|---|---|
| O plano do dia define o conteúdo | O conteúdo é definido pelo mundo, a qualquer hora |
| Latência de horas é aceitável | Latência de minutos define se o post vale algo |
| A cadência é estável e previsível | Um dia morto tem 2 posts; um dia de escândalo tem 15 |
| O conteúdo é gerado, então é seguro | O conteúdo é sobre pessoas reais — risco jurídico |

O monitor é a peça que resolve as três primeiras. Os [guardrails de arquétipo](08-guardrails-e-autonomia.md) resolvem a quarta.

---

## 2. Anatomia

Duas tabelas separam **catálogo** de **processo vivo**:

- **`sources`** — o catálogo: *o que existe para ser observado* (um perfil, uma hashtag, um RSS, um canal). Estático, reutilizável entre monitores.
- **`monitors`** — o processo: *quem está observando o quê, com que frequência, para qual conta, e o que considera digno de post*. Vivo, com estado e saúde.

```
sources (catálogo)          monitors (processo vivo)        monitor_hits (o que viu)
  @perfil_famoso     ◀───── vigia a cada 180s ─────▶  hit: post novo às 14:03
  #hashtag_do_nicho          dono: @fofoca_alerta          triggered: true
  canal do youtube           coletores: [conta_a, conta_b]  → content_item
```

### Campos que importam

| Campo | Papel |
|---|---|
| `owner_account_id` | Qual conta publica a partir deste monitor. Um monitor sem dono só ingere (alimenta o contexto sem virar post) |
| `interval_seconds` | Frequência de checagem. Alvo quente = 60–180s; alvo frio = 1800s+ |
| `collector_pool` | **Quais contas Android fazem a coleta.** Rotaciona — ver §4 |
| `trigger_rules` | O que conta como "novidade que merece post" — ver §5 |
| `next_check_at` | O índice `idx_monitors_due` faz o dispatcher achar os vencidos em O(1) |
| `consecutive_errors` | Backoff automático e alerta; após N erros o monitor vai para `status='error'` |

---

## 3. O ciclo de checagem

```
       tick do dispatcher (a cada 30s)
                  │
                  ▼
   SELECT * FROM monitors
   WHERE status='active' AND next_check_at <= now()
                  │
                  ▼
      task `monitor_check` (fila realtime)
                  │
                  ▼
   coleta via android-orchestrator, com conta sorteada do collector_pool
   (a primitiva depende de sources.kind — ver §3.1)
                  │
                  ▼   ⏱ POLLING: GET /tasks/{id} → GET /jobs/{id}
                  │      (não existe webhook — o resultado a gente vai buscar)
                  ▼
   dedup_hash já existe em ingested_items?
        │ sim → grava monitor_hit (triggered=false) e encerra
        │ não
        ▼
   avalia trigger_rules + relevância via LLM
        │ não dispara → só ingere (vira contexto, não vira post)
        │ dispara
        ▼
   CuratedSource produz content_item ─▶ QA ─▶ (aprovação por nível) ─▶ dispatch
                  │
                  ▼
   monitor_hits.content_item_id preenchido — rastro completo da pauta ao post
```

O `dedup_hash` é o que impede a página de postar o mesmo furo três vezes porque três monitores diferentes o viram. O hash é calculado sobre conteúdo normalizado (texto sem pontuação/emoji + assinatura de mídia), não sobre a URL — a mesma notícia chega por caminhos diferentes.

### 3.1 O que a coleta consegue de fato

> ⚠️ **Ajustado em 2026-08-05**, com o contrato do orchestrator validado contra o código. O desenho anterior assumia **screenshot sob demanda** e **extração do texto de comentários**. **Nenhum dos dois existe.** Ver [contratos/android-orchestrator.md §14](contratos/android-orchestrator.md#14-recon--a-limitação-crítica).

São três primitivas, e `sources.kind` escolhe qual:

| `sources.kind` | Primitiva | O que devolve | Serve para |
|---|---|---|---|
| `profile` | **`instagram.profile-stats`** | `followers`, `following`, `posts`, `you_follow`, `follow_state` (+ os `*_raw`, o texto como o app mostra) | Vigiar **movimento de perfil**: salto de seguidores, post novo (o contador `posts` mudou) |
| `reel` / `post` | **`instagram.reel-download`** com `no_video: true` | `shortcode`, `taken_at`, autor (username, `is_verified`), `caption`, `hashtags`, `mentions`, `tagged_people`, `audio`, `counts` (likes/comments/plays/shares), `video_duration`, `location` | O **conteúdo** de um item específico, em metadado |
| `hashtag` / `explore` / `search` | **`instagram.watch-reels`** com `dry_run: true` | `watched: [{index, author, fingerprint, is_ad, sponsored, watched_s}]` + `source`, `query`, `segment`, `stopped_reason` | **Descoberta em escala**: o que está circulando num nicho, sem engajar |
| `rss` / `http` | HTTP direto, sem device | o que a fonte der | Tudo que não exige estar logado — **sempre prefira este caminho** |

E o que **não** existe, dito sem rodeio:

| Assumido antes | Realidade |
|---|---|
| **Screenshot do alvo** | ❌ Não existe screenshot sob demanda. Prints só saem nas **caixas-pretas de falha** do serviço (`GET /debug/…/screen.png`) — diagnóstico, não coleta. |
| **Texto dos comentários** | ❌ Nenhuma ação lê comentários. `counts.comments` diz *quantos*, nunca *o quê*. Um monitor **não** consegue detectar "a repercussão azedou". |
| **Post de feed / carrossel / foto** | ❌ `reel-download` **só funciona com reels** — o código força `/reel/<shortcode>/` porque `/p/` não cacheia o media info. Alvo que não é reel não é coletável. |
| **Stories de terceiros como dado** | ❌ `watch-stories` **assiste** (e deixa rastro de visualização); devolve só `{author, watched_s, liked}`. Não extrai conteúdo. |
| **DMs recebidas / menções** | ❌ Não existe leitura de inbox do Instagram. |
| **Perfil privado não seguido** | ❌ Sem coleta. O app não cacheia o info → erro. |

**Consequências de desenho:**

1. **`monitors` de perfil detectam *que* algo mudou, não *o quê*.** O caminho normal é em dois passos: `profile-stats` acusa que o contador de posts subiu → uma segunda task `reel-download` busca o item. Cada passo é uma task com seu próprio ciclo de polling, e isso **entra no orçamento de latência da [§6](#6-latência-o-caminho-do-furo)**.
2. **`watch-reels --dry-run` é o único caminho de descoberta ampla** — e é caro: `timeout` de até **7200 s** no catálogo. Não serve para monitor de intervalo curto. Use-o em monitores frios (`interval_seconds` ≥ 1800), como alimentador de `trends`, não como detector de furo.
3. **A relevância é julgada por texto.** Sem imagem e sem comentários, a camada 2 da [§5](#5-regras-de-disparo) opera sobre caption, hashtags, autor e contadores. Ela **precisa** declarar isso — ver `evidence_basis` em [06-campanhas §3.2](06-campanhas.md#32-a-análise--texto-não-visão).
4. **`require_media` não é verificável como antes.** O que dá para saber é o **formato** (`video_duration`, `carousel`) do item, não se a imagem "tem" algo.

> ⚠️ **Nenhuma dessas três primitivas foi executada no device.** `reel-download`, `profile-stats` e `watch-reels` constam da lista de "não testado" do `KNOWN_ISSUES.md` do serviço. Monitor é o subsistema da **Fase 4** — até lá, o smoke test das três é pré-requisito, não detalhe.

---

## 4. Coleta: o problema das contas queimadas

Um monitor com intervalo de 180s faz **480 checagens por dia**. Se sempre a mesma conta abre sempre os mesmos 20 perfis, essa conta tem um padrão de navegação que nenhum humano tem.

Regras:

1. **Pool rotativo.** `collector_pool` lista várias contas; cada checagem sorteia uma, ponderada por quem agiu menos recentemente.
2. **Coletor ≠ publicador.** A conta que publica a fofoca não é a que fica vigiando. Separar o comportamento de leitura do de publicação.
3. **Coletores dedicados.** O ideal é um pool de contas de baixo valor cuja função é só observar — se uma cair, não se perde audiência. Elas nunca postam.
4. **Intervalo com jitter.** `next_check_at` recebe ±20% de ruído. Checagem exata a cada 180.000ms é assinatura de robô.
5. **Respeitar o teto global.** `ACCOUNT_MAX_ACTIONS_PER_HOUR` vale também para coleta. Monitor não fura rate limit.

6. **Uma checagem = uma raia ocupada.** Um device é uma raia única: enquanto a coleta roda, **nenhuma outra conta daquele container age**. Um monitor de 180 s ocupando uma raia por 60 s consome um terço da capacidade daquele device. Capacidade de monitoramento se compra com **devices**, não com contas.

> **Alternativa mais barata quando existir:** nem todo alvo precisa de device. RSS, APIs públicas e feeds abertos devem ser consumidos por HTTP direto — só use o farm de Android para o que exige estar logado. `sources.kind` distingue os dois caminhos. Com o custo real de uma coleta no device agora explícito ([§3.1](#31-o-que-a-coleta-consegue-de-fato) e [§6](#6-latência-o-caminho-do-furo)), esta não é mais uma preferência estética: **é a primeira coisa a tentar para cada alvo novo.**

---

## 5. Regras de disparo

Nem toda novidade merece post. `trigger_rules` é o filtro que separa ruído de pauta, em duas camadas.

**Camada 1 — determinística (barata, roda sempre):**

```json
{
  "min_author_followers": 50000,
  "require_video": true,
  "keywords_any": ["separação", "processo", "novo affair", "confirmou"],
  "keywords_none": ["publi", "parceria paga", "sorteio"],
  "min_engagement": {"likes": 5000, "within_minutes": 30},
  "max_age_minutes": 45,
  "cooldown_minutes": 90
}
```

De onde vem cada campo — ✅ tudo verificável com as primitivas da [§3.1](#31-o-que-a-coleta-consegue-de-fato):

| Regra | Fonte |
|---|---|
| `min_author_followers` | `profile-stats.followers` (ou `reel-download.author` + uma consulta de perfil) |
| `require_video` | `reel-download.video_duration` / `carousel`. ⚠️ **Substituiu `require_media`**: dá para saber o *formato*, não se a imagem "tem" algo |
| `keywords_any` / `keywords_none` | `reel-download.caption` + `hashtags` + `mentions` |
| `min_engagement.likes` | `reel-download.counts.likes` |
| `max_age_minutes` | `reel-download.taken_at` |

**Camada 2 — LLM (só para o que passou na camada 1):** avalia relevância editorial contra o Persona Card da página dona, **sobre texto** — caption, hashtags, autor, contadores. Devolve `relevance` (0..1), `novelty` (0..1) e um `claim_status` provisório. Abaixo do limiar, o item é ingerido mas não vira post.

⚠️ **A LLM não vê a imagem nem os comentários.** Duas consequências concretas para uma página de fofoca:

- **O visual pode contradizer a caption.** Um reel com legenda neutra sobre um assunto grave passa na camada 2. Por isso `claim_status` provisório **nunca** dispensa a revisão humana da [§6](#6-latência-o-caminho-do-furo), e o card de aprovação mostra explicitamente *o que ninguém olhou*.
- **Não dá para medir a temperatura da repercussão.** `counts.comments` diz que há 900 comentários; não diz se são apoio, chacota ou processo a caminho. Uma pauta cujo valor **depende** do clima da seção de comentários é uma pauta que o sistema não sabe avaliar — trate como `novelty` alta e `relevance` incerta, e deixe o humano decidir.

Os dois campos vão para `ingested_items.relevance` e `.novelty`. Distinguir os dois importa: um assunto pode ser altamente relevante e ter novidade zero — é exatamente o caso em que a página deve ficar calada, porque todo mundo já postou.

**`cooldown_minutes`** existe para o dia de escândalo: sem ele, uma notícia quente com 30 desdobramentos em uma hora vira 30 posts, a página parece um bot e o alcance despenca.

---

## 6. Latência: o caminho do furo

Para conteúdo com `max_age_minutes` curto, o pipeline usa a fila `realtime` e um caminho encurtado:

| Etapa | Alvo | Como |
|---|---|---|
| Detecção | ≤ 3 min | Intervalo curto no monitor |
| **Coleta no device + polling** | **30–90 s** | ⚠️ **etapa nova, e não é grátis.** A task vai para o nó, espera a raia do device (um device = uma raia), executa, e o resultado só chega quando **nós** perguntamos: `GET /tasks/{id}` → `GET /jobs/{id}`. Não há webhook. Em alvo quente, use polling de 10 s e `priority` alta no `POST /tasks` |
| **Segundo salto** (perfil → item) | +30–90 s | Quando `profile-stats` só acusa *que* mudou, buscar o item é **outra** task com outro polling — ver [§3.1](#31-o-que-a-coleta-consegue-de-fato) |
| Dedup + trigger | ≤ 5 s | Determinístico primeiro; LLM só no que passou |
| Produção | ≤ 60 s | LLM local, prompt curto, arte de template — não geração pesada |
| QA | ≤ 10 s | Automático; jurídico é bloqueante |
| Aprovação humana | **o gargalo** | Ver abaixo |
| Dispatch | ≤ 30 s | Fila `realtime` tem prioridade absoluta |

**A aprovação humana é o gargalo, e é intencional.** Em L1 (o teto recomendado para fofoca) todo post passa por um humano. A resposta certa não é remover a revisão — é tornar a revisão instantânea: notificação push, card pronto no painel com um toque para aprovar, e o texto já formatado. O objetivo é **tempo de revisão < 60s**, não zero revisão.

`tasks.expires_at` fecha o ciclo: um furo que não foi aprovado dentro da janela vira `expired` em vez de ser publicado atrasado. Publicar notícia velha é pior do que não publicar.

---

## 7. Saúde e falha

| Situação | Comportamento |
|---|---|
| Coleta falha 1× (`ok: false`) | Retry com backoff; `consecutive_errors++`. `reel-download` e `profile-stats` são **read-only e idempotentes** — retry aqui é seguro |
| `consecutive_errors >= 5` | `status='error'`, alerta no painel, para de checar |
| Alvo sumiu (perfil privado/deletado) | `status='disabled'` + evento; exige decisão humana |
| **Alvo mudou de formato** (o perfil passou a postar carrossel/foto em vez de reel) | ⚠️ **`reel-download` só coleta reels.** O monitor vira cego sem falhar: nenhuma coleta dá erro, simplesmente nada vem. Detecta-se pelo `posts` de `profile-stats` subir **sem** hit correspondente → alerta |
| **Task presa em `pending` sem job** | ⚠️ **task fantasma** do orchestrator (ação não qualificada ou `params` vazio). O nó só faz `log.warning` e a task nunca sai de `pending`. Alerta próprio, não conta como `consecutive_errors` — ver [contrato §13.1](contratos/android-orchestrator.md#131-a-armadilha-grave-a-task-fantasma-pending-para-sempre) |
| **Nó fora do ar** | As contas coletoras **daquele host** somem. Rerotear pelo `account_placements`; sem alternativa, o monitor entra em `degraded`, não em `error` — é infraestrutura, não fracasso do monitor |
| Conta coletora banida | Sai do `collector_pool` automaticamente; monitor segue com as demais |
| Pool vazio | `status='error'` — monitor sem coletor é monitor cego, não silencioso |
| Zero hits por 24h em alvo quente | Alerta de **falso silêncio**: provavelmente a coleta quebrou, não o mundo parou |

O último é o mais traiçoeiro: um monitor quebrado não grita, ele apenas para de trazer pauta. Por isso "silêncio prolongado em alvo historicamente ativo" é alerta, não ausência de alerta.

---

## 8. Monitores fora da fofoca

O subsistema não é exclusivo do arquétipo `gossip`:

- **Influencer** — monitorar um concorrente (`instagram.profile-stats`) ou uma hashtag do nicho (`instagram.watch-reels` com `dry_run: true` e `source: "search"`) para alimentar o plano do dia com contexto real ("o nicho está falando de X hoje"). ⚠️ **Monitorar as próprias menções não é possível** — não existe leitura de inbox nem de comentários recebidos no Instagram.
- **Clipagem** (Fase 5) — monitorar canais/lives de criadores; quando cai conteúdo longo novo, dispara o `media-forge`. Aqui o "furo" é o corte publicado antes dos outros clipadores. Fora do Instagram, então fora do device: HTTP direto.
- **Trend engine** — monitores sem dono (`owner_account_id IS NULL`) que só alimentam a tabela `trends`, compartilhada por toda a frota. **É o uso natural do `watch-reels --dry-run`**: sessão longa, intervalo frio, sem urgência, devolvendo `watched[]` com autor, `fingerprint`, `is_ad` e `sponsored` por reel. O `fingerprint` é o que permite dedup entre execuções e entre contas.
  ⚠️ O `audio` de um reel vem de `reel-download` como **título e artista**, não como id reutilizável — e a publicação seleciona faixa por **busca textual** (`audio_search`/`audio_title`), não por id. `trends.audio_ref` precisa ser um termo de busca.

---

## Referências

- [Contrato do android-orchestrator](contratos/android-orchestrator.md) — **as primitivas de coleta e seus limites** (§14) e o aviso de ações não testadas (§15)
- [Modelo de dados](02-modelo-de-dados.md) — tabelas `monitors`, `monitor_hits`, `sources`, `ingested_items`
- [Loop do agente](05-loop-do-agente.md) — o `CuratedSource`
- [Guardrails](08-guardrails-e-autonomia.md) — regras jurídicas do arquétipo `gossip`
- [Fase 4 — fofoca](fases/fase-4-fofoca.md)
