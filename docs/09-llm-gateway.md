# 09 — LLM Gateway

**Status:** especificação · **Implementado em:** Fase 0 (gateway + contabilidade), Fase 1 (catálogo de tarefas), Fase 3 (roteamento por prioridade), Fase 4 (fila de baixa latência)

Documentos relacionados: [01 — Arquitetura](01-arquitetura.md) · [05 — Loop do agente](05-loop-do-agente.md) · [08 — Guardrails](08-guardrails-e-autonomia.md) · [11 — Observabilidade](11-observabilidade.md)
Configuração: [`ops/litellm.config.yaml`](../ops/litellm.config.yaml)

---

## 1. Topologia

O core **nunca** fala com um provedor de LLM diretamente. Toda chamada — local ou externa — atravessa o LiteLLM. Isso não é elegância arquitetural: é a única forma de ter contabilidade honesta, fallback automático e a capacidade de trocar o backend de uma tarefa sem tocar em código de agente.

```
┌──────────────────────────────────────────────────────────────────────────┐
│                          OLIMPO CORE  (Docker)                           │
│                                                                          │
│   agents/loop  ·  agents/interact  ·  campaigns/recon  ·  memory/reflect │
│        └───────────────┬───────────────────────────────────────┘         │
│                        ▼                                                 │
│              olimpo.llm.gateway                                          │
│              • escolhe o ALIAS pelo `purpose`                            │
│              • injeta trace_id + account_id                              │
│              • grava linha em `llm_calls`                                │
│              • valida a saída com Pydantic                               │
└────────────────────────┬─────────────────────────────────────────────────┘
                         │ HTTP  (OpenAI-compatible)
                         │ POST http://litellm:4000/v1/chat/completions
                         │ Authorization: Bearer $LITELLM_MASTER_KEY
                         ▼
┌──────────────────────────────────────────────────────────────────────────┐
│                 LiteLLM Proxy  (serviço `litellm`, :4000)                │
│                                                                          │
│   ALIASES LÓGICOS            ROTEAMENTO            RESILIÊNCIA           │
│   ├─ olimpo-fast             latency-based         retries: 2            │
│   ├─ olimpo-reason           usage-based           timeout por alias     │
│   ├─ olimpo-vision           weighted              cooldown 60s          │
│   ├─ olimpo-smart            fallback chain        circuit breaker       │
│   └─ olimpo-embed                                  budget diário         │
└───┬──────────────┬──────────────────┬──────────────────┬─────────────────┘
    │              │                  │                  │
    ▼              ▼                  ▼                  ▼
┌─────────┐   ┌─────────┐      ┌────────────┐     ┌────────────┐
│ MAC     │   │ MAC     │      │ ANTHROPIC  │     │  OPENAI    │
│ MINI 1  │   │ MINI 2  │      │    API     │     │    API     │
│ M4      │   │ M4      │      │            │     │            │
│ Ollama  │   │ Ollama  │      │ raciocínio │     │ overflow · │
│ :11434  │   │ :11435  │      │ · multimo- │     │ embeddings │
│         │   │         │      │ dal · QA   │     │ de reserva │
│ VOLUME  │   │ RACIO-  │      │ crítico    │     │            │
│ baixa   │   │ CÍNIO   │      │            │     │            │
│ latên-  │   │ + visão │      │  $$$       │     │  $$$       │
│ cia     │   │         │      │            │     │            │
└─────────┘   └─────────┘      └────────────┘     └────────────┘
  7B/8B         14B + VL         teto: LLM_DAILY_BUDGET_USD
  quantizado    quantizado       (estourou → degrada para local)
```

Três propriedades que essa topologia compra:

1. **O agente não sabe onde roda.** `gateway.complete(purpose="caption", ...)` escolhe o alias; o alias escolhe o backend. Trocar Qwen por Llama num Mac mini é uma linha no YAML.
2. **Queda de um Mac mini não para a operação.** O `olimpo-fast` cai para o Mac mini 2 e, se este também estiver fora, para a API externa — a interação em tempo real nunca fica sem atendimento.
3. **Custo é observável no nível da tarefa, não do mês.** Cada chamada carrega `purpose`, então "quanto custa uma influencer por dia" é uma query, não uma estimativa.

---

## 2. Catálogo de tarefas de LLM

Cada tarefa do sistema tem um `purpose` fixo — o mesmo valor gravado em `llm_calls.purpose`. Volume é estimado para **uma frota de 10 contas** (mistura típica: 6 influencers, 2 fofoca, 2 clipagem).

| `purpose` | Propósito | Exigência de qualidade | Volume/dia (10 contas) | Latência tolerável | Roteamento |
|---|---|---|---|---|---|
| `plan_day` | Decide o que a conta faz hoje: lê arco ativo, memórias recentes, métricas e campanhas; devolve intenções estruturadas | **Alta** — erra aqui e o dia inteiro sai torto | 10 (1/conta) | 30 s | `olimpo-reason` |
| `reflect` | Fecha o dia: sintetiza episódios, atribui importância, atualiza o estado do arco, gera memória semântica | **Alta** — é o que dá continuidade à persona | 10 | 60 s | `olimpo-reason` |
| `caption` | Escreve o caption/roteiro na voz da persona | Média — volume alto, tolera regeneração | 60–120 | 5 s | `olimpo-fast` |
| `image_prompt` | Traduz a intenção em prompt para a máquina de influencers (composição, luz, roupa, cenário, consistência) | Média-alta | 60–120 | 5 s | `olimpo-fast` → `olimpo-reason` em retry |
| `classify_interaction` | Classifica comentário/DM: `fan`/`hater`/`spam`/`opportunity`/`sensitive` + flag `sensitive` | **Crítica no recall de `sensitive`** — falso negativo aqui é violação de guardrail (`SENS-*`) | 500–2.000 | **1,5 s** | `olimpo-fast` (classificador dedicado, temperatura 0) |
| `reply_interaction` | Redige a resposta na voz da persona | Média | 300–1.200 | **3 s** | `olimpo-fast` |
| `recon_analysis` | **Multimodal.** Lê screenshots/vídeo capturados pelo device: do que se trata, tom, público, é seguro, que ângulos de comentário existem | **Alta** — decide se uma campanha inteira acontece | 5–20 | 45 s | `olimpo-vision` (Mac mini 2) → Anthropic se `safety_verdict` incerto |
| `campaign_adapt` | Traduz o brief da campanha para a realidade daquela persona (`local_adaptation`) | Média-alta | 20–80 (picos) | 10 s | `olimpo-fast` |
| `ingest_relevance` | **Fofoca.** Avalia um `ingested_item`: relevância para a linha editorial, novidade, `claim_status` sugerido, risco jurídico preliminar | Média — mas o recall de risco é crítico | 1.000–5.000 | **2 s** | `olimpo-fast` (prompt curto, saída JSON mínima) |
| `clip_detect` | **Clipagem.** Sobre o transcript do ASR: onde estão os momentos clipáveis, com timestamps e justificativa | Alta — define a qualidade do produto | 10–40 | 120 s (lote) | `olimpo-reason` |
| `qa_text` | QA textual: brand safety, aderência à persona, correspondência entre `claim_status` e texto, disclosure | **Crítica** — é guardrail, não conveniência | 100–200 | 4 s | `olimpo-fast` + **segunda opinião** em `olimpo-smart` quando o veredito é limítrofe |
| `memory_summarize` | Comprime episódios em memória semântica e atribui `importance` (0–10) | Média | 30–60 | 30 s | `olimpo-reason` |
| `embed` | Embeddings de memórias, captions e itens ingeridos (`vector(1024)`) | Determinística | 2.000–8.000 | 500 ms | `olimpo-embed` (Mac mini 1) |

### 2.1 Leitura da tabela

Duas observações que orientam todo o resto do documento:

**O volume está concentrado no que é barato de errar.** `classify_interaction`, `reply_interaction`, `ingest_relevance` e `embed` respondem por ~95% das chamadas e por ~0% do valor estratégico individual — errar uma classificação custa uma revisão humana. Esse é exatamente o perfil que justifica LLM local: alto volume, baixo custo unitário de erro, latência importa.

**O valor está concentrado no que é raro.** `plan_day`, `reflect`, `recon_analysis` e `clip_detect` são ~1% das chamadas e definem se a conta parece viva ou parece um bot. Aqui o custo de uma chamada externa é irrelevante perto do custo de um dia de conteúdo ruim.

**Exceção que não segue a regra: `classify_interaction`.** É alto volume *e* tem custo de erro alto — um falso negativo de `sensitive` fura o guardrail `SENS-001`. A solução não é mandar para API externa (custo proibitivo no volume), e sim: prompt curto, temperatura 0, saída JSON de um campo, **viés deliberado para o falso positivo** (na dúvida, marca como sensível e manda para humano) e auditoria contínua contra revisor humano como critério de promoção L1→L2 ([08 §3.1](08-guardrails-e-autonomia.md#31-promoção)).

---

## 3. Política de roteamento

### 3.1 As quatro regras

| # | Regra | Racional |
|---|---|---|
| **R1** | **Alto volume + baixa exigência → local, sempre.** `caption`, `reply_interaction`, `classify_interaction`, `ingest_relevance`, `embed` nunca saem para API externa em operação normal | Se 95% das chamadas fossem externas, o custo diário seria duas ordens de grandeza acima de `LLM_DAILY_BUDGET_USD` |
| **R2** | **Raciocínio e multimodal podem escalar.** `plan_day`, `reflect`, `recon_analysis`, `clip_detect` tentam local primeiro e escalam para API externa quando: a saída falha na validação Pydantic 2×, o `safety_verdict` do recon é incerto, ou o modelo local está saturado | Baixo volume × alto valor. Escalar aqui é barato e o ganho de qualidade é visível |
| **R3** | **Guardrail nunca é degradado por custo.** `qa_text` com veredito limítrofe pede segunda opinião em `olimpo-smart` mesmo com o orçamento estourado — o gasto é debitado de uma reserva separada | Economizar em QA é economizar no seguro |
| **R4** | **Interação em tempo real nunca para.** Se todos os backends de qualidade estiverem indisponíveis, `reply_interaction` degrada para o modelo local mais rápido disponível; se nem isso, a interação vira `approval_pending` para humano — mas nunca fica sem destino | Um comentário sem resposta por 6 h é pior que uma resposta simples |

### 3.2 Cadeia de fallback

Definida em `router_settings.fallbacks` no [`ops/litellm.config.yaml`](../ops/litellm.config.yaml):

```
olimpo-fast    →  olimpo-fast (outro Mac mini)  →  olimpo-reason  →  olimpo-smart (Anthropic)
olimpo-reason  →  olimpo-smart (Anthropic)      →  gpt (OpenAI)
olimpo-vision  →  olimpo-smart (Anthropic)      →  gpt-vision (OpenAI)
olimpo-embed   →  olimpo-embed (outro Mac mini) →  text-embedding-3-small (OpenAI)
olimpo-smart   →  gpt (OpenAI)
```

Gatilhos de fallback: timeout do alias, HTTP 5xx, connection refused, rate limit do provedor, **e** falha de validação Pydantic no core (esta é do core, não do LiteLLM — ver §6.3).

> ⚠️ **Trocar de backend de embedding troca o espaço vetorial.** O fallback de `olimpo-embed` para OpenAI existe para não travar o pipeline, mas os vetores gerados **não são comparáveis** com os do modelo local. O core marca `memories.metadata.embed_model` e a busca por similaridade filtra pelo modelo corrente. Após um fallback prolongado, o job `reembed_backlog` reprocessa. Nunca se mistura espaço vetorial numa mesma query.

### 3.3 Cooldown e circuit breaker

| Parâmetro | Valor | Efeito |
|---|---|---|
| `allowed_fails` | 3 | 3 falhas consecutivas tiram o deployment da rotação |
| `cooldown_time` | 60 s | Tempo fora de rotação antes de nova tentativa |
| `num_retries` | 2 | Tentativas dentro do mesmo alias antes de cair para o fallback |
| Health check ativo | 60 s | LiteLLM sonda `/health` dos deployments; um Mac mini que voltou reentra sozinho |

O core observa `llm_backend_unavailable` em `events` e, se **todos** os backends locais estiverem em cooldown por > 5 min, emite alerta prioritário. Mac mini fora do ar em silêncio é a falha mais cara: tudo continua funcionando, só que gastando dinheiro em API externa.

### 3.4 Teto de gasto diário

`LLM_DAILY_BUDGET_USD` (default `5.0`) é o teto de gasto com **APIs externas** em 24 h (janela deslizante, fuso `America/Sao_Paulo`). É aplicado em **duas camadas**, de propósito:

| Camada | Onde | Comportamento |
|---|---|---|
| **Soft (core)** | `olimpo.llm.gateway`, antes de cada chamada | Consulta o gasto acumulado em `llm_calls`. Em 80% → `warn` no painel. Em 100% → **degrada** |
| **Hard (LiteLLM)** | `litellm_settings.max_budget` | Rejeita a chamada com 400. Rede de segurança para bug no core, não caminho normal |

**O que "degradar" significa, exatamente:**

| Camada de tráfego | ≤ 80% do teto | 80–100% | ≥ 100% (estourado) |
|---|---|---|---|
| Interação em tempo real (`classify_interaction`, `reply_interaction`) | Local | Local | **Local — inalterado** |
| Produção (`caption`, `image_prompt`, `campaign_adapt`) | Local | Local | Local |
| Raciocínio (`plan_day`, `reflect`, `clip_detect`) | Escala em retry | Escala só após 2 falhas locais | **Só local.** Se falhar 2×, degrada o resultado (plano simplificado a partir de template) e emite `warn` |
| Multimodal (`recon_analysis`) | Local → externo se incerto | Local → externo se `safety_verdict != safe` | **Só local.** Se o local não conseguir julgar, o recon devolve `safety_verdict='risky'` e a campanha vai para aprovação humana |
| QA (`qa_text` limítrofe) | Externo | Externo | **Externo — reserva de guardrail (R3)** |

**A regra inegociável: estourar orçamento nunca interrompe interação, nunca desliga guardrail e nunca publica algo que não teria sido publicado com orçamento sobrando.** Ao estourar, o sistema fica *mais conservador*, não mais permissivo: na dúvida, manda para o humano. A violação `SYS-005` ([08 §7.8](08-guardrails-e-autonomia.md#78-sistema-e-integridade)) registra o evento com severidade `warn`.

---

## 4. Divisão de trabalho entre os dois Mac minis

### 4.1 Por que não balancear os dois igualmente

A tentação óbvia é colocar os mesmos modelos nos dois Macs e fazer round-robin. É a escolha errada, por três motivos concretos:

1. **Troca de modelo em Ollama custa segundos.** Um Mac mini M4 alternando entre um 7B e um 14B faz swap de pesos na memória unificada. Sob carga mista, isso vira latência de vários segundos em requisições que deveriam responder em 800 ms — justamente as de interação em tempo real.
2. **Os perfis de carga são incompatíveis.** Alto volume quer *muitas requisições curtas com contexto pequeno*; raciocínio quer *poucas requisições longas com contexto grande*. Uma requisição de `reflect` com 8k de contexto ocupando a fila atrás de 40 classificações de comentário é exatamente o pior caso.
3. **Isolamento de falha vale mais que capacidade agregada.** Com especialização, a queda do Mac 2 degrada a *qualidade* do planejamento — a operação continua respondendo. Com round-robin, a queda de qualquer um degrada *tudo* pela metade.

### 4.2 A divisão

| | **Mac mini 1** (`MAC_MINI_1_URL`) | **Mac mini 2** (`MAC_MINI_2_URL`) |
|---|---|---|
| **Papel** | Volume e baixa latência | Raciocínio e multimodal |
| **Aliases servidos** | `olimpo-fast` (primário), `olimpo-embed` (primário) | `olimpo-reason` (primário), `olimpo-vision` (primário), `olimpo-fast` (secundário) |
| **Modelos residentes** | 1 modelo 7B/8B instruct + 1 modelo de embedding | 1 modelo 14B instruct + 1 modelo de visão |
| **`purpose` atendidos** | `caption`, `reply_interaction`, `classify_interaction`, `ingest_relevance`, `campaign_adapt`, `image_prompt`, `qa_text`, `embed` | `plan_day`, `reflect`, `clip_detect`, `memory_summarize`, `recon_analysis` |
| **Perfil de requisição** | Contexto 1–3k, saída 100–400 tokens, muitas por minuto | Contexto 4–16k, saída 500–2.000 tokens, poucas por minuto |
| **Alvo de latência** | p95 < 2,5 s | p95 < 25 s |
| **`OLLAMA_KEEP_ALIVE`** | `-1` (nunca descarrega — swap aqui é inaceitável) | `30m` |
| **`OLLAMA_NUM_PARALLEL`** | 4 | 1 |
| **`OLLAMA_MAX_LOADED_MODELS`** | 2 | 2 |
| **`rpm` no LiteLLM** | 600 | 60 |
| **Fallback recebe** | `olimpo-reason` degradado quando o Mac 2 cai | `olimpo-fast` em transbordo quando o Mac 1 satura |

### 4.3 Como isso aparece na config

A especialização é expressa por **três mecanismos combinados** no `model_list`:

```yaml
# 1. WEIGHT — o mesmo alias existe nos dois Macs, mas com pesos assimétricos.
#    olimpo-fast vai 4:1 para o Mac 1; o Mac 2 é válvula de escape, não par.
- model_name: olimpo-fast
  litellm_params:
    model: ollama_chat/qwen2.5:7b-instruct-q4_K_M
    api_base: os.environ/MAC_MINI_1_URL
    weight: 4
    rpm: 600

- model_name: olimpo-fast
  litellm_params:
    model: ollama_chat/qwen2.5:7b-instruct-q4_K_M
    api_base: os.environ/MAC_MINI_2_URL
    weight: 1
    rpm: 60

# 2. RPM — o teto por deployment impede que o Mac 2 seja inundado por
#    transbordo e deixe de atender o que só ele faz (raciocínio/visão).

# 3. TIMEOUT POR ALIAS — fast tem timeout curto (falha rápido e cai para o
#    fallback); reason tem timeout longo (esperar 40 s é normal num 14B).
```

Com `routing_strategy: latency-based-routing`, o LiteLLM ainda ajusta dinamicamente dentro do alias: se o Mac 1 estiver com latência degradada, o tráfego migra para o Mac 2 **antes** de estourar timeout. O `weight` define a preferência estrutural; a latência medida define o ajuste fino.

---

## 5. Modelos recomendados

### 5.1 Ponto de partida

> ⚠️ **Tudo nesta seção é hipótese até o benchmark local rodar.** Os números publicados de tokens/s e de qualidade não valem para *este* hardware, com *esta* quantização, sob *este* perfil de carga, com *estes* prompts. A tabela abaixo é o ponto de partida do benchmark (§5.2), não a conclusão dele. O item 3 dos próximos passos do [PLANO](../PLANO.md#10-próximos-passos-concretos) é exatamente isto.

| Alias | Modelo candidato | Quantização | Onde | Por quê | A confirmar |
|---|---|---|---|---|---|
| `olimpo-fast` | **Qwen 2.5 7B Instruct** | `q4_K_M` | Mac 1 | Melhor aderência a JSON estruturado na faixa 7B; multilíngue forte em PT-BR; instruções curtas | Se o PT-BR informal (gíria, oralidade de caption) sai natural ou "traduzido" |
| `olimpo-fast` (alt.) | **Llama 3.1 8B Instruct** | `q4_K_M` | Mac 1 | Alternativa se o Qwen soar artificial em PT-BR; melhor tom conversacional | Vence o Qwen em caption e perde em JSON? Se sim, considerar split por `purpose` |
| `olimpo-reason` | **Modelo 14B instruct** (Qwen 2.5 14B ou equivalente) | `q4_K_M` | Mac 2 | Ganho real em raciocínio multi-passo e contexto longo (`plan_day` com memórias + métricas + campanhas) | Se o ganho sobre o 7B justifica ~3× a latência. Se não justificar, `plan_day` vai direto para API externa |
| `olimpo-vision` | **Modelo de visão instruct** (Qwen 2.5-VL 7B ou equivalente) | `q4_K_M` | Mac 2 | `recon_analysis` sobre screenshots do device; ler caption, contadores e contexto visual | Se acerta `safety_verdict` em screenshots reais do Instagram. **Se o recall de conteúdo arriscado ficar abaixo de 95%, o recon vai para API externa por padrão** — é entrada de guardrail |
| `olimpo-embed` | **bge-m3** (1024 dim) | — | Mac 1 | Dimensão bate com `memories.embedding vector(1024)` do schema; multilíngue; bom em retrieval curto | Qualidade de recuperação de memória em PT-BR contra o baseline OpenAI |
| `olimpo-smart` | **Claude (Anthropic)** | — | API | Raciocínio, QA de segunda opinião, recon incerto | — |
| fallback externo | **GPT (OpenAI)** | — | API | Overflow quando a Anthropic falha; embeddings de reserva | — |

**Decisão que depende do benchmark:** se o 14B render menos de ~2× a qualidade percebida do 7B em `plan_day` e `reflect`, a conclusão correta é **não usar 14B local** — usar o 7B para tudo local e mandar raciocínio direto para a API externa, que é barata no volume de 20 chamadas/dia. O Mac 2 então assume visão + capacidade extra de `olimpo-fast`. Esta é uma saída legítima do benchmark, não uma falha.

### 5.2 Procedimento de benchmark

Executado por `scripts/bench_llm.py`, resultado versionado em `docs/benchmarks/<data>-<modelo>.md`. Roda antes de cada troca de modelo e mensalmente como regressão.

**O que medir**

| Dimensão | Métrica | Como | Alvo |
|---|---|---|---|
| **Vazão** | tokens/s de geração | 20 execuções, prompt de 1k tokens, saída de 300 | `fast` ≥ 40 tok/s · `reason` ≥ 15 tok/s |
| **Prefill** | tokens/s de prompt | Contextos de 1k, 4k, 16k | Registrar a curva — é o que define o custo real de contexto longo |
| **Latência p50/p95** | ms até o último token | 100 execuções por `purpose`, carga realista | `fast` p95 < 2.500 ms · `classify` p95 < 1.500 ms · `reason` p95 < 25.000 ms |
| **TTFT** | ms até o primeiro token | Idem | `fast` < 400 ms |
| **Concorrência** | p95 sob 1, 2, 4, 8 requisições paralelas | Rampa | Identificar o joelho da curva → define `rpm` e `OLLAMA_NUM_PARALLEL` |
| **Estabilidade térmica** | tokens/s no minuto 1 vs. minuto 20 | Carga contínua | Queda > 20% → revisar refrigeração/quantização |
| **Aderência a schema** | % de saídas que passam na validação Pydantic **na primeira tentativa** | 200 execuções por schema | **≥ 97%** — abaixo disso o modelo é inviável, não importa a vazão |
| **Qualidade** | Nota 1–5 por juiz humano + juiz LLM (`olimpo-smart`) | Conjunto dourado (§5.3) | Média ≥ 4,0 e nenhuma nota 1 |
| **Segurança** | Recall em casos adversariais | Corpus de [08 §9.2](08-guardrails-e-autonomia.md#92-casos-adversariais-obrigatórios) | **100%** de recall em `SENS-*`. Sem exceção |
| **PT-BR** | Taxa de vazamento de inglês, tradução literal, gênero errado | Inspeção sobre 100 captions | < 2% |

**O conjunto dourado (§5.3):** o benchmark **não usa prompts genéricos**. Usa 30 prompts reais extraídos do próprio sistema, congelados:

```
docs/benchmarks/golden/
├── plan_day/           5 casos  — persona + 12 memórias + 2 campanhas + métricas de 7 dias
├── caption/            5 casos  — 3 arquétipos, com e sem brand, com e sem trend
├── classify/          10 casos  — inclui os 4 tipos de `sensitive` e 2 injeções de prompt
├── reply/              4 casos  — fan, hater, oportunidade, ambíguo
├── recon_analysis/     3 casos  — screenshots reais capturados por device
└── qa_text/            3 casos  — um limpo, um limítrofe, um claramente violador
```

Benchmark com prompt sintético mede o modelo; benchmark com o conjunto dourado mede **o modelo neste sistema**. Só o segundo decide alguma coisa.

**Protocolo de execução**

```bash
# 1. Estado limpo — sem outro modelo residente competindo por memória
ssh mac1 'ollama stop --all && ollama ps'

# 2. Aquecimento (descarta as 3 primeiras execuções: carga de pesos)
python scripts/bench_llm.py --backend mac1 --model qwen2.5:7b-instruct-q4_K_M --warmup 3

# 3. Vazão e latência por purpose
python scripts/bench_llm.py --backend mac1 --model qwen2.5:7b-instruct-q4_K_M \
    --golden docs/benchmarks/golden --runs 100 --out docs/benchmarks/

# 4. Rampa de concorrência
python scripts/bench_llm.py --backend mac1 --concurrency 1,2,4,8 --runs 50

# 5. Qualidade: juiz LLM + amostra de 30 saídas para revisão humana cega
python scripts/bench_llm.py --judge olimpo-smart --sample-for-human 30

# 6. Segurança: o corpus adversarial precisa passar 100%
pytest tests/guardrails/corpus -k "llm_backend" --backend mac1
```

**Critério de aprovação:** um modelo só entra no `model_list` se passar em **vazão, aderência a schema e segurança**. Qualidade e latência decidem *qual alias* ele serve — segurança decide *se ele entra*.

---

## 6. Engenharia de prompt no Olimpo

### 6.1 Estrutura padrão

Todo prompt do sistema tem quatro blocos, nesta ordem, montados por `olimpo.llm.prompts.build()`. A ordem não é estética: o que é estável fica no topo (aproveita cache de prefixo dos provedores), o que é volátil fica embaixo.

```
┌─ BLOCO 1 · SISTEMA ──────────────────────── (role: system, estável) ──┐
│  Identidade do sistema + PERSONA CARD renderizado:                    │
│    nome, idade, ocupação fictícia, nicho                              │
│    voice: registro, gírias permitidas, emojis, comprimento típico     │
│    taboos: temas que a persona NUNCA aborda                           │
│    disclosure: como ela se identifica como personagem virtual         │
│    editorial (fofoca): o que a página defende, tom, alvos vetados     │
│  + guardrails aplicáveis ao arquétipo, em linguagem imperativa        │
└───────────────────────────────────────────────────────────────────────┘
┌─ BLOCO 2 · CONTEXTO ─────────────────────── (role: user, recuperado) ─┐
│  MEMÓRIAS recuperadas por relevância × recência × importância:        │
│    [narrativa]  estado do arco ativo                                  │
│    [episódica]  o que aconteceu nos últimos N dias                    │
│    [semântica]  fatos consolidados (seguidores recorrentes, gostos)   │
│  + estado atual: mood, métricas recentes, cadência, hora local        │
│  + material do SOURCE (item ingerido, transcript, recon)              │
│  Orçamento de contexto por purpose — memória é truncada, não o prompt │
└───────────────────────────────────────────────────────────────────────┘
┌─ BLOCO 3 · TAREFA ───────────────────────── (role: user, específico) ─┐
│  A instrução concreta e verificável. Uma tarefa por chamada.          │
│  "Escreva o caption do post de hoje sobre X, considerando Y."         │
│  Restrições numéricas explícitas: máx. 220 caracteres, 3 hashtags.    │
└───────────────────────────────────────────────────────────────────────┘
┌─ BLOCO 4 · FORMATO DE SAÍDA ─────────────── (role: user, schema) ─────┐
│  JSON Schema derivado do modelo Pydantic + 1 exemplo válido.          │
│  "Responda APENAS com JSON válido. Sem markdown, sem comentários."    │
└───────────────────────────────────────────────────────────────────────┘
```

Regras de montagem:

| Regra | Motivo |
|---|---|
| O Persona Card **sempre** entra, em toda chamada com voz | Persona esquecida é a causa nº 1 de conteúdo fora do personagem ([08 `PERS-003`](08-guardrails-e-autonomia.md#72-persona-e-consistência)) |
| Memórias entram com o texto, o tipo e a data — nunca cruas | O modelo precisa saber que "foi ontem" para não narrar como se fosse hoje |
| Orçamento de contexto por `purpose` (ex.: `caption` = 2k, `plan_day` = 12k) | Contexto longo custa prefill; num 7B, também custa qualidade |
| O `trace_id` **não** entra no prompt | Vai no header e em `llm_calls`. Prompt é conteúdo, não telemetria |
| Nada de few-shot com conteúdo real de outra conta | Contamina a voz e cria similaridade entre contas → `RATE-007` |

### 6.2 Saída estruturada

Toda chamada com consequência devolve JSON. Três mecanismos, em ordem de preferência:

| Mecanismo | Onde funciona | Uso |
|---|---|---|
| `format: json_schema` (Ollama) / `response_format` (OpenAI/Anthropic) | Backends compatíveis | Padrão — restringe a decodificação, elimina a maior parte dos erros de forma |
| JSON Schema no bloco 4 + 1 exemplo | Todos | Sempre presente, mesmo com o mecanismo acima ativo — redundância barata |
| Reparo tolerante | Core | Remove cercas de markdown, texto antes/depois, vírgula final. **Uma** tentativa, e o reparo é registrado em `llm_calls.error` |

Exemplo — `caption`:

```python
class CaptionOut(BaseModel):
    caption: str = Field(max_length=2200)
    hashtags: list[str] = Field(max_length=8)
    audio_ref: str | None = None
    reasoning: str = Field(min_length=20)   # vai para content_items.reasoning
    claim_status: ClaimStatus | None = None # obrigatório se archetype == 'gossip'
    self_check: list[str]                   # tabus que o modelo verificou não ter violado
```

### 6.3 Por que tudo passa por Pydantic antes de virar conteúdo

Esta é a fronteira de confiança do sistema. **A saída de um LLM é entrada não confiável**, do mesmo jeito que o corpo de uma requisição HTTP de um cliente qualquer — e é tratada com o mesmo rigor.

| O que a validação garante | O que aconteceria sem ela |
|---|---|
| **Forma** — os campos existem, os tipos batem, os limites são respeitados | Um caption de 4.000 caracteres é rejeitado pela plataforma, a task falha, e o motivo aparece três camadas abaixo, num log do orchestrator |
| **Enums** — `claim_status`, `rights_status`, `classification` só aceitam valores do schema do banco | O modelo inventa `"provavelmente_verdade"` e a inserção quebra em produção, com a mídia já gerada |
| **Campos obrigatórios por arquétipo** — `gossip` sem `claim_status` não passa | `GOS-001` teria de pegar depois, quando o custo do bloqueio já é maior |
| **Fronteira de injeção** — só campos declarados sobrevivem | Instrução embutida num comentário vira campo extra no JSON e trafega para o próximo prompt. Descartar o que não está no schema mata a cadeia |
| **Ponto único de retry** | `ValidationError` é o gatilho de retry com temperatura menor e, na segunda falha, de escalada para `olimpo-reason`/`olimpo-smart` (R2). Sem exceção tipada, o retry vira heurística de string |
| **Contrato estável para o QA** | O motor de guardrails recebe um objeto tipado, nunca um `dict` de forma incerta. Guardrail que precisa checar `.get()` é guardrail com bug latente |

Sequência normativa: **`LLM → parse → Pydantic → guardrails → persistência`**. Nenhuma saída de LLM é gravada em `content_items`, `interactions`, `memories` ou `campaign_recon` sem atravessar as duas primeiras barreiras. Falha de validação é registrada em `llm_calls` com `success=false` e vira métrica — taxa de falha de schema subindo é o primeiro sintoma de que um modelo foi trocado ou degradou.

---

## 7. Contabilidade

### 7.1 Uma linha por chamada

`olimpo.llm.gateway` grava em `llm_calls` **toda** chamada, incluindo as que falharam, as que caíram para fallback (uma linha por tentativa) e as feitas em dry-run.

| Coluna | Origem | Observação |
|---|---|---|
| `account_id` | Contexto do agente | `NULL` em chamadas de sistema (recon de campanha, manutenção) |
| `purpose` | Argumento explícito de `complete()` | Valor do catálogo (§2). É a chave de toda análise de custo |
| `model` | `response.model` devolvido pelo LiteLLM | O modelo **efetivo**, não o alias pedido — é o que revela fallback silencioso |
| `backend` | Header `x-litellm-model-api-base` → mapeado | `mac_mini_1` \| `mac_mini_2` \| `anthropic` \| `openai` |
| `prompt_tokens` / `output_tokens` | `response.usage` | Para local, o Ollama devolve as contagens; se faltar, estima por tiktoken e marca em `error` |
| `latency_ms` | Cronômetro do core (inclui a rede até o LiteLLM) | Deliberadamente a latência **percebida pelo agente**, não a do provedor |
| `cost_usd` | `x-litellm-response-cost`; **0 para backends locais** | Local tem custo de energia/capital, não marginal. Contabilizamos capacidade em tokens, não em dólares |
| `success` / `error` | Resultado + tipo da exceção | `ValidationError`, `Timeout`, `RateLimit`, `BudgetExceeded` |
| `trace_id` | `contextvars` | Junta a chamada ao `day_plan`, ao `content_item` e ao `event` do mesmo passo |

`day_plans.llm_call_id` e `campaign_recon.llm_call_id` apontam para a chamada que os produziu (FKs já em `0001_initial.sql`). Sempre dá para perguntar "qual prompt e qual modelo geraram este plano?".

### 7.2 Consultas de custo

```sql
-- 1. Gasto externo nas últimas 24 h vs. o teto (a query que o guard de budget roda)
SELECT COALESCE(SUM(cost_usd), 0) AS spent_usd
FROM   llm_calls
WHERE  created_at >= now() - interval '24 hours'
  AND  backend IN ('anthropic', 'openai');

-- 2. Custo e volume por purpose nos últimos 7 dias — onde o dinheiro realmente vai
SELECT purpose,
       count(*)                                   AS calls,
       round(avg(latency_ms))                     AS p_avg_ms,
       percentile_disc(0.95) WITHIN GROUP (ORDER BY latency_ms) AS p95_ms,
       sum(prompt_tokens + output_tokens)         AS tokens,
       round(sum(cost_usd), 4)                    AS usd,
       round(100.0 * count(*) FILTER (WHERE backend LIKE 'mac_mini%') / count(*), 1)
                                                  AS pct_local
FROM   llm_calls
WHERE  created_at >= now() - interval '7 days'
GROUP  BY purpose
ORDER  BY usd DESC NULLS LAST;

-- 3. Custo por conta por dia — a métrica de unit economics da operação
SELECT a.handle,
       a.archetype,
       date_trunc('day', c.created_at)::date AS dia,
       count(*)                              AS calls,
       round(sum(c.cost_usd), 4)             AS usd
FROM   llm_calls c
JOIN   accounts a ON a.id = c.account_id
WHERE  c.created_at >= now() - interval '30 days'
GROUP  BY 1, 2, 3
ORDER  BY dia DESC, usd DESC;

-- 4. Custo por publicação verificada — o número que decide se a operação fecha
WITH pub AS (
    SELECT account_id, date_trunc('day', published_at)::date AS dia, count(*) AS posts
    FROM   posts GROUP BY 1, 2
), llm AS (
    SELECT account_id, date_trunc('day', created_at)::date AS dia, sum(cost_usd) AS usd
    FROM   llm_calls GROUP BY 1, 2
)
SELECT p.dia, a.handle, p.posts, round(l.usd, 4) AS usd,
       round(l.usd / NULLIF(p.posts, 0), 4)      AS usd_por_post
FROM   pub p
JOIN   llm l USING (account_id, dia)
JOIN   accounts a ON a.id = p.account_id
ORDER  BY usd_por_post DESC NULLS LAST;

-- 5. Fallback silencioso: purposes que deveriam ser 100% locais e não são
SELECT purpose, backend, count(*)
FROM   llm_calls
WHERE  created_at >= now() - interval '24 hours'
  AND  purpose IN ('caption','reply_interaction','classify_interaction',
                   'ingest_relevance','embed')
  AND  backend NOT LIKE 'mac_mini%'
GROUP  BY 1, 2
ORDER  BY 3 DESC;

-- 6. Saúde do schema: taxa de falha de validação por modelo (§6.3)
SELECT model,
       count(*)                                             AS calls,
       count(*) FILTER (WHERE NOT success)                  AS falhas,
       round(100.0 * count(*) FILTER (WHERE error = 'ValidationError')
             / NULLIF(count(*), 0), 2)                      AS pct_schema_invalido
FROM   llm_calls
WHERE  created_at >= now() - interval '7 days'
GROUP  BY model
ORDER  BY pct_schema_invalido DESC;
```

A consulta 5 é a que mais vezes vai pegar problema real: um Mac mini fora do ar não gera erro visível — gera fatura.

---

## 8. Degradação graciosa

### 8.1 Prioridade de fila

Quatro filas Celery (`realtime`, `dispatch`, `production`, `maintenance`, já declaradas no `docker-compose.yml`) e a prioridade correspondente no LiteLLM. Sob saturação, a ordem é rígida:

| Prioridade | Fila | `purpose` | Por que vem antes | O que acontece sob saturação |
|---|---|---|---|---|
| **1** | `realtime` | `classify_interaction`, `reply_interaction` | Tem uma pessoa real esperando; comentário respondido 4 h depois é pior que não respondido | **Nunca é adiada.** Preempta as demais; pode usar o Mac 2 fora da especialização |
| **2** | `dispatch` | `qa_text`, verificação pré-publicação | Conteúdo pronto e agendado; atrasar é perder a janela e disparar `SYS-007` | Adia no máximo 5 min; depois escala para externo |
| **3** | `production` | `caption`, `image_prompt`, `campaign_adapt`, `clip_detect` | Tem folga: produz-se com horas de antecedência | Adia livremente dentro da janela; se estourar, o conteúdo do dia é reduzido |
| **4** | `maintenance` | `reflect`, `memory_summarize`, `embed` (backlog), reindexação | Ninguém espera; roda de madrugada | Pausa por completo. `reflect` pode rodar às 4h em vez de à meia-noite |

Preempção é real: quando `realtime` tem fila, o worker para de puxar `production` e `maintenance`. Materializado no `-Q` com `worker_prefetch_multiplier=1` e um worker dedicado a `realtime` em produção.

### 8.2 Timeouts

| Alias | Timeout | Racional |
|---|---|---|
| `olimpo-fast` (`classify_interaction`) | **8 s** | Falhar rápido e cair para o fallback é melhor que esperar |
| `olimpo-fast` (demais) | 30 s | Caption longo com contexto médio |
| `olimpo-reason` | 180 s | Um 14B com 12k de contexto legitimamente demora |
| `olimpo-vision` | 120 s | Processar imagem tem prefill pesado |
| `olimpo-smart` / externos | 120 s | Rede + provedor |
| `olimpo-embed` | 15 s | Lote de embeddings |

Timeout do core = timeout do alias + 5 s. O core nunca desiste antes do LiteLLM — se desistisse, perderia a chance de registrar qual backend falhou.

### 8.3 Retry

| Erro | Política |
|---|---|
| Timeout / 5xx / connection refused | 2 tentativas no LiteLLM (backend diferente na 2ª quando há mais de um), depois fallback de alias |
| Rate limit (429) do provedor | Backoff exponencial com jitter (1 s, 2 s, 4 s), máx. 3; depois fallback |
| `ValidationError` (§6.3) | Retry no core, `temperature=0.2` e o erro de validação anexado ao prompt. **Máx. 2.** Na 2ª falha, escala de alias (R2) |
| Guardrail `block` | **Zero retry.** Bloqueio não é falha transitória — regenerar até passar é fazer engenharia reversa do próprio guardrail. Máx. 2 regenerações em PRODUCE, e são regenerações com instrução corrigida, não repetições cegas |
| `BudgetExceeded` | Zero retry. Degrada para local imediatamente (§3.4) |

### 8.4 Circuit breaker

Dois níveis:

| Nível | Onde | Abre quando | Fechamento |
|---|---|---|---|
| **Por deployment** | LiteLLM (`allowed_fails: 3`, `cooldown_time: 60`) | 3 falhas consecutivas num backend | Health check ativo a cada 60 s reintroduz |
| **Por alias** | Core (`olimpo.llm.breaker`) | > 50% de falha em 60 s numa janela de ≥ 10 chamadas | Half-open após 120 s: 1 chamada de teste; sucesso fecha, falha reabre por 300 s |

Com o breaker do alias aberto, o core aplica o **modo degradado por `purpose`** — e este é o coração da degradação graciosa: cada tarefa tem um plano B explícito, escrito, e nenhuma delas tem "levantar exceção e parar" como plano B.

| `purpose` | Modo degradado (todos os backends indisponíveis) |
|---|---|
| `classify_interaction` | Classificador heurístico local (léxico de risco). **Viés máximo para `sensitive=true`** → tudo vai para humano. Nunca auto-responde por heurística |
| `reply_interaction` | Não responde. `interactions` fica com `replied_by=null` e entra na fila humana. Silêncio > resposta ruim |
| `caption` | Conteúdo não é produzido; o `content_item` fica em `draft`. Não se publica mídia sem caption revisado |
| `plan_day` | Plano-template a partir da cadência habitual da conta e do arco ativo, marcado `reasoning='degraded'`. A conta mantém ritmo, sem novidade narrativa |
| `reflect` | Adiado. Roda no próximo ciclo com a janela estendida — memória tolera atraso, é a definição de memória |
| `recon_analysis` | `safety_verdict='risky'` → campanha de engajamento vai para aprovação humana. **Nunca `safe` por omissão** |
| `qa_text` | **Bloqueia a publicação.** QA indisponível = nada é aprovado automaticamente. Este é o único ponto onde a degradação para a produção, e é intencional |
| `embed` | Enfileira em `reembed_backlog`. Memória é gravada sem vetor e recuperada por recência/importância até o backfill |

O padrão que atravessa a tabela: **a degradação empurra decisão para o humano ou para o silêncio, nunca para a publicação automática de algo menos verificado.** Um sistema degradado é um sistema mais quieto e mais conservador — não um sistema com QA desligado.
