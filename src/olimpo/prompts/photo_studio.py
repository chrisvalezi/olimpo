"""Motor de prompt do Photo Studio — porte literal de ``src/utils/photoStudioPrompt.js``.

Comentário original do topo do arquivo, traduzido:

    Motor de prompt do Photo Studio para o GPT Image 2.
    Baseado na spec de realismo do ``photo-studio-influencer-guide.md``.

A filosofia declarada no guia é **"This Is an Edit, Not a Generation"**: o Photo
Studio SEMPRE passa imagens de referência, então o modelo já sabe como a pessoa
é. O trabalho do texto é dizer o que FAZER com ela — onde colocar, como posar,
qual o clima. Redescrever o que a referência já mostra é token gasto diluindo a
instrução. Alvo: **60–140 palavras**, um parágrafo único e coeso, lido como um
diretor briefando um fotógrafo, não como lista de tags.

Nada aqui foi "melhorado" no porte. Cada string é resultado de tentativa e erro
contra o modelo real; uma vírgula muda a imagem gerada.
"""

from __future__ import annotations

import random
from dataclasses import dataclass, field
from typing import Literal

from .tipos import Genero, Persona, PromptMontado, Referencia

__all__ = [
    "ASPECTS",
    "EXPRESSIONS",
    "LOCATIONS",
    "OUTFIT_PRESETS",
    "OUTFIT_PRESETS_FEMALE",
    "OUTFIT_PRESETS_MALE",
    "POSES",
    "POSES_FEMALE_STANDING",
    "POSES_MALE_STANDING",
    "PROP_SUGGESTIONS",
    "TIMES",
    "VIBES",
    "ImagensDaPersona",
    "Opcao",
    "Props",
    "RefDeProduto",
    "Roupa",
    "TagsDeReferencia",
    "get_outfit_presets",
    "get_outfit_prompt",
    "get_poses",
    "montar_prompt_photo_studio",
    "montar_referencias",
    "parametros_aleatorios",
]

ModoDeProduto = Literal["holding", "wearing"]


@dataclass(frozen=True, slots=True)
class Opcao:
    """Item das listas da UI: o ``id`` é a chave dos mapas, o ``label`` é o rótulo."""

    id: str
    label: str
    icon: str = ""


LOCATIONS: tuple[Opcao, ...] = (
    Opcao("coffee-shop", "Coffee Shop", "☕"),
    Opcao("city-street", "City Street", "🏙"),
    Opcao("beach", "Beach", "🌊"),
    Opcao("rooftop", "Rooftop", "🌆"),
    Opcao("bedroom", "Bedroom", "🛏"),
    Opcao("bathroom", "Mirror Selfie", "🪞"),
    Opcao("mall", "Mall", "🛍"),
    Opcao("gym", "Gym", "💪"),
    Opcao("park", "Park", "🌿"),
    Opcao("restaurant", "Restaurant", "🍽"),
    Opcao("hotel", "Hotel Room", "🏨"),
    Opcao("studio", "Studio", "🎨"),
)

TIMES: tuple[Opcao, ...] = (
    Opcao("morning", "Morning"),
    Opcao("afternoon", "Afternoon"),
    Opcao("golden-hour", "Golden Hour"),
    Opcao("night", "Night"),
)

# ── Listas de pose por gênero + stance ───────────────────────────────────────

POSES_FEMALE_STANDING: tuple[Opcao, ...] = (
    Opcao("front", "Front-Facing"),
    Opcao("handheld", "Handheld"),
    Opcao("candid", "Candid"),
    Opcao("cute-posed", "Hair Touch"),
    Opcao("hip-pop", "Hip Pop"),
    Opcao("over-shoulder", "Over Shoulder"),
    Opcao("facing-away", "Facing Away"),
    Opcao("walking", "Walking"),
    Opcao("mid-turn", "Mid Turn"),
    Opcao("long-line", "Step Out"),
    Opcao("lean", "Wall Lean"),
)

POSES_MALE_STANDING: tuple[Opcao, ...] = (
    Opcao("front", "Confident Front"),
    Opcao("handheld", "Handheld"),
    Opcao("hands-pockets", "Hands In Pockets"),
    Opcao("crossed-arms", "Crossed Arms"),
    Opcao("lean", "Environment Lean"),
    Opcao("walking", "Walking"),
    Opcao("over-shoulder", "Over Shoulder"),
    Opcao("facing-away", "Facing Away"),
)

#: Compatibilidade com qualquer import antigo de ``POSES``.
POSES: tuple[Opcao, ...] = POSES_FEMALE_STANDING


def get_poses(gender: str) -> tuple[Opcao, ...]:
    """A lista de poses é a mesma independente do stance.

    Sentar só adapta o enquadramento e a posição — por isso a função recebe
    apenas o gênero.

    NOTA (bug preservado): no original ``getPoses`` também aceita um único
    argumento, mas ``randomParams`` a chama com dois (``getPoses('Female',
    stance)``). Em JavaScript o segundo argumento é silenciosamente descartado;
    aqui a assinatura de um argumento só torna isso explícito. Ver
    :func:`parametros_aleatorios`.
    """
    return POSES_MALE_STANDING if gender == "Male" else POSES_FEMALE_STANDING


VIBES: tuple[Opcao, ...] = (
    Opcao("candid", "Candid"),
    Opcao("editorial", "Editorial"),
    Opcao("luxury", "Luxury"),
    Opcao("street", "Street"),
    Opcao("cozy", "Cozy"),
)

# ── Expressões ───────────────────────────────────────────────────────────────

EXPRESSIONS: tuple[Opcao, ...] = (
    Opcao("natural", "Natural"),
    Opcao("smiling", "Smiling"),
    Opcao("laughing", "Mid-Laugh"),
    Opcao("serious", "Serious"),
)

EXPRESSION_MAP: dict[str, str] = {
    "natural": "",
    "smiling": "Expression: a genuine soft smile — lips parted slightly, upper teeth just visible, the smile reaching the outer corners of the eyes with warmth.",
    "laughing": "Expression: caught mid-laugh at the apex — head tilted slightly back, eyes fully crinkled with genuine amusement, mouth open, the laugh spontaneous and unposed.",
    "serious": "Expression: direct and serious — neutral mouth at rest, steady gaze into the lens, no smile. Composed and self-assured.",
    "looking-away": "",  # legado — hoje quem trata isso é o toggle de olhar
}

#: Sugestões de prop por local, exibidas como placeholder na UI.
PROP_SUGGESTIONS: dict[str, str] = {
    "coffee-shop": "e.g. holding a coffee cup",
    "restaurant": "e.g. holding a wine glass",
    "beach": "e.g. holding sunglasses or a hat",
    "gym": "e.g. holding a water bottle",
    "mall": "e.g. carrying shopping bags",
    "city-street": "e.g. adjusting a bag strap",
    "bedroom": "e.g. holding a phone or book",
    "bathroom": "e.g. holding a phone (mirror selfie)",
    "hotel": "e.g. holding a clutch or room key",
    "rooftop": "e.g. holding a drink or phone",
    "park": "e.g. holding a takeaway coffee",
    "studio": "e.g. no prop",
}

ASPECTS: tuple[str, ...] = ("9:16", "16:9")

OUTFIT_PRESETS_FEMALE: tuple[Opcao, ...] = (
    Opcao("Casual", "Casual"),
    Opcao("Streetwear", "Streetwear"),
    Opcao("Chic", "Chic"),
    Opcao("Athleisure", "Athleisure"),
    Opcao("Minimal", "Minimal"),
    Opcao("Glam", "Glam"),
    Opcao("Party", "Party"),
    Opcao("Cozy", "Cozy"),
)

OUTFIT_PRESETS_MALE: tuple[Opcao, ...] = (
    Opcao("Casual", "Casual"),
    Opcao("Streetwear", "Streetwear"),
    Opcao("Smart Casual", "Smart Casual"),
    Opcao("Athleisure", "Athleisure"),
    Opcao("Minimal", "Minimal"),
    Opcao("Business", "Business"),
    Opcao("Party", "Party"),
    Opcao("Cozy", "Cozy"),
)

#: Export antigo mantido para código que ainda referencia a lista de ids.
OUTFIT_PRESETS: tuple[str, ...] = tuple(o.id for o in OUTFIT_PRESETS_FEMALE)

OUTFIT_PRESET_MAP_FEMALE: dict[str, str] = {
    "Casual": "High-waist straight-leg jeans, a fitted white tee or ribbed tank tucked at the front, white leather sneakers or ballet flats. Effortless and put-together.",
    "Streetwear": "Baggy cargo pants or wide-leg denim, an oversized graphic tee or cropped hoodie, chunky sneakers, a fitted cap or bucket hat. Bold and urban.",
    "Chic": "Tailored wide-leg trousers or a midi wrap skirt, a fitted silk or satin blouse, pointed-toe kitten heels or mules, delicate gold jewellery. Polished and intentional.",
    "Athleisure": "High-waist leggings or biker shorts, a cropped sports bra or fitted zip-up hoodie, clean white trainers. Gym-to-street energy.",
    "Minimal": "Neutral tones only — cream, beige, stone, white. A clean ribbed tank or fitted turtleneck, straight-leg trousers or a long linen skirt, simple leather sneakers or mules. Nothing extra.",
    "Glam": "A slinky satin slip dress or a fitted co-ord set in a jewel tone or champagne, strappy stilettos, statement drop earrings, a small evening clutch. Dressed up.",
    "Party": "A bodycon mini dress or a spaghetti-strap top with low-rise straight jeans, heeled ankle boots or platform sandals, a small chain-strap bag. Night-out confident.",
    "Cozy": "An oversized chunky knit cardigan or hoodie over a longline tee, soft wide-leg sweatpants or joggers, fluffy socks, slide sandals. Stay-home comfortable.",
}

OUTFIT_PRESET_MAP_MALE: dict[str, str] = {
    "Casual": "Well-fitted straight-leg jeans or chinos in a neutral tone, a clean crew-neck tee or lightweight chambray shirt, white leather sneakers. Everyday sharp.",
    "Streetwear": "Baggy cargo pants or wide-leg denim, an oversized graphic tee or heavyweight hoodie, chunky sneakers, a fitted cap or beanie. Urban and confident.",
    "Smart Casual": "Slim dark jeans or tapered chinos, an untucked linen or Oxford button-down with sleeves rolled up, clean suede loafers or leather trainers. Effortlessly put-together.",
    "Athleisure": "Tapered joggers or training shorts, a fitted moisture-wicking tee or quarter-zip pullover, clean running shoes. Ready to move.",
    "Minimal": "Neutral palette — black, white, grey, stone. A fitted crewneck or Henley, slim straight trousers, simple clean sneakers or Chelsea boots. No logos, nothing extra.",
    "Business": "Slim-fit chinos or tailored trousers, a tucked Oxford shirt with the collar open, a sharp unstructured blazer in navy or camel, leather loafers or clean dress shoes.",
    "Party": "Dark slim jeans or tailored trousers, a fitted satin or textured button-up left partially open, leather Chelsea boots or clean dress shoes, a simple watch.",
    "Cozy": "An oversized knit sweater or zip-up hoodie, relaxed wide-leg sweatpants or joggers, thick crew socks, slide sandals or low-profile sneakers.",
}


def get_outfit_presets(gender: str) -> tuple[Opcao, ...]:
    """Presets de guarda-roupa disponíveis para o gênero."""
    return OUTFIT_PRESETS_MALE if gender == "Male" else OUTFIT_PRESETS_FEMALE


def get_outfit_prompt(preset_id: str, gender: str) -> str | None:
    """Texto de guarda-roupa de um preset, ou ``None`` se o preset não existe."""
    mapa = OUTFIT_PRESET_MAP_MALE if gender == "Male" else OUTFIT_PRESET_MAP_FEMALE
    return mapa.get(preset_id) or None


# ── Variantes de cena — 4 pontos significativamente distintos por local ───────
# `variation_idx` escolhe qual; iluminação e horário vêm de SHORT_LIGHTING à parte.

SCENE_VARIANTS: dict[str, tuple[str, ...]] = {
    "coffee-shop": (
        "Near the front window, street traffic soft and blurred outside the glass, a flat white on the small table beside her.",
        "Standing at the café counter, the espresso machine just behind, ceramic cups stacked on the shelf, the barista area softly out of focus.",
        "In a cosy corner of the café, a trailing plant catching the light, a low shelf with books visible to one side.",
        "In the open café floor, chairs and tables surrounding her, a chalkboard menu visible on the far wall.",
    ),
    "city-street": (
        "A wide pedestrian pavement, shopfronts with awnings receding behind her, a couple of parked cars at the kerb.",
        "A narrow side street, worn brick walls on either side, a tiled doorway or iron gate partially visible behind her.",
        "At a busy intersection corner, a pedestrian crossing ahead, taxis and delivery bikes soft and distant behind.",
        "Standing outside a boutique entrance, the lit shop window display visible to one side, the street stretching ahead of her.",
    ),
    "beach": (
        "At the waterline, the ocean stretching flat to the horizon directly behind her, wet sand at her feet.",
        "Higher up the dry beach near the dunes, beach grass or low scrub visible in the far background.",
        "Near a weathered lifeguard tower or beach bar structure, the timber frame partially in frame, the beach spreading beyond.",
        "On a beach boardwalk, wooden planks underfoot, the beach and ocean visible below and behind.",
    ),
    "rooftop": (
        "At the rooftop ledge, hands near the concrete parapet, the city grid stretching to the horizon behind her.",
        "Near a water tower structure on the roof, the tower framing one side of the background.",
        "In a rooftop garden area, terracotta pots and low trailing greenery around her, the skyline behind.",
        "Under a timber pergola with string lights threaded overhead, the city glowing behind her in the distance.",
    ),
    "bedroom": (
        "Standing by the window, curtains half-drawn, natural light falling across from one side, the bed and nightstand soft behind.",
        "In front of the full-length mirror or dresser, the room reflected behind her, jewellery and a perfume bottle on the surface.",
        "Near the bed, the rumpled duvet and stacked pillows in the background, a bedside lamp just visible to one side.",
        "By the open wardrobe or bedroom door, clothes loosely visible inside, a floor lamp casting warm light in the corner.",
    ),
    "bathroom": (
        "Directly facing the bathroom mirror, the room reflected behind her, products lined along the counter.",
        "Side-on near the sink, the tap and basin just visible, a shelf of skincare products in the background.",
        "Against the tiled wall beside the shower screen, steam or condensation present, a towel on the rail behind.",
        "At a vanity with lit mirror surrounds, the mirror frame catching the light, products and a small candle arranged beside.",
    ),
    "mall": (
        "In the centre of a wide mall corridor, storefronts on both sides receding symmetrically behind her.",
        "Just outside a store entrance, the shop window display softly glowing to one side of the frame.",
        "On an upper floor landing near an escalator, the levels of the mall visible below.",
        "In a quiet mall atrium, a large overhead skylight above, marble floors reflecting the ambient light below.",
    ),
    "gym": (
        "In the free weights area, a dumbbell rack and weight plates partially visible in the soft background.",
        "In front of the floor-to-ceiling mirrored gym wall, her reflection and the equipment receding behind her.",
        "Near a cable machine or a flat bench, other equipment visible and blurred in the background.",
        "By the gym entrance or water station, a reception counter or locker bank softly visible behind her.",
    ),
    "park": (
        "On a paved park path, mature trees lining both sides and the path curving away behind her.",
        "On the open grass beside a large tree, the trunk partially at the edge of frame, the lawn and sky beyond.",
        "Near a park bench, the wooden bench just visible behind her, a flower bed or low hedge in the distance.",
        "On a stone bridge over a small pond or near a fountain, the water and surrounding greenery soft behind.",
    ),
    "restaurant": (
        "At a window table, the street or garden just visible through the glass behind her, linen on the table.",
        "In the centre of the dining room, dressed tables and chairs stretching away in the warm background.",
        "At the bar end of the restaurant, bottles and the bar-back shelving softly visible behind her.",
        "In a corner booth, slightly dimmer and more intimate, a candle on the table, the main dining room beyond.",
    ),
    "hotel": (
        "At the floor-to-ceiling window, the city skyline or landscape soft and luminous behind her.",
        "Near the bed, the upholstered headboard and crisp white pillows composing the background.",
        "At the hotel room desk or dressing table, the large wall mirror reflecting the room behind her.",
        "In the carpeted hotel corridor just outside the room, the hallway receding with wall sconces behind her.",
    ),
    "studio": (
        "Against a white seamless paper backdrop, nothing but clean negative space behind and below.",
        "In front of a large north-facing window, soft daylight from one side, plain white walls all around.",
        "Near a bare metal clothing rail, a few garments loosely hanging just to one side of the frame.",
        "In an open studio loft, exposed brick or industrial ceiling details soft in the background behind her.",
    ),
}

# ── Figurantes de fundo — ciclam com variation_idx para dar variedade natural ──
# Índices pares = ambiente vazio; índices ímpares = figuras suaves ao fundo.

BACKGROUND_PEOPLE: tuple[str, ...] = (
    "No other people in frame.",
    "One or two blurred figures in the far background, naturally present in the environment, completely out of focus.",
    "No other people in frame.",
    "A few soft figures visible in the background going about their day, entirely out of focus and incidental.",
)

# ── Horário — a atmosfera é declarada explicitamente ANTES da nota de luz ─────

TIME_ATMO: dict[str, str] = {
    "morning": "Time of day: early morning — the sun has just risen, low on the horizon, the world is quiet and calm, air is cool and still.",
    "afternoon": "Time of day: midday afternoon — the sun is high overhead, full bright daylight, warm temperatures, the day at its most active.",
    "golden-hour": "Time of day: golden hour — the sun is sitting just at the horizon, warm amber and orange tones flood the entire scene, long soft shadows stretching across everything.",
    "night": "Time of day: night — it is fully dark outside, the sky is black or deep navy, zero natural light, all illumination comes from artificial sources in the environment.",
}

# ── Rótulo do local — o cenário é declarado ANTES da descrição da cena ────────

LOCATION_LABEL: dict[str, str] = {
    "coffee-shop": "The location is a coffee shop interior.",
    "city-street": "The location is an outdoor city street.",
    "beach": "The location is a beach outdoors.",
    "rooftop": "The location is a rooftop terrace.",
    "bedroom": "The location is a private bedroom interior.",
    "bathroom": "The location is a bathroom — this is a mirror selfie.",
    "mall": "The location is inside a shopping mall.",
    "gym": "The location is inside a gym.",
    "park": "The location is an outdoor park.",
    "restaurant": "The location is inside a restaurant.",
    "hotel": "The location is a hotel room interior.",
    "studio": "The location is a photography studio.",
}

# ── Luz curta (1 frase por combinação local × horário) ────────────────────────
# 48 combinações escritas à mão. Não há geração — cada uma foi testada.

SHORT_LIGHTING: dict[str, dict[str, str]] = {
    "coffee-shop": {
        "morning": "Soft morning window light from one side, cool and directional.",
        "afternoon": "Warm filtered window light, slight tungsten from overhead pendants.",
        "golden-hour": "Low warm golden-hour sun through the window, amber and tungsten mixing.",
        "night": "Warm tungsten pendant overhead as key, intimate and dim.",
    },
    "city-street": {
        "morning": "Cool early skylight, thin strip of low sun catching one shoulder.",
        "afternoon": "Hard direct sun from front-left, sharp clean shadows.",
        "golden-hour": "Low warm backlight rimming the hair, cool sky-bounce fill on the face.",
        "night": "Warm sodium streetlamp overhead, cool blue twilight, neon accents in background.",
    },
    "beach": {
        "morning": "Cool soft overcast skylight, even and shadowless.",
        "afternoon": "Strong direct sun from above, sand-bounce fill on the shadow side.",
        "golden-hour": "Golden rim light from the horizon, warm reflected light from wet sand below.",
        "night": "Warm market string lights overhead, contained and intimate.",
    },
    "rooftop": {
        "morning": "Cool blue morning sky as ambient fill, thin first sun from one side.",
        "afternoon": "Full direct sun, hard shadows, open sky as fill.",
        "golden-hour": "Low sun backlighting from the city horizon, cool sky-bounce on the face.",
        "night": "City glow as soft ambient, string lights or LED overhead as warm key.",
    },
    "bedroom": {
        "morning": "Soft directional window light, single source, clean and warm.",
        "afternoon": "Window daylight from one side, cool and clean.",
        "golden-hour": "Narrow amber light slanting through the window, high contrast.",
        "night": "Bedside lamp as warm tungsten key, intimate falloff.",
    },
    "bathroom": {
        "morning": "Overhead bathroom light plus soft side window daylight, two temperatures.",
        "afternoon": "Frosted window as soft side key, overhead light supplementing.",
        "golden-hour": "Overhead bathroom light primary, small warm window accent.",
        "night": "Bright overhead bathroom light, contained and even.",
    },
    "mall": {
        "morning": "Soft cool skylight overhead, warm storefront spill from the sides.",
        "afternoon": "Bright skylight from above, warm store lighting from the sides.",
        "golden-hour": "Artificial pendants taking over as skylight fades, warm transitional.",
        "night": "Warm mall pendants overhead, cool-neutral LED from store entries.",
    },
    "gym": {
        "morning": "Cool fluorescent overhead, first daylight from a side window.",
        "afternoon": "Overhead fluorescent, bright and flat, mirror bounce.",
        "golden-hour": "Warm window flood from one side, cool fluorescent overhead.",
        "night": "Overhead fluorescent, even and institutional, no natural fill.",
    },
    "park": {
        "morning": "Cool dappled light through leaf canopy, soft and slightly green-tinted.",
        "afternoon": "Dappled sun patches through canopy, warm and cool mixing.",
        "golden-hour": "Low warm backlight through leaves, sky-bounce fill on the face.",
        "night": "Warm lamp posts creating pools, cool dark gaps between.",
    },
    "restaurant": {
        "morning": "Bright garden-facing window, clean natural key light.",
        "afternoon": "Warm pendant interior, window daylight from one side.",
        "golden-hour": "Golden-hour window table, warm outdoor light mixing with warm interior.",
        "night": "Candlelight key from below, warm restaurant pendants overhead.",
    },
    "hotel": {
        "morning": "Strip of morning daylight through the curtains, warm bedside lamp as fill.",
        "afternoon": "Floor-to-ceiling window, bright clean directional daylight.",
        "golden-hour": "Golden-hour flooding floor-to-ceiling glass, warm and rich.",
        "night": "Floor lamp as warm key, city glow through window as ambient.",
    },
    "studio": {
        "morning": "Large north-facing window, soft cool diffuse daylight from one side.",
        "afternoon": "West window directional daylight, studio walls bouncing a soft fill.",
        "golden-hour": "Golden-hour flooding from the west window, warm and directional.",
        "night": "Two softboxes — primary right at 3 o'clock, fill left at 9 o'clock.",
    },
}


# ── Descrições de pose ────────────────────────────────────────────────────────
# Todas as poses — indexadas pelo id, usadas em montar_prompt_photo_studio.

POSE_MAP: dict[str, str | None] = {
    # ── Feminina em pé ──────────────────────────────────────────
    "plandid": "Natural plandid moment — caught in a real activity, not posing. Interacting with the environment, not looking at the camera.",
    # resolvido dinamicamente em montar_prompt_photo_studio — não usar direto
    "candid": None,
    "handheld": "Handheld selfie — one arm extended straight toward the camera, phone gripped in that hand with fingers curling around it, pointing directly at the lens. Shot from the phone's perspective: the subject fills most of the frame, arm foreshortened toward the viewer. Looking straight into the front-facing lens. Other arm relaxed at the side or resting naturally. Natural slight-below-eye-level selfie angle. Candid and personal.",
    "cute-posed": "Cute posed stance — one hand lightly touching the hair or face, soft and natural.",
    "walking": "Mid-stride, walking naturally, completely off the camera.",
    "mid-turn": "Caught mid-turn, as if just hearing their name called — body still turning, head looking back.",
    "front": "Facing the camera directly, confident and composed.",
    "hip-pop": "Hip pop pose — natural S-curve with one hip shifted out to the side.",
    "triangle": "One hand on hip, natural triangle shape with the arm, relaxed fashion pose.",
    "over-shoulder": "Body turned away from camera, looking back over the shoulder.",
    "facing-away": "Fully facing away from the camera — back to the lens, both hands clasped together behind the back, standing with a subtle relaxed forward tilt, head looking forward or slightly down. Shot from behind. Completely unaware of being photographed.",
    "long-line": "Tall elegant fashion pose, one leg extended forward, long clean line through the body.",
    "lean": "Leaning casually against a wall or surface, relaxed and at ease.",
    # ── Masculina em pé ─────────────────────────────────────────
    "hands-pockets": "Hands in pockets, relaxed and natural, not performing.",
    "crossed-arms": "Arms crossed, confident and settled.",
    # ── Sentada — feminina ──────────────────────────────────────
    "seated-casual": "Seated casually, relaxed and natural.",
    "seated-extended": "Seated with legs extended out in front, relaxed and elongated.",
    "seated-crossed": "Seated cross-legged, comfortable and grounded.",
    "seated-lean": "Seated and leaning slightly forward, relaxed and engaged.",
}


# Ações candid específicas — uma é escolhida deterministicamente por slot de imagem.
# "laughing" está de fora de propósito: é reservada para o preset de expressão rindo.
CANDID_ACTIONS: tuple[str, ...] = (
    "caught mid-sip, raising a drink to their lips",
    "caught mid-step, walking naturally",
    "caught mid-reach, grabbing something nearby",
    "looking down at their phone",
    "caught adjusting their hair",
)


# ── Entradas do motor ─────────────────────────────────────────────────────────


@dataclass(slots=True)
class Roupa:
    """Como o guarda-roupa entra no prompt.

    A precedência é rígida e está no original: imagem (tag) > texto livre >
    preset > "their current outfit".
    """

    #: Imagem de guarda-roupa JÁ RESOLVIDA pela camada de cima (character sheet
    #: quando o preset é 'current', ou a imagem do slot escolhido).
    imagem: str | None = None
    #: Texto livre digitado. Quando presente, DESLIGA a imagem de guarda-roupa.
    wardrobe_text: str = ""
    #: Id do preset já resolvido ('Casual', 'Chic', 'current', …).
    outfit_preset: str = ""
    #: Penteado. Sobrescreve o que aparece em qualquer imagem de referência.
    hairstyle_text: str = ""


@dataclass(slots=True)
class RefDeProduto:
    """Uma imagem de produto (brand deal) e como ela deve aparecer."""

    imagem: str
    mode: ModoDeProduto = "holding"


@dataclass(slots=True)
class Props:
    """Produtos na mão/no corpo + texto livre de prop."""

    refs: list[RefDeProduto] = field(default_factory=list)
    texto: str = ""


@dataclass(slots=True)
class ImagensDaPersona:
    """As imagens de identidade que a persona já tem.

    São as 4 imagens do kit de identidade mais as fotos de perfil extras.
    """

    face: str | None = None
    close_up_1: str | None = None
    close_up_2: str | None = None
    #: Fotos de referência adicionais — a MESMA pessoa em outros ângulos.
    #: Entram no array SEMPRE POR ÚLTIMO (ver :func:`montar_referencias`).
    ref_photos: list[str] = field(default_factory=list)


@dataclass(slots=True)
class TagDeProduto:
    """A tag ``@imageN`` atribuída a um produto, com o modo dele."""

    tag: str
    mode: ModoDeProduto


@dataclass(slots=True)
class TagsDeReferencia:
    """As tags ``@imageN`` que o texto do prompt vai citar inline.

    ``None`` significa "essa referência não existe" — e a tag simplesmente
    some do prompt, sem deixar buraco na numeração.
    """

    face_tag: str | None = None
    wardrobe_tag: str | None = None
    close_up1_tag: str | None = None
    close_up2_tag: str | None = None
    prop_refs: list[TagDeProduto] = field(default_factory=list)


def montar_referencias(
    imagens: ImagensDaPersona,
    roupa: Roupa,
    props: Props,
) -> tuple[list[Referencia], TagsDeReferencia]:
    """Atribui as tags ``@image1..@imageN`` e monta o array de referências.

    **A ORDEM AQUI É O CONTRATO MAIS IMPORTANTE DO MOTOR.** Porte de
    ``PhotoStudio.jsx:777-784`` (atribuição das tags) e ``:804`` (array).

    As tags são atribuídas SEQUENCIALMENTE, sempre nesta ordem:

        rosto → guarda-roupa → close-up 1 → close-up 2 → produtos

    e o array de imagens é montado NA MESMA ORDEM. O provider (fal/OpenAI)
    recebe um array posicional: ``@image2`` significa literalmente "o segundo
    item do array". Se a ordem do array e a ordem das tags divergirem, o texto
    "wearing the complete outfit from @image2" passa a apontar para a foto de
    rosto — e o modelo copia a roupa da referência de rosto, ignorando o
    guarda-roupa. É por isso que a ordem não pode ser "arrumada".

    Pelo mesmo motivo, as ``ref_photos`` extras entram **SEMPRE POR ÚLTIMO**
    (comentário original em ``PhotoStudio.jsx:801-804``): elas não recebem tag
    nenhuma, só dão mais contexto de identidade/corpo ao modelo, e ficam no fim
    justamente para não deslocar as posições que as tags já reservaram.

    Uma referência ausente não gera tag e não ocupa posição — a numeração é
    contígua sobre o que existe de fato.
    """
    referencias: list[Referencia] = []
    tags = TagsDeReferencia()
    tag_idx = 0

    def _proxima_tag() -> str:
        nonlocal tag_idx
        tag_idx += 1
        return f"@image{tag_idx}"

    # PhotoStudio.jsx:766 — texto livre de guarda-roupa desliga a imagem: com os
    # dois presentes o modelo teria duas fontes conflitantes de roupa.
    imagem_de_roupa = None if roupa.wardrobe_text else roupa.imagem

    if imagens.face:
        tags.face_tag = _proxima_tag()
        referencias.append(Referencia(papel="face", tag=tags.face_tag, url=imagens.face))
    if imagem_de_roupa:
        tags.wardrobe_tag = _proxima_tag()
        referencias.append(
            Referencia(papel="wardrobe", tag=tags.wardrobe_tag, url=imagem_de_roupa)
        )
    if imagens.close_up_1:
        tags.close_up1_tag = _proxima_tag()
        referencias.append(
            Referencia(papel="close_up_face", tag=tags.close_up1_tag, url=imagens.close_up_1)
        )
    if imagens.close_up_2:
        tags.close_up2_tag = _proxima_tag()
        referencias.append(
            Referencia(papel="close_up_detail", tag=tags.close_up2_tag, url=imagens.close_up_2)
        )
    for ref in props.refs:
        tag = _proxima_tag()
        tags.prop_refs.append(TagDeProduto(tag=tag, mode=ref.mode))
        referencias.append(Referencia(papel="product", tag=tag, url=ref.imagem))

    # Extras por último, sem tag — só contexto adicional de identidade.
    for url in imagens.ref_photos:
        referencias.append(Referencia(papel="extra", tag="", url=url))

    return referencias, tags


# ── Montador principal ────────────────────────────────────────────────────────
# O Photo Studio SEMPRE passa imagens de referência — isto é uma EDIÇÃO, não uma
# geração. @image1 = identidade (foto principal da influencer), @image2 = roupa
# (character sheet / cartão de guarda-roupa). O prompt é curto e diretivo:
# posicionamento + ação, só. Não redescreva o que já está nas referências.


def montar_prompt_photo_studio(
    persona: Persona,
    *,
    local: str = "coffee-shop",
    hora: str = "afternoon",
    pose: str = "front",
    stance: str = "standing",
    expressao: str = "natural",
    olhar: str = "at-camera",
    roupa: Roupa | None = None,
    props: Props | None = None,
    imagens: ImagensDaPersona | None = None,
    aspect_ratio: str = "9:16",
    vibe: str = "candid",
    variation_idx: int | None = 0,
    rng: random.Random | None = None,
) -> PromptMontado:
    """Porte de ``buildPhotoStudioPrompt``.

    Monta o prompt de uma foto de uma persona que JÁ EXISTE (já tem as imagens
    de identidade). O modelo sabe como a pessoa é; o texto só diz o que fazer
    com ela.

    Args:
        persona: de onde saem gênero, idade aparente e descrição física (esta
            última só é usada quando NÃO há referência de rosto).
        local: id de :data:`LOCATIONS`. Texto livre também funciona — vira
            "The location is {texto}.".
        hora: id de :data:`TIMES`.
        pose: id de :data:`POSE_MAP`. Texto livre também funciona.
        stance: ``"standing"`` ou ``"sitting"``; muda o prefixo de pose e o
            enquadramento.
        expressao: id de :data:`EXPRESSION_MAP`, ou texto livre.
        olhar: ``"at-camera"`` (padrão) ou ``"looking-away"``.
        roupa: guarda-roupa e penteado.
        props: produtos/brand deals.
        imagens: as imagens de identidade da persona.
        aspect_ratio: ``"9:16"`` ou ``"16:9"``.
        vibe: id de :data:`VIBES`; define o tipo de foto e a lente.

            NOTA (bug preservado): na UI original o vibe fica **congelado em
            ``'candid'``**, o que torna os ramos editorial/luxury/street/cozy
            inalcançáveis na prática. O código deles funciona e está portado
            inteiro — só nunca é exercitado pelo caminho normal.
        variation_idx: índice da imagem dentro do lote. É a CHAVE da
            diversidade: o motor é chamado N vezes com ``0..N-1`` e o índice
            varia a cena (``% 4``), a ação candid (``% 5``) e os figurantes
            (``% 4``). ``None`` reproduz o caminho aleatório do original.
        rng: fonte de aleatoriedade explícita, usada apenas quando
            ``variation_idx is None``. Nunca o módulo global ``random``.

    Returns:
        O texto montado + o array de referências na ordem exata do provider.
    """
    roupa = roupa or Roupa()
    props = props or Props()
    imagens = imagens or ImagensDaPersona()
    rng = rng or random.Random()

    referencias, tags = montar_referencias(imagens, roupa, props)

    loc = local or "coffee-shop"
    tod = hora or "afternoon"
    pose_id = pose or "front"
    ratio = aspect_ratio or "9:16"
    is_sitting = stance == "sitting"

    # ── Abertura: tipo de foto ────────────────────────────────────────
    if vibe == "editorial":
        shot_type = "Editorial photo"
    elif vibe == "luxury":
        shot_type = "Luxury lifestyle photo"
    elif vibe == "street":
        shot_type = "Street style photo"
    else:
        shot_type = "Candid iPhone photo"

    # ── Sujeito — tag de referência se houver, texto mínimo como fallback ──
    if tags.face_tag:
        subject = tags.face_tag
    else:
        phys_desc = persona.physical_desc or ""
        age = f"{persona.apparent_age}-year-old" if persona.apparent_age else "young"
        genero_palavra = "man" if persona.gender == "Male" else "woman"
        subject = (
            f"a {age} {genero_palavra}, {phys_desc}" if phys_desc else f"a {age} {genero_palavra}"
        )

    # ── Guarda-roupa — tag se houver, descrição textual caso contrário ──
    gender: Genero | str = persona.gender or "Female"
    outfit_map = OUTFIT_PRESET_MAP_MALE if gender == "Male" else OUTFIT_PRESET_MAP_FEMALE
    has_hairstyle_override = bool(roupa.hairstyle_text.strip())
    if tags.wardrobe_tag:
        # Sem o override de penteado, a referência de roupa também manda no cabelo.
        sufixo_de_cabelo = (
            "" if has_hairstyle_override else ". Match the hairstyle from this reference exactly"
        )
        wardrobe = (
            f"the complete outfit from {tags.wardrobe_tag} — reproduce every item exactly as shown: "
            "all clothing, headwear, and accessories must match the reference. If headwear is present "
            f"in the reference, it must be worn on the head{sufixo_de_cabelo}"
        )
    else:
        preset_texto = ""
        if roupa.outfit_preset and roupa.outfit_preset != "current":
            preset_texto = outfit_map.get(roupa.outfit_preset, "")
        wardrobe = roupa.wardrobe_text.strip() or preset_texto or "their current outfit"

    # ── Linha de close-up (textura de pele + detalhe de rosto) ───────
    # É a trava de pele/face: sem ela o modelo "limpa" a textura da pessoa.
    if tags.close_up1_tag and tags.close_up2_tag:
        close_up_line = (
            f"Match skin texture and facial detail from {tags.close_up1_tag} "
            f"and {tags.close_up2_tag}."
        )
    elif tags.close_up1_tag:
        close_up_line = f"Match skin texture and facial detail from {tags.close_up1_tag}."
    else:
        close_up_line = ""

    # ── Pose — mantida detalhada, é a direção principal do prompt ─────
    candid_action_idx: int | None = None
    if pose_id == "candid":
        # Escolhe UMA ação específica — nunca entregue uma lista de opções ao
        # modelo. Usa variation_idx para que cada slot do lote receba uma ação
        # diferente.
        if variation_idx is not None:
            idx = variation_idx
        else:
            idx = rng.randrange(len(CANDID_ACTIONS))
        candid_action_idx = idx % len(CANDID_ACTIONS)
        action = CANDID_ACTIONS[candid_action_idx]
        base_pose = f"Candid pose — {action}. Unaware of the camera."
    elif POSE_MAP.get(pose_id):
        base_pose = POSE_MAP[pose_id] or ""
    else:
        # Pose customizada digitada pelo usuário.
        base_pose = pose_id.strip() or (POSE_MAP["front"] or "")

    stance_prefix = (
        "Subject is clearly seated. "
        if is_sitting
        else "Subject is standing upright on both feet — not sitting, not crouching. "
    )
    pose_desc = stance_prefix + base_pose

    # ── Expressão (omitida se natural; texto livre se não for preset) ─
    if expressao in EXPRESSION_MAP:
        expression_desc = EXPRESSION_MAP[expressao] or ""
    else:
        texto_expressao = (expressao or "").strip()
        expression_desc = f"Expression: {texto_expressao}." if texto_expressao else ""

    # ── Direção do olhar — suprimida quando as costas estão para a câmera ──
    face_away_pose = pose_id == "facing-away"
    gaze_desc = (
        "Eyes directed off-axis — looking to the side or slightly above the camera, as if unaware of being photographed."
        if (not face_away_pose and olhar == "looking-away")
        else ""
    )

    # ── Produtos / brand deals ────────────────────────────────────────
    prop_desc = _montar_bloco_de_props(
        prop_refs=tags.prop_refs,
        prop_text=props.texto,
        gender=gender,
        wardrobe_tag=tags.wardrobe_tag,
    )

    # ── Cena — um ponto distinto por variação ────────────────────────
    variants = SCENE_VARIANTS.get(loc)
    cena_idx: int | None = None
    if variants:
        if variation_idx is not None:
            cena_idx = variation_idx % len(variants)
        else:
            cena_idx = rng.randrange(len(variants))
        scene = variants[cena_idx]
    else:
        scene = ""

    # ── Figurantes — locais de selfie de espelho são sempre vazios; o resto cicla ──
    people_idx: int | None = None
    if loc in ("bathroom", "bedroom", "hotel"):
        people_line = "No other people in frame."
    else:
        # NOTA: aqui, ao contrário da cena, o original NÃO sorteia quando
        # variationIdx é null — ele cai no índice 0. Preservado.
        people_idx = variation_idx % len(BACKGROUND_PEOPLE) if variation_idx is not None else 0
        people_line = BACKGROUND_PEOPLE[people_idx]

    # ── Rótulo do local + atmosfera do horário + luz ──────────────────
    location_label = LOCATION_LABEL.get(loc) or (
        f"The location is {loc.strip()}." if loc and loc.strip() else ""
    )
    time_atmo = TIME_ATMO.get(tod, "")
    light = SHORT_LIGHTING.get(loc, {}).get(tod, "")

    # ── Câmera + enquadramento ────────────────────────────────────────
    if vibe == "editorial":
        camera_feel = "Eye-level, 50mm lens feel."
    elif vibe == "luxury":
        camera_feel = "Eye-level, 28mm, clinical sharpness."
    else:
        camera_feel = "Eye-level, 24mm, handheld."

    if is_sitting:
        framing = "16:9, waist-up." if ratio == "16:9" else "9:16, 3/4 framing head to mid-thigh."
    else:
        framing = "16:9, waist-up framing." if ratio == "16:9" else "9:16, chest-up framing."

    # ── Override de penteado — ganha de qualquer imagem de referência ──
    hairstyle_desc = (
        f"Hairstyle: {roupa.hairstyle_text.strip()} — apply this hairstyle exactly, "
        "overriding the hairstyle shown in any reference image."
        if has_hairstyle_override
        else ""
    )

    # ── Junta tudo num parágrafo apertado ────────────────────────────
    bloco_de_cena = " ".join(
        parte for parte in (location_label, scene, time_atmo, light) if parte
    )
    partes = (
        f"{shot_type} of {subject}, wearing {wardrobe}.",
        hairstyle_desc,
        close_up_line,
        pose_desc,
        expression_desc,
        gaze_desc,
        prop_desc,
        bloco_de_cena,
        f"{camera_feel} {framing}",
        f"Deep focus, no bokeh, photorealistic. {people_line}",
    )
    texto = " ".join(parte for parte in partes if parte)

    return PromptMontado(
        texto=texto,
        referencias=referencias,
        aspect_ratio=ratio,
        escolhas={
            "local": loc,
            "hora": tod,
            "pose": pose_id,
            "stance": stance,
            "vibe": vibe,
            "expressao": expressao,
            "olhar": olhar,
            "outfit_preset": roupa.outfit_preset,
            "variation_idx": variation_idx,
            "cena_idx": cena_idx,
            "candid_action_idx": candid_action_idx,
            "people_idx": people_idx,
            "tem_tag_de_rosto": tags.face_tag is not None,
            "tem_tag_de_roupa": tags.wardrobe_tag is not None,
            "qtd_props": len(tags.prop_refs),
        },
    )


def _montar_bloco_de_props(
    *,
    prop_refs: list[TagDeProduto],
    prop_text: str,
    gender: str,
    wardrobe_tag: str | None,
) -> str:
    """Bloco de produtos/brand deals. Porte de ``photoStudioPrompt.js:485-513``."""
    if not prop_refs and not prop_text.strip():
        return ""

    if gender == "Male":
        g_pron = "his"
    elif gender == "Female":
        g_pron = "her"
    else:
        # Ramo inalcançável hoje (Persona.gender é Female|Male), portado mesmo assim.
        g_pron = "their"
    hand = f"{g_pron} hand"
    lines: list[str] = []

    # Todas as referências "holding" são agrupadas numa instrução ÚNICA — elas
    # mostram o MESMO produto de ângulos diferentes (original + character sheet).
    # Duas linhas separadas fariam o modelo encher as DUAS mãos, uma com cada
    # versão do mesmo produto.
    holding_refs = [r for r in prop_refs if r.mode == "holding"]
    wearing_refs = [r for r in prop_refs if r.mode == "wearing"]

    if holding_refs:
        tags = " and ".join(r.tag for r in holding_refs)
        plural = "s" if len(holding_refs) > 1 else ""
        lines.append(
            f"Holding the single product shown in {tags} in {hand} — one item, gripped naturally "
            "and clearly visible. It is held in hand only, not worn or placed anywhere on the body. "
            f"Match every product detail, colour, and branding exactly to the reference{plural}."
        )
        lines.append(
            f"{g_pron[0].upper() + g_pron[1:]} other hand is completely empty and relaxed at "
            f"{g_pron} side — absolutely nothing else held, carried, or placed in either hand."
        )
        if wardrobe_tag:
            # Sem este parágrafo o modelo trata o boné do produto e o boné do
            # guarda-roupa como o mesmo item e apaga um dos dois.
            lines.append(
                f"IMPORTANT — this held product is a completely separate item from the outfit in "
                f"{wardrobe_tag}. Any headwear, cap, hat, or accessory that appears in the outfit "
                "reference is part of the outfit and must still be worn on the body exactly as shown. "
                f"The held product in {tags} is an additional prop in the hand — do not conflate it "
                "with outfit items, and do not remove or omit any part of the outfit because of this prop."
            )

    # Cada referência "wearing" tem instrução própria (podem ser itens distintos).
    for ref in wearing_refs:
        lines.append(
            f"Wearing the item from {ref.tag} on the body — it is worn, not held in hand, not carried. "
            "Match every product detail, colour, and branding exactly as shown in the reference."
        )

    if prop_refs:
        lines.append("Product consistency is critical — the item must be identical to the reference.")
    if prop_text.strip():
        lines.append(prop_text.strip() if prop_refs else f"Hands: {prop_text.strip()}.")
    return " ".join(lines)


def parametros_aleatorios(rng: random.Random | None = None) -> dict[str, str]:
    """Porte de ``randomParams`` — sorteia um conjunto de parâmetros da UI.

    NOTA (bugs preservados, ``photoStudioPrompt.js:560-572``):

    1. **O gênero é ignorado.** A função não recebe persona nenhuma e usa
       sempre ``getPoses('Female', …)`` e ``OUTFIT_PRESETS_FEMALE`` — uma
       persona masculina sorteada aqui pode receber pose e preset femininos
       ('Hair Touch', 'Glam'). Portado como está.
    2. **O ``stance`` sorteado é descartado na escolha da pose.** O original
       chama ``getPoses('Female', stance)``, mas ``getPoses`` declara UM único
       parâmetro — em JavaScript o segundo argumento simplesmente evapora. O
       ``stance`` sorteado ainda volta no dicionário e afeta o prefixo de pose
       e o enquadramento; ele só não influencia QUAL pose é escolhida.
    """
    rng = rng or random.Random()
    stance = rng.choice(["standing", "sitting"])
    return {
        "location": rng.choice(LOCATIONS).id,
        "timeOfDay": rng.choice(TIMES).id,
        # NOTA: 'Female' fixo e `stance` descartado — ver docstring.
        "pose": rng.choice(get_poses("Female")).id,
        "vibe": rng.choice(VIBES).id,
        "outfitPreset": rng.choice([o.id for o in OUTFIT_PRESETS_FEMALE]),
        "stance": stance,
        "expression": rng.choice(EXPRESSIONS).id,
    }
