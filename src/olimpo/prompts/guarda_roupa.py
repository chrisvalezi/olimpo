"""Guarda-roupa — porte de `buildWardrobePrompt`
(`src/components/WardrobeDrawer.jsx`, linhas 11–22).

Troca a roupa e/ou o cabelo da persona numa folha de personagem que já existe.
A referência é a própria folha de 4 painéis da identidade, e o prompt é feito
de três blocos que se seguram um no outro:

  1. IDENTITY LOCK — replicar exatamente ossos, nariz, lábios, olhos, pele,
     sardas, pintas, cicatrizes e assimetrias: "zero facial drift".
  2. trava de layout — a mesma folha de 4 painéis FRONT/SIDE/BACK/3⁄4, mesmas
     poses, mesmos rótulos, fundo branco.
  3. "Change only: …" — a lista curta do que o usuário de fato pediu para
     mudar. É o único grau de liberdade do prompt; sem ela o modelo reinterpreta
     a folha inteira.

Aspect ratio esperado:
    • folha de guarda-roupa (`build_wardrobe_prompt`) → 16:9 (é a mesma folha
      de turnaround da identidade, com outra roupa)

⚠️ Os textos em inglês são resultado de tentativa e erro e estão copiados
caractere a caractere. Não corrigir gramática, não reescrever.
"""

from __future__ import annotations

from .tipos import Persona, PromptMontado

__all__ = ["build_wardrobe_prompt"]

ASPECT_RATIO_GUARDA_ROUPA = "16:9"

#: Usado quando o usuário não pediu nada — a folha ainda precisa mudar algo,
#: senão o prompt manda "Change only: ." e o modelo trava.
MUDANCA_PADRAO = "casual stylish outfit, natural hairstyle"


def build_wardrobe_prompt(
    persona: Persona,
    *,
    outfit: str = "",
    hair: str = "",
    custom_text: str | None = None,
) -> PromptMontado:
    """Monta a folha de guarda-roupa: mesma pessoa, mesmo layout, outra roupa.

    NOTA: a origem monta a lista de mudanças com `[a && …, b && …, c].filter(Boolean)`
    — entradas vazias somem. O `custom_text` entra sem prefixo, cru, ao
    contrário de `outfit` e `hair`, que ganham "outfit — " / "hairstyle — ".
    """
    phys = f"The subject: {persona.physical_desc}. " if persona.physical_desc else ""

    identity = (
        "IDENTITY LOCK — replicate exactly from reference: facial bone structure, face "
        "shape, jaw, nose bridge and tip, lip shape, eye shape and color, eyebrow arch "
        "and thickness, skin tone, skin texture and pores, all freckles, moles, marks, "
        "scars, natural asymmetries. Zero facial drift — this must be unmistakably the "
        "same person."
    )
    layout = (
        "Output must be the exact same 4-panel character turnaround sheet as the "
        "reference image. Single row of four equally sized full-body panels with these "
        'labels in clean sans-serif capitals above each: "FRONT VIEW" | "SIDE VIEW" | '
        '"BACK VIEW" | "THREE-QUARTER VIEW". Keep identical body poses, stance, arm '
        "positions, proportions, and panel layout from the reference. Do NOT change "
        "poses, labels, panel structure, background (pure white seamless), or lighting."
    )

    change_parts: list[str] = []
    if outfit:
        change_parts.append(f"outfit — {outfit}")
    if hair:
        change_parts.append(f"hairstyle — {hair}")
    if custom_text and custom_text.strip():
        change_parts.append(custom_text.strip())

    changes = f"Change only: {'; '.join(change_parts) or MUDANCA_PADRAO}."

    texto = (
        f"Professional full-body character turnaround sheet. {phys}Pure white seamless "
        f"background throughout. Soft neutral studio lighting, perfectly flat and even "
        f"across all four panels — no shadows, no color cast.\n"
        f"\n"
        f"{layout}\n"
        f"\n"
        f"{identity}\n"
        f"\n"
        f"{changes}\n"
        f"\n"
        f"Photorealistic RAW photograph quality, ultra-sharp micro detail. Shot on "
        f"Hasselblad X2D 100C."
    )

    return PromptMontado(
        texto=texto,
        aspect_ratio=ASPECT_RATIO_GUARDA_ROUPA,
        escolhas={
            "outfit": outfit,
            "hair": hair,
            "custom_text": custom_text,
            "usou_mudanca_padrao": not change_parts,
        },
    )
