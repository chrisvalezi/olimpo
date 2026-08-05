# 05 — O loop do agente

> A espinha única do sistema. Influencer, página de fofoca e conta de clipagem rodam **exatamente este loop** — muda apenas de onde vem o conteúdo.

```
PLAN ─▶ SOURCE ─▶ PRODUCE ─▶ QA ─▶ DISPATCH ─▶ VERIFY ─▶ RECORD ─▶ REFLECT
  ▲        │                                                          │
  └────────┴──────────────── memória alimenta o próximo dia ──────────┘
```

---

## 1. PLAN — o que eu faço hoje

**Quando:** uma vez por dia, no início da janela `active_hours` da conta, com jitter.
**Onde:** `olimpo.agents.planner` · **Grava em:** `day_plans`

O planner monta o contexto e pede à LLM um plano do dia:

| Entrada | De onde vem |
|---|---|
| Persona Card (lore, personality, niche, cadence) | `accounts.persona_card` |
| Arco narrativo ativo | `story_arcs` onde `active = true` |
| Memórias recuperadas | `memories` — top-K por relevância × recência × importância |
| Métricas recentes | `post_metrics` dos últimos N posts |
| Campanhas atribuídas | `campaign_participants` com slot hoje |
| Tendências do nicho | `trends` |
| Humor / energia | `accounts.mood` |

A saída é estruturada e validada por Pydantic — nunca texto livre:

```json
{
  "narrative": "Terceiro dia da semana de praia. Humor alto, energia média.",
  "reasoning": "Os dois últimos posts de treino tiveram alcance abaixo da média; o público respondeu melhor a conteúdo de rotina. Hoje puxo para o cotidiano.",
  "intents": [
    {"kind": "publish_story", "theme": "café da manhã na varanda", "time_hint": "09:00"},
    {"kind": "publish_post",  "theme": "corrida na orla", "time_hint": "17:30"}
  ]
}
```

**`reasoning` não é enfeite.** É o que torna o sistema auditável: quando alguém no painel perguntar "por que ela postou isso?", esta é a resposta. Ver [Observabilidade](11-observabilidade.md).

Cada `intent` vira um `content_item` em `draft` e uma task de produção. O `time_hint` é sugestão da LLM; o horário final recebe jitter e respeita `active_hours` e `cadence`.

---

## 2. SOURCE — de onde vem o material

**A única peça que varia por arquétipo.** Interface única, três implementações:

```python
class ContentSource(Protocol):
    async def propose(
        self, account: Account, intent: Intent, ctx: PlanContext
    ) -> list[ContentDraft]: ...
```

| Arquétipo | Implementação | O conteúdo é | Chama |
|---|---|---|---|
| `influencer` | `GeneratedSource` | **Gerado** a partir da vivência fictícia do dia | máquina de influencers |
| `gossip` | `CuratedSource` | **Curado** a partir do que os monitores trouxeram | `ingested_items` + trend engine |
| `clipper` | `DerivedSource` | **Derivado** de vídeo longo | `media-forge` |

**`GeneratedSource`** traduz o `intent` em um prompt de imagem/vídeo coerente com a estética canônica da persona, chama a máquina de influencers com a **referência facial fixa** (`persona_card.visual.face_ref` — sem isso a persona muda de cara entre posts) e aguarda o job assíncrono.

**`CuratedSource`** pega o `ingested_item` que disparou o monitor, decide o formato (card de texto, print com moldura, carrossel) e monta a arte. Preenche obrigatoriamente `source_refs`, `attribution` e `claim_status`.

**`DerivedSource`** submete o vídeo longo ao `media-forge`, recebe N clipes com score de "clipabilidade" e escolhe. Preenche `rights_status` e `attribution`.

O restante do loop **não sabe** qual source rodou. É isso que faz um arquétipo novo custar uma classe, e não um sistema.

---

## 3. PRODUCE — a voz

**Onde:** `olimpo.agents.producer` · **Grava em:** `content_items`

A mídia existe; falta a linguagem. Uma chamada de LLM (local, alta frequência — alias `olimpo-fast`) gera caption, hashtags e escolha de áudio, recebendo:

- `persona_card.voice` — tom, gírias que usa e que **nunca** usa, comprimento típico, uso de emoji, **e os exemplos de frases** (few-shot: é o que mais segura a voz no lugar)
- `persona_card.taboos` — o que ela nunca diz
- O `intent` e o `narrative` do dia
- Memórias relevantes (para referenciar o próprio passado: *"lembram que semana passada eu falei que..."*)

Em campanha, entra também a `local_adaptation` de `campaign_participants` — a tradução do brief para a realidade daquela persona.

---

## 4. QA — o portão

**Onde:** `olimpo.core.qa` · **Grava em:** `content_items.qa_report`, `guardrail_violations`

Quatro checagens. Qualquer bloqueio manda o item para `qa_failed`; nada segue adiante.

| Checagem | O que valida | Bloqueia? |
|---|---|---|
| **Visual** | Rosto gerado bate com o canônico (similaridade de embedding); mídia não corrompida; formato/resolução corretos | Sim |
| **Textual** | Está na voz da persona? Violou tabu? Repetiu caption recente? Tamanho válido para a plataforma? | Sim |
| **Brand safety** | Conteúdo impróprio, discurso de ódio, tema proibido, promessa de saúde/finanças | Sim |
| **Legal** (por arquétipo) | `claim_status` coerente com o texto (fofoca); `attribution` e `rights_status` preenchidos (clipagem); disclosure presente (publipost) | Sim |

**A checagem de consistência facial é o guardrail mais subestimado do arquétipo influencer.** Uma persona que muda de rosto entre posts perde credibilidade de forma irreversível — e o erro só aparece quando já está publicado.

---

## 5. DISPATCH — a ponte para o mundo real

**Onde:** `olimpo.workers.dispatcher` · **Grava em:** `tasks`

O tick do dispatcher roda a cada 30s e busca o que está vencido:

```sql
SELECT * FROM tasks
WHERE status IN ('pending','scheduled') AND scheduled_for <= now()
ORDER BY priority, scheduled_for
LIMIT 50;   -- serve o índice idx_tasks_due
```

Antes de despachar, cada task passa por vetos:

1. `GLOBAL_KILL_SWITCH` ligado → não despacha nada
2. `accounts.status != 'active'` → não despacha
3. Teto `ACCOUNT_MAX_ACTIONS_PER_HOUR` estourado → reagenda
4. Fora de `active_hours` → reagenda para a próxima janela
5. Nível de autonomia exige aprovação e não há → segura em `approval_pending`
6. `expires_at` já passou → `expired`, não publica atrasado
7. `DISPATCH_ENABLED=false` → grava em `dryrun/` e marca `verified` (modo dry-run)

Passando, a task vai ao android-orchestrator com chave de idempotência, e recebe `external_task_id` + status `dispatched`.

> **O core nunca fala com o Instagram.** Toda ação atravessa esta fronteira. Ver [ADR-0002](adr/0002-separacao-core-execucao.md).

---

## 6. VERIFY — não basta enviar

**Onde:** `olimpo.api.webhooks` (primário) + `olimpo.workers.reconciler` (fallback)

Uma task só é real quando o orchestrator **confirma que a ação aconteceu**. O caminho primário é webhook; o reconciliador varre periodicamente as tasks presas em `dispatched`/`running` e faz polling — rede falha, webhook se perde.

| Resultado | Efeito |
|---|---|
| Sucesso | `verified`, `verified_at` preenchido; se for publicação, cria `posts` |
| Falha recuperável | `attempts++`, reagenda com backoff até `max_attempts` |
| Falha permanente | `failed` + evento + alerta |
| Sem resposta além do timeout | Reconciliador consulta; se ambíguo, **não republica** — republicação duplicada é pior que post perdido |

---

## 7. RECORD — escrever no diário

**Onde:** `olimpo.agents.recorder` · **Grava em:** `memories` (kind `episodic`), `posts`, `post_metrics`, `events`

O que aconteceu vira memória em primeira pessoa, do ponto de vista da persona:

> *"Postei a corrida na orla às 17h40. Foi o melhor alcance da semana. A @carol_fit comentou de novo — ela comenta em quase tudo meu."*

A LLM atribui `importance` (0–10) na escrita. Isso é o que torna a recuperação viável mais tarde: sem importância, a memória vira um log cronológico onde tudo pesa igual e o contexto do agente enche de irrelevância.

Métricas são coletadas em janelas (1h, 24h, 7d) por tasks `collect_metrics` e viram memória quando notáveis — *"aquele post explodiu"* é memória; um post mediano não é.

---

## 8. REFLECT — o que isso significa

**Quando:** fim do dia, e semanalmente.
**Onde:** `olimpo.agents.reflector` · **Grava em:** `memories` (kind `semantic`/`narrative`), `story_arcs`

Este é o passo que separa "agente com história" de "agendador de posts". A reflexão lê as memórias episódicas recentes e sintetiza em **insights** — memória semântica:

> *"Meu público responde melhor a rotina do que a treino. Conteúdo de manhã performa acima da média. A @carol_fit virou presença fixa nos comentários."*

E atualiza a **narrativa**: onde a persona está no arco, o que muda no humor, e — quando o arco acaba — propõe o próximo.

```
memórias episódicas do dia
        │
        ├─▶ insights (semantic)   → o agente aprende sobre si e o público
        └─▶ estado do arco (narrative) → a história avança
                    │
                    ▼
          alimenta o PLAN de amanhã
```

**Insights alimentam o planner do dia seguinte.** É por aqui que o sistema melhora sozinho: o agente que percebeu que rotina performa melhor vai planejar mais rotina amanhã — sem ninguém ter ajustado nada.

### Eventos narrativos

O motor narrativo injeta eventualmente eventos aleatórios coerentes com a persona (*"perdeu o voo"*, *"adotou um gato"*, *"machucou o joelho"*). Isso existe por uma razão prática, não estética: **conteúdo previsível é conteúdo com alcance decrescente.** Uma persona cuja vida nunca tem incidente produz um feed que o algoritmo aprende a ignorar.

---

## 9. Onde cada passo roda

| Passo | Fila Celery | Toca device? | LLM |
|---|---|---|---|
| PLAN | `production` | Não | Raciocínio (`olimpo-reason`) |
| SOURCE | `production` | Não | Prompt de mídia |
| PRODUCE | `production` | Não | Rápida (`olimpo-fast`) |
| QA | `production` | Não | Rápida + visão |
| DISPATCH | `dispatch` | **Sim** | Não |
| VERIFY | `realtime` | — | Não |
| RECORD | `maintenance` | Não | Rápida |
| REFLECT | `maintenance` | Não | Raciocínio |

Interação em tempo real (responder comentário) e verificação usam a fila `realtime` e têm prioridade absoluta: quando os Mac minis saturam, adia-se ideação, nunca resposta a um seguidor que está esperando.

---

## Referências

- [Persona Card](04-persona-card-spec.md) · [Campanhas](06-campanhas.md) · [Monitores](07-monitores.md)
- [Guardrails](08-guardrails-e-autonomia.md) · [LLM Gateway](09-llm-gateway.md)
- [ADR-0006 — arquétipos e ContentSource](adr/0006-arquetipos-e-contentsource.md)
