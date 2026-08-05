"""Motor de prompt de imagem para criar uma persona do zero (wizard "Create").

Porte literal de `src/utils/systemPrompt.js` da máquina de influencers.

Isto NÃO é um prompt de LLM: é um motor determinístico que monta o texto que
vai para o gerador de imagem. Cada string aqui é resultado de tentativa e erro
contra o modelo — mudar uma vírgula muda a imagem gerada. Por isso o porte é
literal: nada de "melhorar" o inglês, corrigir gramática ou otimizar o
algoritmo.

O que o motor sabe fazer, em camadas:

1. `TIME_CONFIGS` — 6 horários, cada um com um parágrafo de física de luz.
2. `POSES` — 5 poses, escolhidas pelo slider de personalidade.
3. `WARDROBE` — a biblioteca de looks, indexada por gênero, energia e tags.
4. `select_wardrobe` — filtra por gênero, hard-filter pela vibe, pontua por
   distância de energia e overlap de tags, adiciona ruído e sorteia entre os
   6 melhores.
5. `BACKSTORY_ARCHETYPES` — arquétipos de profissão (regex sobre a backstory)
   que devolvem tags, nicho de cena, tipo físico, marcador visível e — o mais
   importante — a cena travada da terceira variação.
6. `montar_prompt` / `montar_tres_variacoes` — os montadores finais.

⚠️ Aleatoriedade: a origem usa `Math.random()` global. Aqui toda função que
sorteia recebe um `random.Random` explícito (`rng`), para que o mesmo seed
reproduza o mesmo prompt — coisa que a origem não conseguia fazer. A ORDEM dos
sorteios foi preservada exatamente como na origem, porque é ela que define o
que sai de cada seed.

Não portado de propósito (código morto ou do provider Higgsfield, que foi
removido): `POSES_SOUL`, `HIGGSFIELD_SYSTEM`, `buildPromptInput` e todos os
branches `model === 'soul_2'`.
"""

from __future__ import annotations

import random
import re
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from typing import Any

from .tipos import Persona, PromptMontado

#: Assinatura de uma pose: recebe o prop (ou nada nas mãos) e devolve o
#: parágrafo de pose. É função porque o prop entra no meio do texto.
Pose = Callable[[str | None], str]


# ── Helpers ───────────────────────────────────────────────────────────────


def _pick(seq: Sequence[Any], rng: random.Random) -> Any:
    """Equivalente ao helper `R` da origem: sorteia um item da lista.

    Reproduz `arr[Math.floor(Math.random() * arr.length)]` — um único sorteio,
    para que a sequência de números consumidos do `rng` bata com a origem.
    """
    return seq[int(rng.random() * len(seq))]


# ── Configurações de hora do dia / iluminação ─────────────────────────────


@dataclass(frozen=True, slots=True)
class TimeConfig:
    """Um horário e o parágrafo de física de luz que o acompanha."""

    label: str
    lighting: str


TIME_CONFIGS: tuple[TimeConfig, ...] = (
    TimeConfig(
        label="golden hour late afternoon",
        lighting="late afternoon sun hitting from behind-right — warm golden light rimming the hair and the lit cheek, open sky filling the shadow side in cooler bluish tones. Both temperatures visible on the face at the same time. The kind of accidental perfect light you get outside around 5pm.",
    ),
    TimeConfig(
        label="overcast soft daylight",
        lighting="solid overcast sky diffusing everything into even, directionless softness — no hard shadows anywhere, just gentle top-down modeling from the brightest patch of cloud. Cool and neutral throughout. Flat but honest.",
    ),
    TimeConfig(
        label="bright sunny mid-morning",
        lighting="direct mid-morning sun from the front-left — hard, honest daylight with a clear nose shadow and a bright lit side. The shadow side filled by open blue sky. Exactly the kind of full-sun look an iPhone captures with auto exposure outdoors.",
    ),
    TimeConfig(
        label="blue-hour dusk",
        lighting="sun already down — a warm sodium streetlamp from camera-left is the main light, directional and orange. The open sky fills the shadow side in deep cool blue. A nearby shopfront or neon sign catches the hair from behind. Three natural color sources on the face simultaneously, all because of where she is standing.",
    ),
    TimeConfig(
        label="soft indoor afternoon window",
        lighting="afternoon daylight coming through a large window to camera-left — the near cheek lit and warm, falling off across the shadow side. Warm overhead room lighting fills the shadows gently. Window light and ambient room fill present at the same time. Natural, found, not arranged.",
    ),
    TimeConfig(
        label="morning indoor café",
        lighting="window to the right letting in morning daylight as the main light source — warm overhead café bulbs secondary, faint cool screen or phone glow on the shadow side. Three natural light sources visible on the face because of where she's sitting, not because anything was set up.",
    ),
)


def get_times_for_niche() -> tuple[TimeConfig, ...]:
    """Devolve todos os horários.

    NOTA: existe na origem mas nunca é chamada — a filtragem de horário é
    feita inline em `montar_prompt` (cenas externas não podem receber luz de
    interior). Portada para não perder a superfície do módulo original.
    """
    return TIME_CONFIGS


# ── Pose — dirigida pelo slider de personalidade ──────────────────────────


def _pose_frontfacing(prop: str | None) -> str:
    maos = (
        f"{prop} held naturally in front of the body, cradled loosely in both hands at mid-chest"
        if prop
        else "arms relaxed at the sides or one hand resting loosely near the hip, fingers natural"
    )
    return (
        "body facing directly toward camera, weight shifted onto one leg for a subtle hip tilt — relaxed, not stiff. "
        f"{maos}. Eyes meeting the lens directly with a quiet, present expression — not forced, not a performance. "
        'The "comfortable in front of the camera" framing. Face fully visible and front-lit.'
    )


def _pose_contemplative(prop: str | None) -> str:
    maos = (
        f"{prop} held loosely at the side, almost forgotten"
        if prop
        else "hands relaxed loosely in front, fingers barely interlaced"
    )
    return (
        "body facing 45° away from camera, weight forward on one leg. "
        f"{maos}. Head turned back toward the lens mid-thought, eyes glancing toward but not fully meeting it — "
        "somewhere else mentally. A quiet, inward expression — not performing."
    )


def _pose_plandid(prop: str | None) -> str:
    maos = (
        f"{prop} held naturally in one hand, wrist relaxed"
        if prop
        else "one hand mid-loose-gesture near the hip, the other hanging naturally"
    )
    return (
        "body angled 25–30° to camera, weight settled on the back leg, hips slightly offset. "
        f"{maos}. Eyes glancing down-and-off-axis, 15° away from lens. "
        "Expression caught mid-thought — a specific private moment. "
        'The "noticed the camera half a second ago" framing.'
    )


def _pose_posed_cute(prop: str | None) -> str:
    maos = (
        f"{prop} held in both hands at chest height, elbows soft"
        if prop
        else "one hand gently touching the side of the jaw, fingers loose and natural"
    )
    return (
        "body in soft 3/4 angle to camera, shoulders relaxed and slightly dropped. "
        f"{maos}. Eyes meeting the lens with a quiet small expression — a half-smile just forming, "
        "not fully committed. Posing but acting like she isn't."
    )


def _pose_candid(prop: str | None) -> str:
    # Só troca para "mid-sip" quando o prop é uma bebida — levar um livro à
    # boca não faz sentido.
    acao = (
        f"bringing the {prop} toward the mouth, mid-sip, body naturally leaning slightly forward"
        if prop and DRINK_PROP_PATTERN.search(prop)
        else "a genuine mid-laugh or bright spontaneous expression, body and shoulders caught in motion, one hand mid-gesture near the chest"
    )
    return (
        f"mid-action — caught at the apex of {acao}. "
        "Eyes looking directly toward the lens — spontaneous, unguarded eye contact full of real energy. "
        "Not posed, not looking away — the camera caught them in a real moment while they were already looking at it."
    )


#: As 5 poses. A identidade das funções é usada para nomear o "Use case" no
#: prompt final, então elas precisam ser objetos estáveis no módulo.
POSES: dict[str, Pose] = {
    "frontfacing": _pose_frontfacing,
    "contemplative": _pose_contemplative,
    "plandid": _pose_plandid,
    "posed_cute": _pose_posed_cute,
    "candid": _pose_candid,
}


def get_pose_from_personality(p: int, rng: random.Random) -> Pose:
    """Personalidade → pose. Introvertido olha para dentro; extrovertido encara."""
    if p < 28:
        return POSES["contemplative"]
    if p < 50:
        return POSES["plandid"]
    if p < 70:
        return POSES["plandid"] if rng.random() > 0.5 else POSES["posed_cute"]
    return POSES["candid"] if rng.random() > 0.5 else POSES["posed_cute"]


# ── Biblioteca de guarda-roupa ────────────────────────────────────────────
# Cada entrada: { text, gender, tags, energy (0–100), niches }
# energy: 0 = mais discreto/quieto, 100 = mais ousado/expressivo
# O slider de personalidade mapeia direto na faixa de energia — é isto que
# faz o motor ser "mais profundo que a pose".
#
# Famílias de tags:
#   quiet, minimalist, clean, structured, editorial
#   casual, urban, street, dark, moody
#   earthy, natural, bohemian, cottagecore
#   y2k, playful, nostalgic
#   glam, bold, evening
#   sport, functional, active
#   preppy, classic, polished


@dataclass(frozen=True, slots=True)
class WardrobeEntry:
    """Um look da biblioteca."""

    gender: str
    energy: int
    tags: tuple[str, ...]
    niches: tuple[str, ...]
    text: str


WARDROBE: tuple[WardrobeEntry, ...] = (
    # ── FEMININO ──────────────────────────────────────────────
    # Quieto / discreto (energia 0–20)
    WardrobeEntry(
        gender="female", energy=5,
        tags=("quiet", "minimalist", "natural"),
        niches=("fashion", "lifestyle", "travel", "any"),
        text="Oversized linen shirt worn as a dress, belted loosely at the waist with a thin leather cord, hem falling mid-thigh; bare legs; flat leather mule — no jewelry, no logos visible",
    ),
    WardrobeEntry(
        gender="female", energy=10,
        tags=("quiet", "minimalist", "clean"),
        niches=("fashion", "beauty", "lifestyle", "any"),
        text="Wide-leg linen trousers, slightly cropped at the ankle; fine-knit long-sleeve top tucked in; no accessories; clean leather slide — no logos visible",
    ),
    WardrobeEntry(
        gender="female", energy=12,
        tags=("quiet", "minimalist", "structured"),
        niches=("fashion", "tech", "finance", "lifestyle"),
        text="Soft oversized turtleneck, hem just untucked; wide-leg tailored trousers, clean break; worn-in leather loafer — no accessories, no logos visible",
    ),
    WardrobeEntry(
        gender="female", energy=18,
        tags=("quiet", "glam", "minimalist"),
        niches=("fashion", "lifestyle", "any"),
        text="Slip dress in heavy satin, midi length, thin straps, no jewelry; flat leather thong sandal — no logos visible",
    ),
    # Discreto, com um elemento intencional (energia 20–40)
    WardrobeEntry(
        gender="female", energy=22,
        tags=("editorial", "minimalist", "structured"),
        niches=("fashion", "tech", "finance"),
        text="Oversized blazer worn as a top, one button fastened, no shirt visible underneath; wide-leg trousers; clean pointed-toe leather flat — one thin gold chain, the only jewelry — no logos visible",
    ),
    WardrobeEntry(
        gender="female", energy=28,
        tags=("clean", "minimalist", "casual"),
        niches=("lifestyle", "fashion", "any"),
        text="Straight-leg denim, clean hem; fitted ribbed tank top tucked in; two thin layered delicate gold chains at the collarbone; clean low-top leather sneakers — no logos visible",
    ),
    WardrobeEntry(
        gender="female", energy=30,
        tags=("quiet", "minimalist", "casual"),
        niches=("lifestyle", "travel", "fashion"),
        text="Worn oversized cashmere crewneck sweater, slightly pilling at the cuffs; slim straight denim; clean leather loafer; small pebbled leather crossbody — no logos visible",
    ),
    WardrobeEntry(
        gender="female", energy=35,
        tags=("minimalist", "clean", "travel"),
        niches=("travel", "lifestyle", "fashion"),
        text="Linen shirt, collar open, sleeves slightly rolled once at the forearm; high-waist straight-leg linen trousers, clean hem; leather strappy sandal, minimal ankle strap — no logos visible",
    ),
    WardrobeEntry(
        gender="female", energy=38,
        tags=("preppy", "classic", "polished"),
        niches=("fashion", "lifestyle", "tech"),
        text="Fitted polo shirt, collar up slightly; high-waist straight-leg chino; leather sneaker, clean; a single thin gold band ring — no logos visible",
    ),
    # Equilibrado / versátil (energia 40–60)
    WardrobeEntry(
        gender="female", energy=42,
        tags=("casual", "clean", "urban"),
        niches=("lifestyle", "travel", "any"),
        text="Oversized vintage-wash tee, neckline slightly relaxed, tucked loosely at the front; high-waist straight-leg denim; low-top canvas shoe, faintly scuffed — no logos visible",
    ),
    WardrobeEntry(
        gender="female", energy=45,
        tags=("minimalist", "structured", "clean"),
        niches=("fashion", "lifestyle", "tech"),
        text="Fitted ribbed mock-neck top; tailored wide-leg trousers, clean hem; minimal leather loafer; a thin gold bracelet — no logos visible",
    ),
    WardrobeEntry(
        gender="female", energy=48,
        tags=("earthy", "casual", "natural"),
        niches=("lifestyle", "travel", "any"),
        text="Well-worn straight-leg medium-wash denim; linen shirt tucked in, collar open; small simple gold hoop earrings; clean sneaker — no logos visible",
    ),
    WardrobeEntry(
        gender="female", energy=52,
        tags=("casual", "quiet", "cottagecore"),
        niches=("lifestyle", "any"),
        text="Long open cardigan in soft knit over a simple fitted tee; straight-leg jeans; leather low sneaker; a thin woven cord bracelet — no logos visible",
    ),
    WardrobeEntry(
        gender="female", energy=55,
        tags=("bohemian", "earthy", "natural"),
        niches=("travel", "lifestyle", "wellness"),
        text="Flowy midi dress in lightweight gauze or crinkled cotton, slightly tiered skirt, adjustable thin straps; flat leather sandal worn soft at the footbed; layered cord and thin beaded bracelets on both wrists, a simple thin pendant necklace — no logos visible",
    ),
    WardrobeEntry(
        gender="female", energy=58,
        tags=("clean", "casual", "sport"),
        niches=("fitness", "lifestyle", "any"),
        text="Oversized cotton hoodie, slightly cropped, hood down; bike shorts; thick-sole trainer; small silver hoop earrings — no logos visible",
    ),
    # Peças de personalidade começam a aparecer (energia 60–80)
    WardrobeEntry(
        gender="female", energy=62,
        tags=("editorial", "urban", "street"),
        niches=("fashion", "entertainment", "lifestyle"),
        text="Fluid-cut collarless oversized blazer worn open over a fitted ribbed bodysuit or tank; clean straight-leg tailored trousers in a contrasting neutral; pointed-toe leather mule, slight block heel; one sculptural resin or stone ring — no logos visible",
    ),
    WardrobeEntry(
        gender="female", energy=65,
        tags=("y2k", "casual", "playful"),
        niches=("lifestyle", "entertainment", "fashion"),
        text="Y2K-adjacent low-rise straight-leg medium-wash denim; fitted ribbed halter top; a thin silver chain belt; clean low-top sneaker — no logos visible",
    ),
    WardrobeEntry(
        gender="female", energy=68,
        tags=("earthy", "bohemian", "natural"),
        niches=("travel", "lifestyle", "fashion"),
        text="Wrap blouse in lightweight viscose, tied at the front, slightly billowing at the sleeves; high-waist wide-leg denim; leather wedge espadrille; two thin layered necklaces — no logos visible",
    ),
    WardrobeEntry(
        gender="female", energy=72,
        tags=("dark", "moody", "street"),
        niches=("entertainment", "fashion", "lifestyle"),
        text="Oversized faded graphic tee, neckline slightly wide, tucked at the front into high-waist straight-leg denim with subtle knee distress; chunky platform boot, zip detail at ankle; a worn leather cuff — no logos visible",
    ),
    WardrobeEntry(
        gender="female", energy=75,
        tags=("casual", "street", "y2k"),
        niches=("lifestyle", "entertainment", "fashion"),
        text="Cropped raw-edge denim jacket worn open over a fitted ribbed tank; high-waist flared denim; a simple thin leather belt; clean vintage leather sneaker — no logos visible",
    ),
    WardrobeEntry(
        gender="female", energy=78,
        tags=("editorial", "structured", "polished"),
        niches=("fashion", "finance", "tech"),
        text="Sharply tailored double-breasted blazer with a strong shoulder, worn as the only top layer; matching wide-leg trouser; clean pointed-toe leather oxford; a single architectural earring — no logos visible",
    ),
    # Ousado / expressivo (energia 80–100)
    WardrobeEntry(
        gender="female", energy=82,
        tags=("glam", "editorial", "bold"),
        niches=("fashion", "entertainment"),
        text="Faux-fur trim coat, worn open; fitted ribbed turtleneck underneath; straight tailored trousers; pointed-toe ankle boot; statement oversized resin drop earrings — no logos visible",
    ),
    WardrobeEntry(
        gender="female", energy=88,
        tags=("editorial", "bold", "structured"),
        niches=("fashion",),
        text="Wide-leg trousers in structured crepe; matching blazer worn open over a barely-there bralette; sculptural square-toe block heel; one architectural cuff — no logos visible",
    ),
    WardrobeEntry(
        gender="female", energy=92,
        tags=("editorial", "dark", "glam"),
        niches=("fashion", "entertainment"),
        text="Sheer organza blouse, buttons to the collar, translucent over a bralette; wide-leg tailored trousers; strappy heeled sandal; a single large sculptural resin ring — no logos visible",
    ),
    WardrobeEntry(
        gender="female", energy=96,
        tags=("bold", "playful", "editorial"),
        niches=("fashion", "entertainment"),
        text="Cropped structured blazer worn open, no top underneath; wide-leg tailored trouser; pointed-toe leather kitten heel; a stack of thin gold rings — no logos visible",
    ),
    # Arrumado / glam em energia média
    WardrobeEntry(
        gender="female", energy=48,
        tags=("glam", "evening", "polished"),
        niches=("fashion", "lifestyle", "entertainment", "any"),
        text="Satin slip midi dress, thin adjustable straps, fabric draping softly at the hip with a subtle liquid sheen; dainty thin-strap sandal; two delicate layered chains at the collarbone — no logos visible",
    ),
    WardrobeEntry(
        gender="female", energy=56,
        tags=("glam", "editorial", "structured"),
        niches=("fashion", "entertainment", "lifestyle"),
        text="Tailored blazer dress, single-button, hitting mid-thigh with slight shoulder structure; sheer tights; pointed-toe kitten heel; a simple gold bracelet — no logos visible",
    ),
    WardrobeEntry(
        gender="female", energy=62,
        tags=("glam", "evening", "polished"),
        niches=("fashion", "entertainment", "any"),
        text="Fitted ribbed knit midi dress with a modest scoop neckline; strappy heeled sandal; thin layered gold chains and small sculptural gold hoops — no logos visible",
    ),
    WardrobeEntry(
        gender="female", energy=70,
        tags=("glam", "bold", "evening"),
        niches=("fashion", "entertainment"),
        text="Silky wrap midi dress, thin tie at the waist, the fabric catching light as she moves; pointed-toe strappy heeled sandal; gold drop earrings and a single thin chain — no logos visible",
    ),
    # Dark & Moody (expandido) — todas as entradas trazem a cor escura
    # escrita explicitamente, senão o modelo não renderiza direito
    WardrobeEntry(
        gender="female", energy=60,
        tags=("dark", "moody", "structured"),
        niches=("fashion", "entertainment", "lifestyle"),
        text="Fitted ribbed all-black turtleneck, long sleeve; high-waist wide-leg tailored trousers in black or deep charcoal; pointed-toe ankle boot in black leather; a single thin silver chain at the collarbone — no logos visible",
    ),
    WardrobeEntry(
        gender="female", energy=65,
        tags=("dark", "moody", "editorial"),
        niches=("fashion", "entertainment"),
        text="Midi-length slip-style dress in deep black matte satin, thin straps, fabric skimming the body closely; barely-there heeled sandal in black; a thin silver chain stacked with a small drop pendant — no logos visible",
    ),
    WardrobeEntry(
        gender="female", energy=78,
        tags=("dark", "moody", "street"),
        niches=("fashion", "entertainment", "lifestyle"),
        text="Oversized structured black leather-look jacket, slightly stiff at the shoulder; fitted ribbed black midi skirt underneath; chunky black lug-sole boot; no jewelry — no logos visible",
    ),
    WardrobeEntry(
        gender="female", energy=85,
        tags=("dark", "moody", "editorial"),
        niches=("fashion", "entertainment"),
        text="Long draped overcoat in black or deep charcoal, minimal lapel, worn open or loosely belted; slim-cut trousers in the same dark tone; sleek pointed-toe boot in black leather; one architectural sculptural ring — no logos visible",
    ),
    # Cottagecore (expandido)
    WardrobeEntry(
        gender="female", energy=30,
        tags=("cottagecore", "natural", "earthy"),
        niches=("lifestyle", "any"),
        text="Flowy midi-length printed cotton dress, fitted at the chest with a square neckline and soft gathered skirt; flat leather sandal with a simple strap; small delicate stud earrings — no logos visible",
    ),
    WardrobeEntry(
        gender="female", energy=42,
        tags=("cottagecore", "earthy", "quiet"),
        niches=("lifestyle", "any"),
        text="Linen pinafore dress, adjustable shoulder straps, worn over a long-sleeve fitted top; simple leather mule; small woven basket bag — no logos visible",
    ),
    WardrobeEntry(
        gender="female", energy=55,
        tags=("cottagecore", "natural", "bohemian"),
        niches=("lifestyle", "any"),
        text="Puff-sleeve cotton blouse with smocking at the wrists, collar slightly open; high-waist straight-leg denim; leather ankle boot, round-toe; a small delicate crossbody bag — no logos visible",
    ),
    WardrobeEntry(
        gender="female", energy=65,
        tags=("cottagecore", "earthy", "editorial"),
        niches=("lifestyle", "fashion"),
        text="Prairie-style cotton midi dress with a fitted bodice, long flowing skirt, and small puffed sleeves; leather loafer or low boot; thin layered necklaces, dainty and botanical in feel — no logos visible",
    ),
    # Coastal (feminino)
    WardrobeEntry(
        gender="female", energy=25,
        tags=("coastal", "natural", "quiet"),
        niches=("lifestyle", "travel", "any"),
        text="Wide-leg linen trousers, slightly relaxed; simple fitted ribbed tank, thin straps; flat leather sandal, worn soft; no jewelry — no logos visible",
    ),
    WardrobeEntry(
        gender="female", energy=42,
        tags=("coastal", "natural", "casual"),
        niches=("lifestyle", "travel", "any"),
        text="Linen button-down shirt, oversized and half-tucked, sleeves rolled high; straight-leg denim cut-offs, frayed hem at mid-thigh; flat leather slide; thin woven cord anklet — no logos visible",
    ),
    WardrobeEntry(
        gender="female", energy=58,
        tags=("coastal", "natural", "earthy"),
        niches=("lifestyle", "travel", "fashion"),
        text="Lightweight crochet cover-up worn over a simple ribbed bralette, hem hitting mid-thigh; flat woven sandal; layered cord and natural-material necklaces — no logos visible",
    ),
    WardrobeEntry(
        gender="female", energy=72,
        tags=("coastal", "natural", "bohemian"),
        niches=("lifestyle", "travel", "fashion"),
        text="Flowy wrap midi skirt in lightweight woven fabric, tied at the hip; fitted linen crop top; strappy flat sandal; layered shell and cord jewellery, sun-worn and natural — no logos visible",
    ),
    # Fitness/esporte — contexto de academia
    WardrobeEntry(
        gender="female", energy=30,
        tags=("sport", "functional", "clean"),
        niches=("fitness", "wellness", "lifestyle"),
        text="Seamless sports bra with subtle vertical ribbing; matching high-waist compression leggings, slight sheen at the hip; clean training shoe — no logos visible",
    ),
    WardrobeEntry(
        gender="female", energy=40,
        tags=("sport", "earthy", "natural"),
        niches=("fitness", "wellness", "lifestyle"),
        text="Seamless sports bra with subtle cross-back detail; matching high-waist compression leggings; trail-running shoe — no logos visible",
    ),
    WardrobeEntry(
        gender="female", energy=50,
        tags=("sport", "clean", "natural"),
        niches=("fitness", "wellness", "lifestyle"),
        text="Matching set: ribbed high-neck sports bra and high-waist leggings; clean low-profile trainer; small pearl stud earrings — no logos visible",
    ),
    WardrobeEntry(
        gender="female", energy=65,
        tags=("sport", "earthy", "casual"),
        niches=("fitness", "wellness", "lifestyle"),
        text="Sports bra with wide straps; matching full-length high-waist leggings; clean gum-sole running shoe — a thin gold chain necklace — no logos visible",
    ),
    # Athleisure — pegada esportiva para contextos de lifestyle fora da academia
    WardrobeEntry(
        gender="female", energy=48,
        tags=("sport", "casual", "clean"),
        niches=("lifestyle", "fashion", "any"),
        text="Wide-leg track pants in soft jersey fabric, slight sheen; fitted ribbed crop top with thin straps; clean low-profile trainer; small gold hoops — no logos visible",
    ),
    WardrobeEntry(
        gender="female", energy=55,
        tags=("sport", "casual", "urban"),
        niches=("lifestyle", "fashion", "any"),
        text="Pleated tennis mini skirt; fitted ribbed athletic tank, thin straps; clean low-top leather sneaker; a thin chain necklace — no logos visible",
    ),
    WardrobeEntry(
        gender="female", energy=62,
        tags=("sport", "casual", "street"),
        niches=("lifestyle", "fashion", "entertainment", "any"),
        text="Fitted zip-up track jacket, zipped halfway; matching wide-leg track pants; chunky clean trainer; small hoop earrings — no logos visible",
    ),
    WardrobeEntry(
        gender="female", energy=75,
        tags=("sport", "casual", "playful"),
        niches=("lifestyle", "fashion", "entertainment", "any"),
        text="Pleated tennis mini skirt; fitted cropped athletic zip-up, zipped halfway; clean chunky-sole trainer; small gold hoops — no logos visible",
    ),
    WardrobeEntry(
        gender="female", energy=85,
        tags=("sport", "casual", "bold"),
        niches=("lifestyle", "fashion", "entertainment"),
        text="Pleated micro tennis mini skirt; fitted cropped graphic athletic tee, slightly oversized at the shoulder; bold chunky trainer with a thick sole; stacked thin gold chains — no logos visible",
    ),
    # ── MASCULINO ─────────────────────────────────────────────
    # Quieto / discreto (energia 0–20)
    WardrobeEntry(
        gender="male", energy=8,
        tags=("quiet", "minimalist", "natural"),
        niches=("fashion", "lifestyle", "travel", "any"),
        text="Washed linen overshirt, all buttons done, sleeves folded; loose straight-leg trousers; leather sandal; no accessories — no logos visible",
    ),
    WardrobeEntry(
        gender="male", energy=15,
        tags=("quiet", "minimalist", "clean"),
        niches=("fashion", "tech", "finance", "lifestyle"),
        text="Soft cotton crewneck sweater, slightly oversize; straight-leg wool-blend trousers, clean break at the ankle; clean leather low sneaker — no logos visible",
    ),
    WardrobeEntry(
        gender="male", energy=18,
        tags=("quiet", "structured", "minimalist"),
        niches=("tech", "finance", "fashion"),
        text="Fine-knit turtleneck; tailored straight-leg dark trousers; clean leather oxford; no accessories — no logos visible",
    ),
    # Discreto, com um elemento intencional (energia 20–40)
    WardrobeEntry(
        gender="male", energy=25,
        tags=("minimalist", "earthy", "casual"),
        niches=("lifestyle", "travel", "fashion"),
        text="Unstructured linen jacket worn open; fitted tee underneath; slim straight denim; leather loafer — no logos visible",
    ),
    WardrobeEntry(
        gender="male", energy=30,
        tags=("minimalist", "quiet", "preppy"),
        niches=("tech", "finance", "lifestyle"),
        text="Soft quarter-zip merino sweater; slim straight chino; clean low-profile sneaker; a simple stainless steel watch — no logos visible",
    ),
    WardrobeEntry(
        gender="male", energy=35,
        tags=("casual", "clean", "natural"),
        niches=("lifestyle", "travel", "any"),
        text="Relaxed-fit linen button-down, collar open, tucked at front only; straight-leg medium-wash denim; clean suede chukka — no logos visible",
    ),
    WardrobeEntry(
        gender="male", energy=38,
        tags=("preppy", "classic", "clean"),
        niches=("lifestyle", "fashion", "tech"),
        text="Relaxed Oxford shirt, collar open one button; slim straight-leg chino; clean leather low sneaker — a single thin woven bracelet — no logos visible",
    ),
    # Equilibrado (energia 40–60)
    WardrobeEntry(
        gender="male", energy=42,
        tags=("casual", "clean", "urban"),
        niches=("any",),
        text="Heavyweight cotton tee, crew neck slightly relaxed from washing; well-worn straight-leg dark denim; clean low-profile trainer — no logos visible",
    ),
    WardrobeEntry(
        gender="male", energy=45,
        tags=("casual", "dark", "urban"),
        niches=("lifestyle", "fashion", "entertainment"),
        text="Washed overshirt worn open over a fitted tee; slim straight dark jeans; clean leather low sneaker — no logos visible",
    ),
    WardrobeEntry(
        gender="male", energy=50,
        tags=("earthy", "casual", "natural"),
        niches=("lifestyle", "travel"),
        text="Soft relaxed-fit shirt, collar open two buttons, sleeves rolled; straight-leg chino; leather loafer — no logos visible",
    ),
    WardrobeEntry(
        gender="male", energy=55,
        tags=("minimalist", "casual", "clean"),
        niches=("lifestyle", "any"),
        text="Relaxed-fit linen shirt, collar open, slightly untucked; straight-leg denim; clean canvas slip-on; no accessories — no logos visible",
    ),
    # Dark & Moody (masculino) — cor escura explícita pelo mesmo motivo
    WardrobeEntry(
        gender="male", energy=55,
        tags=("dark", "moody", "casual"),
        niches=("lifestyle", "entertainment", "fashion"),
        text="Oversized ribbed crewneck sweater in black or deep charcoal, relaxed fit, hem sitting at the hip; dark straight-leg denim; clean black leather low sneaker; a worn thin leather cord bracelet — no logos visible",
    ),
    # Peças de personalidade (energia 60–80)
    WardrobeEntry(
        gender="male", energy=62,
        tags=("street", "urban", "casual"),
        niches=("fashion", "entertainment", "lifestyle"),
        text="Washed oversized heavyweight jersey crewneck, dropped shoulder seam; straight-leg dark denim, clean hem; clean low-profile leather sneakers, toe box faintly creased — no logos visible",
    ),
    WardrobeEntry(
        gender="male", energy=68,
        tags=("urban", "casual", "dark"),
        niches=("lifestyle", "travel", "entertainment"),
        text="Waxed canvas overshirt, collar slightly popped; fitted tee underneath; slim straight dark denim; clean leather low sneaker; a worn thin leather cord necklace — no logos visible",
    ),
    WardrobeEntry(
        gender="male", energy=72,
        tags=("street", "y2k", "urban"),
        niches=("fashion", "entertainment"),
        text="Wide-leg carpenter denim with subtle hardware at the thigh pocket; oversized washed tee, hem asymmetric; clean chunky trainer; a thin chain around the neck — no logos visible",
    ),
    WardrobeEntry(
        gender="male", energy=75,
        tags=("street", "casual", "urban"),
        niches=("lifestyle", "entertainment"),
        text="Oversized washed crewneck hoodie, drawstrings slightly uneven; wide-leg sweatpant, tapered at the ankle; clean low-top canvas shoe; a small hoop earring — no logos visible",
    ),
    WardrobeEntry(
        gender="male", energy=78,
        tags=("editorial", "structured", "dark"),
        niches=("fashion", "entertainment"),
        text="Long black overcoat in a heavy wool-blend, collar turned up; fitted black turtleneck; slim straight trouser in the same dark tone; clean leather chelsea boot in black; no accessories — no logos visible",
    ),
    # Ousado / expressivo (energia 80–100)
    WardrobeEntry(
        gender="male", energy=82,
        tags=("dark", "moody", "editorial"),
        niches=("fashion", "entertainment"),
        text="Heavy structured overcoat in all-black, draped open; fitted black turtleneck underneath; slim straight trouser in black; pointed-toe leather boot in black; one thin silver chain at the collarbone — no logos visible",
    ),
    WardrobeEntry(
        gender="male", energy=85,
        tags=("editorial", "bold", "glam"),
        niches=("fashion", "entertainment"),
        text="Wide-leg patterned suit trouser; matching blazer worn open, no shirt, a thin chain at the collarbone; clean pointed-toe leather oxford — no logos visible",
    ),
    WardrobeEntry(
        gender="male", energy=92,
        tags=("editorial", "bold", "colorful"),
        niches=("fashion", "entertainment"),
        text="Wide-leg cord trousers; fitted ribbed turtleneck; clean leather boot; a stack of thin silver rings — no logos visible",
    ),
    # Fitness masculino — contexto de academia
    WardrobeEntry(
        gender="male", energy=30,
        tags=("sport", "functional", "clean"),
        niches=("fitness", "lifestyle"),
        text="Fitted performance tee, slightly damp at the collar; tapered training shorts with small side-slit; clean training shoe, toe box faintly scuffed — no logos visible",
    ),
    WardrobeEntry(
        gender="male", energy=50,
        tags=("sport", "casual", "street"),
        niches=("fitness", "lifestyle"),
        text="Oversized washed hoodie, hem hitting mid-thigh; fitted training short; clean minimalist training shoe — no logos visible",
    ),
    WardrobeEntry(
        gender="male", energy=40,
        tags=("sport", "clean", "functional"),
        niches=("fitness", "lifestyle"),
        text="Tech-fabric sleeveless training vest, slight drape; compression training shorts; clean trainer — no logos visible",
    ),
    # Athleisure masculino — pegada esportiva para contextos de lifestyle
    WardrobeEntry(
        gender="male", energy=45,
        tags=("sport", "casual", "urban"),
        niches=("lifestyle", "any"),
        text="Straight-leg jogger in soft jersey, slightly tapered at the ankle; clean fitted crewneck; low-profile trainer; no accessories — no logos visible",
    ),
    WardrobeEntry(
        gender="male", energy=60,
        tags=("sport", "casual", "street"),
        niches=("lifestyle", "entertainment", "any"),
        text="Athletic shorts, mid-thigh, slight technical drape; clean fitted tee; clean mid-top trainer; a thin cord bracelet — no logos visible",
    ),
    # Coastal (masculino)
    WardrobeEntry(
        gender="male", energy=20,
        tags=("coastal", "natural", "quiet"),
        niches=("lifestyle", "travel", "any"),
        text="Loose straight-leg linen trousers, slightly creased from wear; simple fitted cotton tee; leather sandal; no accessories — no logos visible",
    ),
    WardrobeEntry(
        gender="male", energy=35,
        tags=("coastal", "casual", "natural"),
        niches=("lifestyle", "travel", "any"),
        text="Linen short-sleeve shirt, collar open three buttons, slightly oversized, half-tucked; straight-leg chino shorts, hem at mid-thigh; leather sandal; a thin cord necklace — no logos visible",
    ),
    WardrobeEntry(
        gender="male", energy=52,
        tags=("coastal", "casual", "earthy"),
        niches=("lifestyle", "travel"),
        text="Worn canvas shorts, slightly baggy, faded at the hem; fitted cotton tee, slightly faded; flat leather sandal; a thin leather wristband — no logos visible",
    ),
    WardrobeEntry(
        gender="male", energy=65,
        tags=("coastal", "natural", "casual"),
        niches=("lifestyle", "travel", "fashion"),
        text="Loose linen trousers, cropped slightly above the ankle; linen overshirt, collar open, sleeves rolled; woven sandal; a small pendant on a cord — no logos visible",
    ),
    # ── OLD MONEY / LUXO DISCRETO ─────────────────────────────
    WardrobeEntry(
        gender="male", energy=15,
        tags=("old-money", "classic", "quiet", "polished"),
        niches=("lifestyle", "fashion", "travel", "any"),
        text="Unstructured linen blazer, lapels slightly soft from wear, over an Oxford shirt with three buttons casually open; slim straight-leg chino with a faint crease; penny loafer in full-grain leather, slightly worn at the heel; a chunky gold signet ring on the left pinky; a simple leather-strap watch on the wrist — no logos visible",
    ),
    WardrobeEntry(
        gender="male", energy=18,
        tags=("old-money", "natural", "casual"),
        niches=("lifestyle", "travel", "any"),
        text="Fine-knit cotton polo, very relaxed fit, lightly sun-faded; well-worn straight-leg denim with a clean hem; leather tennis sneaker, slightly soft at the toe; a gold signet ring on the left pinky; a steel watch on a leather strap — no logos visible",
    ),
    WardrobeEntry(
        gender="male", energy=22,
        tags=("old-money", "preppy", "polished"),
        niches=("lifestyle", "fashion"),
        text="Linen blazer worn open over a faded linen shirt, collar three buttons open, sleeves pushed; straight-leg chino; leather loafer, worn smooth at the sole edge; a thin gold watch with a worn leather strap — no logos visible",
    ),
    WardrobeEntry(
        gender="male", energy=28,
        tags=("old-money", "casual", "quiet"),
        niches=("lifestyle", "travel"),
        text="Soft washed Oxford shirt, oversized and fully untucked, buttons open to mid-chest; well-worn straight-leg chino; faded canvas boat shoe; a gold signet ring; a simple leather-strap watch — no logos visible",
    ),
    WardrobeEntry(
        gender="male", energy=20,
        tags=("old-money", "structured", "quiet"),
        niches=("fashion", "lifestyle"),
        text="Tailored linen trousers with a clean crease; a linen shirt, collar open, untucked; leather loafer with a light scuff at the toe; a thin gold chain at the collarbone; a slim leather-strap watch — no logos visible",
    ),
    WardrobeEntry(
        gender="male", energy=35,
        tags=("old-money", "classic", "relaxed"),
        niches=("lifestyle", "any"),
        text="Soft cable-knit crewneck sweater, slightly oversize, hem relaxed over the waistband; slim straight-leg chino; clean leather low sneaker; a gold signet ring; no other jewelry — no logos visible",
    ),
    WardrobeEntry(
        gender="female", energy=12,
        tags=("old-money", "classic", "quiet", "polished"),
        niches=("lifestyle", "fashion", "any"),
        text="Perfectly tailored wide-leg linen trousers; a fine-knit short-sleeve polo, tucked neatly; a single strand of small pearls at the collarbone; leather loafer, buffed; a simple gold bracelet — no logos visible",
    ),
    WardrobeEntry(
        gender="female", energy=18,
        tags=("old-money", "natural", "casual"),
        niches=("lifestyle", "travel", "fashion"),
        text="Oversize washed Oxford shirt, three buttons open, half-tucked into straight-leg linen trousers; gold signet ring; worn-in leather sandal with a minimal strap — no logos visible",
    ),
    WardrobeEntry(
        gender="female", energy=22,
        tags=("old-money", "preppy", "polished"),
        niches=("lifestyle", "fashion"),
        text="Slim-fit blazer, single button, worn over a striped poplin shirt, collar open; straight-leg chino, clean hem; flat leather loafer; a thin gold chain and small pearl stud earrings — no logos visible",
    ),
    WardrobeEntry(
        gender="female", energy=28,
        tags=("old-money", "classic", "quiet"),
        niches=("lifestyle", "any"),
        text="Soft cashmere crewneck sweater, slightly oversize; straight-leg linen trousers; clean leather sneaker; a thin gold chain; a small leather tote held at the crook of the arm — no logos visible",
    ),
    # ── CLEAN GIRL — entradas dedicadas (distintas do minimalista genérico) ─
    # Pense Hailey Bieber, Sofia Richie: polido, sem esforço, ouro, malha canelada
    WardrobeEntry(
        gender="female", energy=22,
        tags=("clean", "natural", "quiet"),
        niches=("lifestyle", "fashion", "any"),
        text="Fitted ribbed tank top tucked neatly into a high-waist tailored midi skirt in warm ecru or oat; flat leather sandal, thin strap; small gold huggie hoop earrings; no other jewelry — no logos visible",
    ),
    WardrobeEntry(
        gender="female", energy=38,
        tags=("clean", "natural", "casual"),
        niches=("lifestyle", "fashion", "any"),
        text="Matching ribbed set: fitted long-sleeve crop top and wide-leg ribbed lounge trousers in warm oat or cream; clean leather low sneaker or flat; small gold huggie hoops; a single thin delicate gold chain — no logos visible",
    ),
    WardrobeEntry(
        gender="female", energy=55,
        tags=("clean", "natural", "casual"),
        niches=("lifestyle", "fashion", "any"),
        text="Fitted white or cream ribbed tank tucked into wide-leg linen or satin-finish trousers in camel or warm oat; strappy leather flat sandal; small gold hoop earrings; a layered delicate gold chain necklace — no logos visible",
    ),
    WardrobeEntry(
        gender="female", energy=68,
        tags=("clean", "natural", "polished"),
        niches=("lifestyle", "fashion", "any"),
        text="Fitted satin-finish or ribbed cami midi dress in warm ecru or nude; strappy leather flat sandal; gold hoop earrings; stacked thin delicate gold chains and a simple gold bracelet — no logos visible",
    ),
    # ── STREETWEAR — energia baixa e média (não existia nada abaixo de 62) ─
    WardrobeEntry(
        gender="female", energy=22,
        tags=("street", "casual", "clean"),
        niches=("lifestyle", "fashion", "any"),
        text="Oversized washed heavyweight tee, crew neck, hem slightly cropped by tucking once at the front; high-waist straight-leg dark denim; clean white low-top canvas sneaker, slightly broken in — no logos visible",
    ),
    WardrobeEntry(
        gender="female", energy=38,
        tags=("street", "casual", "urban"),
        niches=("lifestyle", "fashion", "any"),
        text="Relaxed straight-leg dark denim; fitted ribbed tank, thin straps, half-tucked; clean low-profile leather sneaker; a small thin chain at the collarbone — no logos visible",
    ),
    WardrobeEntry(
        gender="female", energy=52,
        tags=("street", "urban", "casual"),
        niches=("lifestyle", "fashion", "entertainment", "any"),
        text="Wide-leg dark denim, clean hem; fitted graphic tee tucked loosely at one side; clean chunky low-top trainer; a thin chain layered over the tee — no logos visible",
    ),
    WardrobeEntry(
        gender="male", energy=22,
        tags=("street", "casual", "clean"),
        niches=("lifestyle", "fashion", "any"),
        text="Straight-leg dark denim, clean hem; heavyweight cotton tee, crew neck, slightly oversized; clean white low-top canvas sneaker — no logos visible",
    ),
    WardrobeEntry(
        gender="male", energy=38,
        tags=("street", "urban", "casual"),
        niches=("lifestyle", "fashion", "entertainment"),
        text="Straight-leg dark denim; loose overshirt worn fully open over a fitted tee; clean low-profile leather sneaker — no logos visible",
    ),
    WardrobeEntry(
        gender="male", energy=52,
        tags=("street", "casual", "urban"),
        niches=("lifestyle", "fashion", "entertainment"),
        text="Wide-leg dark denim, clean hem; oversized washed crewneck, dropped shoulder; clean low-profile trainer; a thin cord bracelet — no logos visible",
    ),
    # ── BOHEMIAN — masculino (não havia nenhuma entrada) ──────
    WardrobeEntry(
        gender="male", energy=28,
        tags=("bohemian", "earthy", "natural"),
        niches=("lifestyle", "travel", "any"),
        text="Loose linen shirt, collar open three buttons, slightly oversized, untucked; relaxed straight-leg linen trousers; flat leather sandal; a single thin cord or wooden-bead bracelet — no logos visible",
    ),
    WardrobeEntry(
        gender="male", energy=48,
        tags=("bohemian", "natural", "earthy"),
        niches=("lifestyle", "travel"),
        text="Loose woven cotton shirt, slightly sun-faded, collar open; relaxed wide-leg linen trousers, cropped above the ankle; flat leather sandal; a layered cord and natural-bead necklace — no logos visible",
    ),
    WardrobeEntry(
        gender="male", energy=65,
        tags=("bohemian", "earthy", "casual"),
        niches=("lifestyle", "travel", "fashion"),
        text="Linen overshirt worn open, fabric slightly crumpled; wide-leg cotton drawstring trousers; leather sandal with a woven strap; layered thin cord necklaces, worn and natural — no logos visible",
    ),
    # ── GLAM — masculino, energia baixa e média (só havia 1 entrada, na energia 85) ─
    WardrobeEntry(
        gender="male", energy=32,
        tags=("glam", "polished", "structured"),
        niches=("fashion", "lifestyle", "entertainment"),
        text="Tailored straight-leg trousers in a subtle satin-finish fabric; fitted fine-knit turtleneck; clean leather chelsea boot; a single thin gold chain — no logos visible",
    ),
    WardrobeEntry(
        gender="male", energy=55,
        tags=("glam", "evening", "polished"),
        niches=("fashion", "entertainment", "lifestyle"),
        text="Fitted ribbed turtleneck; slim tailored trousers with a faint sheen at the fabric; clean pointed-toe leather oxford; a thin gold bracelet — no logos visible",
    ),
    WardrobeEntry(
        gender="male", energy=68,
        tags=("glam", "bold", "evening"),
        niches=("fashion", "entertainment"),
        text="Satin-finish button-up shirt, collar open two buttons, slightly relaxed; well-tailored slim trousers; clean pointed-toe leather boot; a gold signet ring and thin chain at the collarbone — no logos visible",
    ),
    # ── Y2K — energia baixa e média (era tudo 62+ no feminino, 72 no masculino) ─
    WardrobeEntry(
        gender="female", energy=28,
        tags=("y2k", "playful", "casual"),
        niches=("lifestyle", "fashion", "entertainment"),
        text="Low-rise straight-leg denim, slightly flared at the hem; fitted ribbed baby tee, slightly cropped, scoop neck; flat strappy sandal; a thin chain bracelet — no logos visible",
    ),
    WardrobeEntry(
        gender="female", energy=48,
        tags=("y2k", "casual", "clean"),
        niches=("lifestyle", "fashion", "any"),
        text="Low-rise bootcut denim in a clean medium wash; cropped fitted polo, collar slightly open; clean white low-top sneaker; a delicate thin choker-style necklace — no logos visible",
    ),
    WardrobeEntry(
        gender="male", energy=32,
        tags=("y2k", "casual", "urban"),
        niches=("lifestyle", "fashion", "entertainment"),
        text="Wide-leg straight denim, clean medium wash; fitted tee with a subtle graphic, crew neck; clean low-top sneaker; a thin chain necklace — no logos visible",
    ),
    WardrobeEntry(
        gender="male", energy=52,
        tags=("y2k", "urban", "casual"),
        niches=("lifestyle", "entertainment"),
        text="Baggy low-rise straight-leg denim, slightly faded at the knees; zip-up track jacket in a tonal colour, collar slightly popped, worn open over a fitted tee; clean low-top skate-style sneaker; a thin silver chain at the collarbone — no logos visible",
    ),
    # ── EDITORIAL — masculino, faixa média (só havia 78, 82, 85) ─
    WardrobeEntry(
        gender="male", energy=42,
        tags=("editorial", "structured", "clean"),
        niches=("fashion", "lifestyle"),
        text="Collarless structured cotton jacket, slightly boxy, worn fully buttoned as the only top layer — no shirt visible; clean straight-leg tailored trousers in a contrasting neutral; square-toe leather oxford; no accessories — no logos visible",
    ),
    WardrobeEntry(
        gender="male", energy=60,
        tags=("editorial", "structured", "dark"),
        niches=("fashion", "entertainment"),
        text="Structured overshirt in heavy black fabric worn fully closed as a jacket, strong shoulder line; fitted black turtleneck underneath; slim tailored trousers in dark charcoal; clean black leather chelsea boot — no logos visible",
    ),
    # ── DARK & MOODY — feminino, energia baixa (não havia nada abaixo de 60) ─
    WardrobeEntry(
        gender="female", energy=22,
        tags=("dark", "minimalist", "quiet"),
        niches=("fashion", "lifestyle", "entertainment"),
        text="Fine-knit fitted black turtleneck, long sleeve, slightly thin at the cuffs; slim straight-leg black trousers, clean hem; clean black leather flat; no jewelry — no logos visible",
    ),
    WardrobeEntry(
        gender="female", energy=38,
        tags=("dark", "moody", "casual"),
        niches=("fashion", "lifestyle", "entertainment"),
        text="Fitted ribbed long-sleeve crew-neck top in black or deep charcoal; slim straight dark denim or black trousers; clean leather ankle boot in black, low block heel; a single thin silver chain necklace — no logos visible",
    ),
    # ── PREPPY — expandido (eram 2F / 2M) ─────────────────────
    WardrobeEntry(
        gender="female", energy=25,
        tags=("preppy", "classic", "clean"),
        niches=("lifestyle", "fashion", "any"),
        text="Fine-knit crewneck sweater in a clean neutral, slightly fitted; slim straight-leg chino; leather loafer; a simple thin gold bracelet — no logos visible",
    ),
    WardrobeEntry(
        gender="female", energy=55,
        tags=("preppy", "classic", "polished"),
        niches=("lifestyle", "fashion"),
        text="Striped poplin shirt, collar open, tucked into high-waist straight-leg chino; leather loafer; small pearl stud earrings; a simple gold bracelet — no logos visible",
    ),
    WardrobeEntry(
        gender="female", energy=68,
        tags=("preppy", "editorial", "polished"),
        niches=("lifestyle", "fashion"),
        text="Slim blazer in a classic check, single button, lapels neat; fitted crew-neck tee underneath; straight-leg chino; penny loafer; a thin gold chain — no logos visible",
    ),
    WardrobeEntry(
        gender="male", energy=22,
        tags=("preppy", "classic", "quiet"),
        niches=("lifestyle", "fashion", "any"),
        text="Soft cotton crewneck sweater, slightly relaxed, in a clean neutral; well-worn slim straight-leg chino; leather loafer, buffed slightly; a thin stainless watch — no logos visible",
    ),
    WardrobeEntry(
        gender="male", energy=55,
        tags=("preppy", "classic", "clean"),
        niches=("lifestyle", "fashion"),
        text="Oxford button-down, collar open, slightly tucked at the front; straight-leg chino, clean break; leather loafer; a simple woven cord bracelet — no logos visible",
    ),
    WardrobeEntry(
        gender="male", energy=68,
        tags=("preppy", "polished", "editorial"),
        niches=("lifestyle", "fashion"),
        text="Linen blazer with a natural rumple, worn open; fitted Oxford shirt underneath, collar two buttons open; slim chino; leather loafer; a gold signet ring — no logos visible",
    ),
)


# Tag que define cada vibe — usada para hard-filter da biblioteca ANTES da
# pontuação. É isto que garante que a vibe escolhida sempre ganha da
# proximidade de energia. A personalidade continua controlando COMO a roupa é
# usada (`get_styling_note`), não SE a vibe é respeitada.
VIBE_PRIMARY_TAG: dict[str, str] = {
    "Minimalist": "minimalist",
    "Editorial": "editorial",
    "Streetwear": "street",
    "Bohemian": "bohemian",
    "Glam": "glam",
    "Sporty": "sport",
    "Y2K": "y2k",
    "Dark & Moody": "dark",
    "Clean Girl": "clean",
    "Cottagecore": "cottagecore",
    "Tech Bro": "minimalist",
    "Preppy": "preppy",
    "Old Money": "old-money",
    "Coastal": "coastal",
}


# Vibe → conjunto de tags (não é 1:1 com um look — elas filtram a biblioteca)
VIBE_TAG_MAP: dict[str, tuple[str, ...]] = {
    "Minimalist": ("minimalist", "quiet", "clean"),
    "Editorial": ("editorial", "structured", "bold"),
    "Streetwear": ("street", "urban", "casual"),
    "Bohemian": ("bohemian", "earthy", "natural"),
    "Glam": ("glam", "evening", "bold"),
    "Sporty": ("sport", "functional", "casual"),
    "Y2K": ("y2k", "playful", "casual"),
    "Dark & Moody": ("dark", "moody", "structured"),
    "Clean Girl": ("clean", "natural", "casual"),
    "Cottagecore": ("cottagecore", "natural", "earthy"),
    "Tech Bro": ("structured", "quiet", "minimalist"),
    "Preppy": ("preppy", "classic", "polished"),
    "Old Money": ("old-money", "classic", "polished", "quiet"),
    "Coastal": ("coastal", "natural", "casual"),
}


# Vibe → dica de paleta de cores injetada na seção Wardrobe
VIBE_PALETTE_MAP: dict[str, str] = {
    "Minimalist": "Palette: muted neutrals — off-white, ecru, stone, warm grey, oat. No strong saturated colors.",
    "Editorial": "Palette: bold monochrome or one statement color — all-black, deep charcoal, pure white, or a single saturated hue.",
    "Streetwear": "Palette: washed-out neutrals — faded black, washed grey, dirty white, faded indigo denim.",
    "Bohemian": "Palette: warm earthy tones — terracotta, rust, warm ochre, tobacco, deep olive, sun-faded warm neutrals.",
    "Glam": "Palette: rich evening tones — deep burgundy, warm ivory, champagne, or a bold jewel-tone statement piece.",
    "Sporty": "Palette: clean athletic colors — sage green, lavender, warm rust, classic black-and-white, or a pastel matching set.",
    "Y2K": "Palette: 2000s nostalgia — baby blue, lilac, pale pink, warm denim wash, silver hardware, white.",
    "Dark & Moody": "Palette: deep darks only — all-black, deep charcoal, dark forest, inky navy. No light or pastel tones.",
    "Clean Girl": "Palette: clean warm neutrals — warm white, oat, ecru, nude blush. Nothing saturated or dark.",
    "Cottagecore": "Palette: soft botanical — dusty rose, sage, warm cream, muted floral prints, soft lavender.",
    "Tech Bro": "Palette: monochrome and minimal — all-black, deep charcoal, dark navy, all-grey. No bright or warm tones.",
    "Preppy": "Palette: classic collegiate — navy and white, hunter green, camel and cream, burgundy.",
    "Old Money": "Palette: quiet luxury — camel, cream, stone, pale blue, navy, warm tan. Nothing loud or trend-driven.",
    "Coastal": "Palette: sun-bleached coastal — off-white linen, warm sand, ocean-washed denim, natural rope and shell tones.",
}


def get_vibe_tags(vibe_words: Sequence[str] | None) -> list[str]:
    """Achata as vibes escolhidas em uma lista de tags (com repetições)."""
    return [tag for v in (vibe_words or []) for tag in VIBE_TAG_MAP.get(v, ())]


# ── Motor de arquétipos de backstory ──────────────────────────────────────
# Cada entrada: test (regex), tags (empurrão no guarda-roupa),
# scene_niche (chave do pool de cenas), build_hint (tipo físico inferido —
# usado se o usuário deixou o campo em branco), physical_detail (marcador
# visível — óculos etc., somado ao sujeito), locked_scene (cena específica
# para a variação 3; None = sorteia do pool do scene_niche)


@dataclass(frozen=True, slots=True)
class Archetype:
    """Um arquétipo de profissão detectado por regex na backstory."""

    test: re.Pattern[str]
    tags: tuple[str, ...]
    scene_niche: str | None = None
    build_hint: str | None = None
    physical_detail: str | None = None
    locked_scene: str | None = None


BACKSTORY_ARCHETYPES: tuple[Archetype, ...] = (
    # ── FITNESS ───────────────────────────────────────────────
    Archetype(
        test=re.compile(r"yoga.?instructor|yoga.?teacher|yoga.?coach"),
        tags=("sport", "natural", "earthy", "bohemian"),
        scene_niche="fitness",
        build_hint="lean, flexible, long-limbed",
        locked_scene="yoga studio, pale wood floor, floor-to-ceiling mirror wall, soft diffused morning light through large windows",
    ),
    Archetype(
        test=re.compile(r"pilates.?instructor|pilates.?teacher|reformer"),
        tags=("sport", "clean", "natural"),
        scene_niche="fitness",
        build_hint="lean, toned, precise upright posture",
        locked_scene="pilates studio, reformer machines visible soft in background, clean bright space with natural light",
    ),
    Archetype(
        test=re.compile(r"crossfit|hiit.?coach|functional.?fitness"),
        tags=("sport", "functional", "casual"),
        scene_niche="fitness",
        build_hint="muscular, functional build",
        locked_scene="crossfit box, pull-up rigs and bumper plates visible soft behind, industrial ceiling and rubber flooring",
    ),
    Archetype(
        test=re.compile(r"personal.?trainer|fitness.?coach|strength.?coach|gym.?owner|gym.?instructor"),
        tags=("sport", "functional", "casual"),
        scene_niche="fitness",
        build_hint="athletic, visibly fit",
        locked_scene="minimalist gym interior, rubber floor, weight racks and barbells visible soft in background, large windows letting in hard morning side-light from camera-left",
    ),
    Archetype(
        test=re.compile(r"nutritionist|dietitian|sports.?nutritionist"),
        tags=("sport", "earthy", "natural"),
        scene_niche="fitness",
    ),
    Archetype(
        test=re.compile(r"marathon|ultra.?runner|long.?distance.?runner"),
        tags=("sport", "earthy", "functional"),
        scene_niche="fitness",
        build_hint="lean endurance build",
    ),
    Archetype(
        test=re.compile(r"\brunner\b|running.?coach|track.?athlete"),
        tags=("sport", "functional", "casual"),
        scene_niche="fitness",
        build_hint="lean runner's build",
    ),
    Archetype(
        test=re.compile(r"cyclist|road.?bike|mountain.?bik"),
        tags=("sport", "functional"),
        scene_niche="fitness",
        build_hint="lean, powerful legs",
    ),
    Archetype(
        test=re.compile(r"\bswimmer\b|swimming|open.?water"),
        tags=("sport", "coastal", "natural"),
        scene_niche="fitness",
        build_hint="broad-shouldered, lean swimmer's build",
    ),
    Archetype(
        test=re.compile(r"\bsurfer\b|surfing|surf.?instructor"),
        tags=("coastal", "natural", "casual", "earthy"),
        scene_niche="fitness",
        build_hint="lean, sun-bronzed",
    ),
    Archetype(
        test=re.compile(r"dancer|dance.?teacher|choreographer|ballet|contemporary.?dance"),
        tags=("editorial", "bold", "sport"),
        scene_niche="entertainment",
        build_hint="lean, long-limbed, precise upright posture",
        locked_scene="dance studio, sprung wood floor, floor-to-ceiling mirror, barre visible in background",
    ),
    Archetype(
        test=re.compile(r"martial.?art|mma|boxing|kickboxing|judo|jiu.?jitsu|muay.?thai"),
        tags=("sport", "dark", "functional"),
        scene_niche="fitness",
        build_hint="lean, compact, coiled",
    ),
    Archetype(
        test=re.compile(r"gymnast|gymnastics"),
        tags=("sport", "functional", "editorial"),
        scene_niche="fitness",
        build_hint="compact, powerfully built",
    ),
    Archetype(
        test=re.compile(r"meditation.?teacher|mindfulness.?coach|breathwork"),
        tags=("earthy", "natural", "bohemian"),
        scene_niche="fitness",
    ),
    Archetype(
        test=re.compile(r"life.?coach|wellness.?coach|holistic.?health|health.?coach"),
        tags=("natural", "clean", "earthy"),
        scene_niche="lifestyle",
    ),
    Archetype(
        test=re.compile(r"professional.?athlete|pro.?athlete|olympic|national.?team"),
        tags=("sport", "functional"),
        scene_niche="fitness",
        build_hint="peak athletic build",
    ),
    Archetype(
        test=re.compile(r"\bcoach\b|sports.?coach|athletic.?director"),
        tags=("sport", "casual", "functional"),
        scene_niche="fitness",
        build_hint="fit, athletic",
    ),
    Archetype(
        test=re.compile(r"\bgym\b|weightlifting|powerlifting|bodybuilding"),
        tags=("sport", "functional", "casual"),
        scene_niche="fitness",
        build_hint="visibly muscular, powerful frame",
        locked_scene="gym interior, squat rack and plates visible soft behind, rubber floor, industrial ceiling with high strip lighting",
    ),
    Archetype(
        test=re.compile(r"tennis|padel"),
        tags=("preppy", "sport", "clean"),
        scene_niche="fitness",
    ),
    Archetype(
        test=re.compile(r"\bgolfer\b|\bgolf\b"),
        tags=("old-money", "preppy", "sport"),
        scene_niche="travel",
    ),
    Archetype(
        test=re.compile(r"basketball|nba\b"),
        tags=("street", "sport", "casual"),
        scene_niche="fitness",
        build_hint="tall, athletic",
    ),
    Archetype(
        test=re.compile(r"football|soccer|footballer|pitch"),
        tags=("sport", "casual", "street"),
        scene_niche="fitness",
        build_hint="lean, athletic",
    ),
    Archetype(
        test=re.compile(r"\bskater\b|skateboarding"),
        tags=("street", "y2k", "casual"),
        scene_niche="entertainment",
    ),
    Archetype(
        test=re.compile(r"snowboarder|snowboarding|skiing|\bskier\b"),
        tags=("casual", "earthy", "sport"),
        scene_niche="travel",
    ),
    Archetype(
        test=re.compile(r"rock.?climber|climbing|bouldering"),
        tags=("earthy", "functional", "casual"),
        scene_niche="travel",
        build_hint="lean, wiry and strong",
    ),
    Archetype(
        test=re.compile(r"\bhiker\b|hiking|trekker"),
        tags=("earthy", "functional", "natural"),
        scene_niche="travel",
    ),
    # ── COMIDA & HOSPITALIDADE ────────────────────────────────
    Archetype(
        test=re.compile(r"executive.?chef|head.?chef|michelin|fine.?dining"),
        tags=("editorial", "structured", "polished"),
        scene_niche="lifestyle",
        locked_scene="outside a restaurant entrance, warm kitchen light spilling through the open door behind, city street at dusk",
    ),
    Archetype(
        test=re.compile(r"pastry.?chef|patisserie"),
        tags=("cottagecore", "earthy", "natural"),
        scene_niche="lifestyle",
        locked_scene="bakery counter or café window, warm morning light, pastries visible soft in background",
    ),
    Archetype(
        test=re.compile(r"\bbaker\b|baking|sourdough"),
        tags=("cottagecore", "earthy", "natural"),
        scene_niche="lifestyle",
        locked_scene="bakery counter or café window, warm morning light, bread and pastries soft in background",
    ),
    Archetype(
        test=re.compile(r"\bchef\b|culinary|kitchen|cook\b"),
        tags=("casual", "earthy"),
        scene_niche="lifestyle",
        locked_scene="outside a restaurant or street-food stall, warm ambient evening light, kitchen noise implied behind",
    ),
    Archetype(
        test=re.compile(r"barista|coffee.?roaster|café.?owner"),
        tags=("casual", "minimalist", "clean"),
        scene_niche="lifestyle",
        locked_scene="beside a café counter, espresso machine gleaming soft behind, warm amber overhead light",
    ),
    Archetype(
        test=re.compile(r"bartender|mixologist|bar.?owner"),
        tags=("dark", "casual", "glam"),
        scene_niche="entertainment",
        locked_scene="bar or venue interior, warm practicals above, moody ambient light, bottles soft in background",
    ),
    Archetype(
        test=re.compile(r"sommelier|\bwine\b|winery|vineyard"),
        tags=("old-money", "polished", "earthy"),
        scene_niche="travel",
    ),
    Archetype(
        test=re.compile(r"food.?blogger|food.?photographer|food.?stylist"),
        tags=("casual", "earthy", "lifestyle"),
        scene_niche="lifestyle",
    ),
    Archetype(
        test=re.compile(r"restaurant.?owner|restaurateur"),
        tags=("polished", "editorial", "casual"),
        scene_niche="lifestyle",
        locked_scene="inside or outside their restaurant, warm ambient lighting, tables soft in background",
    ),
    # ── MODA & BELEZA ─────────────────────────────────────────
    Archetype(
        test=re.compile(r"\bmodel\b|fashion.?model|runway|catwalk"),
        tags=("editorial", "structured", "polished"),
        scene_niche="fashion",
        build_hint="tall, lean, long-limbed",
    ),
    Archetype(
        test=re.compile(r"fashion.?designer|clothing.?designer"),
        tags=("editorial", "bold", "structured"),
        scene_niche="fashion",
    ),
    Archetype(
        test=re.compile(r"\bstylist\b|fashion.?stylist|wardrobe.?stylist|personal.?stylist"),
        tags=("editorial", "minimalist", "structured"),
        scene_niche="fashion",
    ),
    Archetype(
        test=re.compile(r"fashion.?blogger|fashion.?influencer"),
        tags=("editorial", "bold"),
        scene_niche="fashion",
    ),
    Archetype(
        test=re.compile(r"makeup.?artist|mua\b|beauty.?artist"),
        tags=("glam", "editorial", "clean"),
        scene_niche="beauty",
    ),
    Archetype(
        test=re.compile(r"hairdresser|hair.?stylist|hair.?colorist"),
        tags=("casual", "editorial", "clean"),
        scene_niche="beauty",
        locked_scene="salon interior, styling chairs visible soft in background, bright salon lighting",
    ),
    Archetype(
        test=re.compile(r"nail.?artist|nail.?technician"),
        tags=("playful", "bold", "clean"),
        scene_niche="beauty",
    ),
    Archetype(
        test=re.compile(r"beauty.?blogger|beauty.?influencer"),
        tags=("glam", "clean", "natural"),
        scene_niche="beauty",
    ),
    Archetype(
        test=re.compile(r"esthetician|skincare.?specialist|facialist"),
        tags=("clean", "natural", "minimalist"),
        scene_niche="beauty",
    ),
    Archetype(
        test=re.compile(r"creative.?director|art.?director"),
        tags=("editorial", "structured", "bold"),
        scene_niche="fashion",
    ),
    # ── TECH & NEGÓCIOS ───────────────────────────────────────
    Archetype(
        test=re.compile(r"software.?engineer|developer|programmer|coder|full.?stack|backend|frontend"),
        tags=("minimalist", "quiet", "clean"),
        scene_niche="tech",
        locked_scene="minimal home desk or co-working space, large monitor soft in background, window light from one side",
    ),
    Archetype(
        test=re.compile(r"startup.?founder|tech.?founder"),
        tags=("minimalist", "structured", "editorial"),
        scene_niche="tech",
        locked_scene="modern co-working space, city view through full-height glass behind",
    ),
    Archetype(
        test=re.compile(r"\bceo\b|\bcoo\b|\bcto\b|c-suite"),
        tags=("old-money", "structured", "polished"),
        scene_niche="tech",
        locked_scene="city office, floor-to-ceiling glass windows, urban skyline visible soft behind",
    ),
    Archetype(
        test=re.compile(r"entrepreneur|business.?owner|self.?employed|founder"),
        tags=("minimalist", "casual", "structured"),
        scene_niche="tech",
    ),
    Archetype(
        test=re.compile(r"ux.?designer|product.?designer|ui.?designer"),
        tags=("minimalist", "clean", "editorial"),
        scene_niche="tech",
    ),
    Archetype(
        test=re.compile(r"product.?manager|\bpm\b"),
        tags=("minimalist", "clean", "preppy"),
        scene_niche="tech",
    ),
    Archetype(
        test=re.compile(r"crypto|blockchain|web3|\bnft\b|\bdefi\b"),
        tags=("street", "y2k", "casual"),
        scene_niche="tech",
    ),
    Archetype(
        test=re.compile(r"real.?estate|property.?developer|realtor"),
        tags=("polished", "preppy", "old-money"),
        scene_niche="lifestyle",
    ),
    Archetype(
        test=re.compile(r"financial.?advisor|wealth.?manager|portfolio.?manager"),
        tags=("old-money", "polished", "preppy"),
        scene_niche="tech",
    ),
    Archetype(
        test=re.compile(r"\bbanker\b|investment.?bank|private.?equity|hedge.?fund"),
        tags=("old-money", "structured", "polished"),
        scene_niche="tech",
    ),
    Archetype(
        test=re.compile(r"day.?trader|\btrader\b|stock.?market"),
        tags=("minimalist", "casual"),
        scene_niche="tech",
    ),
    Archetype(
        test=re.compile(r"\blawyer\b|attorney|solicitor|barrister"),
        tags=("old-money", "structured", "editorial"),
        scene_niche="lifestyle",
        locked_scene="steps of a courthouse or law office building exterior, clean stone steps, city behind",
    ),
    # ── CRIATIVOS ─────────────────────────────────────────────
    Archetype(
        test=re.compile(r"\bphotographer\b|photography"),
        tags=("editorial", "minimalist", "casual"),
        scene_niche="travel",
    ),
    Archetype(
        test=re.compile(r"videographer|cinematographer"),
        tags=("editorial", "casual", "dark"),
        scene_niche="entertainment",
    ),
    Archetype(
        test=re.compile(r"filmmaker|film.?director|\bdirector\b"),
        tags=("editorial", "dark", "structured"),
        scene_niche="entertainment",
    ),
    Archetype(
        test=re.compile(r"graphic.?designer|illustrator"),
        tags=("editorial", "minimalist", "casual"),
        scene_niche="lifestyle",
    ),
    Archetype(
        test=re.compile(r"interior.?designer|interior.?decorator"),
        tags=("minimalist", "editorial", "old-money"),
        scene_niche="lifestyle",
    ),
    Archetype(
        test=re.compile(r"\bartist\b|painter|sculptor|fine.?art"),
        tags=("bohemian", "editorial", "earthy"),
        scene_niche="lifestyle",
        locked_scene="artist's studio, canvases or artworks visible soft behind, north-facing window light",
    ),
    Archetype(
        test=re.compile(r"\bmusician\b|\bsinger\b|vocalist|recording.?artist"),
        tags=("dark", "bohemian", "editorial"),
        scene_niche="entertainment",
        locked_scene="recording studio corner, microphone stand or mixing board soft behind, warm practical lighting",
    ),
    Archetype(
        test=re.compile(r"\bdj\b|music.?producer|producer\b"),
        tags=("street", "dark", "casual"),
        scene_niche="entertainment",
        locked_scene="behind a DJ booth or in a studio, equipment visible soft behind, moody low ambient light",
    ),
    Archetype(
        test=re.compile(r"\brapper\b|hip.?hop|\bmc\b"),
        tags=("street", "dark", "bold"),
        scene_niche="entertainment",
    ),
    Archetype(
        test=re.compile(r"\bactor\b|\bactress\b|performer|theatre"),
        tags=("editorial", "glam", "casual"),
        scene_niche="entertainment",
    ),
    Archetype(
        test=re.compile(r"\bcomedian\b|stand.?up|comedy.?writer"),
        tags=("casual", "street", "y2k"),
        scene_niche="entertainment",
    ),
    Archetype(
        test=re.compile(r"\bwriter\b|\bauthor\b|novelist|screenwriter"),
        tags=("quiet", "minimalist", "earthy"),
        scene_niche="lifestyle",
        locked_scene="café corner, open notebook or laptop soft on table, afternoon window light",
    ),
    Archetype(
        test=re.compile(r"podcaster|podcast.?host"),
        tags=("casual", "minimalist"),
        scene_niche="tech",
        locked_scene="podcast desk setup, microphone visible in foreground or soft behind, acoustic panels or bookshelf in background",
    ),
    Archetype(
        test=re.compile(r"journalist|reporter|correspondent"),
        tags=("casual", "minimalist", "preppy"),
        scene_niche="lifestyle",
    ),
    Archetype(
        test=re.compile(r"youtuber|youtube.?creator|\bvlogg|\bvlog\b"),
        tags=("casual", "y2k", "street"),
        scene_niche="lifestyle",
    ),
    Archetype(
        test=re.compile(r"tiktoker|tiktok.?creator"),
        tags=("y2k", "casual", "playful"),
        scene_niche="lifestyle",
    ),
    Archetype(
        test=re.compile(r"streamer|twitch|gaming.?content"),
        tags=("casual", "street"),
        scene_niche="gaming",
        locked_scene="gaming setup visible behind, RGB lighting as background ambient, one window as key light",
    ),
    Archetype(
        test=re.compile(r"\bblogger\b|content.?creator"),
        tags=("casual", "lifestyle"),
        scene_niche="lifestyle",
    ),
    # ── SAÚDE ─────────────────────────────────────────────────
    Archetype(
        test=re.compile(r"pharmacist|pharmacy"),
        tags=("clean", "minimalist", "preppy"),
        scene_niche="lifestyle",
        physical_detail="thin wire-frame glasses",
        locked_scene="pharmacy counter or clinic corridor, clean institutional light, shelves soft in background",
    ),
    Archetype(
        test=re.compile(r"\bdoctor\b|\bphysician\b|\bmd\b"),
        tags=("clean", "minimalist", "preppy"),
        scene_niche="lifestyle",
        physical_detail="thin wire-frame glasses",
        locked_scene="clinic consultation room, clean desk and framed qualifications soft on the wall behind, diffused overhead light",
    ),
    Archetype(
        test=re.compile(r"\bnurse\b|nursing|registered.?nurse"),
        tags=("clean", "casual", "minimalist"),
        scene_niche="lifestyle",
        locked_scene="hospital corridor, clean white walls, soft institutional overhead light",
    ),
    Archetype(
        test=re.compile(r"therapist|psychologist|counsellor|mental.?health"),
        tags=("quiet", "minimalist", "earthy"),
        scene_niche="lifestyle",
        locked_scene="calm therapy office, a comfortable armchair and soft lamp visible in background, warm quiet light",
    ),
    Archetype(
        test=re.compile(r"acupuncturist|chiropractor|physiotherapist|physical.?therapist"),
        tags=("earthy", "natural", "clean"),
        scene_niche="lifestyle",
    ),
    # ── EDUCAÇÃO ──────────────────────────────────────────────
    Archetype(
        test=re.compile(r"\bstudent\b|college|university|grad.?student"),
        tags=("casual", "street", "preppy"),
        scene_niche="lifestyle",
    ),
    Archetype(
        test=re.compile(r"\bprofessor\b|\blecturer\b|academic"),
        tags=("preppy", "old-money", "minimalist"),
        scene_niche="lifestyle",
        physical_detail="wire-frame glasses",
        locked_scene="university library or faculty corridor, bookshelves soft in background, warm institutional light",
    ),
    Archetype(
        test=re.compile(r"\bteacher\b|educator"),
        tags=("casual", "preppy", "clean"),
        scene_niche="lifestyle",
    ),
    # ── VIAGEM & LIFESTYLE ────────────────────────────────────
    Archetype(
        test=re.compile(r"travel.?blogger|travel.?influencer|travel.?creator|travel.?vlog|\btravell?ing\b"),
        tags=("bohemian", "casual", "coastal"),
        scene_niche="travel",
    ),
    Archetype(
        test=re.compile(r"digital.?nomad"),
        tags=("casual", "bohemian", "minimalist"),
        scene_niche="travel",
    ),
    Archetype(
        test=re.compile(r"\bexpat\b|living.?abroad"),
        tags=("bohemian", "casual", "coastal"),
        scene_niche="travel",
    ),
    # ── VALORES & ESTILO DE VIDA ──────────────────────────────
    Archetype(
        test=re.compile(r"dog.?owner|dog.?lover|pet.?parent|dog.?mom|dog.?dad"),
        tags=("casual", "earthy", "natural"),
        scene_niche="lifestyle",
    ),
    Archetype(
        test=re.compile(r"new.?mom|\bnew mom\b|new.?parent|postpartum"),
        tags=("casual", "clean", "natural"),
        scene_niche="lifestyle",
    ),
    Archetype(
        test=re.compile(r"sustainable|eco.?friendly|environmentalist"),
        tags=("earthy", "natural", "bohemian"),
        scene_niche="lifestyle",
    ),
    Archetype(
        test=re.compile(r"spiritual|mindfulness|meditation|buddhist"),
        tags=("earthy", "bohemian", "natural"),
        scene_niche="lifestyle",
    ),
    Archetype(
        test=re.compile(r"activist|advocate|social.?justice|community.?organizer"),
        tags=("street", "casual", "earthy"),
        scene_niche="lifestyle",
    ),
    Archetype(
        test=re.compile(r"luxury|high.?end|yacht|penthouse"),
        tags=("old-money", "glam", "polished"),
        scene_niche="travel",
    ),
    Archetype(
        test=re.compile(r"\bfreelance\b|freelancer"),
        tags=("casual", "minimalist"),
        scene_niche="lifestyle",
    ),
    # ── ESTÉTICA (sinais de estilo, pega-tudo) ────────────────
    Archetype(
        test=re.compile(r"old.?money|heritage|generational|ivy.?league|equestrian|boarding.?school|country.?club|prep.?school"),
        tags=("old-money", "classic", "polished", "quiet"),
    ),
    Archetype(
        test=re.compile(r"designer|couture|high.?fashion|fashion.?week|rick owens|the row|bottega|celine|loewe"),
        tags=("polished", "editorial", "structured", "old-money"),
        scene_niche="fashion",
    ),
    Archetype(
        test=re.compile(r"street|urban|downtown|brooklyn|graffiti"),
        tags=("street", "urban", "casual"),
    ),
    Archetype(
        test=re.compile(r"\bminimalist\b|\bminimal\b|understated|quiet.?aesthetic|\bquiet\b|\breserved\b|\bintroverted\b"),
        tags=("minimalist", "quiet", "clean"),
    ),
    Archetype(
        test=re.compile(r"boho|bohemian|free.?spirit|wanderer|earthy"),
        tags=("bohemian", "earthy", "natural"),
    ),
    Archetype(
        test=re.compile(r"dark.?aesthetic|gothic|moody|alternative|grunge"),
        tags=("dark", "moody", "urban"),
    ),
    Archetype(
        test=re.compile(r"glam|glamour|sequin|evening.?wear|gala|black.?tie|cocktail"),
        tags=("glam", "evening", "bold"),
    ),
)


@dataclass(slots=True)
class BackstoryContext:
    """O que a backstory diz sobre roupa, cena e corpo."""

    tags: list[str] = field(default_factory=list)
    scene_niche: str | None = None
    build_hint: str | None = None
    physical_detail: str | None = None
    locked_scene: str | None = None
    #: Só chega pela LLM (tier-2); o tier-3 nunca preenche.
    daily_context: str | None = None


def get_backstory_context(physical_desc: str | None, backstory: str | None) -> BackstoryContext:
    """Devolve tags de guarda-roupa + contexto de cena a partir da backstory.

    É o fallback tier-3: roda para todos os usuários, com custo e latência
    zero. O primeiro arquétipo que casar ganha cada campo (os seguintes só
    acrescentam tags), então a ORDEM da tabela é o desempate — arquétipos mais
    específicos vêm antes dos genéricos.
    """
    text = ((physical_desc or "") + " " + (backstory or "")).lower()
    tags: list[str] = []
    scene_niche: str | None = None
    build_hint: str | None = None
    physical_detail: str | None = None
    locked_scene: str | None = None

    for archetype in BACKSTORY_ARCHETYPES:
        if archetype.test.search(text):
            for tag in archetype.tags:
                if tag not in tags:
                    tags.append(tag)
            if not scene_niche and archetype.scene_niche:
                scene_niche = archetype.scene_niche
            if not build_hint and archetype.build_hint:
                build_hint = archetype.build_hint
            if not physical_detail and archetype.physical_detail:
                physical_detail = archetype.physical_detail
            if not locked_scene and archetype.locked_scene:
                locked_scene = archetype.locked_scene

    return BackstoryContext(
        tags=tags,
        scene_niche=scene_niche,
        build_hint=build_hint,
        physical_detail=physical_detail,
        locked_scene=locked_scene,
    )


def get_energy_window(personality: int) -> dict[str, int]:
    """Personalidade → janela de energia do guarda-roupa.

    Introvertido = roupa mais quieta, extrovertido = roupa mais ousada. A
    janela tem sobreposição para a seleção não ficar rígida demais.

    NOTA: existe na origem mas nunca é chamada — quem usa a tolerância de ±28
    é a pontuação dentro de `select_wardrobe`. Portada como está.
    """
    center = personality  # 0–100 mapeia direto
    half = 28  # tolerância de ±28 em torno do centro
    return {"min": max(0, center - half), "max": min(100, center + half)}


def get_styling_note(personality: int) -> str:
    """Nota de styling — descreve COMO a roupa é usada, não qual roupa é."""
    if personality < 25:
        return "Worn without deliberateness — clothes exist, not styled."
    if personality < 45:
        return "Put together but not overthought — one considered choice, the rest just fits."
    if personality < 60:
        return "Casually considered — looks good without looking like effort."
    if personality < 78:
        return "Visibly intentional — pulling the look together on purpose, naturally."
    return "Fully committed — every piece deliberate, confident in the choices."


def select_wardrobe(
    gender: str | None,
    vibe_words: Sequence[str] | None,
    personality: int,
    physical_desc: str | None,
    backstory: str | None,
    force_profession_tags: list[str] | None = None,
    rng: random.Random | None = None,
) -> str:
    """Pontua e escolhe um look da biblioteca.

    `force_profession_tags`: quando a variação está travada pela backstory,
    o pool é hard-filtrado pelas tags da profissão (ignorando a vibe).
    """
    rng = random.Random() if rng is None else rng
    is_male = (gender or "").lower() == "male"
    vibe_tags = [
        *get_vibe_tags(vibe_words),
        *get_backstory_context(physical_desc, backstory).tags,
    ]

    # Havendo vibe escolhida, deriva a tag primária/definidora e hard-filtra
    # antes de tudo. Se as tags de profissão foram forçadas (variação travada
    # pela backstory), usa essas no lugar.
    # NOTA: na origem o teste é `forceProfessionTags || ...`, e em JS uma lista
    # vazia é truthy — ou seja, uma lista vazia forçada NÃO cai no fallback das
    # vibes. Aqui isso vira `is not None` para preservar o comportamento (em
    # Python a lista vazia seria falsy e mudaria o resultado).
    if force_profession_tags is not None:
        primary_vibe_tags = force_profession_tags
    else:
        primary_vibe_tags = [
            VIBE_PRIMARY_TAG[v] for v in (vibe_words or []) if VIBE_PRIMARY_TAG.get(v)
        ]

    # Filtro por gênero
    gender_pool = [
        e for e in WARDROBE if (e.gender == "male" if is_male else e.gender == "female")
    ]

    # Hard-filter pelo pool da vibe; volta atrás se sobrarem poucas entradas
    if primary_vibe_tags:
        filtered = [e for e in gender_pool if any(t in primary_vibe_tags for t in e.tags)]
        pool = filtered if len(filtered) >= 3 else gender_pool
    else:
        pool = gender_pool

    # Pontuação
    scored: list[tuple[float, WardrobeEntry]] = []
    for e in pool:
        score = 0.0

        # Proximidade de energia — quanto mais perto do centro da
        # personalidade, mais pontos
        energy_dist = abs(e.energy - personality)
        if energy_dist <= 15:
            score += 40  # muito próximo
        elif energy_dist <= 28:
            score += 20  # dentro da tolerância
        else:
            score -= 20  # fora da janela — penaliza, mas não exclui

        # Overlap de tags de vibe — cada tag que casa pontua
        if vibe_tags:
            tag_matches = len([t for t in e.tags if t in vibe_tags])
            score += tag_matches * 18

        # Ruído controlado — mantém variedade entre gerações
        score += rng.random() * 14

        scored.append((score, e))

    # Ordenação estável e decrescente, igual ao `sort((a,b) => b.score - a.score)`
    scored.sort(key=lambda par: -par[0])

    # Sorteia entre os melhores para nunca ser determinístico
    top_n = min(6, len(scored))
    chosen = scored[:top_n]
    entry = _pick(chosen, rng)[1]

    return f"{entry.text}. {get_styling_note(personality)}"


# ── Pools de cena por nicho ───────────────────────────────────────────────


SCENE_POOLS: dict[str, tuple[str, ...]] = {
    "fashion": (
        "sun-drenched SoHo loft, whitewashed exposed brick, tall factory windows, morning light pooling on oak floors",
        "Paris Le Marais narrow side street, charcoal limestone facades, cobblestones faintly damp, warm afternoon shade with a bright lane-end opening behind",
        "minimal Copenhagen café interior, raw concrete wall, warm oak counter surface catching window light",
        "Tokyo backstreet alley, clean concrete with layers of soft-focus weathered signage, warm evening ambient",
        "Milanese internal courtyard, faded terracotta plaster walls, afternoon sun cutting a diagonal stripe across the floor",
    ),
    "beauty": (
        "sun-flooded modern bathroom, small window at 9 o'clock, white subway tiles, faint steam in the air, morning",
        "clean neutral hotel room, floor-to-ceiling window, soft afternoon city skyline behind",
        "bedroom with large east-facing window, morning light angled low, white walls",
        "minimalist vanity corner, large mirror catching daylight from a nearby window",
    ),
    "fitness": (
        "minimalist gym, natural oak floors, white walls, high windows with golden side-light from camera-left",
        "outdoor park trail, city skyline softened beyond treeline, morning ground mist",
        "concrete rooftop workout space, city spread below, blue-hour transition light",
        "empty beach at low tide, wet packed sand, low warm morning light catching the surface",
        "urban outdoor running path, tree canopy overhead, soft dappled morning light through leaves",
    ),
    "travel": (
        "terracotta European alleyway, afternoon shade, warm stone walls both sides, bright sunlit opening far behind",
        "elevated viewpoint, city in golden hour atmospheric haze below",
        "harbor wall of a small coastal town, boats soft in background, overcast morning",
        "narrow Kyoto side street, wooden facades, soft diffused late-afternoon light",
        "rooftop above Mediterranean rooflines, warm evening, terracotta and white receding behind",
    ),
    "lifestyle": (
        "warm neighborhood café corner, marble counter, condensation on the window, mid-afternoon light",
        "sun-bleached urban rooftop terrace, city rooflines behind, golden hour",
        "city pavement under a canopy of plane trees, mottled shade-and-sun, late morning",
        "hotel room window ledge, soft city panorama through glass, afternoon",
        "sun-filled apartment kitchen, white tiles, indoor plants catching window light, morning",
    ),
    "tech": (
        "minimal open-plan office, floor-to-ceiling window, cool diffused cloud daylight, industrial ceiling soft behind",
        "coffee shop corner, laptop implied just off-frame, morning window light from 9 o'clock",
        "clean home studio setup, soft north-facing window, minimal desk behind",
        "modern co-working space, city view through full-height glass, afternoon",
    ),
    "gaming": (
        "clean battlestation visible behind, RGB soft-glow as background, one window at 90° as key light",
        "modern space, neon accent soft in background, late evening one-window key",
    ),
    "entertainment": (
        "rooftop at golden hour, city below, warm haze",
        "urban mural wall, bold color out of focus, late afternoon",
        "backstage corridor, warm strip practicals above, evening",
    ),
    "default": (
        "clean urban street corner, warm afternoon, city architecture as soft atmospheric background",
        "bright minimal interior, natural daylight from large windows, white walls receding",
        "rooftop with city skyline behind, golden hour haze, warm atmospheric depth",
        "café window seat, street softened behind glass, afternoon light from the left",
    ),
}


# Cenas só de rua para a variação candid — sempre fora, viva, real
OUTDOOR_CANDID_SCENES: tuple[str, ...] = (
    "tree-lined city sidewalk, mid-morning, soft dappled light filtering through the canopy overhead",
    "outdoor café terrace, small round table nearby, city street softened behind, mid-afternoon sun",
    "sunny boutique shopping street, warm afternoon light on the storefronts, no other people",
    "urban park path, greenery on both sides, natural soft mid-morning light through the trees",
    "corner outside a coffee shop, city intersection behind, warm mid-morning light from the side",
    "sun-warmed city steps or a low ledge at the edge of an open plaza, afternoon light from the front",
    "wide sun-drenched pavement outside a row of boutiques, warm late afternoon",
    "rooftop terrace with a few potted plants, city rooflines visible behind, golden hour light",
    "narrow sunny side street, warm afternoon light cutting diagonally across the pavement",
    "small outdoor market square, light warm and directional, the square otherwise empty",
)


ALL_SCENES: tuple[str, ...] = tuple(s for pool in SCENE_POOLS.values() for s in pool)


#: Nicho declarado pelo usuário → chave do pool de cenas. Vários nichos
#: apontam para o mesmo pool (finance → tech, wellness → fitness).
NICHE_TO_SCENE_KEY: dict[str, str] = {
    "fashion": "fashion",
    "beauty": "beauty",
    "lifestyle": "lifestyle",
    "fitness": "fitness",
    "travel": "travel",
    "food & dining": "lifestyle",
    "food": "lifestyle",
    "tech": "tech",
    "gaming": "gaming",
    "finance": "tech",
    "entertainment": "entertainment",
    "wellness": "fitness",
    "sports": "fitness",
    "sport": "fitness",
}


def get_scene_pool(niches: Sequence[str] | None) -> tuple[str, ...]:
    """Nichos da persona → pool de cenas. Sem nicho reconhecido, tudo vale."""
    if not niches:
        return ALL_SCENES
    keys = list(
        dict.fromkeys(
            k for k in (NICHE_TO_SCENE_KEY.get(n.lower()) for n in niches) if k
        )
    )
    if not keys:
        return ALL_SCENES
    pool = tuple(s for k in keys for s in SCENE_POOLS.get(k, ()))
    return pool if pool else ALL_SCENES


# ── Prop — apropriado ao nicho, ~30% de chance ────────────────────────────

DRINK_PROP_PATTERN = re.compile(r"latte|matcha|coffee|tea|smoothie|cup|straw|thermos|bubble")


def is_drink_prop(prop: str | None) -> bool:
    """A pose candid vira 'mid-sip' só quando o prop é bebida."""
    return bool(prop) and DRINK_PROP_PATTERN.search(prop) is not None


#: 12 objetos + 3 `None` — o `None` na própria lista é o que faz o prop
#: às vezes não existir mesmo depois de passar pelo primeiro corte.
UNIVERSAL_PROPS: tuple[str | None, ...] = (
    "iced matcha latte in a clear to-go cup, bright green, paper straw, slight condensation",
    "iced coffee in a clear coffee shop cup with a dome lid, paper straw",
    "iced latte in a clear cup, light brown, ice visible through the sides, paper straw",
    "stainless steel wide-mouth water bottle, no logo, condensation on the outside",
    "small paper shopping bag held loosely at the side by the handles",
    "small pebbled leather tote held at the crook of the arm",
    "pair of clean sunglasses held loosely in one hand at the side",
    "worn-in paperback, held loosely by the spine",
    "small bouquet of dried or fresh flowers, stems in one hand",
    "small wired earbuds just removed, held loosely in one hand",
    "thin notebook held loosely under one arm",
    "phone held loosely at the side, screen off",
    None,
    None,
    None,
)


def get_prop(no_drink: bool = False, rng: random.Random | None = None) -> str | None:
    """Sorteia um prop — ou nada nas mãos.

    NOTA: o comentário da origem diz "~30% de chance", mas a conta é outra:
    30% saem já no primeiro corte, e dos 70% restantes 3 das 15 entradas da
    lista são `None`. A chance efetiva de vir um prop é ~56%. Portado como está.
    """
    rng = random.Random() if rng is None else rng
    if rng.random() > 0.7:
        return None
    options = (
        [o for o in UNIVERSAL_PROPS if not is_drink_prop(o)] if no_drink else UNIVERSAL_PROPS
    )
    return _pick(options, rng)


# ── Escolha de câmera ─────────────────────────────────────────────────────


def get_camera() -> str:
    """Sempre iPhone 16 Pro.

    A identidade inteira do produto é realismo de iPhone — nunca uma câmera
    profissional. Por isso não há escolha aqui.
    """
    return (
        "iPhone 16 Pro 24mm main lens f/1.78, held at arm's length or by a nearby friend, "
        "automatic exposure, natural sensor noise in shadow areas, slight lens barrel distortion at edges, "
        "9:16 vertical, chest-up framing, face at the upper-third line, subject fills the center of the frame"
    )


# ── Bloco de realismo de pele ─────────────────────────────────────────────


def build_skin_block(
    time_label: str,
    gender: str | None,
    physical_desc: str | None,
    rng: random.Random | None = None,
) -> str:
    """Monta o bloco de pele, adaptado ao tom de pele e ao horário.

    A reação ambiental da pele muda com a luz (rubor de frio no fim de tarde,
    suor fino no sol da manhã) e com o tom de pele descrito — pele clara mostra
    o calor dourado de um jeito, pele escura de outro.
    """
    rng = random.Random() if rng is None else rng
    pronoun = "his" if (gender or "").lower() == "male" else "her"
    p = (physical_desc or "").lower()
    has_fair = "fair" in p or "pale" in p or "light skin" in p
    has_dark = "dark skin" in p or "deep" in p or "ebony" in p or "melanin" in p

    if has_fair:
        golden = "warm golden flush across the forehead and cheekbone tops — more visible on fair skin, the lit side noticeably warm"
    elif has_dark:
        golden = "rich deep skin tones dimensional in the golden light, the high points — forehead, cheekbone — catching warmth"
    else:
        golden = "faint sun-warmth across the forehead and tops of the cheekbones from afternoon outdoor exposure"

    env_reactions = {
        "golden hour late afternoon": golden,
        "overcast soft daylight": "even, cool-environment skin with a natural healthy flush — no sun warmth, no windburn",
        "bright sunny mid-morning": "slight sun-warmth flush on the forehead and nose bridge, micro-fine sweat at the temples catching the hard light",
        "blue-hour dusk": "faint cold-air redness around the nostrils and upper cheeks from the cooling evening temperature",
        "soft indoor afternoon window": "soft thermal indoor flush at the cheeks from the heated space, gentle warmth settling across the face",
        "morning indoor café": "a light warmth-flush as the skin adjusts from outdoor cool to the heated interior, slight color at the cheeks",
    }

    imperfeicao = _pick(
        [
            "a small healing blemish on the left jaw, slightly pinker than surrounding skin",
            "faint asymmetric sun pigmentation near the right temple",
            "two freckles placed asymmetrically across the nose and left cheek",
            "a faint old thin scar below the right jawline — barely there, photographically real",
            "slight horizontal pressure line across the forehead from a hat worn earlier",
        ],
        rng,
    )

    return (
        f"— Visible individual pores across {pronoun} T-zone, nose, and cheeks; pores on the lit side cast tiny directional micro-shadows from the key light\n"
        f"— {env_reactions.get(time_label, env_reactions['overcast soft daylight'])}\n"
        f"— {imperfeicao}\n"
        "— Left brow sits marginally higher than the right; one nostril slightly narrower; cupid's bow peaks uneven — natural asymmetry throughout\n"
        "— Subtle digital sensor noise in the shadow areas consistent with iPhone auto-ISO"
    )


# ── Enquadramento de caráter, a partir da personalidade ───────────────────


def get_character_framing(personality: int) -> str:
    """A energia que a pessoa carrega — sempre com o âncora anti-modelo."""
    if personality < 30:
        return (
            "quiet, inward energy — someone with a rich internal world, not performing for the camera. "
            "Real person, real life, not a model on a shoot"
        )
    if personality > 70:
        return (
            "open, present energy — moves through the world with ease, comfortable being seen. "
            "Real person, real life, not a model on a shoot"
        )
    return "natural, unhurried energy — comfortable in the moment. Real person, real life, not a model on a shoot"


# ── Montador do prompt ────────────────────────────────────────────────────

#: Sentinela para distinguir "prop não informado" (sorteia um) de "prop
#: explicitamente nulo" (mãos vazias). Na origem isso era `'forceProp' in options`.
_PROP_NAO_INFORMADO = object()

#: Cenas externas não podem receber iluminação de interior — o horário é
#: sorteado dentro do subconjunto compatível com a cena.
OUTDOOR_SCENE_PATTERN = re.compile(
    r"\b(park|trail|beach|rooftop|street|pavement|outdoor|alley|plaza|market|terrace|harbor|path|square|city|urban|cobblestone|courtyard|sidewalk|promenade)\b",
    re.IGNORECASE,
)


def montar_prompt(
    persona: Persona,
    force_pose: Pose | None = None,
    *,
    backstory_locked: bool = False,
    force_outdoor: bool = False,
    force_prop: Any = _PROP_NAO_INFORMADO,
    aspect_ratio: str = "9:16",
    rng: random.Random | None = None,
) -> PromptMontado:
    """Monta um prompt. Equivalente a `buildDirectPrompt`.

    Todo campo da persona dirige alguma parte da saída: personalidade dirige
    pose, energia do guarda-roupa e enquadramento de caráter; nichos dirigem a
    cena; a backstory dirige tudo pelo motor de arquétipos.
    """
    rng = random.Random() if rng is None else rng

    gender = persona.gender or "woman"
    age = f"{persona.apparent_age} year old" if persona.apparent_age else "mid-20s"
    physical = (persona.physical_desc or "").strip() or "with dark hair, warm complexion, natural features"
    vibes = list(persona.vibe_words or [])
    personality = 50 if persona.personality_energy is None else persona.personality_energy
    backstory = (persona.backstory or "").strip()
    is_editorial = "Editorial" in vibes

    # O tier-3 (arquétipos) SEMPRE roda — é ele que é dono de build_hint,
    # physical_detail e locked_scene. A LLM (tier-2) contribui com
    # daily_context e pode sobrescrever scene_niche/tags para profissões
    # incomuns. A precedência não é negociável: se a LLM inventar uma cena, ela
    # é ignorada; a cena travada do arquétipo é o que faz a variação 3
    # funcionar.
    tier3_ctx = get_backstory_context(persona.physical_desc, backstory)
    if persona.backstory_context:
        llm = persona.backstory_context
        backstory_ctx = BackstoryContext(
            build_hint=tier3_ctx.build_hint,
            physical_detail=tier3_ctx.physical_detail,
            locked_scene=tier3_ctx.locked_scene,
            tags=list(llm.get("tags") or tier3_ctx.tags),
            scene_niche=llm.get("sceneNiche") or tier3_ctx.scene_niche,
            daily_context=llm.get("dailyContext"),
        )
    else:
        backstory_ctx = tier3_ctx
    scene_niche = backstory_ctx.scene_niche
    build_hint = backstory_ctx.build_hint
    physical_detail = backstory_ctx.physical_detail
    locked_scene = backstory_ctx.locked_scene

    niches = list(persona.niches or [])

    # Cena: travada pela backstory → usa a locked_scene exata ou o pool da
    # profissão; senão, lógica padrão.
    if backstory_locked:
        if locked_scene:
            scene = locked_scene
        elif scene_niche and scene_niche in SCENE_POOLS:
            scene = _pick(SCENE_POOLS[scene_niche], rng)
        else:
            scene = _pick(get_scene_pool(niches), rng)
    elif force_outdoor:
        scene = _pick(OUTDOOR_CANDID_SCENES, rng)
    elif scene_niche and scene_niche in SCENE_POOLS:
        scene = _pick(SCENE_POOLS[scene_niche], rng)
    else:
        scene = _pick(get_scene_pool(niches), rng)

    # Horário compatível com a cena — cena externa não pode pegar luz de interior
    is_outdoor = OUTDOOR_SCENE_PATTERN.search(scene) is not None
    outdoor_times = tuple(
        t for t in TIME_CONFIGS if "indoor" not in t.label and "café" not in t.label
    )
    time_config = _pick(outdoor_times if is_outdoor else TIME_CONFIGS, rng)

    pose_fn = force_pose or get_pose_from_personality(personality, rng)
    # Usa o prop pré-gerado pelo gerenciador de sessão quando informado;
    # senão sorteia um.
    prop = get_prop(rng=rng) if force_prop is _PROP_NAO_INFORMADO else force_prop

    # Travado pela backstory: força as tags de profissão no guarda-roupa quando
    # nenhuma vibe foi escolhida. Para locked_scenes (cenários de profissão
    # específicos, quase sempre internos), derruba tags de lifestyle como
    # 'casual' para que athleisure/street não concorram com o guarda-roupa real
    # de academia/clínica.
    if backstory_locked and not vibes:
        if locked_scene:
            forced_prof_tags = [
                t for t in backstory_ctx.tags if t not in ("casual", "urban", "street")
            ]
        else:
            forced_prof_tags = list(backstory_ctx.tags)
    else:
        forced_prof_tags = None

    wardrobe_base = select_wardrobe(
        gender,
        vibes,
        personality,
        persona.physical_desc,
        persona.backstory,
        forced_prof_tags,
        rng,
    )
    palette_line = " | ".join(
        VIBE_PALETTE_MAP[v] for v in vibes if VIBE_PALETTE_MAP.get(v)
    )
    wardrobe = f"{wardrobe_base}\n{palette_line}" if palette_line else wardrobe_base
    camera = get_camera()
    skin_block = build_skin_block(time_config.label, gender, physical, rng)
    character_framing = get_character_framing(personality)

    prop_desc = (
        f"{prop} held in one hand — no visible brand logo"
        if prop
        else "hands in a natural mid-gesture, nothing held"
    )

    # NOTA: na origem esta linha é uma constante vazia — o daily_context da LLM
    # é calculado e nunca usado. Portado como está.
    daily_ctx_line = ""

    # Acréscimos físicos quando travado pela backstory: tipo de corpo (só se o
    # usuário deixou o campo em branco) + marcador da profissão.
    # NOTA: a origem lê `d.build`, campo que a `Persona` daqui não tem. Sem ele,
    # a condição "usuário deixou em branco" é sempre verdadeira — mesmo
    # comportamento de uma persona sem build preenchido na origem.
    build_usuario = (getattr(persona, "build", "") or "").strip()
    build_desc = f", {build_hint}" if (backstory_locked and build_hint and not build_usuario) else ""
    physical_detail_str = f", {physical_detail}" if (backstory_locked and physical_detail) else ""

    if pose_fn is POSES["frontfacing"]:
        pose_name = "iPhone portrait — direct, relaxed, facing camera"
    elif pose_fn is POSES["contemplative"]:
        pose_name = "iPhone portrait — quiet, present, facing camera"
    elif pose_fn is POSES["plandid"]:
        pose_name = "iPhone candid — soft awareness, facing camera"
    elif pose_fn is POSES["posed_cute"]:
        pose_name = "iPhone feed shot — soft pose, eyes at lens"
    else:
        pose_name = "iPhone candid — mid-moment, eyes at the lens"

    # Cena travada não recebe o rótulo de horário: a locked_scene já traz a luz
    # descrita dentro dela, e repetir criaria conflito.
    scene_time = "" if (backstory_locked and locked_scene) else f", {time_config.label}"

    if aspect_ratio == "16:9":
        framing_constraint = (
            "Horizontal landscape frame — subject standing close to camera, filling at least half the frame height, "
            "environment visible on both sides. Not a distant wide shot — the subject must be close enough that face "
            "and outfit detail are fully legible. This is a wide candid, not a portrait crop rotated sideways."
        )
    else:
        framing_constraint = (
            "Subject fills 60–70% of the 9:16 frame — tight crop, not a wide environmental shot."
        )

    editorial_line = (
        "Editorial vibe applies to the styling only — the photo itself is a raw iPhone snapshot."
        if is_editorial
        else ""
    )

    texto = f"""Photograph style: iPhone 16 Pro snapshot. Taken by the subject or a nearby friend, handheld, automatic settings. No professional crew, no studio, no lighting setup, no direction given. Raw iPhone output — unedited. The subject is unaware this will be published — a personal photo, not intended for any shoot.

Scene: {scene}{scene_time}. Empty of other people. If the location is an interior, it shows real signs of habitation — not a styled showroom. Background is real, in-focus, and unmanipulated exactly as an iPhone captures it — no blur, no bokeh, no artificial depth of field. The subject is the hero through tight framing and natural lighting, not through background manipulation.

Subject: {gender}, {age}, {physical}{build_desc}{physical_detail_str}. {character_framing}.{daily_ctx_line} Natural micro-asymmetries in the face — this is a real iPhone photograph of a real person, not a 3D render or CGI. Real visible pore texture on the nose, cheeks, and forehead — and on all exposed skin including arms, neck, and shoulders. Zero skin smoothing anywhere on the body, zero airbrushing, zero beauty filter applied.

Pose: {pose_fn(prop)} {prop_desc}.

Wardrobe & details: {wardrobe}

Lighting: {time_config.lighting} The subject's face is the brightest element in the frame. This is natural found light — not a lighting setup.

Camera & capture: {camera}.

Skin (rendered as concrete photographic facts, not category words):
{skin_block}

Use case: {pose_name}

Constraints: no people in the background. No visible brand logos on any item. {framing_constraint} No background blur or bokeh. Real pore texture and skin imperfections visible on the face and all exposed body skin — zero beauty retouching. No AI aesthetic markers: no unnaturally bright irises, no perfectly symmetrical face, no plastic-smooth skin, no uncanny glow. No phone screen, no social media UI, no app overlay, no notification bar, no status bar, no interface elements of any kind visible anywhere in the image. This is a raw photograph — no digital overlays, no framing devices, no UI chrome. {editorial_line}"""

    return PromptMontado(
        texto=texto,
        referencias=[],
        aspect_ratio=aspect_ratio,
        escolhas={
            "scene": scene,
            "time_label": time_config.label,
            "pose": pose_name,
            "prop": prop,
            "wardrobe": wardrobe,
            "palette": palette_line,
            "scene_niche": scene_niche,
            "locked_scene": locked_scene,
            "build_hint": build_hint,
            "physical_detail": physical_detail,
            "backstory_tags": list(backstory_ctx.tags),
            "forced_profession_tags": forced_prof_tags,
            "backstory_locked": backstory_locked,
            "force_outdoor": force_outdoor,
            "personality": personality,
        },
    )


def montar_tres_variacoes(
    persona: Persona,
    aspect_ratio: str = "9:16",
    rng: random.Random | None = None,
) -> list[PromptMontado]:
    """As três variações do wizard. Equivalente a `buildThreeVariationPrompts`.

    V1 = frontfacing, V2 = posed_cute, V3 = candid — travada na cena da
    profissão se algum arquétipo foi detectado, senão forçada para a rua.
    """
    rng = random.Random() if rng is None else rng

    # Props são sorteados de uma vez para a sessão inteira:
    # — a pose candid nunca ganha bebida (o mid-sip domina demais a imagem)
    # — no máximo 1 bebida entre as 3 variações
    drink_used = False

    def session_prop(no_drink: bool = False) -> str | None:
        nonlocal drink_used
        p = get_prop(no_drink or drink_used, rng)
        if is_drink_prop(p):
            drink_used = True
        return p

    # A variação 3 é travada pela backstory quando uma profissão é detectada.
    # O tier-3 é sempre dono da locked_scene; a LLM só pode acrescentar um
    # scene_niche para profissões incomuns.
    tier3_ctx = get_backstory_context(persona.physical_desc, (persona.backstory or "").strip())
    has_backstory_lock = bool(
        tier3_ctx.scene_niche
        or tier3_ctx.locked_scene
        or (persona.backstory_context or {}).get("sceneNiche")
    )

    # A ordem de avaliação importa: cada `session_prop()` consome sorteios do
    # rng ANTES da montagem do prompt correspondente, igual à origem.
    v1 = montar_prompt(
        persona, POSES["frontfacing"], force_prop=session_prop(), aspect_ratio=aspect_ratio, rng=rng
    )
    v2 = montar_prompt(
        persona, POSES["posed_cute"], force_prop=session_prop(), aspect_ratio=aspect_ratio, rng=rng
    )
    if has_backstory_lock:
        v3 = montar_prompt(
            persona,
            POSES["candid"],
            backstory_locked=True,
            force_prop=None,
            aspect_ratio=aspect_ratio,
            rng=rng,
        )
    else:
        v3 = montar_prompt(
            persona,
            POSES["candid"],
            force_outdoor=True,
            force_prop=session_prop(True),
            aspect_ratio=aspect_ratio,
            rng=rng,
        )

    return [v1, v2, v3]
