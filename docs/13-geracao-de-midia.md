# 13 — Geração de mídia

> **Código:** `olimpo/media/` (registro de modelos e providers) · `olimpo/prompts/` (motores) · `olimpo/api/routers/media.py` (API) · `olimpo/db/importar_maquina.py` (importação)
> **Schema:** [`db/migrations/0008_absorve_maquina_de_influencers.sql`](../db/migrations/0008_absorve_maquina_de_influencers.sql)
> **Decisão:** [ADR-0014 — Absorver a máquina de influencers](adr/0014-absorver-a-maquina-de-influencers.md)

Este documento descreve o subsistema que produz **imagem e vídeo** de uma persona. Os motores de prompt — o ativo intelectual que decide *o que* é pedido ao modelo — têm documento próprio: [docs/14](14-motores-de-prompt.md).

**Índice**

1. [O que é e de onde veio](#1-o-que-é-e-de-onde-veio)
2. [A fronteira: gateway × cliente direto](#2-a-fronteira-gateway--cliente-direto)
3. [Consistência facial — as quatro coisas frágeis](#3-consistência-facial--as-quatro-coisas-frágeis)
4. [Registro de modelos](#4-registro-de-modelos)
5. [Os motores de prompt](#5-os-motores-de-prompt)
6. [A fila de geração](#6-a-fila-de-geração)
7. [Armazenamento de mídia](#7-armazenamento-de-mídia)
8. [API](#8-api)
9. [O que NÃO existe](#9-o-que-não-existe)

---

## 1. O que é e de onde veio

Até a migração 0008, a geração de imagem e vídeo era um **serviço externo**: a máquina de influencers, um React + Express + SQLite com painel e autenticação próprios, ligada ao Olimpo por `accounts.persona_engine_id`. O Olimpo pedia imagens por API e não sabia como elas eram feitas.

Ela deixou de existir. O motivo não foi a separação em si — foi **onde a identidade morava**: a tabela `influencers` de lá e a `personas` daqui eram a mesma entidade, quase campo a campo, e duas fontes de verdade para a mesma personagem geram a pergunta *"qual está certa?"* toda vez que uma diverge. O raciocínio completo, com as alternativas descartadas, está na [ADR-0014](adr/0014-absorver-a-maquina-de-influencers.md).

### 1.1 O que foi importado

O importador (`olimpo.db.importar_maquina`) é idempotente: personas casam por `legacy_id`, mídia casa por hash de conteúdo. Reexecutar não duplica.

| O que | Quantidade | Vira |
|---|---|---|
| Personas (`influencers`) | 12 | linhas em `personas`, com as colunas novas da 0008 |
| Assets de mídia | 191 | `media_assets` |
| Figurinos | 10 | `wardrobe_items` |
| Marcas | 4 | `brands` |
| Rascunhos de vídeo | 6 | `video_drafts` |

### 1.2 A preservação veio antes do código

**1,6 GB de mídia foram baixados para disco antes de qualquer linha de importação ser escrita** (`scripts/preservar_midia.py`, commit próprio). Isso não é zelo excessivo: 126 das 127 URLs remotas apontavam para CDNs de terceiros — um que vai ser desligado, outro que expira. Uma imagem de identidade perdida **não se regenera**: não existe como pedir "o mesmo rosto de antes".

| Origem | Volume |
|---|---|
| URLs remotas baixadas | 126 de 127 |
| Imagens de identidade | 235 MB |
| Assets já locais | 44 arquivos |
| **Total preservado** | **1,6 GB** |

> ⚠️ **A armadilha do SQLite com WAL.** O `app.db` da origem tinha **4 KB**; o journal `-wal` ao lado tinha **1,3 MB**. Copiar o arquivo `.db` sozinho — o reflexo natural — traria um banco praticamente vazio, e o erro só apareceria depois, na forma de "sumiram quase todas as personas". O snapshot foi feito com `VACUUM INTO`, que consolida o WAL no arquivo de destino. Vale para qualquer SQLite de terceiro que este projeto venha a absorver.

### 1.3 O que saiu junto

O **Higgsfield** foi removido inteiro: ~2.000 linhas de cliente, cinco colunas de token e o CDN de onde 69 mídias vinham. A origem também guardava as chaves dos providers em `localStorage` do navegador e tinha um endpoint que as devolvia em texto claro — isso **não foi portado**, e a regra vale para tudo que vier depois ([docs/12](12-seguranca.md)).

---

## 2. A fronteira: gateway × cliente direto

O princípio geral do projeto é que **todo tráfego de modelo passa pelo LiteLLM** ([ADR-0010](adr/0010-llm-gateway-litellm.md)). Aqui ele não se sustenta inteiro, e a exceção foi verificada contra a instalação real do gateway — não presumida.

| Caminho | Rota | Situação |
|---|---|---|
| texto → texto | gateway | ✅ já em uso (plano do dia, caption, QA) |
| texto → imagem | gateway | ✅ 14 modelos fal registrados, incluindo o seedream |
| imagem OpenAI (`gpt-image-*`) | direto | ⚠️ o gateway não expõe `/images/edits`, e a resposta em base64 não cabe no formato dele |
| **imagem + referência → imagem** | **direto** | ⚠️ o gateway não expõe endpoint de edição |
| texto/imagem → vídeo | direto | ⚠️ nenhum modelo de vídeo registrado no gateway |
| voz | direto | ⚠️ idem |

**Por que isso importa mais do que parece.** O caminho principal da geração de persona é `/edit` com `image_urls` — reenviar as quatro imagens de identidade a cada geração é *o* mecanismo de consistência facial (§3). É exatamente o caminho que o gateway não cobre. Logo:

- **gateway** serve para **criar do zero** — uma personagem nova, sem referência nenhuma. É o caso raro.
- **cliente fal direto** serve para **todo o resto** — que é o caso comum.

Inverter essa leitura ("o gateway cobre imagem, então use o gateway") produz uma persona que muda de rosto entre posts, e o sintoma demora semanas para ser notado.

### 2.1 Como isso aparece no código

`olimpo/media/providers/` tem três arquivos, e a divisão é literal:

| Arquivo | Papel |
|---|---|
| `base.py` | O contrato: `GenerationRequest`, `GeneratedAsset`, o `Protocol` `MediaProvider`, os enums `MediaKind` e `Route` |
| `gateway.py` | O caminho do LiteLLM. Reaproveita chave, roteamento, teto de gasto e contabilidade que o gateway já faz |
| `fal.py` | O cliente direto. Dois regimes: síncrono (`fal.run`) e enfileirado (`queue.fal.run`, usado por vídeo) |

A decisão de rota **não é do chamador**. `GenerationRequest.needs_direct` é uma propriedade derivada do próprio pedido:

```python
@property
def needs_direct(self) -> bool:
    """Referência de imagem, vídeo e áudio não passam pelo gateway."""
    return bool(self.reference_images) or self.kind in {MediaKind.VIDEO, MediaKind.AUDIO}
```

**Ninguém escolhe provider.** Pede-se uma intenção (*gerar retrato desta persona*) e a camada decide. Um chamador que escolhe o provider é um chamador que um dia vai escolher o errado — e a rota efetiva fica registrada em `generation_jobs.route` justamente para que custo e latência sejam atribuíveis depois.

---

## 3. Consistência facial — as quatro coisas frágeis

> **Esta seção é o coração do subsistema.** Quebrar o que está aqui não gera erro, não gera alerta e não gera exceção: gera rostos que derivam devagar. Quando alguém percebe, já há dezenas de posts publicados com uma pessoa que não é a mesma.

**Não existe seed, LoRA, IP-Adapter nem embedding facial por trás disso.** A personagem se mantém a mesma por quatro coisas frágeis, e todas as quatro precisam estar íntegras ao mesmo tempo:

| # | O mecanismo | O que acontece se quebrar |
|---|---|---|
| **(a)** | **As 4 imagens de identidade existirem** — `face`, `character_sheet`, `close_up_face`, `close_up_detail` | Sem a primeira não há de onde ancorar: cada geração produz uma pessoa diferente. Sem as demais, a deriva é mais lenta e igualmente irreversível |
| **(b)** | **A ORDEM do array de referências ser exatamente essa** — a primeira é o rosto, a segunda é a roupa | O prompt fala em posições. Reordenar faz o modelo copiar a roupa da referência de rosto e o rosto da referência de roupa |
| **(c)** | **A tradução das tags `@image1`..`@imageN` para ordinais posicionais** ("the second reference image") | fal e OpenAI não entendem a sintaxe de tag. Um `"a referência"` genérico **colapsaria todas juntas** — e a roupa passaria a ser copiada da referência de rosto |
| **(d)** | **Os textos de trava no prompt** — `"Match skin texture and facial detail from…"`, `"Zero facial drift"` | Sem eles o modelo trata a referência como inspiração e não como identidade a replicar |

### 3.1 A ordem, em três lugares

A mesma ordem está declarada em três pontos do código, e os três precisam concordar. Não é duplicação por descuido — é o contrato aparecendo em cada camada que o toca:

| Onde | Constante |
|---|---|
| Motores de prompt | `olimpo/prompts/tipos.py` → `ORDEM_REFERENCIAS = ("face", "wardrobe", "close_up_face", "close_up_detail")` |
| API / painel | `olimpo/api/routers/media.py` → `ORDEM_IDENTIDADE`, com o rótulo em português e a posição (`@imageN`) |
| Importação | `olimpo/db/importar_maquina.py` → `IDENTIDADE`, mapeando o campo da origem para o papel |

E aparece na interface: a ficha da persona mostra as quatro âncoras **numeradas** (`@image1`..`@image4`). A numeração visível não é enfeite — é o contrato exposto para que ninguém reordene achando que está organizando ([docs/10 § 4.2](10-painel.md#42-contas-e-personas)).

### 3.2 A tradução de tags, concretamente

`olimpo/media/modelos.py` tem uma função de 10 linhas da qual a consistência depende:

```python
FAL_ORDINALS = ("first", "second", "third", "fourth", "fifth", …)

def limpar_prompt(texto: str) -> str:
    """Traduz as tags de referência e normaliza espaços.

    Ordem importa: a versão com número vem primeiro, senão `@image_` sem
    número engoliria o prefixo de `@image_2`.
    """
```

Antes: `"…copy the outfit from @image2, keep the face from @image1…"`
Depois: `"…copy the outfit from the second reference image, keep the face from the first reference image…"`

O fallback (`@image` sem número) vira `"the reference image"`, no singular — e é justamente o resultado que se quer evitar quando há mais de uma. Ele existe só porque a origem tinha prompts antigos com a forma sem número.

> ⚠️ **Referências extras entram no FIM do array.** `personas.reference_asset_ids` (fotos do mesmo rosto em outros ângulos) são concatenadas depois das quatro âncoras. Colocá-las antes deslocaria as posições e invalidaria todas as tags do prompt de uma vez.

### 3.3 Consequência para quem mexe no código

- Toda mudança em `ORDEM_REFERENCIAS`, em `limpar_prompt` ou nos textos de trava é **mudança de comportamento do produto**, não refatoração.
- `media_assets.reference_ids` é `uuid[]` e guarda as referências **na ordem em que foram usadas**. Gravar embaralhado inutiliza o registro para reprodução.
- `GET /v1/media/personas/{id}/identidade` devolve `pode_gerar_com_identidade`, que é simplesmente `bool(face_asset_id)`. É o teste mínimo antes de enfileirar qualquer geração de persona.

---

## 4. Registro de modelos

`olimpo/media/modelos.py` centraliza endpoint, parâmetros e armadilhas de cada modelo. O motivo de ser centralizado é o custo assumido na [ADR-0014](adr/0014-absorver-a-maquina-de-influencers.md#consequências): **assumimos a manutenção dos providers**, e schemas do fal mudam sem aviso. Um endpoint espalhado em cinco arquivos é cinco lugares para descobrir a mudança.

Dois conceitos que **não se misturam**:

| Conceito | O que é | Quando |
|---|---|---|
| `edit` | O endpoint que aceita `image_urls` | Caminho **principal** — a identidade vive nas referências |
| `t2i` | Texto puro para imagem | Só quando não há referência nenhuma (criar personagem do zero) |

### 4.1 Imagem

Padrão: **`fal-flux`**.

| ID | Modelo | Edição | Texto puro | Resoluções |
|---|---|---|---|---|
| `fal-flux` | fal · FLUX.2 | `fal-ai/flux-2/edit` | `fal-ai/flux-2` | — |
| `fal-nano-pro` | fal · Nano Banana Pro | `fal-ai/nano-banana-pro/edit` | `fal-ai/nano-banana` | 1K, 2K, 4K |
| `fal-nano` | fal · Nano Banana | `fal-ai/nano-banana/edit` | `fal-ai/nano-banana` | — |
| `openai-gpt-image-2` | OpenAI · GPT Image 2 | `gpt-image-2` (API própria) | `gpt-image-2` | low, medium, high |
| `fal-gpt-image-2` | fal · GPT Image 2 | `openai/gpt-image-2/edit` | `fal-ai/gpt-image-2` | low, medium, high |
| `fal-seedream` | fal · Seedream v5 Pro | `bytedance/seedream/v5/pro/edit` | `bytedance/seedream/v5/pro/text-to-image` | 1K, 2K |
| `fal-seedream-lite` | fal · Seedream v5 Lite | `fal-ai/bytedance/seedream/v5/lite/edit` | `fal-ai/bytedance/seedream/v5/lite/text-to-image` | 2K, 3K, 4K |

**Armadilhas de imagem:**

- **O `gpt-image-2` está registrado duas vezes de propósito**, uma por caminho. Não é redundância nem indecisão: o direto vai para a conta da OpenAI sem intermediário; o do fal cai na mesma fatura e no mesmo cliente das outras gerações, e dispensa manter uma segunda chave viva. Os parâmetros são os mesmos nos dois — a escolha é de conta, não de capacidade. Detalhes do caminho direto na [§4.4](#44-o-caminho-direto-da-openai).
- **`input_fidelity` NÃO existe no `gpt-image-2`.** É do `gpt-image-1`, e a armadilha é que sondar o parâmetro sozinho engana: a API valida o *enum* (`Supported values are: 'high' and 'low'`) **antes** de checar se o modelo aceita o parâmetro. Só na geração real vem `The model 'gpt-image-2' does not support the 'input_fidelity' parameter`. Por isso o registro tem `suporta_fidelidade`, e o provider só envia quando declarado.
- **O Seedream v5 Pro NÃO leva o prefixo `fal-ai/`; o Lite leva.** Mesma linhagem, mesmo fornecedor, caminhos diferentes — a assimetria é do catálogo do fal. Com o prefixo a resposta é `404 Path /seedream/v5/pro/edit not found`, e isso **não aparece na tela**: o pedido é aceito, vira job, sai da fila e só então falha. Os caminhos sem prefixo estão em `modelos.SEM_PREFIXO_FAL`, e `tests/unit/test_modelos_fal.py` recusa qualquer endpoint que não esteja nem prefixado nem declarado lá. Foi um defeito real: portei o Pro com o prefixo porque todos os vizinhos têm.
- **FLUX.2 e Seedream não aceitam proporção como enum**, só largura e altura em pixels, arredondadas a múltiplo de 16. Os cálculos (`tamanho_flux`, `tamanho_seedream`, `tamanho_seedream_lite`) foram portados sem alteração: **mudar o arredondamento muda o enquadramento que o modelo produz**.
- `fal-seedream-lite` **não recebe `output_format`**. A origem não enviava; o comportamento foi preservado e está marcado com `# NOTA:` no código. É um dos "bugs portados de propósito".
- Proporção fora da lista aceita pelo fal cai em `"auto"` — não em erro. Um aspecto digitado errado gera imagem no formato errado, silenciosamente.

### 4.2 Vídeo

Padrão: **`kling3-pro`**.

| ID | Modelo | Durações | Áudio | Armadilha |
|---|---|---|---|---|
| `kling3-pro` | Kling v3 Pro | 3–15 s | ✅ | usa `start_image_url` |
| `kling3-std` | Kling v3 Standard | 3–15 s | ✅ | usa `start_image_url` |
| `kling3-4k` | Kling v3 4K | 3–15 s | ✅ | usa `start_image_url` |
| `kling26-pro` | Kling v2.6 Pro | 5, 10 s | ✅ | usa `start_image_url` |
| `kling25` | Kling v2.5 Turbo Pro | 5, 10 s | — | usa `image_url` (a exceção dentro da família Kling) |
| `seedance2` | Seedance 2.0 | 4–15 s | ✅ | **recusa rosto fotorrealista** (filtro da ByteDance) · endpoint **sem** prefixo `fal-ai/` |
| `seedance1-pro` | Seedance 1 Pro | 2–12 s | — | **recusa rosto fotorrealista** |
| `hailuo02` | Hailuo 02 | 6, 10 s | — | resoluções `"512P"` / `"768P"` — **P maiúsculo**, e o provider diferencia |
| `veo3` | Veo 3 | 4, 6, 8 s | ✅ | duração vai como `"8s"`, string com sufixo — não o inteiro `8` |

> ⚠️ **TODOS os modelos de vídeo são imagem-para-vídeo.** Nenhum faz texto puro. Sempre existe um frame inicial, e **é dele que a identidade da persona vem** — o que amarra a §3 ao vídeo também: um frame inicial gerado sem as âncoras produz um vídeo de outra pessoa.

**Os dois filtros de rosto** (`seedance2`, `seedance1-pro`) estão declarados como `bloqueia_rosto_real=True` no registro, com a observação em texto. Não é opinião nossa sobre o modelo: é o provider recusando o pedido. Oferecê-los para uma persona fotorrealista é gastar uma chamada para receber recusa.

### 4.3 Voz, avatar falante e prova de roupa

| Recurso | Endpoint | Nota |
|---|---|---|
| TTS | `fal-ai/elevenlabs/tts/multilingual-v2` | |
| Lipsync | `fal-ai/sync-lipsync/v2` | |
| Avatar falante | `fal-ai/bytedance/omnihuman` · `fal-ai/kling-video/v1/pro/ai-avatar` | imagem + áudio → vídeo com sincronia labial numa geração só; mais barato que gerar mudo e sincronizar depois |
| Prova de roupa virtual | `fal-ai/fashn/tryon/v1.6` | copia um look real para a persona (ver `prompts/copiar_look.py`) |

### 4.4 O caminho direto da OpenAI

`olimpo/media/providers/openai.py`. Três coisas o separam do cliente fal:

**A resposta vem em base64, sempre.** Os modelos `gpt-image-*` não aceitam `response_format` — o parâmetro foi removido da API (`Unknown parameter: 'response_format'`). Por isso `GeneratedAsset` ganhou `conteudo`/`mime` e o armazenamento ganhou `guardar_gerado()`, que decide entre baixar de uma URL e gravar bytes. Quem chama não precisa saber de qual dos dois veio.

**A referência vai por multipart.** Não é lista JSON de URLs: são os bytes, em `image[]` repetido. Como consequência, este caminho **dispensa** o upload para o armazenamento do fal descrito na [§7](#7-armazenamento-de-mídia) — as imagens são lidas e enviadas direto.

**O tamanho é calculado, não escolhido de um enum.** Limites verificados contra a API, não presumidos:

| Regra | Valor | Como se manifesta |
|---|---|---|
| Múltiplo de 16 | largura **e** altura | `Width and height must both be divisible by 16` |
| Borda máxima | 3840 | `The longest edge must be less than or equal to 3840` |
| Área mínima | 655.360 px | `below the current minimum pixel budget` |
| Área máxima | 8.294.400 px | idem, no outro extremo |

`tamanho_gpt_image()` mira ~1 MP e tem guarda de piso: proporção extrema com área baixa cairia abaixo do mínimo, e o erro só apareceria no worker. A faixa foi confirmada por dois caminhos independentes — sondagem da API (768×768 recusado, 832×832 aceito) e a documentação do fal para o mesmo modelo (655.360), que caem na mesma janela.

**Saída em JPEG, não no PNG padrão.** O mesmo close saiu com 1,5 MB em PNG contra 210 KB em JPEG. O peso não fica só no disco: as âncoras são reenviadas como referência em **toda** geração, então ele volta em cada chamada.

---

## 5. Os motores de prompt

`olimpo/prompts/` — **5.713 linhas de Python vindas de ~2.600 de JavaScript**. É o ativo que a absorção existia para preservar: não o código, o conhecimento afinado por tentativa e erro contra os modelos reais.

**Não há LLM no caminho principal.** São motores determinísticos que montam texto. O detalhamento completo — camadas, algoritmo de guarda-roupa, arquétipos, política de porte e verificação — está em [docs/14](14-motores-de-prompt.md). Resumo do inventário:

| Módulo | Linhas | O que faz |
|---|---|---|
| `persona_image.py` | 2.323 | Criar uma persona do zero: guarda-roupa por energia, arquétipos de profissão, física de luz por horário, poses, cenas, props |
| `video.py` | 1.365 | Prompt de vídeo em blocos rotulados: 17 presets de câmera, moods falados e mudos, anotação de diálogo, distribuição de shots |
| `photo_studio.py` | 977 | Foto de uma persona **que já existe**: local, horário, pose, expressão, roupa, produto |
| `copiar_look.py` | 249 | Copiar um look inteiro — ou só uma peça — de fotos enviadas para cima da persona |
| `identidade.py` | 230 | As três folhas que ancoram a identidade: character sheet, close-up e feature sheet |
| `produto.py` | 198 | Folha de 6 painéis de um produto, para que ele fique consistente em qualquer pose |
| `referencias.py` | 192 | A instrução textual que explica ao modelo **o papel** de cada imagem de referência |
| `guarda_roupa.py` | 102 | Trocar roupa/cabelo numa folha existente, sem mexer no rosto |
| `tipos.py` | 83 | Os contratos: `Persona`, `Referencia`, `PromptMontado`, `ORDEM_REFERENCIAS` |

**Cada folha tem o seu aspect ratio, e trocar quebra o enquadramento:** character sheet 16:9 · close-up 4:5 · feature sheet 2:3 · copy-look 3:4 · turnaround e folha de produto 16:9.

---

## 6. A fila de geração

`generation_jobs` guarda o **estado**; a execução é do Celery. O modelo de estados veio da origem; a implementação, não — e por um motivo concreto: **na máquina de influencers um job síncrono que morresse no meio ficava preso em `running` para sempre, e o crédito já gasto se perdia.** Com ack tardio e retry isso desaparece.

### 6.1 Estados (`generation_status`)

| Valor | Significado |
|---|---|
| `queued` | Enfileirado, nenhum worker pegou ainda |
| `running` | Worker executando, ou aguardando o provider numa fila assíncrona |
| `done` | Terminou; `result_asset_ids` preenchido |
| `error` | Falhou; `error` preenchido, `attempts` incrementado |
| `cancelled` | Cancelado do nosso lado — ver a ressalva em [§9](#9-o-que-não-existe) |

### 6.2 Colunas que existem por um motivo específico

| Coluna | Por que existe |
|---|---|
| `provider_ref` | Handle do provider em geração assíncrona (fila do fal, predição do Replicate). **Sem ele, um restart perde trabalho já pago** |
| `route` | `gateway` ou `direct` — permite atribuir custo e latência ao caminho certo ([§2](#2-a-fronteira-gateway--cliente-direto)) |
| `request` | O pedido inteiro em jsonb. Reproduzir uma geração é reenviar isto |
| `cost_usd` `numeric(10,6)` | Geração de vídeo é cara o bastante para o custo ser métrica de produto, como em `llm_calls` |
| `next_poll_at` | O índice parcial `idx_genjobs_pendentes` cobre só `status IN ('queued','running')` — o poller varre poucas linhas mesmo com histórico grande |
| `attempts` | Retry precisa de teto; sem ele um modelo que sempre recusa vira loop de gasto |

> ❓ **A tabela existe; o worker ainda não.** Nenhum código Python lê ou escreve `generation_jobs` hoje — a fila está modelada e indexada, e a implementação Celery é item pendente. Enquanto isso, quem chama os providers o faz direto, sem job registrado, e portanto **sem contabilidade de custo**.

---

## 7. Armazenamento de mídia

**Os arquivos ficam no armazenamento do Olimpo, nunca no CDN do provider.** As URLs do fal expiram; o CDN de onde as imagens vieram vai ser desligado. `media_assets.source_url` guarda a origem **para rastrear, nunca para servir**.

| Aspecto | Como é |
|---|---|
| Raiz | `/app/media` no container da API; volume `media` do `docker-compose.yml` |
| Layout | `/app/media/personas/…`, escrito pelo importador |
| Nome do arquivo | Derivado do **hash do conteúdo** |
| Dedupe | `CREATE UNIQUE INDEX idx_media_hash ON media_assets(content_hash) WHERE content_hash IS NOT NULL` — o mesmo arquivo nunca duplica |
| Cache HTTP | `Cache-Control: public, max-age=31536000, immutable` — legítimo porque o nome vem do hash: se o conteúdo mudasse, o id seria outro |

**Validação de path traversal.** `storage_path` vem do banco, mas o endpoint que serve o arquivo confere que o caminho resolvido está sob a raiz:

```python
try:
    arquivo.resolve().relative_to(RAIZ.resolve())
except ValueError:
    raise HTTPException(400, "caminho inválido") from None
```

Confiar no banco sem checar permitiria que uma linha adulterada lesse qualquer arquivo do container. É o mesmo raciocínio de "prompt não é guardrail": *dado interno não é dado confiável quando vira caminho de arquivo*.

> ⚠️ **Volume local é solução de hoje, não de sempre.** 1,6 GB cabe; algumas dezenas de gigabytes não. A [ADR-0014](adr/0014-absorver-a-maquina-de-influencers.md#revisitar-quando) declara esse limiar como gatilho para revisitar e migrar para armazenamento de objeto.

---

## 8. API

`olimpo/api/routers/media.py`, prefixo `/v1/media`.

| Endpoint | O que faz |
|---|---|
| `GET /v1/media` | Lista paginada (`page`, `per_page` até 200, default 60), com filtros `persona_id`, `role` e `kind`. Devolve `total`, `page` e `pages`; faz `LEFT JOIN` em `personas` para trazer o nome |
| `GET /v1/media/{id}/arquivo` | Serve o arquivo com cache imutável e validação de path traversal ([§7](#7-armazenamento-de-mídia)) |
| `GET /v1/media/personas/{id}/identidade` | As âncoras **na ordem**, com quantas existem e se dá para gerar com identidade |
| `GET /v1/media/opcoes` | O que a tela pode oferecer: locais, horas, poses e expressões vindos do motor, mais o catálogo de modelos com proporções, resoluções e **disponibilidade** ([§8.2](#82-o-catálogo-de-motores)) |
| `POST /v1/media/personas/{id}/ancoras/{ancora}` | Enfileira a geração. Aceita `modelo`, `aspect_ratio`, `resolucao`; para `face`, também `quantidade`, `seed` e `referencias` com papel declarado ([§8.3](#83-referências-com-papel)) |
| `DELETE /v1/media/personas/{id}/ancoras/{ancora}` | **Desliga** a âncora sem apagar a imagem — ela volta para a biblioteca como `reference` e pode ser reescolhida. Tirar o rosto devolve `aviso`, porque as outras três derivam dele |
| `POST` / `DELETE /v1/media/personas/{id}/referencias/{asset_id}` | Anexa e retira do acervo de referência (`personas.reference_asset_ids`). Anexar é idempotente: a mesma foto duas vezes seria a mesma URL duas vezes no array |
| `POST /v1/media/personas/{id}/figurinos` | Figurino peça a peça — `top`, `bottom`, `calcado`, `cabelo`, `texto_livre` (sobrescreve), mais `referencias_extras` com fotos de peças reais |

### 8.1 O endpoint de identidade

É o endpoint que a §3 torna necessário. Ele devolve as quatro posições **sempre**, preenchidas ou não — porque o que interessa é a **lacuna**, não a lista do que existe:

```jsonc
{
  "persona": { "id": "…", "name": "Luna Prado" },
  "ancoras": [
    { "posicao": 1, "papel": "face",            "rotulo": "rosto",              "asset_id": "…", "detalhe": { } },
    { "posicao": 2, "papel": "character_sheet", "rotulo": "folha de personagem", "asset_id": "…", "detalhe": { } },
    { "posicao": 3, "papel": "close_up_face",   "rotulo": "close de rosto",      "asset_id": null, "detalhe": null },
    { "posicao": 4, "papel": "close_up_detail", "rotulo": "cartela de detalhe",  "asset_id": null, "detalhe": null }
  ],
  "completas": 2,
  "extras": ["…"],
  "pode_gerar_com_identidade": true
}
```

- **`posicao` é o que o prompt referencia como `@imageN`.** É por isso que ela é explícita no payload e não implícita na ordem do array.
- **`completas` importa mais do que *quais*.** Com as quatro, o rosto se mantém entre gerações. Faltando a primeira, não há de onde ancorar — e é isso que `pode_gerar_com_identidade` responde.
- `extras` são as referências adicionais (`personas.reference_asset_ids`), que entram **depois** das quatro.

### 8.2 O catálogo de motores

`GET /v1/media/opcoes` devolve, por modelo, o que ele aceita **e se ele pode gerar agora**:

```jsonc
{
  "id": "fal-seedream", "label": "fal · Seedream v5 Pro", "provider": "fal",
  "aspectos": ["auto", "21:9", "16:9", …], "resolucoes": ["1K", "2K"],
  "disponivel": true, "motivo": null
}
```

`disponivel` existe porque **a falha por falta de chave acontece longe da tela**: sem ela o pedido é aceito, vira job, sai da fila e só o worker descobre — o operador vê "erro" dez segundos depois, sem dizer o quê. Com ela, o modelo aparece desabilitado dizendo *qual* variável falta (`motivo`).

`aspectos` é por modelo e não global porque nem todo provider aceita a mesma lista: os do fal aceitam onze, e um catálogo com OpenAI aceitaria três. Oferecer 9:16 onde não existe é oferecer um `400`.

A tela consome isso em `panel/components/motor.tsx`, na ordem **modelo → formato → resolução**. A ordem é obrigatória: formato e resolução dependem do modelo, então escolher formato antes seria escolher algo que pode não existir depois. Se o modelo salvo ficar indisponível, o seletor cai sozinho para o primeiro utilizável.

### 8.3 Referências com papel

Ao gerar o **rosto**, a tela envia quantas fotos o operador tiver, cada uma com o papel declarado. O papel não é etiqueta: vira instrução em texto, montada por `prompts/referencias.py`.

| Papel | O que o modelo é instruído a fazer |
|---|---|
| `face` | Recriar este rosto. **Com** `physical_desc`, a foto entra só como geometria facial e o texto manda em pele, cabelo e olhos; **sem** ela, a foto é recriada fielmente |
| `style` | Copiar pose, roupa, cor, cenário e clima — e **ignorar explicitamente** a identidade de quem aparece nela |
| `extra` | A **mesma** pessoa em outro ângulo ou luz. Sem esse aviso o modelo a leria como outra pessoa, e o rosto derivaria |

O serviço **reordena** para face → style → extra antes de publicar as URLs. Não é arrumação: a instrução gerada fala em *"a primeira imagem"* e *"a segunda"*, então a ordem do array precisa bater com o texto — o mesmo contrato da [§3](#3-consistência-facial--as-quatro-coisas-frágeis), aqui na entrada em vez das âncoras.

### 8.4 Fotos de referência × âncoras

Duas coisas parecidas que **não** são a mesma:

| | Âncoras (4) | Fotos de referência (N) |
|---|---|---|
| Onde vivem | Colunas de `personas` | `personas.reference_asset_ids` |
| Posição no prompt | Fixa — `@image1`…`@image4` | Nenhuma; entram depois |
| Papel semântico | Sim, cada uma tem o seu | Não |
| Para que servem | Ser a identidade | Reforçá-la, sobretudo **corpo inteiro** — as âncoras são rosto e closes, então altura e proporção ficam por conta do modelo até existir uma foto de corpo |

---

## 9. O que NÃO existe

Documentado aqui para que ninguém procure — e para que a lacuna seja decisão visível, não descoberta em produção.

| Não existe | Consequência prática |
|---|---|
| **Miniaturas** | O painel serve a imagem em tamanho cheio em qualquer grade. Uma galeria de 60 assets baixa 60 originais |
| **Conversão de formato** | Nada de WebP/AVIF. O que veio do provider (jpeg, png) é o que é servido |
| **Cancelamento real no provider** | `generation_status = 'cancelled'` marca a **nossa** intenção. O job continua rodando no fal e o crédito é gasto igual. Cancelar é parar de esperar, não parar de pagar |
| **Worker Celery da fila** | `generation_jobs` está modelada e indexada, mas nenhum código a lê ou escreve ainda ([§6.2](#62-colunas-que-existem-por-um-motivo-específico)) |
| **QA visual automático** | Nenhuma comparação de embedding facial entre o gerado e as âncoras. A deriva de rosto é hoje detectada por olho humano. ❓ É o candidato natural a primeiro guardrail visual |
| **Telas de Photo Studio e Content Studio** | ~10 mil linhas de React que não se portam. Precisam nascer de novo em Next.js — o **maior item pendente** da absorção ([ADR-0014](adr/0014-absorver-a-maquina-de-influencers.md#negativas-e-custos-aceitos)) |
| **Higgsfield** | Removido. Prompts antigos com tags `@image_N` continuam sendo traduzidos por compatibilidade, mas nenhum código chama o provider |

---

## Ver também

- [ADR-0014](adr/0014-absorver-a-maquina-de-influencers.md) — a decisão, as alternativas e os custos aceitos
- [`db/migrations/0008_absorve_maquina_de_influencers.sql`](../db/migrations/0008_absorve_maquina_de_influencers.sql) — a fonte da verdade do schema
- [docs/14 — Motores de prompt](14-motores-de-prompt.md) — o ativo intelectual: camadas, algoritmos e a política de porte
- [docs/02 — Modelo de dados](02-modelo-de-dados.md) — `media_assets`, `wardrobe_items`, `brands`, `generation_jobs`, `video_drafts`
- [docs/04 — Persona Card](04-persona-card-spec.md) — o que é coluna, o que é asset e o que continua no card
- [docs/09 — LLM Gateway](09-llm-gateway.md) — o gateway que cobre texto e texto→imagem
- [docs/10 — Painel](10-painel.md) — o bloco de identidade visual e a galeria na ficha da persona
- [docs/12 — Segurança](12-seguranca.md) — por que nenhuma chave de provider vai para o navegador
