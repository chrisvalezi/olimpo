# 14 — Motores de prompt

> **Código:** `olimpo/prompts/` · **Origem:** máquina de influencers (JavaScript)
> **Decisão:** [ADR-0014 § Decisão, item 3](adr/0014-absorver-a-maquina-de-influencers.md#decisão) — *"os motores de prompt são portados sem alteração semântica"*
> O subsistema que **usa** estes motores (providers, modelos, fila, armazenamento) está em [docs/13](13-geracao-de-midia.md).

Este documento descreve o **ativo intelectual** da absorção. Quando a [ADR-0014](adr/0014-absorver-a-maquina-de-influencers.md) diz que *"o que vale ali não é o código"*, é disto que está falando: 5.713 linhas de Python vindas de ~2.600 de JavaScript, afinadas empiricamente contra os modelos reais.

**Índice**

1. [Não há LLM no caminho principal](#1-não-há-llm-no-caminho-principal)
2. [Inventário dos motores](#2-inventário-dos-motores)
3. [A arquitetura em camadas do prompt de imagem](#3-a-arquitetura-em-camadas-do-prompt-de-imagem)
4. [Tags e ordinais: o mecanismo](#4-tags-e-ordinais-o-mecanismo)
5. [Seleção de guarda-roupa: o algoritmo](#5-seleção-de-guarda-roupa-o-algoritmo)
6. [Arquétipos de profissão](#6-arquétipos-de-profissão)
7. [O motor de vídeo](#7-o-motor-de-vídeo)
8. [A política de porte: "não melhorar nada"](#8-a-política-de-porte-não-melhorar-nada)
9. [Verificação por teste diferencial](#9-verificação-por-teste-diferencial)
10. [Aleatoriedade reproduzível](#10-aleatoriedade-reproduzível)

---

## 1. Não há LLM no caminho principal

A confusão mais provável de quem chega a esta pasta é achar que "prompt" aqui significa "prompt de LLM". Não significa.

**Estes motores são determinísticos.** Recebem uma `Persona` e um punhado de opções, e devolvem a **string** que vai para o gerador de imagem ou vídeo. Nenhuma chamada de modelo acontece no meio. Uma função pura entra, um parágrafo de inglês sai.

| | Motor de prompt | Chamada de LLM |
|---|---|---|
| Entrada | `Persona` + opções | mensagens + contexto |
| Saída | texto do prompt de imagem/vídeo | texto gerado pelo modelo |
| Custo | zero | tokens, latência, linha em `llm_calls` |
| Reprodutível | sim (dado o mesmo seed) | não |
| Onde o conhecimento vive | **nas constantes do arquivo** | nos pesos do modelo |

O conhecimento é conteúdo de domínio: 119 looks de guarda-roupa mapeados por energia de personalidade, 100 arquétipos de profissão, seis parágrafos de física de luz por horário. Nada disso foi gerado — foi descoberto por tentativa e erro contra os modelos, e é exatamente o que uma reescrita do zero jogaria fora ([ADR-0014 § Alternativas](adr/0014-absorver-a-maquina-de-influencers.md#alternativas-consideradas)).

### 1.1 Onde o LLM entra, então

Em três lugares **opcionais**, todos fora do caminho principal, e sempre pelo gateway ([docs/09](09-llm-gateway.md)):

| Uso | Módulo | O que faz |
|---|---|---|
| Classificação da backstory | `personas.backstory_context` | LLM classifica a história em tags de estilo + nicho de cena. Pode sobrescrever as tags do arquétipo, **nunca a cena travada**. Na origem isso era recalculado a cada geração porque não persistia — agora é coluna |
| Análise de produto | `produto.py` | LLM multimodal olha as fotos e descreve o produto; a descrição substitui "marca + categoria" como sujeito da folha de 6 painéis |
| Análise de peça de roupa | `copiar_look.py` | LLM de visão descreve a peça escolhida, o que afia o prompt da edição flexível |

Nos três casos, **só a montagem das strings foi portada**; a chamada em si é do gateway do Olimpo. E nos três o motor funciona sem o LLM — a análise é um refinamento, não uma dependência.

---

## 2. Inventário dos motores

| Módulo | Linhas | O que produz | Aspect ratio |
|---|---|---|---|
| `persona_image.py` | 2.323 | Foto de uma persona **do zero** (wizard de criação) | 9:16 ou 16:9 |
| `video.py` | 1.365 | Prompt de vídeo em blocos rotulados | conforme o pedido |
| `photo_studio.py` | 977 | Foto de uma persona **que já existe**, com referências | conforme o pedido |
| `copiar_look.py` | 249 | Copiar look (ou uma peça) de fotos enviadas | 3:4 · turnaround 16:9 |
| `identidade.py` | 230 | As folhas que ancoram a identidade | sheet 16:9 · close-up 4:5 · feature 2:3 |
| `produto.py` | 198 | Folha de 6 painéis de um produto | 16:9 |
| `referencias.py` | 192 | A instrução que explica **o papel** de cada referência | — |
| `guarda_roupa.py` | 102 | Trocar roupa/cabelo mantendo o rosto | 16:9 |
| `tipos.py` | 83 | `Persona`, `Referencia`, `PromptMontado`, `ORDEM_REFERENCIAS` | — |

**O aspect ratio de cada folha é parte do prompt, não configuração.** A folha de produto termina com `"16:9 landscape format"` no próprio texto: gerar em 9:16 deixa o texto e o pedido em desacordo, e o modelo resolve o conflito do jeito dele.

### 2.1 O que cada motor sabe, em números

**`persona_image.py`** — o maior, e o único que cria uma personagem que ainda não existe:

| Biblioteca | Conteúdo |
|---|---|
| Guarda-roupa | **119 looks** — 68 femininos, 51 masculinos — cada um com energia (0–100), tags e nichos |
| Arquétipos de profissão | **100**, detectados por regex na backstory — 29 com **cena travada**, 19 com dica de porte físico, 3 com detalhe físico visível |
| Horários | **6 configurações**, cada uma com um parágrafo de física de luz |
| Poses | **5**, escolhidas pelo slider de personalidade |
| Cenas | **9 pools** com **37 cenas**, mais **10 cenas outdoor candid** |
| Props | **12 universais + 3 vazios** (mãos livres) |
| Câmera | sempre **iPhone 16 Pro, lente principal 24mm f/1.78** |

**`photo_studio.py` + `referencias.py`** — a foto de quem já tem rosto:

| Biblioteca | Conteúdo |
|---|---|
| Variantes de cena | **48** — 12 locais × 4 variações |
| Combinações de luz | **48** — 12 locais × 4 horários |
| Poses | **19** |
| Expressões | **5** |

**`video.py`**:

| Biblioteca | Conteúdo |
|---|---|
| Presets de câmera | **17** |
| Humores | **8 falados + 8 mudos** — ver [§7.2](#72-por-que-existem-dois-conjuntos-de-humor) |
| Anotação de diálogo | marcação com `[beat.]`, timestamps e distribuição de shots |

---

## 3. A arquitetura em camadas do prompt de imagem

O prompt não é uma frase: é um documento de blocos rotulados, montado sempre na mesma ordem. A ordem importa porque o modelo lê sequencialmente e o que vem antes contextualiza o que vem depois.

```
Photograph style:   iPhone 16 Pro snapshot, sem equipe, sem estúdio, sem direção
Scene:              cena + horário · vazia de outras pessoas · fundo real, sem bokeh
Subject:            gênero, idade, descrição física, porte, marcador visível
Pose:               função de pose(prop) — a pose muda conforme o que está na mão
Wardrobe & details: o look escolhido + a linha de paleta
Lighting:           o parágrafo de física de luz do horário sorteado
Camera & capture:   iPhone 16 Pro 24mm f/1.78, na mão
Skin:               fatos fotográficos concretos, nunca palavras-categoria
Use case:           o nome da pose — o "para que serve esta foto"
Constraints:        o que NÃO pode aparecer (logos, UI, blur, retoque, gente ao fundo)
```

**Três decisões dessa estrutura merecem explicação, porque parecem arbitrárias e não são:**

| Decisão | Por quê |
|---|---|
| **A câmera é sempre a mesma** | O objetivo do motor é uma foto que parece foto de celular, não produção. Deixar a câmera variar reintroduz o "cheiro de banco de imagens" que o resto do prompt gasta parágrafos combatendo |
| **A pele é descrita como fato fotográfico** | `"Real visible pore texture on the nose, cheeks, and forehead"` funciona; `"realistic skin"` não. Palavra-categoria é interpretada como estilo; fato concreto é interpretado como instrução |
| **O bloco `Constraints` é longo e negativo** | É onde moram os marcadores de IA que denunciam a imagem: íris brilhante demais, rosto perfeitamente simétrico, pele plástica, brilho estranho. Também é onde se proíbe qualquer elemento de interface — barra de status, notificação, overlay de app |

### 3.1 O caso do Photo Studio: *"This Is an Edit, Not a Generation"*

`photo_studio.py` é o motor da persona que **já existe**, e a filosofia declarada no guia de origem é essa frase. A consequência prática é contraintuitiva: **o prompt é curto de propósito.**

Como o Photo Studio sempre passa imagens de referência, o modelo já sabe como a pessoa é. O trabalho do texto é dizer **o que fazer com ela** — onde colocar, como posar, qual o clima. Redescrever o que a referência já mostra é token gasto diluindo a instrução.

- Alvo: **60–140 palavras**, um parágrafo único e coeso.
- Deve ler como um diretor briefando um fotógrafo, **não** como lista de tags.

Isto é o oposto do `persona_image.py`, que não tem referência nenhuma e por isso precisa descrever tudo. Os dois motores existem porque são dois problemas diferentes, e fundi-los pioraria os dois.

### 3.2 O que `referencias.py` acrescenta

Três lições caras, codificadas em texto e que **não devem ser reescritas**:

| Lição | O texto codifica |
|---|---|
| A referência facial é **geometria** | Quando existe descrição física em texto, a referência manda na proporção (espaçamento dos olhos, largura da mandíbula, ponte do nariz) e o **texto** manda em tom de pele, cabelo e cor dos olhos |
| A referência de estilo **não é uma pessoa** | Ela define pose, roupa, paleta e cena — e o modelo é explicitamente proibido de copiar o rosto de quem aparece nela |
| Referências extras são a **mesma** pessoa | Outros ângulos do mesmo rosto, nunca outra pessoa. Sem essa frase, o modelo faz média entre os dois rostos |

Há ainda uma precedência: **nota escrita pelo usuário > descrição física em texto > a referência sozinha**. Uma nota como *"use especificamente as sardas desta referência"* substitui o bloco inteiro.

---

## 4. Tags e ordinais: o mecanismo

> Este é o item **(c)** das quatro coisas frágeis da consistência facial ([docs/13 § 3](13-geracao-de-midia.md#3-consistência-facial--as-quatro-coisas-frágeis)). Vale entender inteiro.

Os prompts foram escritos com tags posicionais — `@image1`, `@image2` — herdadas do provider da origem. **Nem o fal nem a OpenAI entendem essa sintaxe.** Eles recebem um *array* de imagens e um texto; a ligação entre um item do array e uma menção no texto só existe se o texto disser a **posição em linguagem natural**.

### 4.1 O exemplo concreto

Dado o array de referências de uma persona, na ordem do contrato:

| Posição | Papel | Coluna em `personas` |
|---|---|---|
| 1 | `face` | `face_asset_id` |
| 2 | `character_sheet` (a roupa) | `character_sheet_asset_id` |
| 3 | `close_up_face` | `close_up_face_asset_id` |
| 4 | `close_up_detail` | `close_up_detail_asset_id` |

E o prompt escrito com tags:

```
Keep the face from @image1 exactly. Copy the outfit from @image2.
Match skin texture and facial detail from @image3.
```

`limpar_prompt()` (em `olimpo/media/modelos.py`) produz:

```
Keep the face from the first reference image exactly. Copy the outfit from the second reference image.
Match skin texture and facial detail from the third reference image.
```

### 4.2 Os dois modos de quebrar isso

| Erro | O que acontece |
|---|---|
| **Traduzir para um genérico** (`"the reference image"`) | As quatro referências **colapsam numa só** para o modelo. Ele passa a copiar o que for mais saliente — e o mais saliente é o rosto. Resultado: a roupa vem da referência de rosto |
| **Reordenar o array** | A tradução continua correta gramaticalmente e aponta para a imagem errada. O modelo copia rosto da folha de roupa e roupa da foto de rosto. **Não há erro, não há alerta** — só uma pessoa diferente saindo do outro lado |

### 4.3 A sutileza da regex

```python
saida = re.sub(r"@image_?(\d+)", ordinal, texto or "", flags=re.I)   # com número, PRIMEIRO
saida = re.sub(r"@image_?", "the reference image", saida, flags=re.I) # sem número, DEPOIS
```

**A ordem das duas substituições não é estilo.** Invertê-las faz a segunda casar com o prefixo `@image_` de `@image_2`, deixando para trás um `2` órfão no texto e destruindo a referência. É o tipo de linha que parece candidata a "simplificar" e não é.

> ⚠️ **`video.py` usa `@image_1` COM underscore**, diferente do photo studio, que usa `@image1`. A diferença vem do provider da origem e **não deve ser uniformizada** — a regex cobre as duas formas justamente por isso.

---

## 5. Seleção de guarda-roupa: o algoritmo

`select_wardrobe()` é a peça mais interessante do `persona_image.py`: escolhe um look entre 119 de forma que seja coerente com a personagem **e** diferente a cada geração. Cinco etapas, nesta ordem.

### 5.1 As cinco etapas

| # | Etapa | O que faz |
|---|---|---|
| 1 | **Filtro por gênero** | Reduz para o pool feminino (68) ou masculino (51). É filtro duro: não há pontuação envolvida |
| 2 | **Hard-filter pela vibe** | Deriva a tag *primária* de cada vibe escolhida e mantém só os looks que a contenham. **Se sobrarem menos de 3 entradas, volta ao pool inteiro** — um filtro que quase esvazia o pool é pior que filtro nenhum |
| 3 | **Pontuação por energia** | Distância entre a energia do look e `personality_energy` da persona: ≤ 15 → **+40** · ≤ 28 → **+20** · acima → **−20**. A penalidade não exclui: um look distante ainda pode vencer se casar muito bem por tags |
| 4 | **Pontuação por overlap de tags** | **+18 por tag** que casa entre o look e as tags de vibe (somadas às tags do `backstory_context`) |
| 5 | **Ruído + sorteio** | Soma `rng.random() * 14` a cada pontuação, ordena decrescente e **sorteia entre os 6 melhores** |

### 5.2 Por que ruído E sorteio, se um só bastaria

Parece redundância e não é — os dois resolvem problemas diferentes:

- **O ruído (±14)** embaralha a vizinhança da pontuação: dois looks que empatariam sempre na mesma ordem passam a se alternar. Como 14 é menor que a diferença entre as faixas de energia (20 e 40 pontos), ele **nunca inverte uma decisão estrutural** — só desempata.
- **O sorteio entre os 6 melhores** garante variedade mesmo quando um look domina a pontuação. Sem ele, uma persona com vibe muito específica usaria a mesma roupa em todas as fotos.

O efeito combinado é o que se quer de um guarda-roupa real: coerente, com favoritos, e nunca literalmente a mesma peça duas vezes seguidas.

### 5.3 A armadilha da lista vazia

```python
# NOTA: na origem o teste é `forceProfessionTags || ...`, e em JS uma lista
# vazia é truthy — ou seja, uma lista vazia forçada NÃO cai no fallback das
# vibes. Aqui isso vira `is not None` para preservar o comportamento (em
# Python a lista vazia seria falsy e mudaria o resultado).
if force_profession_tags is not None:
```

Um `if force_profession_tags:` — a forma pythônica, a que qualquer revisor pediria — **mudaria o resultado**: uma lista vazia cairia no fallback das vibes, que a origem não fazia. É o exemplo mais limpo do porquê da política da [§8](#8-a-política-de-porte-não-melhorar-nada).

---

## 6. Arquétipos de profissão

**100 arquétipos**, cada um uma regex testada contra a backstory da persona. O primeiro que casar vence. *"Dá aula de pilates numa academia de bairro"* casa `pilates.?instructor|pilates.?teacher|reformer` e traz consigo cinco coisas:

| Campo | O que é | Efeito |
|---|---|---|
| `tags` | Empurrão no guarda-roupa | Entram na pontuação da [§5](#5-seleção-de-guarda-roupa-o-algoritmo) |
| `scene_niche` | Chave do pool de cenas | Restringe as 37 cenas ao subconjunto plausível |
| `build_hint` | Tipo físico inferido | **Só usado se o usuário deixou o campo em branco** — 19 arquétipos têm |
| `physical_detail` | Marcador visível (óculos, etc.) | Somado ao bloco `Subject` — 3 arquétipos têm |
| `locked_scene` | Cena específica e literal | **29 arquétipos têm**, e é o campo mais forte |

### 6.1 A cena travada

`locked_scene` é uma cena escrita por extenso, não uma categoria:

> `"pilates studio, reformer machines visible soft in background, clean bright space with natural light"`

Ela é usada na **terceira variação** do trio de imagens que o motor gera. A lógica editorial: duas variações exploram cenários genéricos plausíveis, e a terceira ancora a personagem no lugar onde ela realmente trabalharia. É o que transforma "uma mulher bonita em um estúdio" em "uma instrutora de pilates no estúdio dela".

> ⚠️ **A classificação por LLM pode sobrescrever as `tags` do arquétipo, mas nunca a `locked_scene`.** A cena travada é conhecimento de domínio explícito; a classificação é inferência. Quando divergem, o explícito vence — mesma regra de `accounts.cadence` × `persona_card.cadence` ([docs/04 § 1.3](04-persona-card-spec.md#13-o-que-o-card-não-é)).

---

## 7. O motor de vídeo

`video.py` produz um prompt em **blocos rotulados** separados por `---`, seguido do bloco `ACTION`:

```
FORMAT / SUBJECT / WARDROBE / PRODUCT / ENVIRONMENT / MOOD /
COLOR LOGIC / STYLE / CAMERA / DELIVERY / DIRECTION / LOGIC RULE
---
ACTION: …
```

### 7.1 Diálogo, palavras e `[beat.]`

O motor **conta palavras por duração** e corta o que não cabe. A tabela vai de 14 palavras em 4 segundos a 52 em 15 segundos. A regra herdada do guia de origem é cortar palavras primeiro, linhas depois — um clipe em que a fala não termina é pior que um clipe com uma frase a menos.

A anotação insere marcações no texto falado:

- `"frase" [beat.]` — pausa depois da linha;
- `At 0:03 — <ação>.` — ações ancoradas em timestamp;
- pivôs de contraste ganham `[beat.]` próprio, para que a quebra da frase caia onde o sentido pede.

### 7.2 Por que existem dois conjuntos de humor

**8 humores falados e 8 mudos**, com as mesmas chaves (`Natural`, `Energetic`, `Luxury`, `Playful`, `Tutorial`, `Dramatic`, `Cozy`, `Confident`).

Não é duplicação. Os humores falados descrevem **entrega de fala** — *"delivery is unhurried"*, *"pauses land where they would in real speech"*. Usados num vídeo sem diálogo, esse vocabulário faz o modelo **animar a boca mesmo assim** (a Kling especialmente mexe os lábios de qualquer jeito). Os humores mudos descrevem a mesma energia **só como linguagem corporal**, com zero vocabulário de fala e um fecho explícito: *"Mouth stays closed, no lip movement."*

O mesmo problema tem uma segunda defesa: quando não há diálogo, o motor emite um negative prompt (`NO_TALK_NEGATIVE`) com `talking, speaking, mouth opening, lip sync…`. **Quando há diálogo, o negative prompt fica ausente** — empilhar negativas na presença de fala causa priming inverso.

### 7.3 A armadilha do `Math.round`

```python
def _js_round(x: float) -> int:
    """`Math.round` do JS: meio sempre para cima.

    O `round()` do Python arredonda para o par (round(2.5) == 2), o que produz
    timestamps e durações de shot diferentes da origem. Não trocar por `round`.
    """
    return math.floor(x + 0.5)
```

**O `round()` do Python não é o `Math.round` do JavaScript.** Python arredonda para o par mais próximo (*banker's rounding*): `round(2.5) == 2`. JavaScript arredonda o meio sempre para cima: `Math.round(2.5) === 3`.

Consequência concreta e verificada: sem o `_js_round` explícito, **`duracao=5` geraria o timestamp `0:02` em vez de `0:03`** — o marcador de ação cairia meio segundo antes do lugar, em todo vídeo de duração ímpar.

É a diferença mais barata de se cometer e a mais cara de se encontrar: não quebra teste de tipo, não quebra lint, não levanta exceção. O teste diferencial da [§9](#9-verificação-por-teste-diferencial) é o que a pegou.

---

## 8. A política de porte: "não melhorar nada"

A regra do porte, declarada na [ADR-0014](adr/0014-absorver-a-maquina-de-influencers.md#decisão) e repetida no cabeçalho de cada módulo:

> **Não "melhorar", não corrigir gramática, não otimizar algoritmo.**
> Os textos em inglês são resultado de tentativa e erro e estão copiados caractere a caractere.

### 8.1 As três justificativas

| Justificativa | Detalhe |
|---|---|
| **Uma vírgula muda a imagem** | Cada string é resultado de iteração contra o modelo real. O inglês torto que funciona vale mais que o inglês correto que não foi testado. Não há como saber qual detalhe carrega o efeito sem regerar tudo |
| **Consertar impede verificar** | Se o Python "melhorado" produz texto diferente do JavaScript, o teste diferencial não pode distinguir *correção intencional* de *bug de porte*. Corrigir durante o porte **destrói o único instrumento de verificação disponível** |
| **Bug estabelecido é contrato** | Onde a origem tinha comportamento errado mas estável, ele foi portado como está, com nota `# NOTA:`. As 191 mídias existentes foram geradas com esse comportamento |

### 8.2 Os bugs portados de propósito

| Onde | Comportamento preservado |
|---|---|
| `select_wardrobe` | Lista vazia forçada não cai no fallback das vibes (truthiness do JS) — [§5.3](#53-a-armadilha-da-lista-vazia) |
| `modelos.py` | `fal-seedream-lite` não recebe `output_format`, porque a origem não enviava |
| `identidade.py` | A backstory é cortada em 300 caracteres **crus**, sem respeitar limite de palavra |
| `produto.py` | `productDesc`/`angles` vazios caem no ramo do `else` (o `\|\|` do JS e o `or` do Python coincidem aqui — e a coincidência está documentada, não presumida) |
| `persona_image.py` | `get_times_for_niche()` existe e nunca é chamada, como na origem |

### 8.3 O que NÃO foi portado

Código morto e código do provider removido:

- `POSES_SOUL`, `HIGGSFIELD_SYSTEM`, `buildPromptInput` e todos os branches `model === 'soul_2'`;
- `VIDEO_TEMPLATES` + `applyTemplate` (nunca chamados), `DIALOGUE_STARTERS` (nunca usado), o bloco inalcançável de fal/replicate dentro de `generate()`;
- tudo de Higgsfield.

**Não portar código morto não viola a política**, porque código que nunca executa não tem comportamento a preservar — e mantê-lo custaria manutenção para sempre. A distinção é entre *comportamento observável* (intocável) e *código inalcançável* (dispensável).

### 8.4 Quando a política deixa de valer

Depois de o porte estar verificado, mudanças de comportamento são legítimas — mas passam a ser **mudança de produto**, com o custo normal: decisão registrada, regeração das referências afetadas, e a consciência de que as imagens antigas foram feitas pelo comportamento antigo. O que a política proíbe é mudar **durante** o porte, quando ainda não há como distinguir melhoria de erro.

---

## 9. Verificação por teste diferencial

A pergunta que o porte precisa responder é: *o Python produz exatamente o mesmo texto que o JavaScript?* A resposta veio de **executar os dois lado a lado e comparar byte a byte**.

| Motor | Comparações | Resultado |
|---|---|---|
| `photo_studio.py` | prompts | **710 / 710 idênticos** |
| `photo_studio.py` + `referencias.py` | instruções de referência | **432 / 432 idênticas** |
| `persona_image.py` | prompts | **168 / 168 idênticos** |
| `video.py` | cenários | **72 / 72 idênticos** |

### 9.1 O truque que torna o motor de persona comparável

`persona_image.py` sorteia — guarda-roupa, prop, cena candid. Comparar saídas aleatórias com saídas aleatórias não prova nada.

A solução foi **substituir `Math.random()` nos dois lados por um gerador determinístico idêntico**. Com isso, os 168/168 provam duas coisas de uma vez:

1. as **strings** são idênticas;
2. a **ORDEM de consumo de aleatoriedade** é idêntica — cada sorteio acontece no mesmo ponto da execução, consumindo o mesmo número da sequência.

O segundo ponto é o mais valioso e o mais fácil de perder: um `_pick()` implementado como `rng.choice()` em vez de `seq[int(rng.random() * len(seq))]` consome a sequência de forma diferente e desalinha **todos os sorteios seguintes**. As strings de um caso isolado ainda casariam; o conjunto de 168, não.

### 9.2 O que o teste diferencial não cobre

- **Não valida se o prompt é bom** — valida que é o mesmo. Qualidade de imagem continua sendo avaliada por olho.
- **Não cobre os caminhos com LLM** ([§1.1](#11-onde-o-llm-entra-então)), porque a resposta do modelo não é determinística. Só a montagem das strings em volta é comparável.
- **Não sobrevive à origem.** Uma vez que a máquina de influencers deixe de existir como código executável, o instrumento some. ❓ Se um dia for preciso reverificar, o caminho é comparar contra os prompts guardados em `media_assets.prompt` — que é exatamente por que essa coluna existe.

---

## 10. Aleatoriedade reproduzível

A origem usava `Math.random()` global. Consequência: **o mesmo prompt nunca podia ser repetido.** Uma foto que saiu ótima não tinha como ser regerada com uma variação; um bug de prompt não tinha como ser reproduzido para investigação.

No porte, toda função que sorteia recebe um `random.Random` explícito:

```python
def _pick(seq: Sequence[Any], rng: random.Random) -> Any:
    """Equivalente ao helper `R` da origem: sorteia um item da lista.

    Reproduz `arr[Math.floor(Math.random() * arr.length)]` — um único sorteio,
    para que a sequência de números consumidos do `rng` bata com a origem.
    """
    return seq[int(rng.random() * len(seq))]
```

Três ganhos, e um cuidado:

| | |
|---|---|
| ✅ | Mesmo seed → mesmo prompt. Regerar uma foto com uma única variação passa a ser possível |
| ✅ | Bug de prompt vira caso de teste reproduzível |
| ✅ | `PromptMontado.escolhas` registra **por que** o prompt ficou assim: cena, horário, pose, prop, guarda-roupa, paleta, tags do arquétipo, se a cena estava travada. Auditoria de imagem, equivalente ao `reasoning` de `day_plans` |
| ⚠️ | **A ordem dos sorteios é contrato.** Inserir um sorteio novo no meio da função desloca todos os seguintes: o mesmo seed passa a produzir outro prompt, e a comparabilidade com o histórico se perde |

---

## Ver também

- [ADR-0014](adr/0014-absorver-a-maquina-de-influencers.md) — por que portar em vez de reescrever; o ativo é o conhecimento, não o código
- [docs/13 — Geração de mídia](13-geracao-de-midia.md) — providers, registro de modelos, fila e armazenamento
- [docs/02 — Modelo de dados](02-modelo-de-dados.md) — as colunas de `personas` que os motores leem, e `media_assets.prompt`
- [docs/04 — Persona Card](04-persona-card-spec.md) — o que virou coluna (alimenta o motor) e o que ficou no card
- [docs/09 — LLM Gateway](09-llm-gateway.md) — por onde passam as três chamadas opcionais de LLM
- [`db/migrations/0008_absorve_maquina_de_influencers.sql`](../db/migrations/0008_absorve_maquina_de_influencers.sql) — o schema que sustenta tudo isto
