""""Copiar look de imagem" — porte de `src/utils/copyLook.js`.

Copia um look inteiro, ou uma peça só (só a blusa, chapéu, calça…), de fotos
de referência enviadas pelo usuário para cima da persona.

Dois caminhos na origem (ver CopyLookPanel):
  • Flexível — edição multi-imagem (FLUX.2 / Nano Banana Pro). Dá conta de
    QUALQUER item (chapéu, acessório, "só a blusa") passando
    [identidade, ...roupas] como imagens de referência, com um prompt que copia
    apenas a peça escolhida.
  • Alta fidelidade — try-on virtual da FASHN (falTryon.js), só roupa.

A análise da peça por LLM de visão (`analyzeGarment` na origem, opcional e
condicionada a haver algum LLM disponível) descrevia a peça escolhida com
precisão, o que afia o prompt da edição flexível. Aqui só a montagem das
strings é portada (`system_prompt_analise_peca`, `mensagem_analise_peca`,
`descricao_da_peca_a_partir_da_analise`); a chamada é do gateway do Olimpo.

Aspect ratio esperado:
    • copy-look, a foto única (`build_copy_look_prompt`)  → 3:4
    • turnaround, a folha de 4 painéis (`build_turnaround_prompt`) → 16:9

⚠️ Os textos em inglês são resultado de tentativa e erro e estão copiados
caractere a caractere. Não corrigir gramática, não reescrever.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from .tipos import Persona, PromptMontado

__all__ = [
    "COPY_ITEMS",
    "IDENTITY",
    "CopyItem",
    "build_copy_look_prompt",
    "build_turnaround_prompt",
    "system_prompt_analise_peca",
    "mensagem_analise_peca",
    "descricao_da_peca_a_partir_da_analise",
]

ASPECT_RATIO_COPY_LOOK = "3:4"
ASPECT_RATIO_TURNAROUND = "16:9"


@dataclass(frozen=True, slots=True)
class CopyItem:
    """Uma opção do seletor de "o que copiar".

    `category` não-nula ⇒ o try-on da FASHN está disponível para ela (mapeia
    para tops/bottoms/one-pieces da FASHN). `whole` ⇒ copiar o look inteiro.
    """

    id: str
    i18n: str        # rótulo exibido, chave de tradução na origem
    label: str       # entra literalmente no prompt
    category: str | None = None
    whole: bool = False


#: O que o usuário pode copiar. A ordem é a do seletor na origem.
COPY_ITEMS: tuple[CopyItem, ...] = (
    CopyItem(id="full",      i18n="Whole look",       label="the entire outfit",                       category=None,          whole=True),
    CopyItem(id="top",       i18n="Top / blouse",     label="the top / blouse",                        category="tops"),
    CopyItem(id="bottom",    i18n="Bottom / pants",   label="the bottom (pants, skirt or shorts)",     category="bottoms"),
    CopyItem(id="dress",     i18n="Dress / jumpsuit", label="the dress / jumpsuit",                    category="one-pieces"),
    CopyItem(id="jacket",    i18n="Jacket / coat",    label="the jacket / outerwear",                  category=None),
    CopyItem(id="hat",       i18n="Hat / cap",        label="the hat / cap",                           category=None),
    CopyItem(id="shoes",     i18n="Shoes",            label="the footwear",                            category=None),
    CopyItem(id="accessory", i18n="Accessories",      label="the accessories (bag, jewelry, glasses)", category=None),
)

#: A trava de identidade. Entra inteira no prompt de cópia de look.
IDENTITY = (
    "Keep the person's face and identity EXACTLY from the first reference image — same "
    "facial bone structure, face shape, jaw, nose, lips, eye shape and color, eyebrows, "
    "skin tone, skin texture and pores, freckles, moles, marks and natural asymmetries. "
    "Zero facial drift — unmistakably the same person. Keep their hair unless told "
    "otherwise."
)


def build_copy_look_prompt(
    persona: Persona | None,
    *,
    item_label: str = "",
    garment_desc: str | None = None,
    custom_text: str | None = None,
    whole_look: bool = False,
) -> PromptMontado:
    """Monta o prompt flexível de cópia multi-imagem.

    Produz UMA foto de corpo inteiro da pessoa vestindo a roupa copiada, e NÃO
    uma character sheet — a folha ancora demais na roupa original da pessoa e o
    modelo a mantém. O turnaround é gerado num segundo passo, a partir desta
    foto (ver `build_turnaround_prompt`).

    A PRIMEIRA referência é a pessoa (identidade); as demais são as roupas de
    onde copiar. Essa ordem é o contrato — ver `ORDEM_REFERENCIAS` em tipos.py.
    """
    phys = f"The person: {persona.physical_desc}. " if persona and persona.physical_desc else ""
    routing = (
        "The first reference image shows the PERSON — use it for identity only. The "
        "remaining reference image(s) show CLOTHING to copy — take ONLY the garment(s) "
        "from them; completely ignore any person, face, body, pose, background or crop "
        "in those clothing images."
    )
    if whole_look:
        target = (
            "Task: dress the person in the COMPLETE outfit from the clothing reference "
            "image(s). REPLACE all of their current clothing with every piece shown — the "
            "top, the bottom, any outerwear, footwear and accessories — each reproduced "
            "exactly (colors, materials, fabrics, textures, patterns, prints, logos, cut "
            "and fit). None of their original outfit remains."
        )
    else:
        target = (
            f"Task: put {item_label} onto the person, copied EXACTLY from the clothing "
            f"reference image(s) — exact color, material, fabric, texture, pattern, print, "
            f"logos, cut and fit. REPLACE whatever they are currently wearing in that same "
            f"category with this item; keep the rest of their outfit unchanged."
        )
    detail = (
        f" Exact details of what to copy: {garment_desc.strip()}."
        if garment_desc and garment_desc.strip()
        else ""
    )
    extra = f" {custom_text.strip()}." if custom_text and custom_text.strip() else ""

    texto = (
        "Photorealistic full-body photograph of ONE person, standing and facing the "
        "camera, framed head-to-toe, on a clean pure-white seamless studio background "
        f"with soft even lighting. {phys}\n"
        f"\n"
        f"{routing}\n"
        f"\n"
        f"{target}{detail}{extra}\n"
        f"\n"
        f"{IDENTITY}\n"
        f"\n"
        "Natural realistic fabric drape and fit on the body. RAW photo quality, "
        "ultra-sharp detail. Single full-body shot — no text, no labels, no collage, no "
        "split panels. Shot on Hasselblad X2D 100C."
    )

    return PromptMontado(
        texto=texto,
        aspect_ratio=ASPECT_RATIO_COPY_LOOK,
        escolhas={
            "item_label": item_label,
            "whole_look": whole_look,
            "tem_descricao_da_peca": bool(detail),
            "tem_texto_do_usuario": bool(extra),
        },
    )


def build_turnaround_prompt(persona: Persona | None) -> PromptMontado:
    """Transforma a foto única já gerada numa folha de 4 painéis.

    A referência aqui é a foto copiada — roupa certa e identidade já assadas
    dentro dela —, então isto é multiplicação limpa de pose: não há cópia de
    roupa e nem o problema de ancoragem que impede gerar a folha direto.
    """
    phys = f"The person: {persona.physical_desc}. " if persona and persona.physical_desc else ""

    texto = (
        f"Professional full-body character turnaround sheet built from the reference "
        f"photo. {phys}Keep the EXACT same person — face, identity, hair — and the EXACT "
        f"same complete outfit, footwear and accessories as shown in the reference image: "
        f"same colors, materials, fabrics, patterns, prints, logos, cut and fit. Do not "
        f"restyle or change anything.\n"
        f"\n"
        f"Output a single row of four equally sized full-body panels, each labelled in "
        f'clean sans-serif capitals above the figure: "FRONT VIEW" | "SIDE VIEW" | "BACK '
        f'VIEW" | "THREE-QUARTER VIEW". Panel 1 faces the camera, Panel 2 is a left '
        f"profile, Panel 3 faces directly away (show the back of the same outfit), Panel 4 "
        f"is a 45-degree three-quarter view. Same body, same proportions, same outfit in "
        f"every panel. Pure white seamless background, soft even flat studio lighting, no "
        f"shadows, no color cast.\n"
        f"\n"
        f"Photorealistic RAW photograph quality, ultra-sharp micro detail, single "
        f"connected sheet. Shot on Hasselblad X2D 100C."
    )

    return PromptMontado(texto=texto, aspect_ratio=ASPECT_RATIO_TURNAROUND)


# ---------------------------------------------------------------------------
# Análise da peça por LLM de visão (`analyzeGarment` na origem).
# Só a montagem das strings; a chamada é do gateway (maxTokens 1200,
# task 'vision').
# ---------------------------------------------------------------------------

#: Orçamento de tokens que a origem passava para a chamada de visão.
MAX_TOKENS_ANALISE_PECA = 1200


def system_prompt_analise_peca() -> str:
    """O system prompt da análise da peça."""
    return (
        "You are a fashion and apparel expert. You study clothing photos and describe "
        "garments precisely so they can be recreated photorealistically. Output JSON "
        "only — nothing else."
    )


def mensagem_analise_peca(
    item_label: str,
    whole_look: bool = False,
    quantidade_de_imagens: int = 1,
) -> str:
    """A mensagem de usuário da análise da peça.

    `quantidade_de_imagens` é o tamanho do array de fotos que acompanha a
    mensagem — a origem calculava a partir das imagens e pluralizava o texto.
    """
    focus = (
        "the entire outfit — every visible garment and accessory"
        if whole_look
        else f"{item_label} (ignore the person, pose, background, and any other clothing)"
    )
    plural = "s" if quantidade_de_imagens > 1 else ""

    return (
        f"You are given {quantidade_de_imagens} image{plural} of a look. Describe ONLY "
        f"{focus}.\n"
        f"\n"
        f'Output a JSON object with exactly one field "garmentDesc": a precise, definitive '
        f"description — exact colors on every surface, material/fabric, texture, pattern "
        f"or print, all logos and text, cut / silhouette / fit, closures and hardware, "
        f'notable details. Be confident — no hedging words like "likely" or "appears". No '
        f"markdown."
    )


def descricao_da_peca_a_partir_da_analise(resposta: dict[str, Any] | None) -> str:
    """Extrai `garmentDesc` do JSON já parseado; erra se não vier.

    NOTA: a origem erra em português mesmo, com o começo da resposta crua
    anexado ("A IA não conseguiu descrever a peça. Resposta: …"). Aqui o
    recorte da resposta crua fica com o gateway, que é quem a tem.
    """
    if not resposta or not resposta.get("garmentDesc"):
        raise ValueError("A IA não conseguiu descrever a peça.")
    return resposta["garmentDesc"]
