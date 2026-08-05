# Especificação — media-forge

> ⚠️ **Este serviço NÃO EXISTE.** Diferente dos outros documentos deste diretório, aqui não há contrato a confirmar — **este documento é a especificação**. É o que será construído na **Fase 5** e, até lá, é o alvo contra o qual o cliente do Olimpo pode ser escrito e testado com um fake.
>
> Onde os outros docs perguntam, este **decide**. Marcas ❓ aqui indicam decisão de implementação em aberto, não desconhecimento sobre um serviço alheio.

| | |
|---|---|
| **Papel** | Corte e edição de vídeo: vídeo longo → N clipes verticais prontos para publicar |
| **Variáveis** | `MEDIA_FORGE_URL`, `MEDIA_FORGE_TOKEN` |
| **Cliente** | `src/olimpo/integrations/media_forge/` |
| **Consumidor no core** | `DerivedSource` — o `ContentSource` do arquétipo **clipagem** |
| **Fase** | 5 |
| **Por que existe** | É o buraco nº 1 do [PLANO §1.2](../../PLANO.md): nem a máquina de influencers nem o orchestrator fazem edição |

## O problema que resolve

Uma conta de clipagem consome uma live de 3 h, um podcast de 90 min, um vídeo de YouTube — e precisa entregar 5 a 10 clipes verticais de 30–60 s, legendados, que funcionem sozinhos e prendam nos primeiros 2 segundos. Hoje isso é trabalho manual de horas. O media-forge é o serviço que transforma isso em uma chamada de API.

Vale também para o influencer (Fase 3+): montar um reels de 30 s a partir de clipes de 8 s gerados pela máquina de influencers, com legenda e trilha, é exatamente a mesma pipeline com o `subtitles` e `soundtrack` ligados.

---

## 1. Arquitetura: sempre assíncrono

Processar uma live de 3 h leva de minutos a dezenas de minutos. Não existe modo síncrono.

```
POST /v1/jobs  ──▶  job_id  ──▶  webhook (primário) + polling (fallback)  ──▶  GET /v1/jobs/{id}  ──▶  N clipes
```

Mesmo padrão do orchestrator: **webhook como caminho feliz, polling como rede de segurança** — ver [android-orchestrator §5](android-orchestrator.md#5-verificação-de-conclusão--webhook-com-fallback-de-polling). Latência não é crítica aqui (clipagem roda em lote), mas task zumbi continua sendo inaceitável.

### Autenticação

```http
POST /v1/jobs HTTP/1.1
Authorization: Bearer ${MEDIA_FORGE_TOKEN}
Content-Type: application/json
Idempotency-Key: olimpo:job:7c1a9f30-…
X-Trace-Id: olimpo-2026-08-05-7f3a91
```

Bearer estático de serviço. Webhook de volta assinado com HMAC-SHA256 sobre o corpo bruto, mesmo esquema do orchestrator.

---

## 2. A pipeline

```
┌─────────────────────────────────────────────────────────────────────┐
│  1. INGEST      baixa (URL) ou recebe (upload) · valida · normaliza  │
│                 saída: mezanino H.264, áudio PCM                    │
├─────────────────────────────────────────────────────────────────────┤
│  2. AUDIO       extrai faixa de áudio, normaliza para 16 kHz mono   │
├─────────────────────────────────────────────────────────────────────┤
│  3. ASR         Whisper → transcrição com timestamp por palavra     │
│                 + diarização de locutor (quem fala quando)          │
├─────────────────────────────────────────────────────────────────────┤
│  4. DETECT      encontra momentos de alto valor                     │
│                 sinais: semântico (LLM) + prosódia + retórica       │
│                 saída: candidatos com score de "clipabilidade"      │
├─────────────────────────────────────────────────────────────────────┤
│  5. CUT         corta em fronteira de sentença/silêncio             │
│                 nunca no meio de uma palavra                        │
├─────────────────────────────────────────────────────────────────────┤
│  6. REFRAME     16:9 → 9:16 com tracking de sujeito                 │
│                 rosto/pessoa ativa sempre no enquadramento          │
├─────────────────────────────────────────────────────────────────────┤
│  7. SUBTITLE    legenda queimada estilizada, palavra a palavra      │
│                 sincronizada ao word-level timestamp do ASR         │
├─────────────────────────────────────────────────────────────────────┤
│  8. AUDIO MIX   [opcional] trilha/áudio em alta sob a fala,         │
│                 com ducking automático                              │
├─────────────────────────────────────────────────────────────────────┤
│  9. ENCODE      H.264 High, 1080×1920, entrega + metadados          │
└─────────────────────────────────────────────────────────────────────┘
```

### 2.1 Detecção de momento — o passo que define o produto

Corte, reframe e legenda são engenharia resolvida. **Escolher o que vira clipe é o valor real do serviço.** Um serviço que corta bem os momentos errados é inútil.

Combinação de sinais, com pesos configuráveis:

| Sinal | Como | Peso |
|---|---|---|
| **Semântico** | LLM lê a transcrição em janelas e pontua: tem tese? tem virada? funciona fora de contexto? | 0.40 |
| **Gancho de abertura** | as primeiras palavras seguram sozinhas? (pergunta, número, afirmação polêmica) | 0.20 |
| **Prosódia** | pico de energia/pitch, aceleração da fala, ênfase | 0.15 |
| **Reação** | risada, exclamação, sobreposição de falas, silêncio dramático | 0.10 |
| **Autocontenção** | o trecho se entende sem o que veio antes? (penaliza "como eu disse") | 0.10 |
| **Densidade** | palavras/segundo dentro de faixa útil (nem arrastado, nem atropelado) | 0.05 |

O `clipability_score` (0..1) devolvido por clipe é a **combinação normalizada** desses sinais, e vem sempre com o `score_breakdown` — o Olimpo precisa saber *por que* um trecho pontuou alto, tanto para calibrar quanto para auditar.

**Requisito duro:** a detecção **nunca** entrega clipe que comece no meio de uma frase. Fronteira de corte é sempre silêncio ≥ 250 ms ou fim de sentença detectado pelo ASR.

### 2.2 Reframe com tracking

16:9 → 9:16 perde 56% da largura. Crop estático no centro corta a cabeça de quem está falando na lateral — o modo de falha mais visível.

| Modo | Comportamento | Quando |
|---|---|---|
| `subject_tracking` | segue o sujeito ativo (default) | podcast, entrevista, live |
| `speaker_switch` | alterna o crop conforme quem fala (usa a diarização) | dois ou mais interlocutores |
| `split_screen` | dois rostos empilhados vertical | debate, reação |
| `center_safe` | crop central fixo | gameplay, tela compartilhada |
| `blur_pad` | vídeo inteiro no centro, fundo borrado | último recurso — preserva tudo, ocupa mal |

Regras: movimento de câmera **suavizado** (nada de tremer seguindo micro-movimento); zero cortes bruscos de enquadramento em menos de 1,5 s; e queda para `blur_pad` se o tracking perder confiança, em vez de entregar clipe com a cabeça cortada.

### 2.3 Legenda queimada

Legenda queimada não é acessibilidade — é retenção. A maioria assiste sem som.

- Sincronização **palavra a palavra** (word-level timestamp do Whisper), com a palavra corrente destacada.
- Estilo por preset, configurável pelo Olimpo (fonte, tamanho, contorno, cor de destaque, posição, maiúsculas).
- Máximo de 3–4 palavras por vez na tela.
- Posição segura: fora dos 12% superiores e dos 20% inferiores (onde a UI do Instagram/TikTok cobre).

---

## 3. Submeter um job

```
POST {MEDIA_FORGE_URL}/v1/jobs
```

```json
{
  "client_job_id": "7c1a9f30-4b2e-4f19-8c05-1a9b7d3e2f44",
  "source": {
    "mode": "url",
    "url": "https://www.youtube.com/watch?v=EXEMPLO123",
    "kind": "youtube",
    "range": { "start_seconds": 0, "end_seconds": null }
  },
  "output": {
    "count": { "min": 3, "max": 10 },
    "duration_seconds": { "min": 20, "max": 75, "target": 45 },
    "aspect_ratio": "9:16",
    "resolution": { "width": 1080, "height": 1920 },
    "min_clipability_score": 0.55
  },
  "asr": {
    "model": "whisper-large-v3",
    "language": "pt",
    "diarization": true,
    "word_timestamps": true
  },
  "detect": {
    "strategy": "hybrid",
    "topics_of_interest": ["disciplina", "rotina", "erro comum"],
    "avoid_topics": ["política", "saúde"],
    "require_self_contained": true
  },
  "reframe": {
    "mode": "subject_tracking",
    "fallback_mode": "blur_pad",
    "smoothing": 0.7
  },
  "subtitles": {
    "enabled": true,
    "style_preset": "bold_yellow_highlight",
    "position": "center_lower",
    "max_words_visible": 3,
    "uppercase": false,
    "highlight_color": "#FFD400",
    "font": "Inter Black",
    "outline_width": 6
  },
  "soundtrack": {
    "enabled": false,
    "audio_ref": null,
    "volume": 0.12,
    "ducking": true
  },
  "watermark": {
    "enabled": true,
    "text": "@corta.do.dia",
    "position": "bottom_right",
    "opacity": 0.6
  },
  "attribution": {
    "creator_handle": "@criador.alvo",
    "source_url": "https://www.youtube.com/watch?v=EXEMPLO123",
    "burn_in": true,
    "burn_in_position": "top_center"
  },
  "callback": {
    "url": "http://olimpo.local:8000/webhooks/media-forge",
    "events": ["accepted", "progress", "completed", "failed"]
  },
  "metadata": {
    "olimpo_account_id": "5a2c…",
    "olimpo_client_id": "cli_88…",
    "trace_id": "olimpo-2026-08-05-7f3a91"
  }
}
```

### Campos

| Campo | Tipo | Obrig. | Notas |
|---|---|---|---|
| `client_job_id` | uuid | sim | Nosso id; ancora idempotência |
| `source.mode` | `url` \| `upload` | sim | Ver §3.1 |
| `source.kind` | `youtube`\|`twitch`\|`direct`\|`rtmp_recording` | sim | Define o ingestor |
| `source.range` | object | não | Processar só um trecho (live longa em pedaços) |
| `output.count` | `{min,max}` | sim | Serviço entrega **até** `max`, e ≥ `min` se houver material |
| `output.min_clipability_score` | 0..1 | sim | Corte de qualidade. Melhor entregar 3 bons que 10 medianos |
| `asr.language` | ISO 639-1 \| `auto` | sim | `pt` explícito é mais confiável que autodetecção |
| `detect.avoid_topics` | array | não | **Guardrail:** trechos sobre esses temas são descartados |
| `detect.require_self_contained` | bool | sim | Penaliza trecho que só faz sentido em contexto |
| `attribution` | object | **sim para clipagem** | Ver §3.2 |

### 3.1 Upload em vez de URL

```json
{
  "source": { "mode": "upload", "upload_id": "upl_01J8Y…", "kind": "direct" }
}
```

Fluxo: `POST /v1/uploads` → recebe `upload_url` pré-assinada + `upload_id` → PUT do arquivo → referencia no job. Nunca multipart no corpo do job: arquivo de 3 h não cabe em request JSON.

### 3.2 Atribuição é obrigatória e não é opcional na UI

O risco dominante da clipagem é **DMCA/copyright** ([PLANO §9](../../PLANO.md)). O media-forge participa da mitigação de duas formas:

1. `attribution.burn_in: true` queima o crédito no vídeo — não depende da legenda, que o Instagram trunca.
2. Todo clipe retorna `source_timestamp`, ligando cada saída ao ponto exato da origem. É o que sustenta `content_items.source_refs` e a auditoria de direitos.

**Requisito de API:** job de clipagem (`metadata.olimpo_account_id` de conta `archetype = clipper`) **rejeita com `422`** se `attribution.creator_handle` estiver vazio. Guardrail no serviço, não só no cliente — guardrail que mora em um lugar só é guardrail que alguém contorna.

---

## 4. Estados do job

| Estado | Significado | Terminal |
|---|---|---|
| `queued` | aceito, aguardando worker | não |
| `downloading` | baixando ou recebendo a fonte | não |
| `transcribing` | extração de áudio + ASR | não |
| `analyzing` | detecção de momentos | não |
| `rendering` | corte, reframe, legenda, encode | não |
| `completed` | ✅ clipes prontos | **sim** |
| `partial` | ⚠️ alguns clipes falharam, outros prontos | **sim** |
| `failed` | ❌ nenhum clipe produzido | **sim** |
| `cancelled` | abortado via `DELETE` | **sim** |
| `expired` | artefatos removidos por retenção | **sim** |

```
queued ──▶ downloading ──▶ transcribing ──▶ analyzing ──▶ rendering ──┬──▶ completed
   │            │                │              │             │        ├──▶ partial
   │            └────────────────┴──────────────┴─────────────┘        └──▶ failed
   └──▶ cancelled (a qualquer momento antes de terminal)
                                                        completed ──▶ expired (retenção)
```

`partial` existe de propósito: 8 clipes bons e 2 que falharam no reframe é entrega útil. Tratar isso como `failed` jogaria fora 40 min de processamento.

### Polling

```
GET {MEDIA_FORGE_URL}/v1/jobs/{job_id}
```

```json
{
  "job_id": "mf_01J8Y2K7RQ…",
  "client_job_id": "7c1a9f30-…",
  "status": "rendering",
  "progress": 0.72,
  "stage": { "name": "rendering", "detail": "clipe 6 de 9" },
  "source_duration_seconds": 5412,
  "started_at": "2026-08-05T02:10:00-03:00",
  "estimated_completion": "2026-08-05T02:34:00-03:00"
}
```

Backoff 10 s → 20 s → 30 s, teto 60 s; teto total 3× o `estimated_completion` inicial.

---

## 5. Resultado

```json
{
  "job_id": "mf_01J8Y2K7RQ…",
  "client_job_id": "7c1a9f30-…",
  "status": "completed",
  "source": {
    "url": "https://www.youtube.com/watch?v=EXEMPLO123",
    "title": "Como eu treino para maratona — episódio 44",
    "author": "criador.alvo",
    "duration_seconds": 5412,
    "published_at": "2026-08-03T12:00:00-03:00",
    "language_detected": "pt"
  },
  "transcript": {
    "url": "http://media-forge.local:9300/artifacts/mf_01J8Y2K7RQ/transcript.json",
    "format": "whisper_verbose_json",
    "word_count": 11840,
    "speakers": 2
  },
  "clips": [
    {
      "clip_id": "clip_01J8Y3A1…",
      "index": 0,
      "url": "http://media-forge.local:9300/artifacts/mf_01J8Y2K7RQ/clip_00.mp4",
      "thumbnail_url": "http://media-forge.local:9300/artifacts/mf_01J8Y2K7RQ/clip_00.jpg",
      "sha256": "b71e4f…",
      "duration_seconds": 43.6,
      "source_timestamp": { "start_seconds": 1284.2, "end_seconds": 1327.8 },
      "clipability_score": 0.87,
      "score_breakdown": {
        "semantic": 0.91, "hook": 0.88, "prosody": 0.74,
        "reaction": 0.60, "self_contained": 0.95, "density": 0.80
      },
      "transcript_excerpt": "todo mundo quer saber qual o tênis. ninguém quer saber que eu acordo às cinco há quatro anos, inclusive no domingo, inclusive na chuva. o tênis não é o segredo.",
      "hook_text": "todo mundo quer saber qual o tênis.",
      "topics": ["disciplina", "rotina", "erro comum"],
      "speakers": ["SPEAKER_00"],
      "reframe": { "mode_used": "subject_tracking", "confidence": 0.93, "fallback_used": false },
      "subtitles": { "burned_in": true, "sidecar_url": "…/clip_00.srt" },
      "video": {
        "width": 1080, "height": 1920, "fps": 30,
        "codec": "h264", "profile": "high",
        "video_bitrate_kbps": 8200, "audio_bitrate_kbps": 128,
        "audio_codec": "aac", "loudness_lufs": -14.0,
        "file_size_bytes": 44820112
      },
      "warnings": []
    },
    {
      "clip_id": "clip_01J8Y3A2…",
      "index": 1,
      "url": "…/clip_01.mp4",
      "duration_seconds": 31.2,
      "source_timestamp": { "start_seconds": 3102.0, "end_seconds": 3133.2 },
      "clipability_score": 0.64,
      "transcript_excerpt": "o erro que eu mais vejo é começar rápido demais…",
      "hook_text": "o erro que eu mais vejo…",
      "reframe": { "mode_used": "blur_pad", "confidence": 0.41, "fallback_used": true },
      "warnings": ["subject_tracking_low_confidence"]
    }
  ],
  "stats": {
    "candidates_found": 27,
    "clips_delivered": 9,
    "rejected_below_threshold": 15,
    "rejected_by_avoid_topics": 3,
    "processing_time_seconds": 1412,
    "realtime_factor": 0.26
  },
  "artifacts_expire_at": "2026-08-12T02:34:00-03:00",
  "completed_at": "2026-08-05T02:33:32-03:00"
}
```

### O que o Olimpo faz com cada campo

| Campo | Destino |
|---|---|
| `clips[].url` | baixado para storage próprio → `content_items.media_refs` |
| `clips[].source_timestamp` | `content_items.source_refs` — auditoria de direitos |
| `clips[].transcript_excerpt` | insumo da LLM para escrever a caption **na voz da persona** |
| `clips[].hook_text` | primeira linha da caption / texto de capa |
| `clips[].clipability_score` | ordena a fila de publicação; abaixo do gate vai para revisão |
| `clips[].topics` | casa com o nicho da conta e com `trends` |
| `stats.rejected_by_avoid_topics` | prova de que o guardrail rodou — vai para `events` |
| `warnings` | `fallback_used: true` → revisão humana antes de publicar |
| `artifacts_expire_at` | prazo para copiar; o Olimpo nunca depende do storage alheio |

O `transcript_excerpt` merece destaque: sem ele, a conta de clipagem publica com caption genérica. Com ele, a LLM escreve algo que **conversa com o que está dito no clipe** — a diferença entre uma conta de clipagem e um bot de repost.

---

## 6. Webhook

```
POST http://olimpo.local:8000/webhooks/media-forge
X-Olimpo-Signature: sha256=…
X-Olimpo-Delivery: dlv_…
```

```json
{
  "event": "completed",
  "job_id": "mf_01J8Y2K7RQ…",
  "client_job_id": "7c1a9f30-…",
  "status": "completed",
  "clips_count": 9,
  "result_url": "/v1/jobs/mf_01J8Y2K7RQ…",
  "metadata": { "trace_id": "olimpo-…" }
}
```

Payload magro de propósito: o webhook **notifica**, o Olimpo **busca**. O resultado completo de 9 clipes é grande demais para um corpo de webhook, e buscar via `GET` autenticado evita depender da integridade da entrega.

Eventos: `accepted`, `progress` (a cada 25%), `completed`, `partial`, `failed`. Mesmas exigências do orchestrator: HMAC, anti-replay, idempotência por `X-Olimpo-Delivery`, resposta em < 200 ms.

---

## 7. Requisitos não-funcionais

### 7.1 Tempo de processamento

Medido em **realtime factor** (RTF) = tempo de processamento ÷ duração da fonte.

| Fonte | RTF alvo | Máximo aceitável | Exemplo |
|---|---|---|---|
| ≤ 15 min | 0.30 | 0.60 | vídeo de 10 min → 3 min |
| 15–60 min | 0.25 | 0.50 | podcast de 45 min → ~11 min |
| 1–3 h | 0.20 | 0.40 | live de 3 h → ~36 min |
| > 3 h | 0.20 | 0.40 | processar em blocos com `source.range` |

Distribuição alvo por etapa (fonte de 1 h, GPU disponível):

| Etapa | Fatia |
|---|---|
| Download + normalização | 10% |
| ASR (Whisper large-v3, GPU) | 35% |
| Detecção (LLM sobre transcrição) | 15% |
| Render (corte + reframe + legenda + encode) | 40% |

**Requisito de fila:** um job de 3 h não pode bloquear um job de 10 min. Filas separadas por classe de duração, ou preempção — clipagem roda em lote noturno, mas um pedido pontual não deve esperar a live inteira.

### 7.2 Formato de saída

| Parâmetro | Valor | Justificativa |
|---|---|---|
| Container | MP4 (`faststart`) | universal; moov no início permite preview sem baixar tudo |
| Codec de vídeo | H.264 High profile, level 4.2 | máxima compatibilidade com o app do IG no Android |
| Resolução | **1080×1920** (9:16) | nativo do reels/story |
| FPS | 30 (ou preservar 60 se a fonte for 60) | — |
| Bitrate de vídeo | **6–10 Mbps** (VBR, alvo 8, pico 12) | acima disso a plataforma recomprime e não melhora nada |
| GOP / keyframe | 2 s, closed GOP | recompressão mais previsível |
| Codec de áudio | AAC-LC, 128 kbps, 48 kHz, estéreo | — |
| **Loudness** | **−14 LUFS integrado, pico ≤ −1 dBTP** | padrão de plataforma; clipe mais baixo que o resto do feed perde retenção |
| Tamanho | ≤ 100 MB por clipe | limite prático de upload |
| Duração | 15–90 s (alvo 30–60) | fora disso o formato não funciona |
| Thumbnail | JPEG 1080×1920, q85 | capa |

Também entregar, como sidecar: `.srt` da legenda (mesmo com queima) e o `transcript.json` completo. Custam quase nada e destravam reprocessamento sem refazer o ASR.

### 7.3 Confiabilidade e capacidade

| Requisito | Valor |
|---|---|
| Retenção de artefatos | ≥ 7 dias; `artifacts_expire_at` sempre no retorno |
| Idempotência | `Idempotency-Key` honrada por ≥ 24 h |
| Jobs concorrentes | ≥ 4 (Fase 5); escalável horizontalmente por worker |
| Retomada | falha em `rendering` não refaz o ASR — resultado do ASR é cacheado por `sha256` da fonte |
| Determinismo | mesma fonte + mesmos parâmetros = mesmos cortes (detecção com temperatura 0) |
| Cancelamento | `DELETE /v1/jobs/{id}` libera worker em ≤ 10 s |
| Health | `GET /v1/health` com fila, workers, profundidade e GPU |
| Observabilidade | RTF por etapa, taxa de `partial`, distribuição de `clipability_score` |

O cache de ASR por `sha256` é o requisito de maior retorno da lista: reprocessar a mesma live com outro preset de legenda deve custar o render, não os 35% do Whisper.

### 7.4 Falhas previstas

| `error.code` | Causa | Retenta? |
|---|---|---|
| `source_unavailable` | URL morta, privado, geobloqueado | ❌ |
| `source_too_long` | acima do teto configurado | ❌ — usar `source.range` |
| `no_speech_detected` | sem fala (música, gameplay mudo) | ❌ — pipeline depende de ASR |
| `asr_failed` | Whisper quebrou | ✅ 1× |
| `no_candidates_found` | nada acima de `min_clipability_score` | ❌ — **não é erro**: `completed` com `clips: []` |
| `render_failed` | ffmpeg falhou em um clipe | parcial → `partial` |
| `insufficient_capacity` | sem worker/GPU | ✅ backoff |
| `disk_full` | operacional | ❌ alerta crítico |

`no_candidates_found` como sucesso vazio é deliberado: "essa live não tinha nada que virasse clipe" é uma resposta legítima e informativa. Tratá-la como erro faria a conta publicar lixo para não ficar em silêncio.

---

## 8. Decisões de implementação em aberto

Não bloqueiam a escrita do cliente (o contrato acima é suficiente), mas precisam ser resolvidas ao construir o serviço na Fase 5:

| # | Decisão | Opções |
|---|---|---|
| 1 | Whisper: `faster-whisper` (CTranslate2) vs. `whisper.cpp` vs. API | ❓ RTF vs. custo vs. GPU disponível |
| 2 | Detecção de momento: LLM local (Mac minis) ou modelo dedicado | ❓ Reusar o LLM Gateway é o caminho barato |
| 3 | Tracking de sujeito: YOLO + ByteTrack, MediaPipe, ou serviço pronto | ❓ |
| 4 | Diarização: `pyannote` (licença) vs. alternativa | ❓ |
| 5 | Render: ffmpeg puro vs. MoviePy vs. ffmpeg + filtros customizados | ❓ ffmpeg puro é o mais rápido e o mais chato de escrever |
| 6 | Storage de artefatos: MinIO local, S3, disco | ❓ |
| 7 | Fila: Celery/Redis (mesma do Olimpo) ou fila própria | ❓ |
| 8 | Legenda: renderizar como ASS via ffmpeg vs. compor frames | ❓ ASS é ordens de grandeza mais rápido |
| 9 | Serviço separado ou módulo do Olimpo? | **Separado** — precisa de GPU e escala independente do core |
| 10 | Preset de legenda: fixo no serviço ou enviado pelo Olimpo? | ❓ Presets nomeados + override é o meio-termo |

---

## 9. O que fica para depois da Fase 5

Fora de escopo na primeira versão, mas o schema já reserva espaço:

- **B-roll automático** (inserir imagem/vídeo ilustrativo sobre a fala)
- **Zoom dinâmico** sincronizado a ênfase
- **Tradução + dublagem** para outros idiomas
- **A/B de ganchos** — mesmo clipe com 3 aberturas diferentes
- **Montagem multi-fonte** — clipes de fontes distintas no mesmo vídeo
- **Reels de influencer a partir de N clipes gerados** (encosta na Fase 3, mas depende deste serviço existir)

---

## Ver também

- [README deste diretório](README.md) — princípios comuns aos clientes
- [../03-integracoes.md](../03-integracoes.md) — o fluxo ponta a ponta
- [maquina-de-influencers.md](maquina-de-influencers.md) — gera a mídia bruta; **não** edita
- [android-orchestrator.md](android-orchestrator.md) — publica o resultado
- [../08-guardrails-e-autonomia.md](../08-guardrails-e-autonomia.md) — atribuição, direitos, DMCA
- [../fases/fase-5-clipagem.md](../fases/fase-5-clipagem.md) — handoff da fase
