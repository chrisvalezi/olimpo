"""Motor de prompt de VÍDEO — porte do `buildPrompt()` do Content Studio.

Origem: `src/pages/Influencers.jsx` da máquina-de-influencer (componente de 6845
linhas). O motor estava espalhado entre os helpers (`annotateDialogue`,
`parseAdditionalNotes`, `inferAmbientSound`, `CS_CAMERA_PRESETS`) e o montador
principal. A especificação escrita deste motor é `docs/seedance-influencer-guide.md`
— "As 12 Leis" do prompt de vídeo do Seedance 2.0.

O que o motor produz é um prompt em blocos rotulados (FORMAT / SUBJECT /
WARDROBE / PRODUCT / ENVIRONMENT / MOOD / COLOR LOGIC / STYLE / CAMERA /
DELIVERY / DIRECTION / LOGIC RULE), separador `---`, e o bloco ACTION.

⚠️ As tags aqui são `@image_1` (COM underscore), diferente do photo studio que
usa `@image1`. A diferença é do provider (Higgsfield atribui `@image_N` na ordem
em que as refs são passadas para `generate_video`) e NÃO deve ser uniformizada.

⚠️ Este porte é caractere a caractere. Nada foi "melhorado". Onde o original
tem bug ou ambiguidade, o comportamento foi preservado e marcado com `# NOTA:`.

Não portado de propósito (código morto na origem): `VIDEO_TEMPLATES` +
`applyTemplate` (nunca chamados), `DIALOGUE_STARTERS` (nunca usado), o bloco
inalcançável de fal/replicate dentro de `generate()`, e tudo de Higgsfield.
"""

from __future__ import annotations

import math
import random
import re
from dataclasses import dataclass, field

from .tipos import Persona, PromptMontado, Referencia

# ─────────────────────────────────────────────
# Negative prompt para b-roll mudo — impede o modelo de animar a boca quando
# não há diálogo (a Kling especialmente mexe os lábios de qualquer jeito).
NO_TALK_NEGATIVE = (
    "talking, speaking, mouth opening, mouth opening and closing, lip movement, "
    "lip sync, mouthing words, singing, chewing, whispering"
)


def negative_prompt_para(dialogo: str) -> str | None:
    """Negative prompt do vídeo: só existe quando NÃO há diálogo.

    Com diálogo o campo fica ausente (a origem passa `undefined`) — empilhar
    negativas quando há fala causa priming inverso (Lei 7 do guia).
    """
    return None if dialogo.strip() else NO_TALK_NEGATIVE


# ─────────────────────────────────────────────
# Palavras faladas por duração. Passar disso e a fala não cabe no clipe —
# o guia manda cortar palavras primeiro, depois cortar linhas.
VIDEO_MAX_WORDS: dict[int, int] = {
    4: 14, 5: 17, 6: 21, 7: 24, 8: 28, 9: 32,
    10: 35, 11: 38, 12: 42, 13: 45, 14: 48, 15: 52,
}


def max_palavras(duracao: int) -> int:
    """Teto de palavras faladas para a duração (25 quando fora da tabela)."""
    return VIDEO_MAX_WORDS.get(duracao, 25)


# ─────────────────────────────────────────────
# Ambientes (chips) e som ambiente
CS_ENVIRONMENTS: tuple[tuple[str, str], ...] = (
    ("Bedroom", "In a bedroom"),
    ("Bathroom", "In a bathroom"),
    ("Kitchen", "In the kitchen"),
    ("Coffee Shop", "Coffee shop"),
    ("Mall / Store", "At the mall"),
    ("Street", "On the street"),
    ("Gym", "At the gym"),
    ("Studio", "In a studio"),
)

CS_ENV_PRESETS: dict[str, str] = {
    "Bedroom": "in the bedroom",
    "Bathroom": "in the bathroom",
    "Kitchen": "in the kitchen",
    "Coffee Shop": "in a coffee shop",
    "Mall / Store": "in a mall or store",
    "Street": "on the street outside",
    "Gym": "in the gym",
    "Studio": "in a studio",
}

AMBIENT_SOUND: dict[str, str] = {
    "Bedroom": "Quiet room tone — soft, near-silent background.",
    "Bathroom": "Subtle bathroom reverb — clean, minimal background.",
    "Kitchen": "Light kitchen ambience — faint appliance hum, natural room tone.",
    "Coffee Shop": "Ambient coffee shop — low chatter, espresso machine, soft background bustle.",
    "Mall / Store": "Ambient mall — light crowd murmur, distant music.",
    "Street": "Outdoor city ambience — light traffic, natural wind, distant urban activity.",
    "Gym": "Ambient gym — distant weights, low activity, faint background music.",
    "Studio": "Clean studio silence — minimal room tone, no background noise.",
}

#: Texto livre → som ambiente. A ordem importa: a primeira regex que casar vence.
_AMBIENT_REGEX: tuple[tuple[str, str], ...] = (
    (r"restaurant|dining|bistro|brasserie|diner",
     "Ambient restaurant — low dining chatter, cutlery, warm bustle."),
    (r"beach|ocean|sea|shore|surf",
     "Ambient beach — waves, light breeze, distant seagulls."),
    (r"park|garden|nature|forest|woods",
     "Outdoor ambience — birds, light breeze, natural sounds."),
    (r"office|work|corporate|coworking",
     "Quiet office ambience — distant keyboard, low HVAC hum."),
    (r"car|vehicle|driving|road",
     "Ambient car interior — engine hum, road noise."),
    (r"bar|club|lounge|nightclub",
     "Ambient nightlife — low crowd murmur, distant music, gentle bass."),
    (r"pool|spa|resort|hotel",
     "Ambient resort — light water, gentle breeze, relaxed atmosphere."),
    (r"market|bazaar|store|shop",
     "Ambient market — light crowd, distant chatter."),
    (r"rooftop|terrace|balcony",
     "Outdoor rooftop ambience — light wind, distant city sounds."),
    (r"airport|station|transit",
     "Ambient transit sounds — light crowd, distant announcements."),
)


def infer_ambient_sound(env_key: str, environment: str) -> str:
    """Som ambiente do bloco DELIVERY quando o vídeo é mudo.

    O chip de ambiente (`env_key`) tem prioridade; se não houver, a descrição
    livre passa pelas regexes acima. Sem casar nada, cai no genérico.
    """
    if env_key and env_key in AMBIENT_SOUND:
        return AMBIENT_SOUND[env_key]
    e = (environment or env_key or "").lower()
    for padrao, som in _AMBIENT_REGEX:
        if re.search(padrao, e):
            return som
    return "Natural ambient sound — location-appropriate background audio."


# ─────────────────────────────────────────────
# Seletor de câmera unificado — UMA escolha define ao mesmo tempo o
# enquadramento E o movimento (não existe passo separado de movimento, para que
# o enquadramento e o movimento nunca se contradigam). Dois grupos:
#   ig   — planos cotidianos de Instagram (jeito de celular, simples)
#   cine — movimentos de câmera cinematográficos
# Cada preset carrega: enquadramento/lente/estilo (o visual) + move (o movimento).


@dataclass(frozen=True, slots=True)
class CameraPreset:
    """Um preset de câmera: o visual e o movimento amarrados juntos."""

    id: str
    group: str  # 'ig' | 'cine'
    label: str
    framing: str
    lens: str
    style: str
    move: str
    handheld: bool = False
    talking_head: bool = False


CS_CAMERA_PRESETS: tuple[CameraPreset, ...] = (
    # ── Comuns (Instagram) ──
    CameraPreset(
        id="handheld", group="ig", label="Handheld selfie", framing="MCU", lens="24mm",
        handheld=True,
        style="Self-filmed selfie POV — the subject holds the phone at arm's length, "
              "24mm wide front-camera look, natural front-cam color, everything in focus. "
              "NO shallow DOF, NO bokeh, NO blur.",
        move="handheld selfie motion — natural walk-pace bob and drift, never fully static",
    ),
    CameraPreset(
        id="tripod", group="ig", label="Tripod (still)", framing="MS", lens="28mm",
        style="Phone on a tripod, 28mm medium-wide framing, clean natural color, "
              "everything in focus. NO shallow DOF, NO bokeh, NO blur.",
        move="locked static frame, only the subject moves",
    ),
    CameraPreset(
        id="direct", group="ig", label="Direct to camera", framing="MS", lens="35mm",
        style="Subject faces the camera directly with eye contact into the lens, medium "
              "shot from the waist up, 35mm, clean natural color, everything in focus. "
              "NO shallow DOF, NO bokeh, NO blur.",
        move="locked static frame, minimal camera motion",
    ),
    CameraPreset(
        id="talking-head", group="ig", label="Talking head", framing="MS", lens="50mm",
        talking_head=True,
        style="50mm portrait lens, medium shot framed from mid-chest up, subject seated "
              "with hands resting on a desk in the foreground, even soft studio lighting, "
              "everything in focus. NO shallow DOF, NO bokeh, NO blur, clean neutral color.",
        move="locked static frame, only the subject moves",
    ),
    # ── Cinematográficos ──
    CameraPreset(
        id="push-in", group="cine", label="Push in", framing="MCU", lens="35mm",
        style="Cinematic framing, subject centered, natural cinematic color and gentle depth.",
        move="slow smooth dolly push-in, gradually moving closer to the subject",
    ),
    CameraPreset(
        id="pull-out", group="cine", label="Pull out", framing="MS", lens="35mm",
        style="Cinematic framing that reveals more of the scene, natural cinematic color.",
        move="slow smooth dolly pull-out, gradually moving away from the subject to reveal the scene",
    ),
    CameraPreset(
        id="orbit", group="cine", label="Orbit", framing="MS", lens="35mm",
        style="Cinematic framing, subject centered, natural cinematic color and depth.",
        move="smooth orbital arc slowly circling around the subject",
    ),
    CameraPreset(
        id="tracking", group="cine", label="Tracking", framing="MS", lens="35mm",
        style="Cinematic tracking framing, subject in motion, natural cinematic color.",
        move="tracking shot, the camera moves alongside and follows the subject",
    ),
    CameraPreset(
        id="crane-up", group="cine", label="Crane up", framing="WS", lens="28mm",
        style="Cinematic wide framing, subject within the environment, natural cinematic color.",
        move="crane/jib rising upward while keeping the subject framed",
    ),
    CameraPreset(
        id="crane-down", group="cine", label="Crane down", framing="WS", lens="28mm",
        style="Cinematic wide framing, natural cinematic color.",
        move="crane/jib descending downward while keeping the subject framed",
    ),
    CameraPreset(
        id="pan", group="cine", label="Pan", framing="MS", lens="35mm",
        style="Cinematic framing, natural cinematic color.",
        move="smooth horizontal pan following the action",
    ),
    CameraPreset(
        id="tilt", group="cine", label="Tilt", framing="MS", lens="35mm",
        style="Cinematic framing, natural cinematic color.",
        move="smooth vertical tilt revealing the subject from head to toe",
    ),
    CameraPreset(
        id="zoom-in", group="cine", label="Zoom in", framing="MCU", lens="50mm",
        style="Cinematic framing, subject centered, natural cinematic color.",
        move="slow optical zoom in toward the subject",
    ),
    CameraPreset(
        id="zoom-out", group="cine", label="Zoom out", framing="MS", lens="35mm",
        style="Cinematic framing, natural cinematic color.",
        move="slow optical zoom out from the subject",
    ),
    CameraPreset(
        id="overhead", group="cine", label="Overhead", framing="overhead", lens="35mm",
        style="Overhead bird's-eye angle looking straight down at the subject, 35mm "
              "equivalent, everything in focus, clean and graphic. NO shallow DOF, NO "
              "bokeh, NO blur.",
        move="slow overhead move drifting above the subject",
    ),
    CameraPreset(
        id="fpv", group="cine", label="FPV / drone", framing="WS", lens="24mm",
        style="Dynamic cinematic FPV framing gliding through the scene, natural cinematic color.",
        move="dynamic flowing FPV drone-style move gliding through the scene",
    ),
    CameraPreset(
        id="whip-pan", group="cine", label="Whip pan", framing="MS", lens="35mm",
        style="Cinematic framing, natural cinematic color.",
        move="fast whip pan transition with motion blur",
    ),
)

CS_CAMERA_BY_ID: dict[str, CameraPreset] = {c.id: c for c in CS_CAMERA_PRESETS}

#: Valores antigos de câmera salvos (rótulos velhos) → ids novos, para que
#: configurações já gravadas continuem carregando.
CAMERA_LEGACY: dict[str, str] = {
    "Handheld": "handheld",
    "Tripod": "tripod",
    "Talking Head": "talking-head",
    "Direct to camera": "direct",
    "Wide": "tripod",
    "Overhead": "overhead",
    "Slow push-in": "push-in",
    "Close-up": "zoom-in",
}


def norm_camera(v: str) -> str:
    """Normaliza um valor de câmera (id novo, rótulo antigo ou lixo) para um id."""
    if v in CS_CAMERA_BY_ID:
        return v
    return CAMERA_LEGACY.get(v, "handheld")


CS_VIBES: tuple[str, ...] = (
    "Natural", "Energetic", "Luxury", "Playful", "Tutorial", "Dramatic", "Cozy", "Confident",
)


# ─────────────────────────────────────────────
# Vozes


@dataclass(frozen=True, slots=True)
class VoicePreset:
    """Um preset de voz — o texto de `voice` é o que entra em DELIVERY."""

    id: str
    label: str
    sub: str
    voice: str


VOICE_PRESETS: dict[str, tuple[VoicePreset, ...]] = {
    "female": (
        VoicePreset("f-21-american-bright", "21-year-old American", "Bright · fast · TikTok-native",
                    "21-year-old American woman accent, bright and energetic, fast-paced and upbeat."),
        VoicePreset("f-28-american-warm", "28-year-old American", "Warm · confident · grounded",
                    "28-year-old American woman accent, warm and confident, clear and grounded."),
        VoicePreset("f-35-american-calm", "35-year-old American", "Calm · measured · trustworthy",
                    "35-year-old American woman accent, calm and measured, slow and soothing."),
        VoicePreset("f-british-polished", "British — polished", "Refined · elegant · clear",
                    "Polished British woman accent, refined and elegant, clear and measured."),
        VoicePreset("f-british-playful", "British — playful", "Bright · warm · charming",
                    "Playful British woman accent, bright and warm, light and charming."),
        VoicePreset("f-deep-japanese", "Japanese — soft", "Soft · gentle · precise",
                    "Soft Japanese woman accent, gentle and precise, calm and measured."),
    ),
    "male": (
        VoicePreset("m-22-american-energy", "22-year-old American", "Energetic · direct · natural",
                    "22-year-old American man accent, energetic and direct, upbeat and natural."),
        VoicePreset("m-30-american-deep", "30-year-old American", "Deep · confident · authoritative",
                    "30-year-old American man accent, deep and confident, authoritative and measured."),
        VoicePreset("m-38-american-warm", "38-year-old American", "Warm · relaxed · approachable",
                    "38-year-old American man accent, warm and relaxed, approachable and conversational."),
        VoicePreset("m-british-sharp", "British — sharp", "Refined · precise · authoritative",
                    "Sharp British man accent, refined and precise, clear and authoritative."),
        VoicePreset("m-british-story", "British — storyteller", "Warm · engaging · unhurried",
                    "Warm British man storytelling accent, engaging and unhurried, naturally charismatic."),
    ),
}

#: A busca por id varre feminino + masculino na ordem (como o `allPresets` da
#: origem) — o preset não é filtrado pelo gênero da persona.
_TODOS_OS_PRESETS_DE_VOZ: tuple[VoicePreset, ...] = (
    *VOICE_PRESETS["female"], *VOICE_PRESETS["male"],
)

VIBE_META: dict[str, str] = {
    "Natural": "Real and unfiltered — like talking to a friend.",
    "Energetic": "Fast, forward, high energy the whole way through.",
    "Luxury": "Slow and deliberate — every word carries weight.",
    "Playful": "Light and bouncy — makes people smile.",
    "Tutorial": "Clear and confident — step-by-step, no fluff.",
    "Dramatic": "Quiet at first, builds to a strong landing.",
    "Cozy": "Soft and intimate — like a one-on-one chat.",
    "Confident": "Grounded and sure — zero doubt, pure presence.",
}


# ─────────────────────────────────────────────
# Grade de cor por ambiente — chaveada no chip (env_key), para que texto livre
# ainda receba uma grade (o padrão neutro).
COLOR_MAP: dict[str, str] = {
    "Bedroom": "Warm soft palette, amber tones, clean skin highlight.",
    "Bathroom": "Neutral clean tones, slight coolness, face is brightest element.",
    "Kitchen": "Fresh neutral palette, clean whites, warm skin.",
    "Coffee Shop": "Warm caramel tones, soft and inviting.",
    "Mall / Store": "Bright clean palette, commercial whites, product pops.",
    "Street": "Golden-warm with cool sky fill, high-contrast.",
    "Gym": "High contrast, cool-neutral, energetic.",
    "Studio": "Clean neutral, controlled, product-forward.",
}

#: Horários aceitos no sufixo de ENVIRONMENT. Qualquer outro valor vira sufixo vazio.
TIME_OF_DAY_LABELS: dict[str, str] = {
    "morning": "morning",
    "afternoon": "afternoon",
    "golden hour": "golden hour",
    "night": "night",
}


def _mood_map(she: str, She: str) -> dict[str, str]:
    """Arcos de humor para vídeo COM fala — descritos como entrega/delivery."""
    return {
        "Natural":
            "Delivery is unhurried and conversational — pauses land where they would in "
            "real speech, never performed. Micro-expressions are small and honest: a slight "
            "mouth pull before a punchline, a brief brow soften on the reveal. Gestures are "
            "loose and incidental, not choreographed. Eye contact with the camera is easy, "
            "breaks naturally, comes back. Energy stays flat and warm across the whole clip.",
        "Energetic":
            f"Delivery is fast and forward — {she} pushes through lines with minimal pause, "
            "pace never drops. Eyebrows lift on emphasis words. Gestures are sharp and "
            "frequent: quick hand flicks, small head tilts that land on key beats. Body is "
            "slightly forward the whole time. Expression resets fast between lines — no "
            "lingering. Clip ends on full energy, nothing winds down.",
        "Luxury":
            "Delivery is slow and deliberate — every word has weight, pauses are long and "
            "intentional. Micro-expressions are subtle: a slow smirk rather than a smile, "
            "heavy-lidded confidence, no wide eyes. Gestures are minimal and controlled — "
            f"small wrist movements, nothing above the shoulder. {She} never rushes. Eye "
            "contact is held longer than comfortable, then released slowly.",
        "Playful":
            "Delivery has rhythm and bounce — slight sing-song cadence, small upticks at the "
            "ends of phrases. Quick genuine smiles that reach the eyes, eyebrow raises on key "
            "words. Light shoulder movement on emphasis. Pauses are short and teasing, like "
            f"{she}'s about to say something and makes you wait one beat. Gestures are small "
            "and spirited — pointing, light wrist flick.",
        "Tutorial":
            "Delivery is clear and even-paced — deliberate without being slow, every word "
            "lands cleanly. Direct sustained eye contact with the camera, nods on key points. "
            f"Gestures are demonstrative: {she} points at or tilts the product on relevant "
            "beats, uses open-palm gestures when explaining. Expression stays calm and assured "
            "throughout. No uptalk — every sentence lands flat and final.",
        "Dramatic":
            "Delivery is slow-building — early lines are quiet and measured, pace tightens "
            "toward the end. Strategic pauses that hold one beat longer than expected. Eyes "
            "stay on camera longer than normal, expression shifts are controlled and "
            "deliberate. Gestures are restrained — hands stay low, movement is minimal until "
            "the payoff line. The reveal lands with full stillness.",
        "Cozy":
            f"Delivery is soft and low-energy — {she} sounds like {she}'s talking to one "
            "person, not a camera. Slight smile throughout, never fades completely. Minimal "
            "gestures, hands stay relaxed. Pauses feel comfortable, not empty. Eye contact is "
            "warm and personal. Pace is slow enough that every word registers. Expression "
            "stays gentle from first frame to last.",
        "Confident":
            "Delivery is even and controlled — no uptalk, no filler energy, every line lands "
            "flat and sure. Holds eye contact with the camera without excess blinking. "
            "Gestures are purposeful and limited — one clean move per beat, nothing nervous "
            "or decorative. Expression is neutral-warm: not performing happiness, just "
            "completely at ease. Pace stays consistent, never rushes the reveal.",
    }


def _mood_map_silent(She: str) -> dict[str, str]:
    """Humores de b-roll MUDO — a mesma energia descrita só como LINGUAGEM
    CORPORAL, com zero vocabulário de fala ("delivery/lines/words"). Os humores
    falantes acima, se usados sem diálogo, fazem o modelo animar a boca mesmo
    assim — por isso existem dois conjuntos.
    """
    return {
        "Natural":
            "Relaxed, unhurried presence — easy honest micro-expressions, a soft natural "
            "look, loose incidental body language. Calm warm energy throughout. Mouth stays "
            "closed and still.",
        "Energetic":
            "High forward energy — lively posture, quick small head tilts and light hand "
            f"movement, bright alert eyes. {She} stays slightly forward, buzzing with energy "
            "but completely silent. Mouth stays closed, no lip movement.",
        "Luxury":
            "Slow, deliberate, composed — minimal controlled movement, heavy-lidded "
            "confidence, a slow subtle smirk. Effortless poise, never rushed. Mouth closed "
            "and relaxed.",
        "Playful":
            "Light and spirited — small bouncy movements, quick genuine smiles that reach the "
            "eyes, playful glances to camera. Lively but silent. Mouth stays closed.",
        "Tutorial":
            "Calm and assured — steady posture, direct easy gaze to camera, small "
            "demonstrative hand movements. Composed and clear. Mouth closed, no talking.",
        "Dramatic":
            "Slow-building intensity — controlled stillness, measured expression shifts, eyes "
            "holding the camera. Restrained movement, quiet power. Mouth closed and still.",
        "Cozy":
            "Soft low-energy warmth — a gentle slight smile, relaxed hands, comfortable calm "
            "presence. Intimate and easy. Mouth stays closed.",
        "Confident":
            "Even controlled poise — relaxed sure posture, steady gaze, one clean purposeful "
            "movement at a time. At ease, nothing nervous. Mouth closed, no talking.",
    }


# ─────────────────────────────────────────────
# Utilidades de paridade com JavaScript


def _js_round(x: float) -> int:
    """`Math.round` do JS: meio sempre para cima.

    O `round()` do Python arredonda para o par (round(2.5) == 2), o que produz
    timestamps e durações de shot diferentes da origem. Não trocar por `round`.
    """
    return math.floor(x + 0.5)


def _cap(s: str) -> str:
    """`s.charAt(0).toUpperCase() + s.slice(1)` — seguro em string vazia."""
    return s[:1].upper() + s[1:]


def _pad2(n: int) -> str:
    """`String(n).padStart(2, '0')`."""
    return str(n).rjust(2, "0")


# ─────────────────────────────────────────────
# Notas adicionais → beats de ação (injetados no bloco ACTION) e notas de
# direção (seção DIRECTION)


@dataclass(slots=True)
class ActionBeat:
    """Um beat de ação extraído das notas livres.

    `fired` é mutável de propósito: `annotate_dialogue` marca o beat quando o
    injeta na fala, e o montador só anexa aos shots o que sobrou.
    """

    text: str
    timestamp: str
    fraction: float
    fired: bool = False


#: Verbos que transformam uma frase das notas em beat de ação (o resto vira
#: DIRECTION). A ordem das alternativas é a do original.
_ACTION_VERBS = re.compile(
    r"\b(pick|picks up|hold|holds|turn|turns|spin|spins|lean|leans|look|looks at|walk|"
    r"walks|sit|sits|stand|stands|laugh|laughs|smile|smiles|nod|nods|wave|waves|point|"
    r"points|reach|reaches|touch|touches|grab|grabs|show|shows|open|opens|close|closes|"
    r"tilt|tilts|adjust|adjusts|pull|pulls|lift|lifts|flip|flips|drop|drops|step|steps|"
    r"crouch|crouches|glance|glances|wink|winks|pause|pauses|freeze|freezes|stop|stops)\b",
    re.IGNORECASE,
)


def parse_additional_notes(notes: str, duration_secs: int) -> tuple[list[ActionBeat], str]:
    """Quebra as notas livres em beats de ação e notas de direção.

    Frase com verbo de ação (ou sujeito "she/he/they" + verbo, ou "pause") vira
    um beat com timestamp; qualquer outra frase vira linha de DIRECTION.

    O timestamp sai de: "at start"/"at the beginning"/"first" → 0:01;
    "at end"/"last"/"final"/"before it cuts" → duração − 2s (mínimo 1);
    "at N s(econds)" → 0:NN; senão, o meio do clipe.

    Devolve `(beats, notas_de_direcao)`.
    """
    if not notes.strip():
        return [], ""

    sentences = [
        s.strip()
        for s in re.split(r"(?<=[.!?])\s+|[\n]+", notes.strip())
        if s.strip()
    ]

    action_beats: list[ActionBeat] = []
    direction_lines: list[str] = []

    for s in sentences:
        is_action_beat = bool(
            _ACTION_VERBS.search(s)
            or re.search(r"\b(she|he|they)\s+\w+", s, re.IGNORECASE)
            or re.search(r"\bpause\b", s, re.IGNORECASE)
        )

        if is_action_beat:
            # Posição como fração 0–1 do vídeo/diálogo
            fraction = 0.5  # padrão: meio do diálogo
            ts = f"0:{_pad2(_js_round(duration_secs * 0.5))}"

            if re.search(r"\bat (the )?start\b|from the start|at the beginning|^first\b", s, re.IGNORECASE):
                fraction = 0.0
                ts = "0:01"
            elif re.search(r"\bat (the )?end\b|last|final|before (it )?cuts", s, re.IGNORECASE):
                fraction = 1.0
                ts = f"0:{_pad2(max(duration_secs - 2, 1))}"
            else:
                m = re.search(r"at\s+(\d+)\s*s(?:ec(?:ond)?s?)?", s, re.IGNORECASE)
                if m:
                    sec = int(m.group(1))
                    fraction = min(sec / duration_secs, 1)
                    ts = f"0:{_pad2(sec)}"

            text = s
            text = re.sub(r"at (the )?(start|end|beginning)\b[,]?", "", text, flags=re.IGNORECASE)
            text = re.sub(r"from the start\b[,]?", "", text, flags=re.IGNORECASE)
            text = re.sub(r"at \d+\s*s(ec(ond)?s?)?\b[,]?", "", text, flags=re.IGNORECASE)
            # Sem flag global no original: só o prefixo, uma vez (já é ancorado).
            text = re.sub(
                r"^(make sure|ensure|have her|have him|i want|please|note[:]?)\s+",
                "", text, count=1, flags=re.IGNORECASE,
            )
            text = text.strip()
            text = re.sub(r"[.!?]+$", "", text)

            if not re.match(r"^(she|he|they)\b", text, re.IGNORECASE):
                # NOTA: "She" é literal aqui — `parseAdditionalNotes` não recebe
                # gênero na origem, então beats de persona masculina também saem
                # com "She". Bug preservado.
                text = f"She {text[:1].lower()}{text[1:]}"
            text = _cap(text)

            action_beats.append(ActionBeat(text=text, timestamp=ts, fraction=fraction))
        else:
            direction_lines.append(re.sub(r"[.!?]+$", "", s).strip())

    # Ordena os beats por fração para dispararem em ordem cronológica
    action_beats.sort(key=lambda b: b.fraction)

    return action_beats, ". ".join(direction_lines).strip()


# ─────────────────────────────────────────────
# Anotação de diálogo — lê o roteiro cru e o envolve em notação de performance
# seguindo o guia: emoção antes da linha, pausas [beat]/[breath], inclinação do
# produto, micro-expressões (máx. 2), CTA que cai como dica de amigo e não como
# pitch.

#: Pivô de contraste: quebra a frase e ganha [beat.] próprio.
_PIVOT_SPLIT = re.compile(r",\s+(?=(?:but|however|though|yet)\s)", re.IGNORECASE)


def annotate_dialogue(
    raw_text: str,
    product_tag: str | None,
    duration_secs: int,
    is_handheld: bool = False,
    wear_mode: bool = False,
    action_beats: list[ActionBeat] | None = None,
    she: str = "she",
    her: str = "her",
    his: str = "her",  # his = possessivo ('her'/'his')
) -> str:
    """Envolve o diálogo cru com notação de performance.

    `product_tag` é a string `@image_N` do produto (ex.: '@image_5') ou None.
    `is_handheld` é True quando o sujeito se filma andando.
    `wear_mode` é True quando o produto é VESTIDO (não segurado).
    """
    if action_beats is None:
        action_beats = []
    if not raw_text.strip():
        return ""

    # Quebra em orações:
    # 1. No fim de frase (.  !  ?) seguido de espaço
    # 2. Depois, nas quebras de pivô por vírgula: ", but " / ", however " /
    #    ", though " / ", yet "
    sentences: list[str] = []
    for parte in re.split(r"(?<=[.!?])\s+", raw_text.strip()):
        sentences.extend(_PIVOT_SPLIT.split(parte))
    sentences = [s.strip() for s in sentences if s.strip()]

    micro_left = 1 if duration_secs <= 6 else 2

    def use_micro(expr: str) -> str:
        """Micro-expressões são finitas: 1 em clipe curto, 2 no resto."""
        nonlocal micro_left
        if not micro_left:
            return ""
        micro_left -= 1
        return expr

    prod = product_tag or None
    out: list[str] = []

    # Modo vestido: rotaciona gestos naturais de interação para que a cada 2ª
    # frase haja um beat físico com o produto — mantém ele visível sem parecer
    # encenado. Todos os gestos são agnósticos de parte do corpo, para
    # funcionarem com qualquer vestível (boné, pulseira, colar, camisa, tênis,
    # brincos, óculos de sol, etc.)
    She = _cap(she)
    Her = _cap(her)
    worn_gestures: list[str] = [
        f"{She} touches {prod} briefly — natural, not staged.",
        f"{Her} hand goes to {prod} for a beat, then back to natural position.",
        f"{She} glances toward {prod}, then back to lens — draws attention to it without words.",
        f"{She} adjusts {prod} slightly — natural reflex, eyes stay on camera.",
        f"{She} angles {his} body so {prod} is clearly visible, then settles back.",
    ] if prod else []

    worn_gesture_idx = 0
    worn_gesture_counter = 0
    worn_gestures_used = 0
    worn_gesture_max = 1 if duration_secs <= 6 else 2

    def maybe_worn_gesture() -> str | None:
        nonlocal worn_gesture_idx, worn_gesture_counter, worn_gestures_used
        if not wear_mode or not prod:
            return None
        if worn_gestures_used >= worn_gesture_max:
            return None
        worn_gesture_counter += 1
        if worn_gesture_counter % 3 != 0:
            return None
        g = worn_gestures[worn_gesture_idx % len(worn_gestures)]
        worn_gesture_idx += 1
        worn_gestures_used += 1
        return g

    # Estado corporal de abertura — já em pose, produto vestido ou na mão
    if is_handheld:
        if prod:
            if wear_mode:
                out.append(
                    f"@image_1 is self-filming — arm extended toward camera, {prod} worn. "
                    f"{_cap(she)} is already walking. Camera bobs with {his} steps from 0:00. "
                    f"One breath before {she} speaks."
                )
            else:
                out.append(
                    f"@image_1 is self-filming — arm extended toward camera, {prod} in the "
                    f"other hand. {_cap(she)} is already walking. Camera bobs with {his} steps "
                    f"from 0:00. One breath before {she} speaks."
                )
        else:
            out.append(
                f"@image_1 is self-filming — arm extended toward camera, already walking. "
                f"Camera bobs with {his} steps from 0:00. One breath before {she} speaks."
            )
    else:
        if prod:
            if wear_mode:
                out.append(
                    f"@image_1 faces camera, {prod} worn from 0:00. {_cap(she)} touches or "
                    f"adjusts {prod} once early — natural reflex that draws attention to it. "
                    f"One breath before {she} starts."
                )
            else:
                out.append(
                    f"@image_1 faces camera, {prod} in hand from 0:00. One breath before "
                    f"{she} starts."
                )
        else:
            out.append("@image_1 faces camera. Eyes on lens. One breath.")

    # Dispara os beats "at start" antes da primeira frase
    for beat in action_beats:
        if not beat.fired and beat.fraction == 0:
            beat.fired = True
            out.append(f"At {beat.timestamp} — {beat.text}.")

    for i, raw in enumerate(sentences):
        s = raw.strip()
        l = s.lower()
        is_last = i == len(sentences) - 1
        has_pivot = bool(re.match(r"^(but|however|though|yet)\s", l, re.IGNORECASE))
        has_actually = bool(re.search(r"\bactually\b", l))
        has_ellipsis = "..." in s
        ends_exclaim = s.endswith("!")
        is_cta = is_last and bool(re.match(
            r"^(so if|if you|grab|go get|buy|check out|order|pick up|get yours)\b",
            l, re.IGNORECASE,
        ))
        # NOTA: sem flag de case-insensitive no original (o teste roda sobre `l`,
        # já minúsculo). E o `\b` depois de "99%" exige caractere de palavra
        # logo após o '%', então "99% of" NÃO casa. Comportamento preservado.
        is_negative = bool(re.search(
            r"\b(not a fan|taste like|tastes like|99%|don'?t like|dislike|awful|terrible|"
            r"cough syrup|worst|gross)\b",
            l,
        ))

        # CTA — sempre a última, cai leve
        if is_cta:
            out.append(f'"{s}" Lands easy — like a tip from a friend, not a pitch.')
            continue

        # Pivô + reticências
        if has_pivot and has_ellipsis:
            worn_gesture_counter += 1
            if prod:
                out.append(
                    f"She touches {prod} and angles so it's clearly visible to camera."
                    if wear_mode else
                    f"She tilts {prod} toward camera."
                )
            out.append(f'"{s}" Energy up — genuine.' if ends_exclaim else f'"{s}" [beat.]')
            continue

        # Pivô puro ("but...", "however...", "actually...") sem reticências
        if has_pivot or (has_actually and not is_negative):
            worn_gesture_counter += 1  # mantém o contador em sincronia
            if prod:
                out.append(
                    f"She touches {prod}, angles so it's visible. \"{s}\" [beat.]"
                    if wear_mode else
                    f"She tilts {prod} toward camera. \"{s}\" [beat.]"
                )
            else:
                out.append(f'She leans forward slightly. "{s}" [beat.]')
            continue

        # Reticências no meio da frase, sem pivô: "this thing is... incredible"
        if has_ellipsis:
            partes = re.split(r"\.\.\.", s)
            before = partes[0]
            after = partes[1] if len(partes) > 1 else None
            g = maybe_worn_gesture()
            if g:
                out.append(g)
            out.append(f'"{before.strip()}..."')
            out.append("[micro-pause.]")
            after_trimmed = after.strip() if after is not None else None
            if after_trimmed:
                out.append(
                    f'"{after_trimmed}" Energy up — genuine.'
                    if re.search(r"[!]$", after_trimmed) else
                    f'"{after_trimmed}" [beat.]'
                )
            continue

        # Primeira linha — abertura de gancho
        if i == 0:
            m = use_micro(f" Corners of {his} mouth pull back — genuine, not performed.")
            out.append(f'"{s}" [beat.]{m}')
            continue

        # Linha negativa / de descarte — reação honesta discreta
        if is_negative:
            m = use_micro(" Slight face — honest, not dramatic.")
            g = maybe_worn_gesture()
            if g:
                out.append(g)
            out.append(f'"{s}"{m} [beat.]')
            continue

        # Exclamação — energia sobe, genuína
        if ends_exclaim:
            g = maybe_worn_gesture()
            if g:
                out.append(g)
            out.append(f'"{s}" Energy up — genuine, not performed.')
            continue

        # Padrão — afirmação com beat conversacional
        g = maybe_worn_gesture()
        if g:
            out.append(g)
        out.append(f'"{s}" [beat.]')

        # Injeta os beats de ação que caem na posição desta frase ou antes
        if action_beats:
            sentence_fraction = (i + 1) / len(sentences)
            for beat in action_beats:
                if not beat.fired and beat.fraction <= sentence_fraction:
                    beat.fired = True
                    out.append(f"At {beat.timestamp} — {beat.text}.")

    # Dispara os beats que sobraram (ex.: beats "at end" ou o caso sem diálogo)
    for beat in action_beats:
        if not beat.fired:
            beat.fired = True
            out.append(f"At {beat.timestamp} — {beat.text}.")

    # A conversa termina naturalmente — nada de [beat.] pendurado depois da
    # última palavra falada
    if out and out[-1].endswith("[beat.]"):
        out[-1] = out[-1][:-7].rstrip()

    return " ".join(out)


# ─────────────────────────────────────────────
# Referências do vídeo


@dataclass(slots=True)
class ReferenciasVideo:
    """As imagens que entram no array de referências, por papel.

    A ORDEM em que viram `@image_N` é o contrato com o provider (Higgsfield
    atribui `@image_N` na ordem em que as refs são passadas para
    `generate_video`): identidade → guarda-roupa|charsheet → closeup1 →
    closeup2 → home → produtos 1-3.

    Com `start_frame`, tudo colapsa: `@image_1` é o start frame (identidade e
    roupa já assadas nele) e os produtos começam em `@image_2`.
    """

    main_image: str = ""
    #: Look de guarda-roupa selecionado. Quando existe, substitui o charsheet.
    wardrobe_image: str = ""
    character_sheet_image: str = ""
    close_up_image1: str = ""
    close_up_image2: str = ""
    home_image: str = ""
    product1: str = ""
    product2: str = ""
    product3: str = ""
    start_frame: str = ""


def _montar_tag_map(
    refs: ReferenciasVideo, usar_start_frame: bool
) -> tuple[dict[str, str], list[Referencia]]:
    """Mapa papel → tag `@image_N`, mais a lista ordenada de referências.

    A ordem É o contrato — quem vem primeiro é a identidade.
    """
    prod_imgs = [
        (papel, url)
        for papel, url in (
            ("product1", refs.product1),
            ("product2", refs.product2),
            ("product3", refs.product3),
        )
        if url
    ]

    tag_map: dict[str, str] = {}
    ordenadas: list[tuple[str, str]] = []

    if usar_start_frame:
        # Modo start frame: @image_1 = start frame (identidade + roupa assadas
        # dentro dele), @image_2+ = produtos
        tag_map["identity"] = "@image_1"
        ordenadas.append(("identity", refs.start_frame))
        for i, (papel, url) in enumerate(prod_imgs):
            tag_map[papel] = f"@image_{i + 2}"
            ordenadas.append((papel, url))
    else:
        inf_imgs: list[tuple[str, str]] = []
        if refs.main_image:
            inf_imgs.append(("identity", refs.main_image))
        if refs.wardrobe_image:
            inf_imgs.append(("wardrobe", refs.wardrobe_image))
        elif refs.character_sheet_image:
            inf_imgs.append(("charsheet", refs.character_sheet_image))
        if refs.close_up_image1:
            inf_imgs.append(("closeup1", refs.close_up_image1))
        if refs.close_up_image2:
            inf_imgs.append(("closeup2", refs.close_up_image2))

        home_entry = [("home", refs.home_image)] if refs.home_image else []

        for i, (papel, url) in enumerate([*inf_imgs, *home_entry, *prod_imgs]):
            tag_map[papel] = f"@image_{i + 1}"
            ordenadas.append((papel, url))

    referencias = [
        Referencia(papel=papel, tag=tag_map[papel], url=url) for papel, url in ordenadas
    ]
    return tag_map, referencias


def sortear_ajustes_video(rng: random.Random) -> tuple[str, str, str]:
    """Botão "surpreenda-me" da UI de vídeo: `(vibe, env_key, camera_id)`.

    Único ponto do fluxo de vídeo que usava `Math.random()` na origem — e é da
    UI, não do montador. Portado com `rng` explícito para ser reproduzível.
    """
    vibe = rng.choice(list(CS_VIBES))
    env_key = rng.choice(list(CS_ENV_PRESETS.keys()))
    camera = rng.choice(list(CS_CAMERA_PRESETS)).id
    return vibe, env_key, camera


# ─────────────────────────────────────────────
# O montador


def montar_prompt_video(
    persona: Persona,
    *,
    dialogo: str = "",
    duracao: int = 12,
    aspect_ratio: str = "9:16",
    shot_mode: str = "oner",
    camera: str = "",
    vibe: str = "",
    ambiente: str = "",
    voz: str = "",
    notas: str = "",
    produto_vestido: bool = False,
    tem_start_frame: bool = False,
    rng: random.Random | None = None,
    # ── Além da assinatura sugerida: o motor original lê estes do estado do
    # componente, e sem eles o prompt não fecha. ──
    refs: ReferenciasVideo | None = None,
    env_key: str = "",
    hora_do_dia: str = "afternoon",
    voz_preset: str = "",
    tem_audio: bool = False,
    nomes_guarda_roupa: list[str] | None = None,
) -> PromptMontado:
    """Monta o prompt de vídeo em blocos rotulados.

    `dialogo` vazio significa b-roll MUDO: o bloco MOOD troca para o conjunto
    silencioso, DELIVERY nunca emite linha de voz (um preset de voz salvo fazia
    o modelo inventar fala) e o negative prompt `NO_TALK_NEGATIVE` deve ser
    enviado junto (veja `negative_prompt_para`).

    `shot_mode` é 'oner' (1 shot contínuo, zero cortes) ou qualquer outro valor
    para multi-shot, cuja contagem sai da duração.

    `rng` é aceito por simetria com os outros motores, mas o montador de vídeo é
    determinístico: `buildPrompt()` nunca chamou `Math.random()`. A única
    aleatoriedade do fluxo de vídeo vive em `sortear_ajustes_video`.
    """
    del rng  # determinístico — veja a docstring

    refs = refs or ReferenciasVideo()
    nomes_guarda_roupa = nomes_guarda_roupa or []
    usar_start_frame = tem_start_frame or bool(refs.start_frame)

    # NOTA: na origem `name` e `phys` são calculados no topo de `buildPrompt()` e
    # nunca usados — o SUBJECT vem inteiro das tags. Não portados.
    is_male = persona.gender == "Male"
    she = "he" if is_male else "she"
    She = "He" if is_male else "She"
    her = "him" if is_male else "her"
    his = "his" if is_male else "her"

    # Mapa ordenado de tags de imagem — refs do influencer primeiro, produtos
    # depois. O Higgsfield atribui @image_N na ordem em que as refs são passadas
    # para generate_video.
    tag_map, referencias = _montar_tag_map(refs, usar_start_frame)

    # Contagem de shots — oner = sempre 1; multi = automático pela duração
    if shot_mode == "oner":
        shot_count = 1
    elif duracao <= 5:
        shot_count = 1
    elif duracao <= 8:
        shot_count = 2
    elif duracao <= 12:
        shot_count = 3
    else:
        shot_count = 4

    # Um único preset de câmera dita enquadramento + lente + visual + movimento
    # (não há passo separado, então enquadramento e movimento nunca podem se
    # contradizer).
    cam = CS_CAMERA_BY_ID.get(camera) or CS_CAMERA_BY_ID["handheld"]
    style_preset = cam.style
    move = cam.move
    is_talking_head = cam.talking_head
    is_handheld = cam.handheld
    wear_mode = bool(refs.product1 and produto_vestido)

    # Ambiente — em modo start frame a cena está travada no start frame, não no
    # campo de texto
    tod_label = TIME_OF_DAY_LABELS.get(hora_do_dia, "")
    tod_suffix = f", {tod_label}" if tod_label else ""
    if usar_start_frame:
        env_desc = ("Continue from start frame — environment and lighting match "
                    "@image_1 exactly throughout.")
    elif tag_map.get("home"):
        extra = f" {ambiente}" if ambiente else ""
        env_desc = (f"{tag_map['home']} for the location and environment setting."
                    f"{extra}{tod_suffix}")
    else:
        base = ambiente or ("in a studio" if is_talking_head else "indoors")
        env_desc = f"{base}{tod_suffix}"

    # Arco de humor
    full_dialogue = dialogo.strip()  # declarado aqui para o teste de humor mudo abaixo
    mood_map = _mood_map(she, She)
    mood_map_silent = _mood_map_silent(She)
    if not full_dialogue:
        mood_arc = (mood_map_silent.get(vibe, mood_map_silent["Natural"])
                    if vibe else mood_map_silent["Natural"])
    else:
        # NOTA: com vibe desconhecida o fallback é a própria string da vibe
        # (`moodMap[vibe] || vibe`) — texto livre entra cru no bloco MOOD.
        mood_arc = (mood_map.get(vibe, vibe) if vibe else
                    "Delivery is genuine and present throughout. Micro-expressions are "
                    "honest and small. Gestures are natural and uncontrived.")

    # Lógica de cor — chaveada no chip (env_key), para que texto livre ainda
    # receba uma grade
    color_logic = (COLOR_MAP.get(env_key, "Clean neutral, warm skin tones.")
                   if env_key else "Clean neutral, warm skin tones.")

    # Diálogo completo — anotado com a notação de performance do guia
    prod1_tag = tag_map.get("product1") or None

    # As notas são lidas primeiro, para que os beats possam ser tecidos dentro
    # de annotate_dialogue
    action_beats, direction_notes = parse_additional_notes(notas, duracao)

    annotated_dialogue = annotate_dialogue(
        full_dialogue, prod1_tag, duracao, is_handheld, wear_mode, action_beats, she, her, his,
    )
    # NOTA: em modo multi-shot o `annotated_dialogue` acima é descartado (os
    # shots re-anotam frase a frase), MAS a chamada já marcou todos os beats como
    # `fired` — então, com diálogo, os beats de ação somem do prompt multi-shot.
    # Bug do original, preservado.

    # Multi-shot: distribui as frases cruas entre os shots
    dialogue_lines = (
        [s for s in re.split(r"(?<=[.!?])\s+", full_dialogue) if s.strip()]
        if full_dialogue else []
    )

    # Regras de produto (cinto + suspensório: repete a referência ao lado da
    # seção PRODUCT)
    product_rules: list[str] = []
    if tag_map.get("product1"):
        p1 = tag_map["product1"]
        extra = (f" {p1} is WORN — never held. {She} naturally interacts with it once or "
                 f"twice — a brief touch or glance — without overdoing it.") if wear_mode else ""
        product_rules.append(
            f"{p1} is always the same object — same color, label position, and size. "
            f"Never substituted.{extra}"
        )
    if tag_map.get("product2"):
        product_rules.append(f"{tag_map['product2']} is always the same object — never substituted.")
    if tag_map.get("product3"):
        product_rules.append(f"{tag_map['product3']} is always the same object — never substituted.")

    # Seção PRODUCT — bloco dedicado, colocado entre WARDROBE e ENVIRONMENT
    prod_entries = [
        (tag_map[papel], n)
        for papel, n in (("product1", 1), ("product2", 2), ("product3", 3))
        if tag_map.get(papel)
    ]
    product_section = ""
    if prod_entries:
        p_lines = ["PRODUCT:", ""]
        for tag, n in prod_entries:
            p_lines.append(
                f"{tag} — product reference {n}. Use as the exact source for this product's "
                f"color, shape, label text and orientation, and proportions."
            )
        p_lines.append("")
        all_prod_tags = " and ".join(tag for tag, _ in prod_entries)
        verbo = "contribute" if len(prod_entries) > 1 else "contributes"
        p_lines.append(
            f"The product must appear identical in every frame — same label text and "
            f"orientation, same colors, same proportions throughout. Never substituted, "
            f"recolored, or modified. {all_prod_tags} {verbo} ONLY the product — never the "
            f"face, identity, wardrobe, environment, or color grade."
        )
        if wear_mode:
            p_lines.append(
                f"Exception: {tag_map['product1']} is WORN — {she} interacts with it "
                f"naturally once or twice — a brief touch or glance — without overdoing it."
            )
        product_section = "\n".join(p_lines)

    # Monta os shots
    if shot_count == 1:
        shot_durs = [duracao]
    elif shot_count == 2:
        shot_durs = [2, duracao - 2]
    elif shot_count == 3:
        meio = _js_round((duracao - 2) / 2)
        shot_durs = [2, meio, duracao - 2 - meio]
    else:
        shot_durs = [2, 3, math.floor((duracao - 5) / 2), math.ceil((duracao - 5) / 2)]

    framing = cam.framing
    lens = cam.lens

    shots: list[str] = []
    t = 0
    for i in range(shot_count):
        sd = shot_durs[i]
        te = t + sd
        ts = f"0:{_pad2(t)} to 0:{_pad2(te)}"

        if shot_mode == "oner":
            # NOTA: no ramo oner o cabeçalho usa a duração inteira, não `ts` —
            # `t`/`te` ficam sem uso aqui (o laço roda uma vez só).
            start_pin = "Video opens at 0:00 as @image_1 exactly. " if usar_start_frame else ""
            if full_dialogue:
                action_body = annotated_dialogue
            else:
                prefixo = "" if usar_start_frame else "@image_1 faces forward. "
                action_body = (
                    f"{prefixo}NOT speaking — mouth stays closed and relaxed the entire "
                    f"time, ZERO lip movement, no talking. Calm natural expression; only "
                    f"subtle idle motion (breathing, small head/body shifts)."
                )
            oner_tail = (
                f" Natural conversational gestures as {she} speaks. End cleanly with the "
                f"character holding a final pose, no talking or lip movement."
            ) if full_dialogue else ""
            shots.append(
                f"ACTION:\n0:00 to 0:{_pad2(duracao)} — {framing}, {lens}, {move}. "
                f"One continuous take.\n\n{start_pin}{action_body}{oner_tail}".rstrip()
            )
        elif i == 0:
            start_pin = "Video opens at 0:00 as @image_1 exactly. " if usar_start_frame else ""
            if dialogue_lines and dialogue_lines[0]:
                hook_body = annotate_dialogue(
                    dialogue_lines[0], prod1_tag, duracao, is_handheld, wear_mode,
                    [], she, her, his,
                )
            else:
                hook_body = "" if usar_start_frame else "@image_1 faces camera. Eyes on lens at 0:00."
            shots.append(
                f"SHOT 1 — {ts}, {framing}, {lens}, {move}.\n{start_pin}{hook_body}".rstrip()
            )
        else:
            line = dialogue_lines[i] if i < len(dialogue_lines) else ""
            if prod1_tag and i == 1:
                gesture = (f"{she} touches {prod1_tag} and angles toward camera to show it"
                           if wear_mode else
                           f"{she} tilts {prod1_tag} toward camera slightly")
            else:
                gesture = "one hand lifts — palm-up, natural half-shrug"
            line_str = (f'"{line.strip()}" [beat — eyes stay on camera.] '
                        if line else "[holds the moment.] ")
            closing_tail = (" End cleanly with the character holding a final pose, no "
                            "talking or lip movement.") if (full_dialogue and i == shot_count - 1) else ""
            voice_tail = " Voice unhurried. Tone genuine." if full_dialogue else ""
            shots.append(
                f"SHOT {i + 1} — {ts}, {framing}, {lens}, {move}.\n@image_1 continues. "
                f"{gesture}. {line_str}{voice_tail}{closing_tail}".rstrip()
            )
        t = te

    # SUBJECT — identidade + reforço de detalhe vindo dos close-ups
    role_word = "presenter" if full_dialogue else "subject"
    if persona.gender == "Male":
        gender_hint = f"Male {role_word}. "
    elif persona.gender == "Female":
        gender_hint = f"Female {role_word}. "
    else:
        gender_hint = ""

    if usar_start_frame:
        subject_parts = [
            f"{gender_hint}@image_1 is the start frame — begin the video from this exact "
            f"frame. Identity (face, bone structure, skin tone, hair) is locked to @image_1 "
            f"throughout. Match exactly."
        ]
    else:
        subject_parts = [
            f"{gender_hint}@image_1 is the identity — face, bone structure, skin tone, hair. "
            f"Match exactly."
        ]
    if not usar_start_frame and tag_map.get("closeup1"):
        subject_parts.append(
            f"{tag_map['closeup1']} for close-up facial detail — eye color, skin texture, pores."
        )
    if not usar_start_frame and tag_map.get("closeup2"):
        subject_parts.append(
            f"{tag_map['closeup2']} for feature-level accuracy — lip shape, brow arch, skin tone."
        )

    # WARDROBE — em modo start frame a roupa já está assada dentro de @image_1
    if usar_start_frame:
        wardrobe_line = ("Continue outfit from @image_1 exactly — same silhouette, fabric, "
                         "color, styling throughout. Zero variation.")
    elif tag_map.get("wardrobe"):
        w = tag_map["wardrobe"]
        wardrobe_line = (f"Match outfit from {w} exactly — silhouette, fabric, color, styling, "
                         f"zero variation. Outfit comes from {w} only, not @image_1.")
    elif tag_map.get("charsheet"):
        wardrobe_line = (f"Match {tag_map['charsheet']} exactly — same outfit silhouette, "
                         f"fabric, color, styling throughout. Zero variation.")
    else:
        wardrobe_line = (", ".join(n for n in nomes_guarda_roupa if n)
                         or "Casual, stylish, consistent throughout.")

    # Sem diálogo → NUNCA emitir linha de voz (um preset de voz salvo esquecido
    # fazia o modelo inventar fala). Mudo = boca fechada, só som ambiente.
    if not full_dialogue:
        delivery_line = (
            f"No dialogue — completely silent. The subject does NOT speak and does NOT move "
            f"their lips; mouth stays closed and relaxed. "
            f"{infer_ambient_sound(env_key, ambiente)}"
        )
    elif tem_audio:
        delivery_line = "Lip-sync driven by @audio_1."
    elif voz.strip():
        delivery_line = f"Voice: {voz.strip()}"
    elif voz_preset:
        achado = next((v for v in _TODOS_OS_PRESETS_DE_VOZ if v.id == voz_preset), None)
        delivery_line = f"Voice: {achado.voice if achado else ''}"
    else:
        delivery_line = "Natural voice, genuine and present."

    # Anexa ao shot alvo os beats que ainda não dispararam
    shots_with_beats: list[str] = []
    for i, shot in enumerate(shots):
        if shot_mode == "oner":
            unfired = [b for b in action_beats if not b.fired]
        else:
            shot_start = sum(shot_durs[:i])
            shot_end = shot_start + shot_durs[i]
            unfired = [
                b for b in action_beats
                if shot_start <= b.fraction * duracao < shot_end and not b.fired
            ]
        for b in unfired:
            b.fired = True
        if not unfired:
            shots_with_beats.append(shot)
            continue
        if shot_mode == "oner":
            beat_str = " ".join(f"{b.text}." for b in unfired)
        else:
            beat_str = " ".join(f"At {b.timestamp} — {b.text}." for b in unfired)
        shots_with_beats.append(shot + "\n" + beat_str)

    formato_shots = ("1 SHOT — continuous oner, ZERO CUTS" if shot_count == 1
                     else f"{shot_count} SHOTS")
    formato_fala = ("direct address" if full_dialogue
                    else "silent b-roll — NO talking, no lip movement")

    bloco_produto = f"\n{product_section}\n" if product_section else ""
    bloco_direcao = f"\nDIRECTION: {direction_notes}" if direction_notes else ""
    cine_tail = (" This camera movement must be clearly visible and continuous throughout "
                 "the entire shot.") if cam.group == "cine" else ""

    if shot_mode == "oner":
        regra_shots = (f" ZERO CUTS — single uninterrupted take 0:00 to {duracao}s. No jump "
                       f"cuts, no camera switch, no temporal skip. @image_1 moves "
                       f"continuously — never freezes.")
    else:
        regra_shots = " Wardrobe identical across all shots."
    regra_wardrobe = (f" Outfit matches {tag_map['wardrobe']} throughout — do not take outfit "
                      f"from @image_1.") if tag_map.get("wardrobe") else ""
    regra_telefone = ("" if is_handheld else
                      " No phone or smartphone visible in frame at any time — no device in "
                      "hand, on any surface, or in the background.")
    regra_produto = (" " + " ".join(product_rules)) if product_rules else ""

    texto = (
        f"FORMAT: {duracao}s / {formato_shots} / {formato_fala}\n"
        f"\n"
        f"SUBJECT: {' '.join(subject_parts)}\n"
        f"\n"
        f"WARDROBE: {wardrobe_line}\n"
        f"{bloco_produto}\n"
        f"ENVIRONMENT: {env_desc}\n"
        f"\n"
        f"MOOD: {mood_arc}\n"
        f"\n"
        f"COLOR LOGIC: {color_logic}\n"
        f"\n"
        f"STYLE: {style_preset}\n"
        f"\n"
        f"CAMERA: {move}.{cine_tail}\n"
        f"\n"
        f"DELIVERY: {delivery_line}\n"
        f"{bloco_direcao}\n"
        f"LOGIC RULE: @image_1 face is fixed — same bone structure, eye color, skin tone, "
        f"jawline, zero drift. Only one @image_1 in frame at any time."
        f"{regra_shots}{regra_wardrobe}{regra_telefone} No music. No captions. "
        f"No text overlays.{regra_produto}\n"
        f"\n"
        f"---\n"
        f"\n"
        + "\n\n".join(shots_with_beats)
    )

    return PromptMontado(
        texto=texto,
        referencias=referencias,
        aspect_ratio=aspect_ratio,
        escolhas={
            "camera": cam.id,
            "camera_group": cam.group,
            "framing": framing,
            "lens": lens,
            "vibe": vibe,
            "env_key": env_key,
            "hora_do_dia": hora_do_dia,
            "shot_mode": shot_mode,
            "shot_count": shot_count,
            "shot_durs": shot_durs,
            "duracao": duracao,
            "tem_dialogo": bool(full_dialogue),
            "wear_mode": wear_mode,
            "start_frame": usar_start_frame,
            "max_palavras": max_palavras(duracao),
            "negative_prompt": negative_prompt_para(dialogo),
            "action_beats": [b.text for b in action_beats],
            "direction_notes": direction_notes,
        },
    )
