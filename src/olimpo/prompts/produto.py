"""Folha de personagem do PRODUTO — porte de `src/utils/charSheetPrompt.js`.

O que é: uma imagem composta única com exatamente 6 painéis num grid rígido
de 3 colunas × 2 linhas, mostrando o mesmo produto de todos os ângulos. É essa
folha que dá consistência ao produto depois: quando ele aparece na mão da
persona, em qualquer pose, o modelo tem de onde copiar cor, material, logo e
proporção — sem inventar o que não viu.

Aspect ratio esperado:
    • folha de personagem do produto (`build_char_sheet_prompt`) → 16:9
      (o próprio texto termina com "16:9 landscape format" — mudar o ratio da
      geração sem mudar o texto deixa os dois em desacordo)

Dois caminhos, como na origem:
    • determinístico — `build_char_sheet_prompt`, função pura;
    • com LLM multimodal — a origem (`buildCharSheetPromptWithClaude`) mandava
      as fotos do produto para o LLM e usava a resposta como `product_desc` /
      `angles`. Aqui só a montagem das strings é portada
      (`system_prompt_analise_produto`, `mensagem_analise_produto`) e o parse
      da resposta (`char_sheet_a_partir_da_analise`); a chamada em si é do
      gateway do Olimpo.

⚠️ Os textos em inglês são resultado de tentativa e erro e estão copiados
caractere a caractere. Não corrigir gramática, não reescrever.
"""

from __future__ import annotations

from typing import Any

from .tipos import PromptMontado

__all__ = [
    "build_char_sheet_prompt",
    "system_prompt_analise_produto",
    "mensagem_analise_produto",
    "char_sheet_a_partir_da_analise",
]

#: Ratio da folha de personagem do produto. O texto do prompt também o declara.
ASPECT_RATIO_CHAR_SHEET = "16:9"


def build_char_sheet_prompt(
    brand: str,
    category: str | None = None,
    product_desc: str | None = None,
    angles: str | None = None,
) -> PromptMontado:
    """Monta a folha de 6 painéis do produto (determinística, sem LLM).

    `product_desc`, quando existe, substitui inteiramente "marca + categoria"
    como sujeito — é o texto que a análise multimodal devolve.

    `angles`, quando ausente, deixa a escolha dos 6 ângulos com o modelo: a
    origem prefere isso a uma lista fixa porque o conjunto informativo muda
    conforme o tipo de produto (boné não tem sola, tênis não tem forro de aba).

    NOTA: a origem testa `productDesc`/`angles` com `||`, ou seja string vazia
    cai no ramo do `else` igual a `null`. O `or` do Python tem o mesmo
    comportamento — preservado de propósito.
    """
    subject = product_desc or (f"{brand} {category}" if category else f"{brand} product")
    angle_spec = (
        f"The 6 panels show: {angles}."
        if angles
        else (
            "Choose the 6 most informative and commercially relevant angles for this "
            "specific product — select the angles that would appear on a real product "
            "character sheet, such as front, back, sides, top, bottom, and key detail "
            "closeups, based on what is most useful for this product type."
        )
    )

    texto = (
        f"Professional product character sheet on a pure white (#FFFFFF) background. "
        f"The subject is a {subject}. {angle_spec} Create a single composite image with "
        f"exactly 6 panels in a strict uniform 3-column by 2-row grid. All 6 panels are "
        f"perfectly equal in size — no panel larger or smaller than another, no gaps, no "
        f"overlapping, strict grid alignment. The product is visually identical across all "
        f"panels — same exact colors, materials, textures, logos, design details, and "
        f"proportions throughout. For any angle or surface not explicitly described, match "
        f"exactly the colors, materials, and finish shown in the reference image — do not "
        f"invent or assume any detail. All branding, logos, and text physically on the "
        f'product are preserved and clearly visible. No annotation labels, no "Front" / '
        f'"Back" / "Side" captions, no text overlays of any kind. Studio product '
        f"photography: soft even lighting, sharp focus throughout, perfectly clean white "
        f"background, professional commercial quality. 16:9 landscape format."
    )

    return PromptMontado(
        texto=texto,
        aspect_ratio=ASPECT_RATIO_CHAR_SHEET,
        escolhas={
            "brand": brand,
            "category": category,
            "product_desc": product_desc,
            "angles": angles,
            "angulos_escolhidos_pelo_modelo": not angles,
        },
    )


# ---------------------------------------------------------------------------
# Análise das fotos do produto por LLM multimodal.
#
# Na origem isto era `buildCharSheetPromptWithClaude(images, brand, category,
# _apiKey)`: analisava as fotos do produto pelo LLM ativo (Claude ou OpenAI —
# ambos multimodais). O argumento `apiKey` era legado e ignorado; as chaves são
# resolvidas dentro da camada de LLM, e ele só continuava na assinatura por
# causa de quem chamava.
#
# Aqui a chamada não é feita: estas funções só montam as strings, e quem fala
# com o modelo é o gateway do Olimpo (askLLM equivalente: maxTokens 2000,
# task 'vision').
# ---------------------------------------------------------------------------

#: Orçamento de tokens que a origem passava para a chamada de visão.
MAX_TOKENS_ANALISE_PRODUTO = 2000


def system_prompt_analise_produto() -> str:
    """O system prompt da análise multimodal do produto."""
    return (
        "You are a luxury product expert and photography director. You have deep "
        "knowledge of designer brands, product lines, and how they look from every "
        "angle. You study product images and use your training knowledge to produce "
        "detailed, accurate descriptions. Output JSON only — nothing else."
    )


def mensagem_analise_produto(
    brand: str,
    category: str | None = None,
    quantidade_de_imagens: int = 1,
) -> str:
    """A mensagem de usuário da análise multimodal.

    `quantidade_de_imagens` é o tamanho do array de fotos que acompanha a
    mensagem — a origem calculava a partir das imagens e pluralizava o texto.
    """
    linha_categoria = f"\nCategory: {category}" if category else ""
    plural = "s" if quantidade_de_imagens > 1 else ""

    return (
        f"Brand: {brand}{linha_categoria}\n"
        f"\n"
        f"You have been given {quantidade_de_imagens} image{plural} of this product from "
        f"different angles. Study all of them and identify exactly what product this is. "
        f"Use what you can see across all images AND your training knowledge to describe "
        f"it accurately from every angle.\n"
        f"\n"
        f"Output a JSON object with exactly two fields:\n"
        f"\n"
        f'"productDesc" — a precise, complete description covering the entire product: '
        f"exact colors on every surface, materials, all logos and text (front, back, "
        f"sides, interior), construction details, hardware. Use your knowledge of this "
        f"product line to fill in surfaces not visible in the image. Be specific and "
        f'confident — no hedging words like "likely" or "typically". Write it as '
        f"definitive fact.\n"
        f"\n"
        f'"angles" — exactly 6 panel descriptions for a professional character sheet, '
        f"each with specific visual details for that angle. Use your product knowledge to "
        f"describe what is actually on each surface — the real back closure, real side "
        f'panels, real sole or lining — not generic guesses. Example for a cap: "front '
        f"view showing embroidered H logo on structured crown, left profile showing side "
        f"panel seam and brim edge, right profile showing matching side panel, rear view "
        f"showing metal Hermès clasp and tonal strap, top-down view showing crown "
        f'stitching pattern, underside of brim showing contrast lining color and '
        f'stitching"\n'
        f"\n"
        f"Output only valid JSON. No explanation, no markdown."
    )


def char_sheet_a_partir_da_analise(
    brand: str,
    category: str | None,
    resposta: dict[str, Any] | None,
) -> PromptMontado:
    """Fecha o caminho com LLM: pega o JSON já parseado e monta a folha.

    Espelha o fim de `buildCharSheetPromptWithClaude`, inclusive os erros:
    resposta que não virou JSON e resposta sem `productDesc` são fatais.

    NOTA: o prompt pede "exactly 6 panel descriptions" em `angles`, e o modelo
    às vezes devolve lista em vez de string. A origem interpolava o valor
    direto no template — uma lista virava "a,b,c" (junção do JS, sem espaço).
    O porte mantém isso: `angles` é tratado como texto, sem normalização.
    """
    if not resposta:
        raise ValueError("Resposta do LLM não pôde ser interpretada como JSON.")
    product_desc = resposta.get("productDesc")
    if not product_desc:
        raise ValueError("JSON do LLM sem o campo productDesc.")
    return build_char_sheet_prompt(
        brand, category, product_desc, resposta.get("angles") or None
    )
