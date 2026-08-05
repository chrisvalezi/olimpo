"""Separação por plataforma.

Instagram, WhatsApp e TikTok não são variações do mesmo agente. O erro que
estes testes existem para impedir é silencioso e caro: o orchestrator ACEITA
`instagram.post-feed` despachado para uma conta de WhatsApp, e a task morre
no device sem nada aqui indicar o motivo.
"""

import pytest

from olimpo.integrations.actions import (
    ARCHETYPES_BY_PLATFORM,
    HAS_EVIDENCE,
    INSTAGRAM,
    NON_RETRYABLE,
    WHATSAPP,
    PlatformMismatch,
    assert_same_platform,
    default_archetype,
    platform_of,
    resolve_action,
    supported_platforms,
)

#: Catálogos reais, conforme GET /actions de um nó em 2026-08-05.
CATALOGO_IG = {
    "instagram.comment", "instagram.comments", "instagram.dm", "instagram.doctor",
    "instagram.engage", "instagram.follow", "instagram.followers-list",
    "instagram.follow-followers", "instagram.like", "instagram.list-accounts",
    "instagram.post-feed", "instagram.post-insights", "instagram.post-reel",
    "instagram.post-story", "instagram.profile", "instagram.profile-stats",
    "instagram.reel-action", "instagram.reel-download", "instagram.remove-account",
    "instagram.reply", "instagram.reply-story", "instagram.repost-reel",
    "instagram.save-post", "instagram.switch", "instagram.unfollow",
    "instagram.upload-quality", "instagram.watch-reels", "instagram.watch-stories",
    "instagram.whoami",
}
CATALOGO_WA = {
    "whatsapp.accounts", "whatsapp.add-contact", "whatsapp.archive", "whatsapp.chats",
    "whatsapp.contacts", "whatsapp.forward", "whatsapp.gif", "whatsapp.group-add",
    "whatsapp.group-create", "whatsapp.group-demote", "whatsapp.group-link",
    "whatsapp.group-permissions", "whatsapp.group-promote", "whatsapp.groups",
    "whatsapp.inbox", "whatsapp.location", "whatsapp.mark-read", "whatsapp.mark-unread",
    "whatsapp.media-list", "whatsapp.messages", "whatsapp.mute", "whatsapp.poll",
    "whatsapp.profile-name", "whatsapp.profile-photo", "whatsapp.reply",
    "whatsapp.send", "whatsapp.send-file", "whatsapp.sequence", "whatsapp.state",
    "whatsapp.summary", "whatsapp.unarchive", "whatsapp.voice", "whatsapp.watch-inbox",
}


class TestCatalogo:
    def test_acoes_do_instagram_existem(self):
        assert set(INSTAGRAM.values()) <= CATALOGO_IG

    def test_acoes_do_whatsapp_existem(self):
        assert set(WHATSAPP.values()) <= CATALOGO_WA

    def test_nenhuma_acao_cruza_de_plataforma(self):
        """Uma entrada `instagram.*` na tabela do WhatsApp seria exatamente o
        bug que este módulo existe para impedir."""
        assert all(platform_of(a) == "instagram" for a in INSTAGRAM.values())
        assert all(platform_of(a) == "whatsapp" for a in WHATSAPP.values())


class TestResolucao:
    def test_mesmo_conceito_resolve_diferente_por_plataforma(self):
        assert resolve_action("instagram", "send_dm") == "instagram.dm"
        assert resolve_action("whatsapp", "send_message") == "whatsapp.send"

    def test_postar_nao_existe_no_whatsapp(self):
        with pytest.raises(PlatformMismatch, match="não tem feed"):
            resolve_action("whatsapp", "publish_post")

    def test_curtir_nao_existe_no_whatsapp(self):
        with pytest.raises(PlatformMismatch, match="curtida"):
            resolve_action("whatsapp", "like")

    def test_tiktok_falha_alto_em_vez_de_cair_no_instagram(self):
        """Enquanto o orchestrator não suportar TikTok, pedir qualquer ação
        precisa explodir — nunca escorregar para o mapa do Instagram."""
        with pytest.raises(PlatformMismatch, match="TikTok"):
            resolve_action("tiktok", "publish_post")

    def test_erro_indica_onde_a_acao_existe(self):
        with pytest.raises(PlatformMismatch, match="existe em: whatsapp"):
            resolve_action("instagram", "send_message")

    def test_share_continua_inexistente(self):
        with pytest.raises(PlatformMismatch, match="feed"):
            resolve_action("instagram", "share")


class TestBarreiraDeDespacho:
    def test_recusa_acao_de_outra_plataforma(self):
        """A barreira final: a ação pode chegar montada por retry, replay ou
        pelo painel, e é aqui que ela vira efeito no mundo real."""
        with pytest.raises(PlatformMismatch, match="é de instagram, mas a conta é de whatsapp"):
            assert_same_platform("whatsapp", "instagram.post-feed")

    def test_aceita_acao_da_mesma_plataforma(self):
        assert_same_platform("whatsapp", "whatsapp.send")
        assert_same_platform("instagram", "instagram.like")


class TestArquetipos:
    def test_whatsapp_nunca_nasce_influencer(self):
        """Conta de WhatsApp não tem feed para alimentar."""
        assert default_archetype("whatsapp") == "messaging"
        assert default_archetype("instagram") == "influencer"

    def test_arquetipos_de_conteudo_sao_de_rede_social(self):
        assert "influencer" not in ARCHETYPES_BY_PLATFORM["whatsapp"]
        assert "gossip" not in ARCHETYPES_BY_PLATFORM["whatsapp"]
        assert "messaging" not in ARCHETYPES_BY_PLATFORM["instagram"]

    def test_collector_serve_a_todas(self):
        """Conta que só observa existe em qualquer plataforma."""
        assert all("collector" in v for v in ARCHETYPES_BY_PLATFORM.values())


class TestNos:
    def test_plataformas_do_no_saem_do_catalogo(self):
        assert supported_platforms(dict.fromkeys(CATALOGO_IG | CATALOGO_WA)) == [
            "instagram", "whatsapp"
        ]

    def test_no_so_de_whatsapp_e_reconhecido(self):
        """Um nó só de WhatsApp não pode receber task de Instagram só porque
        tem device livre."""
        assert supported_platforms(dict.fromkeys(CATALOGO_WA)) == ["whatsapp"]


class TestRetryEEvidencia:
    def test_envio_de_whatsapp_nao_e_repetivel(self):
        """Reenvio não é post duplicado: é uma pessoa real recebendo a mesma
        mensagem duas vezes."""
        for t in ("send_message", "send_file", "send_voice", "reply_message"):
            assert WHATSAPP[t] in NON_RETRYABLE

    def test_leitura_de_whatsapp_e_repetivel(self):
        for t in ("read_inbox", "list_chats", "chat_summary"):
            assert WHATSAPP[t] not in NON_RETRYABLE

    def test_envio_de_whatsapp_tem_recibo(self):
        """No WhatsApp `sent: false` é falha — o recibo aqui é forte."""
        assert WHATSAPP["send_message"] in HAS_EVIDENCE
