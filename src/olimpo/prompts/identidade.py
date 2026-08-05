"""As folhas que ancoram a identidade da persona — porte de
`buildCharacterSheetPrompt`, `buildCloseUpPrompt` e `buildFeatureSheetPrompt`
(`src/pages/Influencers.jsx`, linhas 68–116), mais `buildInfluencerSheetPrompt`
(`src/utils/charSheetPrompt.js`).

São três imagens geradas uma vez, no nascimento da persona, e usadas como
referência em tudo depois. Cada uma trava uma coisa diferente:

  • character sheet — turnaround de 4 painéis (FRONT/SIDE/BACK/3⁄4), fundo
    branco, "Shot on Hasselblad X2D 100C". Trava corpo, proporção e roupa.
  • close-up — headshot frontal de estúdio, sem pose, sem atitude, tipo foto de
    documento: "Phase One IQ4 150MP, 85mm f/2.8". Trava o rosto.
  • feature sheet — cartela macro EYE / BROW / LIP / SKIN / HAIR / HANDS,
    "Hasselblad 100mm macro". Trava o detalhe fino: íris, poro, sarda, unha.

Aspect ratio esperado (cada folha tem o seu; trocar quebra o enquadramento):
    • character sheet (`build_character_sheet_prompt`)  → 16:9
    • close-up        (`build_close_up_prompt`)         → 4:5
    • feature sheet   (`build_feature_sheet_prompt`)    → 2:3

⚠️ Os textos em inglês são resultado de tentativa e erro e estão copiados
caractere a caractere. Não corrigir gramática, não reescrever.
"""

from __future__ import annotations

from .tipos import Persona, PromptMontado

__all__ = [
    "build_character_sheet_prompt",
    "build_close_up_prompt",
    "build_feature_sheet_prompt",
    "build_influencer_sheet_prompt",
]

ASPECT_RATIO_CHARACTER_SHEET = "16:9"
ASPECT_RATIO_CLOSE_UP = "4:5"
ASPECT_RATIO_FEATURE_SHEET = "2:3"

#: Quanto da backstory entra no prompt da folha. A origem corta em 300
#: caracteres, cru (sem respeitar palavra) — preservado.
LIMITE_BACKSTORY = 300


def _phys(persona: Persona, rotulo: str) -> str:
    """Bloco de descrição física. Vazio quando não há descrição.

    O rótulo muda conforme a folha: as folhas de corpo inteiro dizem
    "The character:", as de rosto dizem "The subject:".
    """
    return f"{rotulo}: {persona.physical_desc}. " if persona.physical_desc else ""


def build_character_sheet_prompt(persona: Persona) -> PromptMontado:
    """Turnaround de 4 painéis em fundo branco — a folha-mãe da identidade."""
    phys = _phys(persona, "The character")
    style = f"Outfit: {persona.clothing_style}. " if persona.clothing_style else ""

    texto = (
        "Professional full-body character turnaround sheet. Pure white background, no "
        "background elements whatsoever. Soft neutral studio lighting, perfectly flat and "
        "even across all four panels — no shadows, no color cast, no vignette.\n"
        "\n"
        f"{phys}{style}\n"
        "\n"
        "Single row of four equally sized full-body shots from head to toe, each with a "
        "small label in clean sans-serif capitals printed above the figure:\n"
        'Panel 1 — "FRONT VIEW": character facing directly forward, arms relaxed at '
        "sides, feet together.\n"
        'Panel 2 — "SIDE VIEW": character in perfect left profile, arms at sides.\n'
        'Panel 3 — "BACK VIEW": character facing directly away, arms relaxed.\n'
        'Panel 4 — "THREE-QUARTER VIEW": character at 45-degree angle facing '
        "forward-right.\n"
        "\n"
        "Replicate every single physical detail identically across all four panels: exact "
        "facial structure and bone structure, unique facial features and natural "
        "asymmetries, precise skin tone, real pore texture, natural blemishes, freckles, "
        "moles, birthmarks, natural moisture and skin sheen, realistic catchlights in the "
        "eyes, exact iris color and detail, exact hair color and texture and styling. Zero "
        "beauty retouching — raw skin imperfections must be visible. Same outfit, same "
        "proportions, same scale in every panel.\n"
        "\n"
        "Shot on Hasselblad X2D 100C, photorealistic, ultra-sharp micro detail, RAW "
        "photograph quality. Character design sheet, model sheet, orthographic turnaround "
        "reference."
    )

    return PromptMontado(texto=texto, aspect_ratio=ASPECT_RATIO_CHARACTER_SHEET)


def build_close_up_prompt(persona: Persona) -> PromptMontado:
    """Headshot frontal de estúdio — trava o rosto.

    O texto insiste quatro vezes em "sem pose": cabeça reta, sem inclinação,
    expressão neutra, "identical to a casting reference or identity card photo".
    É de propósito — qualquer atitude na referência vaza para todas as imagens
    geradas depois.
    """
    phys = _phys(persona, "The subject")

    texto = (
        "Professional studio headshot. Subject facing directly forward, eyes looking "
        "straight into the camera lens. Framed from shoulders up — head, neck, and upper "
        "chest visible. Clean seamless pure white backdrop, soft gradient toward very "
        "light grey at edges, no texture, no cast shadows on background.\n"
        "\n"
        f"{phys}Soft diffused studio lighting: two large softboxes at 45-degree angles "
        "producing soft, even, shadow-free illumination across the face. Subtle "
        "catchlights visible in both eyes. No harsh under-nose or chin shadows. Skin tone "
        "reproduced accurately — natural pore texture, subtle imperfections visible, zero "
        "retouching.\n"
        "\n"
        "Replicate every physical detail from the reference image exactly: facial bone "
        "structure, unique facial features and natural asymmetries, precise skin tone, "
        "freckles, moles, iris color and detail, eyebrow shape, lip shape, hair color, "
        "texture and natural fall. The subject must be unmistakably the same "
        "individual.\n"
        "\n"
        "Subject standing straight, head completely level, facing dead-on into the camera "
        "— no tilt, no turn, no pose. Eyes looking directly into the lens. Neutral "
        "expression, mouth relaxed and closed. No modelling, no attitude, no special pose "
        "whatsoever. Identical to a casting reference or identity card photo.\n"
        "\n"
        "Shot on Phase One IQ4 150MP, 85mm portrait lens, f/2.8, studio strobe. "
        "Photorealistic, ultra-sharp facial detail, RAW photograph quality. Studio "
        "identity reference portrait."
    )

    return PromptMontado(texto=texto, aspect_ratio=ASPECT_RATIO_CLOSE_UP)


def build_feature_sheet_prompt(persona: Persona) -> PromptMontado:
    """Cartela macro de traços — EYE, BROW, LIP, SKIN, HAIR, HANDS.

    Layout de 4 linhas: duas de largura inteira (olho e sobrancelha) e duas com
    dois painéis lado a lado. O prompt pede explicitamente as calhas brancas
    entre os painéis — sem elas o modelo funde tudo numa colagem só.
    """
    phys = _phys(persona, "The subject")

    texto = (
        f"Beauty model feature reference sheet. {phys}Pure white background throughout. "
        "Clinical reference card layout — like a casting or makeup artist reference sheet "
        "printed on white paper. Bold black uppercase sans-serif labels above each panel. "
        "Clear white gutters between every panel and white margins around the outside.\n"
        "\n"
        "Layout — 4 rows stacked top to bottom:\n"
        'Row 1 (full width): one wide panel labelled "EYE" — extreme macro close-up '
        "centered tightly on both irises. The irises fill the majority of the frame. Shows "
        "exact iris color, pattern, and detail. Lashes visible at edges but irises are the "
        "dominant subject.\n"
        'Row 2 (full width): one wide panel labelled "BROW" — close-up from hairline to '
        "mid-nose showing exact brow shape, arch, thickness, hair direction, forehead "
        "skin.\n"
        "Row 3 (two equal side-by-side panels):\n"
        '  Left — labelled "LIP": close-up from nose base to chin showing exact lip shape, '
        "cupid's bow, natural lip color.\n"
        '  Right — labelled "SKIN TEXTURE": macro close-up of cheek skin showing pores, '
        "freckles, natural skin detail, zero retouching.\n"
        "Row 4 (two equal side-by-side panels):\n"
        '  Left — labelled "HAIR TEXTURE": close-up of hair strands showing exact color, '
        "shine, texture, wave or curl pattern.\n"
        '  Right — labelled "HANDS": close-up of hand showing nail shape, length, nail '
        "color or nail art, knuckle skin detail.\n"
        "\n"
        "Replicate the reference person's exact features in every panel: precise skin "
        "tone, freckle placement, hair color, lip shape, brow arch. Zero beauty retouching "
        "— raw photographic detail. White space clearly visible between all panels.\n"
        "\n"
        "Photorealistic RAW photograph quality, ultra-sharp macro detail in each panel. "
        "Shot on Hasselblad 100mm macro lens."
    )

    return PromptMontado(texto=texto, aspect_ratio=ASPECT_RATIO_FEATURE_SHEET)


def build_influencer_sheet_prompt(persona: Persona) -> PromptMontado:
    """Variante da folha de personagem que também usa a backstory.

    NOTA: na origem esta função (`buildInfluencerSheetPrompt`) mora em
    `src/utils/charSheetPrompt.js`, junto com os prompts de PRODUTO, embora não
    tenha nada a ver com produto — por isso foi portada para cá, e não para
    `produto.py`. É idêntica a `build_character_sheet_prompt` a menos de um
    bloco extra de contexto: a backstory cortada em 300 caracteres, com a
    instrução de deixá-la informar físico, presença e energia (personal trainer
    parece atlético, gamer parece relaxado, CEO passa confiança). As duas
    coexistem na origem; qual das duas usar é decisão de quem chama.
    """
    phys = _phys(persona, "The character")
    style = f"Outfit: {persona.clothing_style}. " if persona.clothing_style else ""
    backstory = persona.backstory.strip() if persona.backstory else ""
    ctx = (
        f"Character background: {backstory[:LIMITE_BACKSTORY]}. Let this inform their "
        "physique, presence, and energy — e.g. a personal trainer should look visibly "
        "athletic and fit, a gamer may look relaxed and casual, a CEO projects "
        "confidence. "
        if backstory
        else ""
    )

    texto = (
        "Professional full-body character turnaround sheet. Pure white background, no "
        "background elements whatsoever. Soft neutral studio lighting, perfectly flat and "
        "even across all four panels — no shadows, no color cast, no vignette.\n"
        "\n"
        f"{phys}{style}{ctx}\n"
        "\n"
        "Single row of four equally sized full-body shots from head to toe, each with a "
        "small label in clean sans-serif capitals printed above the figure:\n"
        'Panel 1 — "FRONT VIEW": character facing directly forward, arms relaxed at '
        "sides, feet together.\n"
        'Panel 2 — "SIDE VIEW": character in perfect left profile, arms at sides.\n'
        'Panel 3 — "BACK VIEW": character facing directly away, arms relaxed.\n'
        'Panel 4 — "THREE-QUARTER VIEW": character at 45-degree angle facing '
        "forward-right.\n"
        "\n"
        "Replicate every single physical detail identically across all four panels: exact "
        "facial structure and bone structure, unique facial features and natural "
        "asymmetries, precise skin tone, real pore texture, natural blemishes, freckles, "
        "moles, birthmarks, natural moisture and skin sheen, realistic catchlights in the "
        "eyes, exact iris color and detail, exact hair color and texture and styling. Zero "
        "beauty retouching — raw skin imperfections must be visible. Same outfit, same "
        "proportions, same scale in every panel.\n"
        "\n"
        "Shot on Hasselblad X2D 100C, photorealistic, ultra-sharp micro detail, RAW "
        "photograph quality. Character design sheet, model sheet, orthographic turnaround "
        "reference."
    )

    return PromptMontado(texto=texto, aspect_ratio=ASPECT_RATIO_CHARACTER_SHEET)
