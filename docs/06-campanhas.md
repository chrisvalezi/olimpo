# 06 — Campanhas

> Como o manager emite uma ordem para a frota, por que ela passa por **reconhecimento** antes de virar plano, e como o **espalhamento** impede que a coordenação vire assinatura de farm.

---

## 1. O que é uma campanha

Uma campanha é uma **intenção top-down** que a frota executa **bottom-up**. O humano (ou o manager) descreve o que quer; cada agente traduz aquilo pela própria história, voz e realidade fictícia.

```
Brief: "todas postem usando o áudio X, tema superação"
  ├─ Luna (fitness, arco "treinando maratona") → reels correndo ao amanhecer
  ├─ Kai (gamer, arco "subindo de rank")       → clipe de comeback em partida
  └─ @fofoca_alerta (página de entretenimento)  → post "o áudio que tomou conta do TL"
```

**A campanha nunca escreve o conteúdo final.** Ela entrega um brief; quem escreve é o agente, com o seu Persona Card. Duas contas jamais publicam o mesmo texto — isso é requisito de segurança, não estética (ver [§5](#5-espalhamento)).

### Tipos (`campaigns.kind`)

| Tipo | O que faz | Exige recon? |
|---|---|---|
| `content` | Todas produzem conteúdo sobre um tema/áudio/marca | Não |
| `engagement` | Todas interagem com um alvo externo (curtir, comentar, salvar, compartilhar) | **Sim, obrigatório** |
| `mixed` | Conteúdo + engajamento cruzado | Sim, se houver alvo externo |

---

## 2. O ciclo de vida

Estados em `campaigns.status`:

```
draft ──▶ recon_pending ──▶ recon_done ──▶ planning ──▶ approval_pending
                │                                              │
                │ (safety_verdict = blocked)                   ▼
                └────────────▶ aborted                     approved
                                                               │
                                                               ▼
                                              running ──▶ completed
                                                 │
                                                 └──▶ aborted (kill switch)
```

| Transição | Quem dispara | Condição |
|---|---|---|
| `draft → recon_pending` | Humano publica a campanha | Campanha de engajamento com `target_url` |
| `draft → planning` | Humano publica a campanha | Campanha de conteúdo (sem alvo externo) |
| `recon_pending → recon_done` | Worker | Task `extract_content` verificada + análise da LLM gravada |
| `recon_pending → aborted` | Motor de guardrails | `campaign_recon.safety_verdict = 'blocked'` |
| `recon_done → planning` | Worker | Automático |
| `planning → approval_pending` | Worker | Plano gerado (participantes + ações + slots) |
| `approval_pending → approved` | Humano | Sempre exige humano, em **qualquer** nível de autonomia |
| `approved → running` | Dispatcher | Entrou em `window_start` |
| `running → completed` | Dispatcher | Todas as ações verificadas ou expiradas |
| `* → aborted` | Kill switch | Manual ou guardrail de severidade `kill` |

> **Campanha sempre passa por humano.** Mesmo uma frota inteira em L3 não auto-aprova campanha. O motivo: L3 é confiança na *conta individual* dentro do arco dela; campanha é ação coordenada, cujo modo de falha é sistêmico — erra uma, erram todas ao mesmo tempo.

---

## 3. RECON — olhar antes de agir

> ⚠️ **Reescrito em 2026-08-05**, depois de o contrato do android-orchestrator ser validado contra o código. O desenho anterior desta seção assumia que a coleta devolvia **screenshot**, **caption** e **top comments**, e que uma LLM **multimodal** analisava a imagem. **Duas dessas três coisas não existem.** Ver [contratos/android-orchestrator.md §14](contratos/android-orchestrator.md#14-recon--a-limitação-crítica).

**O problema, inalterado.** Uma campanha de engajamento aponta para conteúdo que **não é nosso**. Comentar às cegas em uma URL significa que a frota pode acabar comentando "amei demais 😍" em um post sobre um acidente, no perfil de um concorrente, em conteúdo adulto, em um golpe, ou numa armadilha deixada de propósito para farm.

**A solução possível hoje.** Antes de qualquer planejamento, uma conta Android **abre o reel e lê os metadados estruturados que o app já cacheia**; uma LLM analisa **texto**. Só então o sistema decide quem participa e o que cada persona diz.

```
┌──────────────────────────────────────────────────────────────────┐
│ 1. COLETA — DOIS despachos ao android-orchestrator                │
│    a) instagram.reel-download  { target, no_video: true }         │
│         → shortcode · autor · caption · hashtags · mentions ·     │
│           tagged_people · audio · counts · duração · localização  │
│    b) instagram.profile-stats  { target: <autor> }                │
│         → followers · following · posts · you_follow              │
│    ❌ SEM screenshot · ❌ SEM texto de comentários · ❌ SÓ REELS   │
└──────────────────────────┬───────────────────────────────────────┘
                           ▼  campaign_recon.raw_capture
┌──────────────────────────────────────────────────────────────────┐
│ 2. ANÁLISE — LLM de TEXTO (não multimodal)                        │
│    Sobre o que é? tom? público? é seguro? que ângulos cabem?      │
│    Entrada: caption + hashtags + autor + contadores + áudio       │
└──────────────────────────┬───────────────────────────────────────┘
                           ▼  campaign_recon.analysis / safety_verdict
┌──────────────────────────────────────────────────────────────────┐
│ 3. VEREDITO — safe → planeja · risky → exige humano · blocked →  │
│    aborta a campanha e registra em guardrail_violations           │
│    ⚠️ alvo que NÃO é reel → blocked automático, sem passar por 2  │
└──────────────────────────────────────────────────────────────────┘
```

### 3.1 A coleta — o que dá e o que não dá

✅ **A única primitiva de extração que existe é `instagram.reel-download`.** Ela abre o reel, deixa o app cachear o JSON público da mídia e lê esse cache. É read-only (não curte, não comenta), roda em qualquer conta e é idempotente.

Com `no_video: true` ela **não baixa o vídeo** — só os metadados, que é exatamente o que queremos: o vídeo iria para o `/sdcard` do device e **não há rota HTTP para buscá-lo de lá**.

`campaign_recon.raw_capture` passa a guardar isto:

```json
{
  "target_url": "https://www.instagram.com/reel/C9aB3XyPqrs/",
  "captured_at": "2026-08-05T14:03:11-03:00",
  "reel": {
    "shortcode": "C9aB3XyPqrs",
    "taken_at": "2026-08-04T18:12:00-03:00",
    "author": {"username": "marca_energetico", "full_name": "Marca Energético",
               "is_verified": true},
    "caption": "Lançamento oficial da nova lata 🔥 #energia",
    "hashtags": ["#energia", "#lancamento"],
    "mentions": ["atleta.parceiro"],
    "tagged_people": [{"username": "atleta.parceiro", "x": 0.42, "y": 0.61}],
    "collaborators": [],
    "audio": {"title": "Som original", "artist": "marca_energetico", "id": "…"},
    "counts": {"likes": 12400, "comments": 380, "plays": 210500, "shares": 90},
    "video_duration": 27,
    "location": "São Paulo, Brazil"
  },
  "author_stats": {
    "target": "marca_energetico",
    "followers": 184000, "following": 312, "posts": 940,
    "you_follow": false, "follow_state": "Seguir"
  }
}
```

**O que se perde em relação ao desenho anterior** — e vale enunciar sem eufemismo:

| Perdido | Consequência real |
|---|---|
| **A imagem / o vídeo em si** | Não dá para avaliar **o que está na tela**. Um reel com caption inócua ("hoje foi assim 🖤") sobre um velório passa incólume pela análise de texto. **Este é o buraco mais perigoso do recon atual.** |
| **O texto dos comentários** | `counts.comments` diz *quantos*, nunca *o quê*. Não dá para ler o clima da seção de comentários — se a plateia está elogiando, brigando ou fazendo piada de algo que a caption não revela. |
| **Post de feed, carrossel e foto** | `reel-download` **só funciona com reels**: o código força a URL `/reel/<shortcode>/` porque `/p/` não cacheia o media info. Alvo que não é reel é **inanalisável hoje**. |
| **Perfil privado / conta não seguida** | O app não cacheia o info → erro. Sem coleta, sem análise. |
| **Screenshot sob demanda** | Não existe. Prints só aparecem nas caixas-pretas de **falha** do serviço (`GET /debug/…/screen.png`). Não serve para recon. |

O que **permanece** intacto e continua valendo:

- **Quem coleta não participa.** A conta usada no recon sai do pool de participantes daquela campanha. Uma conta que visita o alvo e depois comenta nele tem um rastro comportamental diferente de uma que só comenta.
- **Rotação do coletor.** `monitors.collector_pool` e o pool de recon rotacionam contas para não queimar sempre a mesma. Uma conta que abre 40 perfis alheios por dia é anômala.
- **Coleta é barata e reutilizável.** O recon de um alvo vale para várias campanhas dentro de uma janela curta; consultar `campaign_recon` por `target_url` recente evita recoleta. Agora é ainda mais barato: são duas leituras, sem download de mídia.

> ⚠️ **`instagram.reel-download` e `instagram.profile-stats` nunca foram executados no device.** Constam da lista de "não testado" do `KNOWN_ISSUES.md` do serviço. Enquanto não houver smoke test verde, **o recon inteiro fica em dry-run** — e campanha de engajamento não sai do papel. Ver [contratos/android-orchestrator.md §15](contratos/android-orchestrator.md#15-aviso-quase-nada-foi-executado-no-device).

### 3.2 A análise — texto, não visão

A LLM recebe **apenas texto estruturado** (caption, hashtags, mentions, autor, contadores, áudio, duração, localização) e devolve **saída estruturada validada por Pydantic** — nunca texto livre:

```json
{
  "summary": "Reel de lançamento de produto de uma marca de energético...",
  "topic": "lançamento de produto",
  "tone": "comemorativo, publicitário",
  "audience": "jovem adulto, consumo fitness/gaming",
  "sentiment": "positivo",
  "safety_verdict": "safe",
  "safety_reasons": [],
  "is_competitor": false,
  "evidence_basis": "metadata_only",
  "unseen": ["conteúdo visual do vídeo", "seção de comentários"],
  "confidence": 0.62,
  "engagement_hooks": [
    "hashtag de lançamento sugere expectativa de disponibilidade",
    "áudio original da própria marca — a campanha pode reaproveitá-lo"
  ],
  "suggested_angles": [
    {"angle": "curiosidade sobre disponibilidade", "fits_niches": ["fitness", "gamer"]},
    {"angle": "entusiasmo com o sabor", "fits_niches": ["lifestyle"]},
    {"angle": "pergunta sobre composição/cafeína", "fits_niches": ["fitness"]}
  ]
}
```

Três campos são **novos e obrigatórios**, e existem exatamente porque a análise ficou mais fraca:

| Campo | Papel |
|---|---|
| `evidence_basis` | `metadata_only` hoje, sempre. Se um dia houver visão, o valor muda e os registros antigos continuam interpretáveis. |
| `unseen` | O que a LLM **não teve acesso**. Vai para o card de aprovação humana — o revisor precisa saber que ninguém olhou o vídeo. |
| `confidence` | Confiança da própria análise. **Abaixo de `CAMPAIGN_RECON_MIN_CONFIDENCE` (default 0.7) o veredito é rebaixado** para pelo menos `risky`. |

**`suggested_angles` continua sendo o coração do recon** — mas mudou de matéria-prima. Antes, o melhor ângulo saía de *"o que a plateia já está dizendo"* nos top comments. Sem eles, os ângulos saem de caption, hashtags, áudio e do perfil do autor: mais genéricos, e por isso **mais propensos ao padrão de farm** que a `spread_policy` existe para destruir.

Compensações, todas do nosso lado:

1. **Ângulo derivado de metadado é declarado como tal** — o prompt de adaptação local recebe `evidence_basis` e é instruído a **não fingir intimidade com a conversa** ("todo mundo aqui já falou isso" vira mentira detectável quando ninguém leu os comentários).
2. **`action_mix` pende para o lado silencioso.** Sem saber o clima da seção de comentários, **curtir e salvar são seguros; comentar não é**. O default de `comment` cai, e comentário em alvo externo passa a exigir `safety_verdict = safe` **e** `confidence ≥ 0.8`.
3. **Menos participantes.** `max_participation` para alvo externo é reduzido enquanto o recon for cego ao visual.

### 3.3 O veredito de segurança

| `safety_verdict` | Significado | Ação |
|---|---|---|
| `safe` | Conteúdo comum, sem sinal de risco, **e** `confidence ≥ 0.7` | Segue para `planning` |
| `risky` | Tema sensível, polêmico ou ambíguo — **ou** `confidence` baixa — **ou** só metadado num nicho onde a imagem importa | Segue, mas **exige revisão humana reforçada**; participação reduzida; comentário desabilitado |
| `blocked` | Tragédia, menor de idade, conteúdo adulto, discurso de ódio, golpe, tema de saúde/morte, judicialização | **Aborta a campanha** e grava em `guardrail_violations` |

### 3.3.1 ⚠️ Alvo que não é reel: `blocked` por padrão

**Regra nova, e é a mudança mais importante desta seção.**

Se o `target_url` não é um reel — post de feed `/p/`, carrossel, foto, perfil, story — **não existe coleta possível hoje**. Nesse caso:

```
target_url não é /reel/  ──▶  campaign_recon.safety_verdict = 'blocked'
                              safety_reasons = ['nao_analisavel:formato_nao_suportado']
                              campanha → aborted
```

Isto **não** é conservadorismo decorativo. A alternativa é literalmente comentar às cegas — que é exatamente o cenário que o recon existe para impedir, e que o [ADR-0008](adr/0008-recon-antes-da-campanha.md) proíbe. Um alvo que não pode ser olhado não é "um alvo com menos informação": é **um alvo não visto**.

Saída para o humano, não para o sistema: um operador pode **abrir o post, olhar e liberar manualmente**, gravando `campaign_recon.analysis` com `evidence_basis: "human"`. É trabalho manual de propósito — é o preço de agir sobre algo que a máquina não consegue ver.

O mesmo vale para coleta que falha (`ok: false`, perfil privado, reel indisponível): **qualquer falha de recon é `blocked`**, nunca "seguir sem".

Regra de ouro, inalterada e agora mais literal: **na dúvida, `blocked`.** O custo de uma campanha abortada é um humano reagendar. O custo de 30 contas comentando algo alegre num post sobre uma morte é a operação inteira.

---

## 4. Planejamento

Com o recon em mãos (ou direto, se for campanha de conteúdo), o planner monta o plano:

**1. Elegibilidade** — filtra contas por `campaigns.account_filter`: arquétipo, nicho, faixa de seguidores, cliente, status `active`, autonomia mínima. Descarta quem está em aquecimento, suspensa, ou já no teto de ações do dia.

**2. Amostragem** — de todas as elegíveis, sorteia no máximo `CAMPAIGN_MAX_PARTICIPATION` da frota (default 0.6). **Nem toda conta entra em toda campanha** — ver [§5](#5-espalhamento).

**3. Adaptação local** — para cada participante, uma chamada de LLM recebe: o brief, os `suggested_angles` do recon, o Persona Card e o arco narrativo atual. Devolve a ação concreta: o comentário na voz dela, ou a ideia de conteúdo coerente com a vida fictícia dela. Vai para `campaign_participants.local_adaptation` e `.actions`.

**4. Sorteio de slot** — cada participante recebe um `slot_at` dentro da janela, respeitando o jitter e o `active_hours` da conta. Uma conta que "dorme" às 23h não comenta às 3h.

**5. Materialização** — cada ação vira linha em `tasks`, com `origin='campaign'` e `scheduled_for = slot_at`. A partir daqui o fluxo é o mesmo de qualquer task.

---

## 5. Espalhamento

> **`spread_policy` é `NOT NULL` em `campaigns`.** Não é configuração opcional. Ver [ADR-0007](adr/0007-spread-policy-obrigatoria.md).

N contas fazendo a mesma ação na mesma janela é o sinal nº 1 de detecção de comportamento coordenado. As plataformas não precisam entender o conteúdo — basta a correlação temporal. Cinco componentes atacam isso:

| Componente | Regra | Por que existe |
|---|---|---|
| **Jitter temporal** | Slots sorteados com distância mínima entre contas (`CAMPAIGN_JITTER_MIN_MINUTES`, default 25min) e espalhados por toda a janela | Correlação temporal é o sinal mais barato de detectar |
| **Texto sempre único** | Cada comentário/caption gerado na voz da persona. Nunca template, nunca rotação de frases prontas | Similaridade de texto entre contas é trivialmente detectável, e não custa nada evitar — já temos LLM local |
| **Participação parcial** | Máximo `CAMPAIGN_MAX_PARTICIPATION` da frota elegível, sorteada | Se as *mesmas* N contas aparecem juntas em toda campanha, o conjunto vira uma assinatura |
| **Limite de grafo** | Teto de quantas contas da casa interagem entre si e com o mesmo alvo externo | Interação cruzada é poderosa para alcance, mas desenha um grafo denso e fechado — o formato clássico de rede de farm |
| **Cadência natural** | Campanha nunca ultrapassa `accounts.cadence`, nem a janela `active_hours` da conta | Uma conta que posta 1×/dia e de repente faz 6 ações numa hora é anomalia no próprio histórico dela |

Formato de `spread_policy`:

```json
{
  "jitter_minutes": {"min": 25, "max": 240},
  "max_participation": 0.6,
  "respect_active_hours": true,
  "respect_cadence": true,
  "max_accounts_per_target": 12,
  "min_gap_same_target_minutes": 20,
  "text_uniqueness": "per_persona",
  "action_mix": {"like": 1.0, "comment": 0.35, "save": 0.2, "share": 0.1}
}
```

**Sobre `action_mix`:** nem todo participante faz tudo. Uma seção de comentários onde *todos* que curtiram também comentaram é tão anômala quanto o inverso. A proporção imita o funil natural — muita curtida, poucos comentários, menos saves ainda.

---

## 6. Campanhas de conteúdo com marca

O caso "influencer X segurando a lata do energético Y" usa `brand_refs`, que é repassado à máquina de influencers na geração da mídia. Três exigências:

1. **Disclosure.** `persona_card.commercial.disclosure` define como a conta sinaliza publicidade. O QA bloqueia publipost sem disclosure.
2. **Categoria permitida.** O Persona Card lista categorias de marca proibidas por persona. Uma persona fitness que declara não anunciar suplemento não recebe essa campanha, mesmo elegível pelo nicho.
3. **Coerência com o arco.** O planner recebe o arco atual. Se o arco diz "está viajando na praia", a mídia gerada precisa ser compatível — produto na praia, não em um estúdio urbano.

---

## 7. Observação e encerramento

Durante `running`, o painel mostra a timeline de execução: quem já agiu, quem está agendado, quem falhou. Métricas de cada ação alimentam `post_metrics` e `events`.

**Abortar em execução** é seguro: tasks ainda não despachadas são canceladas; tasks em voo são deixadas concluir (cancelar no meio deixa o device em estado inconsistente) e marcadas para não gerar retry.

Uma campanha vira `completed` quando toda ação está `verified`, `failed` ou `expired`. O relatório final consolida: participação real vs. planejada, ações verificadas, alcance somado, violações registradas e custo de LLM.

---

## Referências

- [ADR-0007 — spread_policy obrigatória](adr/0007-spread-policy-obrigatoria.md)
- [ADR-0008 — recon antes da campanha](adr/0008-recon-antes-da-campanha.md)
- [Loop do agente](05-loop-do-agente.md) · [Guardrails](08-guardrails-e-autonomia.md) · [Contrato do orchestrator](contratos/android-orchestrator.md)
