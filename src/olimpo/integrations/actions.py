"""Registro de ações por plataforma.

Instagram, WhatsApp e TikTok não são variações do mesmo agente. Uma conta de
WhatsApp não tem feed, não tem seguidor e não roda o loop de conteúdo — ela
conversa. Misturar as três produz o pior tipo de erro: o orchestrator aceita
`instagram.post-feed` despachado para uma conta de WhatsApp, e a task morre
lá dentro sem nada aqui indicar o motivo.

Por isso a resolução de ação é SEMPRE (plataforma, tipo de task). Não existe
mapa global — pedir uma ação sem dizer a plataforma é erro de programação.

Validado contra o catálogo real de um nó em 2026-08-05:
29 ações `instagram.*` e 33 `whatsapp.*`.
"""

from __future__ import annotations

from typing import Literal

Platform = Literal["instagram", "whatsapp", "tiktok"]


class PlatformMismatch(ValueError):
    """A ação pedida não existe nesta plataforma."""


# =============================================================================
# INSTAGRAM — rede social: publica, engaja, é medido por alcance
# =============================================================================
INSTAGRAM: dict[str, str] = {
    "publish_post": "instagram.post-feed",
    "publish_story": "instagram.post-story",
    "publish_reels": "instagram.post-reel",
    "like": "instagram.like",
    "comment": "instagram.comment",
    "follow": "instagram.follow",
    "unfollow": "instagram.unfollow",
    "save": "instagram.save-post",
    "watch_reels": "instagram.watch-reels",
    "view_stories": "instagram.watch-stories",
    "send_dm": "instagram.dm",
    "reply_comment": "instagram.reply",
    "extract_content": "instagram.reel-download",
    "collect_metrics": "instagram.profile-stats",
    "health_check": "instagram.doctor",
    "whoami": "instagram.whoami",
}

# =============================================================================
# WHATSAPP — mensageria: conversa, distribui, é medido por entrega
# =============================================================================
# Note que os tipos de task são OUTROS. Não é o mesmo vocabulário com nomes
# diferentes: não existe "publicar" nem "seguidor" aqui.
WHATSAPP: dict[str, str] = {
    "send_message": "whatsapp.send",
    "send_file": "whatsapp.send-file",
    "send_voice": "whatsapp.voice",
    "send_location": "whatsapp.location",
    "send_gif": "whatsapp.gif",
    "reply_message": "whatsapp.reply",
    "forward_message": "whatsapp.forward",
    "read_inbox": "whatsapp.inbox",
    "read_messages": "whatsapp.messages",
    "list_chats": "whatsapp.chats",
    "watch_inbox": "whatsapp.watch-inbox",
    "mark_read": "whatsapp.mark-read",
    "mark_unread": "whatsapp.mark-unread",
    "archive_chat": "whatsapp.archive",
    "unarchive_chat": "whatsapp.unarchive",
    "mute_chat": "whatsapp.mute",
    "add_contact": "whatsapp.add-contact",
    "list_contacts": "whatsapp.contacts",
    "create_group": "whatsapp.group-create",
    "add_to_group": "whatsapp.group-add",
    "list_groups": "whatsapp.groups",
    "send_poll": "whatsapp.poll",
    "set_profile_name": "whatsapp.profile-name",
    "set_profile_photo": "whatsapp.profile-photo",
    "chat_summary": "whatsapp.summary",
    "collect_metrics": "whatsapp.state",
    "whoami": "whatsapp.accounts",
}

# =============================================================================
# TIKTOK — ainda não existe no orchestrator. Deixar vazio é intencional:
# pedir qualquer ação para uma conta TikTok falha alto, com mensagem clara,
# em vez de silenciosamente cair no mapa do Instagram.
# =============================================================================
TIKTOK: dict[str, str] = {}

REGISTRY: dict[str, dict[str, str]] = {
    "instagram": INSTAGRAM,
    "whatsapp": WHATSAPP,
    "tiktok": TIKTOK,
}

#: Por que um tipo de task não existe naquela plataforma. Mensagem de erro
#: útil vale mais que um KeyError.
UNSUPPORTED: dict[tuple[str, str], str] = {
    ("instagram", "share"): (
        "não existe compartilhar post de feed. Só reels, via "
        "instagram.reel-action do=[share|story|repost]"
    ),
    ("instagram", "collect_interactions"): (
        "não existe leitura do texto de comentários nem de DMs recebidas"
    ),
    ("whatsapp", "publish_post"): "WhatsApp não tem feed",
    ("whatsapp", "publish_story"): "status do WhatsApp não é exposto pelo serviço",
    ("whatsapp", "follow"): "WhatsApp não tem seguidores",
    ("whatsapp", "like"): "WhatsApp não tem curtida",
    ("tiktok", "*"): "TikTok ainda não é suportado pelo android-orchestrator",
}

#: Ações que NÃO podem ser repetidas cegamente, por plataforma.
#: `like` é toggle (retry descurte); publicação repetida duplica; mensagem
#: repetida chega duas vezes para uma pessoa real.
NON_RETRYABLE: frozenset[str] = frozenset({
    # instagram
    "instagram.post-feed", "instagram.post-reel", "instagram.post-story",
    "instagram.repost-reel", "instagram.like", "instagram.comment",
    "instagram.comments", "instagram.reply", "instagram.engage",
    "instagram.dm", "instagram.reply-story", "instagram.watch-reels",
    "instagram.watch-stories", "instagram.follow-followers",
    "instagram.reel-action", "instagram.remove-account",
    # whatsapp — tudo que envia é irreversível e visível para uma pessoa real
    "whatsapp.send", "whatsapp.send-file", "whatsapp.voice", "whatsapp.gif",
    "whatsapp.location", "whatsapp.reply", "whatsapp.forward",
    "whatsapp.poll", "whatsapp.group-create", "whatsapp.group-add",
    "whatsapp.sequence",
})

#: Ações que produzem prova de efeito. As demais devolvem `data` vazio, e
#: `ok: true` é tudo o que se tem.
HAS_EVIDENCE: frozenset[str] = frozenset({
    "instagram.post-feed", "instagram.post-reel", "instagram.post-story",
    "instagram.dm", "instagram.save-post", "instagram.follow",
    "instagram.unfollow", "instagram.reel-action",
    # No WhatsApp `sent: false` é FALHA — o recibo aqui é forte
    "whatsapp.send", "whatsapp.send-file", "whatsapp.voice", "whatsapp.reply",
})

#: Arquétipos válidos por plataforma — espelha o CHECK da migração 0004.
ARCHETYPES_BY_PLATFORM: dict[str, frozenset[str]] = {
    "instagram": frozenset({"influencer", "gossip", "clipper", "collector"}),
    "tiktok": frozenset({"influencer", "gossip", "clipper", "collector"}),
    "whatsapp": frozenset({"messaging", "collector"}),
}

#: Arquétipos que NÃO rodam o loop de conteúdo (PLAN→SOURCE→PRODUCE→…).
NON_AGENT_ARCHETYPES: frozenset[str] = frozenset({"messaging", "collector"})


def resolve_action(platform: str, task_type: str) -> str:
    """(plataforma, tipo) → ação qualificada do orchestrator.

    Levanta em vez de devolver algo plausível: uma ação errada aqui vira
    task aceita pelo serviço que morre no device, sem sinal de erro.
    """
    table = REGISTRY.get(platform)
    if table is None:
        raise PlatformMismatch(f"plataforma desconhecida: {platform!r}")

    action = table.get(task_type)
    if action:
        return action

    reason = UNSUPPORTED.get((platform, task_type)) or UNSUPPORTED.get((platform, "*"))
    if reason:
        raise PlatformMismatch(f"{platform}.{task_type}: {reason}")

    outras = [p for p, t in REGISTRY.items() if task_type in t]
    dica = f" (existe em: {', '.join(outras)})" if outras else ""
    raise PlatformMismatch(f"{platform} não tem a ação {task_type!r}{dica}")


def platform_of(action: str) -> str:
    """Prefixo de uma ação qualificada: 'instagram.post-feed' → 'instagram'."""
    return action.split(".", 1)[0]


def assert_same_platform(account_platform: str, action: str) -> None:
    """Última barreira antes do despacho.

    Vale a redundância com resolve_action: uma ação pode chegar montada por
    outro caminho (retry, replay, painel), e este é o ponto onde ela vira
    efeito no mundo real.
    """
    got = platform_of(action)
    if got != account_platform:
        raise PlatformMismatch(
            f"ação {action!r} é de {got}, mas a conta é de {account_platform}"
        )


def default_archetype(platform: str) -> str:
    """Arquétipo com que uma conta descoberta nasce.

    WhatsApp nunca nasce como influencer — não tem feed para alimentar.
    """
    return "messaging" if platform == "whatsapp" else "influencer"


def supported_platforms(action_catalog: dict[str, object]) -> list[str]:
    """Deduz as plataformas de um nó a partir do catálogo /actions dele.

    Um nó só de WhatsApp não deve receber task de Instagram só porque tem
    device livre.
    """
    return sorted({platform_of(a) for a in action_catalog})
