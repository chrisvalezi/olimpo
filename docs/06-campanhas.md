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

**O problema.** Uma campanha de engajamento aponta para conteúdo que **não é nosso**. Comentar às cegas em uma URL significa que a frota pode acabar comentando "amei demais 😍" em um post sobre um acidente, no perfil de um concorrente, em conteúdo adulto, em um golpe, ou numa armadilha deixada de propósito para farm.

**A solução.** Antes de qualquer planejamento, uma conta Android abre o conteúdo e **extrai o que ele é**; uma LLM multimodal analisa. Só então o sistema decide quem participa e o que cada persona diz.

```
┌──────────────────────────────────────────────────────────────────┐
│ 1. COLETA — task `extract_content` no android-orchestrator        │
│    Uma conta do collector pool abre o alvo e captura:            │
│    screenshot/vídeo · caption · autor · contadores · top comments│
└──────────────────────────┬───────────────────────────────────────┘
                           ▼  campaign_recon.raw_capture / media_refs
┌──────────────────────────────────────────────────────────────────┐
│ 2. ANÁLISE — LLM multimodal (alias `olimpo-vision`)              │
│    Do que se trata? tom? público? é seguro? que ângulos cabem?   │
└──────────────────────────┬───────────────────────────────────────┘
                           ▼  campaign_recon.analysis / safety_verdict
┌──────────────────────────────────────────────────────────────────┐
│ 3. VEREDITO — safe → planeja · risky → exige humano · blocked →  │
│    aborta a campanha e registra em guardrail_violations           │
└──────────────────────────────────────────────────────────────────┘
```

### 3.1 A coleta

A task `extract_content` é despachada ao android-orchestrator como qualquer outra ação. Detalhes que importam:

- **Quem coleta não participa.** A conta usada no recon sai do pool de participantes daquela campanha. Uma conta que visita o alvo e depois comenta nele tem um rastro comportamental diferente de uma que só comenta.
- **Rotação do coletor.** `monitors.collector_pool` e o pool de recon rotacionam contas para não queimar sempre a mesma. Uma conta que abre 40 perfis alheios por dia é anômala.
- **Coleta é barata e reutilizável.** O recon de um alvo vale para várias campanhas dentro de uma janela curta; consultar `campaign_recon` por `target_url` recente evita recoleta.

Payload esperado de retorno (ver [contratos/android-orchestrator.md](contratos/android-orchestrator.md)):

```json
{
  "target_url": "https://instagram.com/p/XXXX",
  "captured_at": "2026-08-05T14:03:11Z",
  "author": {"handle": "@marca_energetico", "followers": 184000, "verified": true},
  "caption": "Lançamento oficial da nova lata 🔥 #energia",
  "counters": {"likes": 12400, "comments": 380, "shares": 90},
  "media": [{"kind": "image", "ref": "media://recon/abc123.jpg"}],
  "top_comments": [
    {"author": "@user1", "text": "quando chega em BH?", "likes": 45}
  ]
}
```

### 3.2 A análise

A LLM recebe a mídia e o texto capturados e devolve **saída estruturada validada por Pydantic** — nunca texto livre:

```json
{
  "summary": "Post de lançamento de produto de uma marca de energético...",
  "topic": "lançamento de produto",
  "tone": "comemorativo, publicitário",
  "audience": "jovem adulto, consumo fitness/gaming",
  "sentiment": "positivo",
  "safety_verdict": "safe",
  "safety_reasons": [],
  "is_competitor": false,
  "engagement_hooks": [
    "dúvida recorrente sobre disponibilidade regional",
    "muita gente pedindo sabor novo"
  ],
  "suggested_angles": [
    {"angle": "curiosidade sobre disponibilidade", "fits_niches": ["fitness", "gamer"]},
    {"angle": "entusiasmo com o sabor", "fits_niches": ["lifestyle"]},
    {"angle": "pergunta sobre composição/cafeína", "fits_niches": ["fitness"]}
  ]
}
```

**`suggested_angles` é o coração do recon.** Não é o comentário pronto — é a matéria-prima que cada persona vai transformar no *seu* comentário. Ângulos diferentes distribuídos entre contas diferentes produzem uma seção de comentários que parece orgânica, e não um enxame.

### 3.3 O veredito de segurança

| `safety_verdict` | Significado | Ação |
|---|---|---|
| `safe` | Conteúdo comum, sem sinal de risco | Segue para `planning` |
| `risky` | Tema sensível, polêmico, ou ambíguo | Segue, mas **exige revisão humana reforçada**; participação reduzida |
| `blocked` | Tragédia, menor de idade, conteúdo adulto, discurso de ódio, golpe, tema de saúde/morte, judicialização | **Aborta a campanha** e grava em `guardrail_violations` |

Regra de ouro: **na dúvida, `blocked`.** O custo de uma campanha abortada é um humano reagendar. O custo de 30 contas comentando algo alegre num post sobre uma morte é a operação inteira.

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
