# Contrato — máquina de influencers (persona engine)

> **Estado:** o serviço **existe e é funcional** (em desenvolvimento, repositório privado). Este documento descreve o **contrato que o Olimpo assume**, escrito sem acesso ao código.
>
> Cada ponto marcado ❓ **A CONFIRMAR** depende de checar a API real. As perguntas objetivas estão na seção final. **Este doc é checklist de integração.**

| | |
|---|---|
| **Papel** | Fábrica de persona e de mídia: história, geração de foto e vídeo, suporte a brands |
| **Variáveis** | `PERSONA_ENGINE_URL`, `PERSONA_ENGINE_TOKEN` |
| **Cliente** | `src/olimpo/integrations/persona_engine/` |
| **Consumidor no core** | `GeneratedSource` — o `ContentSource` do arquétipo **influencer** |
| **Fase** | 0 (dry-run) → 1 (geração real) → 3 (brands em campanha) |
| **Vínculo no banco** | `accounts.persona_engine_id` |

## Capacidades declaradas pelo dono

1. **Cria persona de influencer com história** — não é só um rosto, é um personagem.
2. **Gera fotos e vídeos para postagem.**
3. **Suporte a brands** — *"influencer de ID X segurando uma lata de energético para um anúncio"*.
4. **Não gera cortes/edição de vídeo** — isso é o buraco que o [media-forge](media-forge.md) preenche na Fase 5.

## Divisão de responsabilidade (a fronteira que não pode borrar)

Persona existe nos **dois** lados, com significados diferentes. Confundi-los cria duas fontes de verdade.

| | máquina de influencers | Olimpo |
|---|---|---|
| **Guarda** | identidade **visual**: rosto, corpo, estética, referência facial | identidade **narrativa e operacional** |
| Artefato | `persona_id` + face reference/seed | `accounts.persona_card` (jsonb) + `story_arcs` + `memories` |
| Responde | *"como ela se parece?"* | *"o que ela viveu, o que ela pensa, o que ela vai postar hoje?"* |
| Muda quando | raramente (mudar o rosto é refundar a conta) | todo dia (`REFLECT` reescreve a história) |

**Regra:** a máquina de influencers é a **fonte de verdade da aparência**; o Olimpo é a **fonte de verdade da vida**. O Olimpo nunca duplica a face reference; a máquina nunca precisa saber o que a persona postou ontem.

O elo é um único campo: `accounts.persona_engine_id`.

```
accounts
├── id                 9b1e7c44-…       ← identidade no Olimpo
├── handle             luna.corre
├── persona_card       {voz, tabus, estética, nicho, …}   ← Persona Card (docs/04)
└── persona_engine_id  "per_01J7Y…"     ← identidade na máquina de influencers
```

❓ **A CONFIRMAR:** o `persona_id` é string opaca? Há relação 1:1 com conta, ou uma persona pode servir várias contas (mesmo rosto, contas diferentes)? *Do ponto de vista anti-detecção, reusar o mesmo rosto em contas diferentes é péssima ideia — mas é uma decisão que a API pode permitir ou não.*

---

## 1. Autenticação

```http
POST /v1/personas HTTP/1.1
Authorization: Bearer ${PERSONA_ENGINE_TOKEN}
Content-Type: application/json
Idempotency-Key: olimpo:media:3f81c2a0-…:0
X-Trace-Id: olimpo-2026-08-05-7f3a91
```

❓ **A CONFIRMAR:** esquema (`Bearer` vs `X-API-Key`), rotação, escopo.

---

## 2. Criação e consulta de persona

### 2.1 Criar persona

```
POST {PERSONA_ENGINE_URL}/v1/personas
```

```json
{
  "name": "Luna",
  "backstory": "23 anos, mudou do interior pra capital, virou corredora depois de um ano difícil. Treina de manhã cedo. Fala pouco, mas quando fala é direto.",
  "appearance": {
    "age_range": [22, 26],
    "ethnicity": "parda",
    "hair": "castanho escuro, ondulado, na altura do ombro",
    "build": "atlética, magra",
    "distinctive_features": ["sarda no nariz", "cicatriz pequena na sobrancelha esquerda"],
    "style": "roupa esportiva funcional, cores neutras, sem maquiagem pesada"
  },
  "aesthetic": {
    "palette": ["bege", "cinza", "verde musgo"],
    "lighting": "luz natural, hora dourada, sombras suaves",
    "settings": ["rua urbana ao amanhecer", "parque", "apartamento pequeno e claro"],
    "camera": "fotografia de celular, levemente granulada, sem look de estúdio"
  },
  "constraints": {
    "no_real_person_likeness": true,
    "nsfw": false
  },
  "metadata": { "olimpo_account_id": "9b1e7c44-…" }
}
```

⚠️ `no_real_person_likeness: true` não é decoração. O guardrail *"rosto 100% sintético, nunca baseado em pessoa real"* ([PLANO §7](../../PLANO.md)) é obrigação legal, não preferência estética. ❓ **A CONFIRMAR:** existe esse controle, e o serviço rejeita prompts que referenciem pessoa real?

### Resposta esperada

```json
{
  "persona_id": "per_01J7YQ8M3KD5N9ZR",
  "name": "Luna",
  "status": "ready",
  "face_reference": {
    "reference_id": "faceref_01J7YQ9…",
    "seed": 884213771,
    "model": "sdxl-turbo-persona-v3",
    "preview_url": "http://persona-engine.local:9100/artifacts/per_01J7Y…/face.png",
    "embedding_available": true
  },
  "created_at": "2026-08-05T10:02:11-03:00"
}
```

O Olimpo grava `persona_id` em `accounts.persona_engine_id` e guarda o bloco `face_reference` **inteiro** em `accounts.persona_card.visual` — não porque precise usá-lo diretamente, mas para conseguir **auditar** quando a consistência quebrar (§4).

❓ **A CONFIRMAR:** a criação é síncrona ou vira job? Se a persona precisa "treinar" (LoRA, embedding facial), leva minutos ou horas — o Olimpo precisa de `status: pending` + polling.

### 2.2 Consultar persona

```
GET {PERSONA_ENGINE_URL}/v1/personas/{persona_id}
```

Usado em: seed do painel, health de conta, e verificação de que a persona ainda existe antes de gerar. ❓ Retorna as gerações anteriores? Seria útil para o QA visual comparar com o histórico.

### 2.3 Atualizar persona

❓ **A CONFIRMAR:** dá para atualizar (novo guarda-roupa, corte de cabelo, mudança de estação)? **Se sim, é operação sensível**: mudar a persona invalida a consistência com os posts anteriores. O Olimpo trataria isso como versionamento — mesma lógica de `persona_versions`, com registro em `events`.

---

## 3. Geração de imagem

```
POST {PERSONA_ENGINE_URL}/v1/personas/{persona_id}/images
```

```json
{
  "prompt": "correndo em uma avenida vazia ao amanhecer, vista de trás em três quartos, respiração visível no ar frio",
  "negative_prompt": "estúdio, maquiagem pesada, pose de modelo, mãos deformadas",
  "count": 3,
  "aspect_ratio": "4:5",
  "resolution": { "width": 1080, "height": 1350 },
  "consistency": {
    "face_reference_id": "faceref_01J7YQ9…",
    "seed": 884213771,
    "seed_mode": "fixed",
    "strength": 0.85
  },
  "context": {
    "time_of_day": "amanhecer",
    "setting": "avenida urbana vazia",
    "outfit": "regata cinza, shorts preto, tênis de corrida"
  },
  "metadata": {
    "olimpo_content_item_id": "3f81c2a0-…",
    "olimpo_account_id": "9b1e7c44-…",
    "trace_id": "olimpo-2026-08-05-7f3a91"
  }
}
```

| Campo | Notas |
|---|---|
| `prompt` | Escrito pela LLM local a partir do `day_plans.narrative` + `persona_card.aesthetic` |
| `count` | Gera N variações; o QA escolhe. **3 é o default** — gerar uma só e aceitar é como o rosto derrapa sem ninguém notar |
| `aspect_ratio` | `4:5` feed, `9:16` story/reels, `1:1` grid |
| `consistency` | **O bloco mais importante do documento.** Ver §4 |
| `context` | Estruturado, não só prosa: facilita ao serviço manter coerência de guarda-roupa e cenário |

### Resposta (síncrona)

```json
{
  "generation_id": "gen_01J8XP2R…",
  "persona_id": "per_01J7YQ8M3KD5N9ZR",
  "status": "completed",
  "images": [
    { "index": 0,
      "url": "http://persona-engine.local:9100/artifacts/gen_01J8XP2R/0.png",
      "width": 1080, "height": 1350, "sha256": "a91f…",
      "seed_used": 884213771,
      "face_similarity": 0.94,
      "expires_at": "2026-08-12T10:00:00-03:00" },
    { "index": 1, "url": "…/1.png", "seed_used": 884213772, "face_similarity": 0.91 },
    { "index": 2, "url": "…/2.png", "seed_used": 884213773, "face_similarity": 0.72 }
  ],
  "generation_time_ms": 18400,
  "model": "sdxl-turbo-persona-v3"
}
```

`face_similarity` é o campo que o Olimpo mais gostaria que existisse: transforma o QA visual em um número. ❓ **A CONFIRMAR se existe.** Se não existir, o Olimpo calcula por conta própria — embedding facial da imagem gerada vs. embedding canônico da persona, comparação por cosseno, gate configurável (ver §4).

No exemplo acima, a imagem `index: 2` (0.72) seria **rejeitada** pelo QA.

---

## 4. ⚠️ Consistência facial — o risco nº 1 do arquétipo influencer

> Uma influencer que muda de cara entre posts **não é uma influencer**. É um gerador de imagens com um handle. Nenhum seguidor real segue isso, e o padrão é imediatamente reconhecível como IA descuidada.

O [PLANO §9](../../PLANO.md) lista "inconsistência visual" como risco do arquétipo influencer, com a mitigação: *"face reference/seed fixa na máquina de influencers + QA por embedding de rosto vs. canônico"*. Este documento é onde essa mitigação vira contrato.

### Os três mecanismos

| Mecanismo | O que é | Força | Custo |
|---|---|---|---|
| **Seed fixa** | mesmo ruído inicial | fraca isolada — muda pose e a cara vai junto | zero |
| **Face reference** | imagem/embedding facial canônico injetado (IP-Adapter, InstantID, LoRA…) | **forte** — é o mecanismo que resolve | depende do modelo |
| **LoRA / fine-tune por persona** | modelo treinado no rosto | mais forte, mais caro, mais lento de criar | alto |

❓ **A CONFIRMAR — este é o bloco de perguntas mais importante do documento:**

1. **Qual mecanismo o serviço usa?** Seed? Face reference? LoRA? Combinação?
2. **É determinístico?** Mesmo `persona_id` + mesma seed + mesmo prompt = mesma imagem?
3. **Qual o nome real do parâmetro?** (`face_reference_id`? `identity_id`? `lora_name`? `ip_adapter_image`?)
4. **Existe controle de intensidade** (`strength`, `identity_weight`)? Qual a faixa útil? *(Alto demais engessa a pose e produz sempre a mesma foto; baixo demais perde o rosto.)*
5. **O serviço devolve alguma métrica de similaridade facial?**
6. **A consistência se mantém em ângulo difícil?** Perfil, de costas, longe, mal iluminado, parcialmente coberto — é onde esses sistemas quebram.
7. **Vale para vídeo também**, ou vídeo tem consistência mais fraca? *(Normalmente tem. Se tiver, é decisão de produto: talvez reels da persona só apareça em plano em que o rosto não é o assunto.)*

### O gate do Olimpo (independente da resposta)

O Olimpo **não confia** na consistência do serviço. O QA visual da Fase 1 é obrigatório e roda sobre toda imagem antes de virar `content_item`:

```
imagem gerada
    │
    ├─▶ detecta rosto ──── nenhum rosto? ──▶ aceita (foto sem rosto é válida)
    │
    ├─▶ embedding facial vs. embedding canônico da persona
    │
    ├─ similaridade ≥ 0.90 ──▶ ✅ aprovada
    ├─ 0.80 ≤ sim < 0.90   ──▶ ⚠️ vai para revisão humana
    └─ similaridade < 0.80 ──▶ ❌ descartada, regenera (até 3×)
```

O embedding canônico nasce do `face_reference.preview_url` na criação da persona e fica congelado em `accounts.persona_card.visual.canonical_embedding`. Resultado de cada checagem entra em `content_items.qa_report`.

**Deriva é gradual e é assim que ela passa despercebida:** cada imagem individual parece aceitável, mas o rosto de agosto não é o de junho. Por isso o embedding canônico é **congelado na criação e nunca recalculado a partir de imagens geradas** — recalcular seria realimentar a própria deriva.

---

## 5. Geração de vídeo

```
POST {PERSONA_ENGINE_URL}/v1/personas/{persona_id}/videos
```

```json
{
  "prompt": "amarrando o tênis na calçada, levanta e começa a correr, câmera acompanha lateralmente",
  "duration_seconds": 8,
  "aspect_ratio": "9:16",
  "resolution": { "width": 1080, "height": 1920 },
  "fps": 30,
  "consistency": {
    "face_reference_id": "faceref_01J7YQ9…",
    "seed": 884213771,
    "seed_mode": "fixed"
  },
  "init_image": {
    "generation_id": "gen_01J8XP2R…",
    "index": 0
  },
  "audio": { "mode": "none" },
  "metadata": { "olimpo_content_item_id": "3f81c2a0-…", "trace_id": "olimpo-…" }
}
```

`init_image` (image-to-video a partir de uma imagem já aprovada no QA) é o caminho preferido do Olimpo: a consistência facial já foi validada no frame inicial, o que reduz muito o risco de deriva no vídeo.

❓ **A CONFIRMAR:** image-to-video existe? Qual a duração máxima? Gera áudio, ou é sempre mudo?

⚠️ **Limite conhecido:** a máquina de influencers **não faz corte nem edição**. Um reels de 30 s montado de 4 clipes de 8 s com legenda queimada e trilha **não sai daqui** — sai do [media-forge](media-forge.md). Na Fase 1, reels da influencer é clipe único e curto; montagem é Fase 5.

---

## 6. Geração com brand (produto na mão)

O caso citado pelo dono: *"influencer de ID X segurando uma lata de energético para um anúncio"*. É o que sustenta o publipost — a monetização da Fase 6.

```
POST {PERSONA_ENGINE_URL}/v1/personas/{persona_id}/images
```

```json
{
  "prompt": "sentada no meio-fio depois do treino, segurando a lata na altura do peito, sorriso discreto, olhando pra câmera",
  "count": 4,
  "aspect_ratio": "4:5",
  "consistency": { "face_reference_id": "faceref_01J7YQ9…", "seed": 884213771 },
  "brand": {
    "brand_id": "brd_energetico_x",
    "product": {
      "name": "Energético X 350ml",
      "reference_images": [
        "https://olimpo.local/brand-assets/energetico-x/lata-frente.png",
        "https://olimpo.local/brand-assets/energetico-x/lata-lateral.png"
      ],
      "placement": "held_in_hand",
      "visibility": "label_facing_camera",
      "scale_hint": "lata de 350ml, proporção real na mão"
    },
    "constraints": {
      "no_logo_distortion": true,
      "no_competitor_products": true,
      "keep_label_legible": true
    }
  },
  "metadata": { "olimpo_campaign_id": "c1d4…", "olimpo_content_item_id": "3f81c2a0-…" }
}
```

| Campo | Nota |
|---|---|
| `brand_id` | Persistido (catálogo do serviço) ou efêmero por request? ❓ |
| `reference_images` | Vêm de `campaigns.brand_refs`. ❓ URL nossa ou upload prévio? |
| `placement` | `held_in_hand`, `on_table`, `background`, `worn` ❓ quais existem? |
| `constraints` | Requisito de cliente pagante: **logo torto é entrega rejeitada** |

### QA de brand (dobrado)

Conteúdo com marca passa por **dois** gates, não um:

| Gate | Verifica | Falha significa |
|---|---|---|
| Facial (§4) | é a persona certa? | regenera |
| **Brand** | o produto está visível, íntegro, legível, sem deformação? | regenera + **nunca publica sem revisão humana** |

Publipost com produto deformado é dano de reputação com um terceiro envolvido. Guardrail do Olimpo: **conteúdo com `brand` nunca é auto-publicado**, independente do nível de autonomia da conta — nem em L3.

❓ **A CONFIRMAR:** o serviço devolve alguma métrica de fidelidade do produto? Aceita múltiplas referências (frente/lateral/topo)? Lida bem com texto na embalagem — ou o texto sai borrado, que é o modo de falha clássico?

---

## 7. Formato de retorno da mídia

❓ **A CONFIRMAR — decisão de arquitetura relevante.** Três formatos possíveis:

| Formato | Como é | Prós | Contras | Preferência |
|---|---|---|---|---|
| **URL** | `{"url": "http://persona-engine.local/artifacts/…"}` | payload leve, retry barato | expira? é acessível pelo orchestrator? | ✅ **preferido** |
| **Bytes / base64** | JSON com base64 | sem dependência de storage | payload gigante, log poluído, memória | ❌ |
| **id + download** | `{"asset_id": "…"}` + `GET /assets/{id}` | limpo, controla acesso | um round-trip a mais | 🟡 aceitável |

### O que o Olimpo faz em qualquer um dos casos

**Copia a mídia para o storage próprio antes de usar.** Sempre.

```
persona-engine ──▶ Olimpo baixa ──▶ storage próprio ──▶ URL estável
                                                          │
                                                          ├─▶ QA visual
                                                          ├─▶ content_items.media_refs
                                                          └─▶ payload do orchestrator
```

Três razões:

1. **Expiração.** Artefato que some em 7 dias quebra republicação, auditoria e reprocessamento.
2. **Acessibilidade.** O orchestrator precisa baixar a mídia. Se ele estiver em outra rede, a URL do persona-engine pode não resolver.
3. **Imutabilidade.** `content_items.media_refs` referencia bytes que não mudam, com `sha256` verificado.

`media_refs` no Olimpo:

```json
[
  {
    "kind": "image",
    "url": "https://olimpo.local/media/9c02f1a4.png",
    "sha256": "a91f2c…",
    "width": 1080, "height": 1350,
    "origin": {
      "service": "persona_engine",
      "generation_id": "gen_01J8XP2R…",
      "index": 0,
      "seed_used": 884213771,
      "face_similarity": 0.94,
      "brand_id": null
    }
  }
]
```

O bloco `origin` é o que permite responder *"com que seed essa foto foi feita?"* seis meses depois — e reproduzir a geração.

❓ **A CONFIRMAR:** os artefatos expiram? Em quanto tempo? Há autenticação para baixar (o Olimpo precisa mandar o token no `GET`)?

---

## 8. Tempo de geração e geração assíncrona

Geração de imagem leva segundos a dezenas de segundos; vídeo, minutos. Nenhum dos dois pode segurar um worker.

### Expectativa de latência

| Operação | Esperado ❓ | Timeout HTTP | Modo |
|---|---|---|---|
| Criar persona | 5 s – 10 min | 30 s | assíncrono se > 30 s |
| Imagem (1) | 5 – 30 s | 120 s | síncrono aceitável |
| Imagem (4 variações) | 20 – 120 s | 180 s | 🟡 limítrofe |
| Vídeo 8 s | 2 – 15 min | — | **sempre assíncrono** |
| Imagem com brand | +30 – 50% | — | idem imagem |

### Padrão adotado: job + polling, mesmo quando síncrono é possível

O cliente do Olimpo **sempre** trata geração como job. Se o serviço responder síncrono, o cliente devolve um job já concluído. Isso mantém um caminho de código só, e imuniza contra o dia em que o serviço mudar de comportamento sob carga.

```json
// POST .../images  →  202 Accepted
{
  "job_id": "job_01J8XQ7T…",
  "status": "queued",
  "estimated_seconds": 45,
  "poll_url": "/v1/jobs/job_01J8XQ7T…",
  "created_at": "2026-08-05T14:30:55-03:00"
}
```

```
GET {PERSONA_ENGINE_URL}/v1/jobs/{job_id}
```

```json
{
  "job_id": "job_01J8XQ7T…",
  "status": "running",
  "progress": 0.6,
  "queue_position": 0,
  "started_at": "2026-08-05T14:31:02-03:00"
}
```

```json
{
  "job_id": "job_01J8XQ7T…",
  "status": "completed",
  "result": { "generation_id": "gen_01J8XP2R…", "images": [ /* §3 */ ] },
  "generation_time_ms": 41200,
  "completed_at": "2026-08-05T14:31:44-03:00"
}
```

### Política de polling

| Parâmetro | Valor |
|---|---|
| Primeiro poll | após `estimated_seconds × 0.6`, mínimo 3 s |
| Intervalo | backoff 3 s → 5 s → 8 s → 13 s, teto 30 s |
| Teto total | imagem 5 min · vídeo 30 min · persona 60 min |
| Ao estourar | `failed`, evento `persona_engine.job.timeout`, cancela se houver `DELETE /v1/jobs/{id}` ❓ |
| Onde roda | worker dedicado (`WORKER_CONCURRENCY`), nunca no request HTTP do painel |

### Por que a produção é adiantada

Geração é lenta e imprevisível. O agente **não** gera no instante de publicar — publicar uma imagem que ainda está na fila é impossível. O pipeline separa:

```
PRODUCE (manhã)  ─── gera, faz QA, aprova ──▶ content_items.status = approved
                                                        │
DISPATCH (19h)   ─── mídia já pronta ───────────────────┘ → task no orchestrator
```

O buffer típico é de horas. Isso também dá espaço para regenerar quando o QA facial reprova — sem estourar a janela de publicação.

❓ **A CONFIRMAR:** é assíncrono ou síncrono? Existe fila? O `estimated_seconds`/`queue_position` existe? Há webhook de conclusão (dispensaria o polling)? Qual o throughput real — quantas gerações em paralelo antes de degradar?

---

## 9. Falha e degradação

| Erro | Reação do Olimpo |
|---|---|
| `persona_id` não existe | `ContractViolation` — conta com `persona_engine_id` órfão; bloqueia a conta e alerta |
| Job falhou (erro de modelo) | Retry 1× com seed diferente; 2 falhas → conteúdo do dia adiado |
| Fila cheia / `429` | Backoff; se persistir, o agente replaneja o dia com conteúdo de banco |
| Serviço fora (circuito aberto) | Produção adiada; **nunca** publicar sem mídia |
| QA facial reprovou 3× | `content_item` → `qa_failed`, revisão humana, evento `persona.drift.suspected` |
| Timeout de geração | Job cancelado; conta mantém a cadência com conteúdo já aprovado do banco |

**Banco de conteúdo aprovado** (previsto na Fase 6, mas útil desde a 1): manter 2–3 peças aprovadas em reserva por conta transforma "serviço de geração fora do ar" em não-evento. Sem reserva, a conta simplesmente some do ar naquele dia — e sumir e voltar é mais anômalo do que postar menos.

---

## 10. Perguntas para validar com o serviço real

### 🔴 Bloqueiam a Fase 1

| # | Pergunta | Por que bloqueia |
|---|---|---|
| 1 | **Qual o mecanismo de consistência facial** — seed, face reference, LoRA, combinação? | Sem resposta, não há influencer viável |
| 2 | **Qual o nome e a forma exata do parâmetro de consistência?** | É o campo mais importante de todo request |
| 3 | **É determinístico?** Mesma seed + mesmo prompt = mesma imagem? | Define se dá para reproduzir e auditar |
| 4 | Endpoints reais de **criar persona**, **gerar imagem** e **gerar vídeo** | Sem isso não há cliente |
| 5 | **Síncrono ou assíncrono?** Se assíncrono, qual o formato do job e do polling? | Define a arquitetura do worker |
| 6 | **Formato de retorno da mídia** — URL, bytes ou id? Expira? Exige token no download? | Define o storage |
| 7 | O `persona_id` é **1:1 com conta** ou reusável? | Define `accounts.persona_engine_id` |
| 8 | Existe **métrica de similaridade facial** no retorno? | Se não, o Olimpo tem que implementar o embedding — trabalho extra na Fase 1 |

### 🟡 Bloqueiam Fases 2–3

| # | Pergunta |
|---|---|
| 9 | Quais **aspect ratios / resoluções** são suportados? Sai 1080×1350 e 1080×1920 nativo? |
| 10 | Dá para gerar **N variações** em um request? Qual o teto? |
| 11 | Existe **`negative_prompt`**? |
| 12 | Existe controle de **intensidade** da identidade (`strength`)? Qual a faixa útil? |
| 13 | A consistência aguenta **ângulo difícil** (perfil, longe, contraluz, rosto parcial)? |
| 14 | **Image-to-video** existe (animar uma imagem já aprovada)? |
| 15 | Duração máxima de vídeo? Gera **áudio**? |
| 16 | Consistência facial em **vídeo** é tão boa quanto em imagem? |
| 17 | Dá para **atualizar** uma persona (roupa, cabelo, estação) sem quebrar a consistência histórica? |
| 18 | Existe **catálogo de personas** (`GET /v1/personas`) para reconciliar com `accounts`? |

### 🟢 Brand (Fase 3+, monetização)

| # | Pergunta |
|---|---|
| 19 | Como o **brand** é passado — `brand_id` persistido ou referências por request? |
| 20 | Quantas **imagens de referência** do produto o serviço aceita? Ângulos diferentes ajudam? |
| 21 | Quais **placements** existem (`held_in_hand`, `on_table`, `worn`, `background`)? |
| 22 | O serviço mantém **logo e texto da embalagem legíveis**? Qual a taxa de acerto real? |
| 23 | Existe **métrica de fidelidade do produto** no retorno? |
| 24 | Dá para gerar **vídeo com brand**, ou só imagem? |

### ⚪ Operacionais e de guardrail

| # | Pergunta |
|---|---|
| 25 | Autenticação: `Bearer`? Rotação? Escopo? |
| 26 | Existe `/health`? Expõe fila e GPUs disponíveis? |
| 27 | **Throughput real** — quantas gerações concorrentes antes de degradar? |
| 28 | Existe **rate limit**? Retorna `Retry-After`? |
| 29 | O serviço **recusa** prompts com semelhança a pessoa real? (obrigação legal) |
| 30 | Há filtro NSFW? É configurável? |
| 31 | Existe **webhook** de conclusão de job? (dispensaria o polling) |
| 32 | Existe OpenAPI/Swagger? *(**peça isto primeiro**)* |
| 33 | Dá para **cancelar** um job em andamento? |
| 34 | Os artefatos **expiram**? Em quanto tempo? |

---

## Ver também

- [README deste diretório](README.md) — princípios comuns aos clientes
- [../03-integracoes.md](../03-integracoes.md) — o fluxo ponta a ponta
- [../04-persona-card-spec.md](../04-persona-card-spec.md) — o Persona Card do lado do Olimpo
- [media-forge.md](media-forge.md) — o que a máquina de influencers **não** faz
- [../08-guardrails-e-autonomia.md](../08-guardrails-e-autonomia.md) — guardrails do arquétipo influencer
