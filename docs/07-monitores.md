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
                  │
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

---

## 4. Coleta: o problema das contas queimadas

Um monitor com intervalo de 180s faz **480 checagens por dia**. Se sempre a mesma conta abre sempre os mesmos 20 perfis, essa conta tem um padrão de navegação que nenhum humano tem.

Regras:

1. **Pool rotativo.** `collector_pool` lista várias contas; cada checagem sorteia uma, ponderada por quem agiu menos recentemente.
2. **Coletor ≠ publicador.** A conta que publica a fofoca não é a que fica vigiando. Separar o comportamento de leitura do de publicação.
3. **Coletores dedicados.** O ideal é um pool de contas de baixo valor cuja função é só observar — se uma cair, não se perde audiência. Elas nunca postam.
4. **Intervalo com jitter.** `next_check_at` recebe ±20% de ruído. Checagem exata a cada 180.000ms é assinatura de robô.
5. **Respeitar o teto global.** `ACCOUNT_MAX_ACTIONS_PER_HOUR` vale também para coleta. Monitor não fura rate limit.

> **Alternativa mais barata quando existir:** nem todo alvo precisa de device. RSS, APIs públicas e feeds abertos devem ser consumidos por HTTP direto — só use o farm de Android para o que exige estar logado. `sources.kind` distingue os dois caminhos.

---

## 5. Regras de disparo

Nem toda novidade merece post. `trigger_rules` é o filtro que separa ruído de pauta, em duas camadas.

**Camada 1 — determinística (barata, roda sempre):**

```json
{
  "min_author_followers": 50000,
  "require_media": true,
  "keywords_any": ["separação", "processo", "novo affair", "confirmou"],
  "keywords_none": ["publi", "parceria paga", "sorteio"],
  "min_engagement": {"likes": 5000, "within_minutes": 30},
  "max_age_minutes": 45,
  "cooldown_minutes": 90
}
```

**Camada 2 — LLM (só para o que passou na camada 1):** avalia relevância editorial contra o Persona Card da página dona. A LLM devolve `relevance` (0..1), `novelty` (0..1) e um `claim_status` provisório. Abaixo do limiar, o item é ingerido mas não vira post.

Os dois campos vão para `ingested_items.relevance` e `.novelty`. Distinguir os dois importa: um assunto pode ser altamente relevante e ter novidade zero — é exatamente o caso em que a página deve ficar calada, porque todo mundo já postou.

**`cooldown_minutes`** existe para o dia de escândalo: sem ele, uma notícia quente com 30 desdobramentos em uma hora vira 30 posts, a página parece um bot e o alcance despenca.

---

## 6. Latência: o caminho do furo

Para conteúdo com `max_age_minutes` curto, o pipeline usa a fila `realtime` e um caminho encurtado:

| Etapa | Alvo | Como |
|---|---|---|
| Detecção | ≤ 3 min | Intervalo curto no monitor |
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
| Coleta falha 1× | Retry com backoff; `consecutive_errors++` |
| `consecutive_errors >= 5` | `status='error'`, alerta no painel, para de checar |
| Alvo sumiu (perfil privado/deletado) | `status='disabled'` + evento; exige decisão humana |
| Conta coletora banida | Sai do `collector_pool` automaticamente; monitor segue com as demais |
| Pool vazio | `status='error'` — monitor sem coletor é monitor cego, não silencioso |
| Zero hits por 24h em alvo quente | Alerta de **falso silêncio**: provavelmente a coleta quebrou, não o mundo parou |

O último é o mais traiçoeiro: um monitor quebrado não grita, ele apenas para de trazer pauta. Por isso "silêncio prolongado em alvo historicamente ativo" é alerta, não ausência de alerta.

---

## 8. Monitores fora da fofoca

O subsistema não é exclusivo do arquétipo `gossip`:

- **Influencer** — monitorar as próprias menções, um concorrente, ou uma hashtag do nicho para alimentar o plano do dia com contexto real ("o nicho está falando de X hoje").
- **Clipagem** (Fase 5) — monitorar canais/lives de criadores; quando cai conteúdo longo novo, dispara o `media-forge`. Aqui o "furo" é o corte publicado antes dos outros clipadores.
- **Trend engine** — monitores sem dono (`owner_account_id IS NULL`) que só alimentam a tabela `trends`, compartilhada por toda a frota.

---

## Referências

- [Modelo de dados](02-modelo-de-dados.md) — tabelas `monitors`, `monitor_hits`, `sources`, `ingested_items`
- [Loop do agente](05-loop-do-agente.md) — o `CuratedSource`
- [Guardrails](08-guardrails-e-autonomia.md) — regras jurídicas do arquétipo `gossip`
- [Fase 4 — fofoca](fases/fase-4-fofoca.md)
