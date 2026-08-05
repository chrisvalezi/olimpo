"""Tradução entre o contrato do android-orchestrator e o estado interno.

Este mapeamento é segurança, não conveniência: interpretar errado um status
externo leva a republicar um post que já foi publicado.

Os nomes de ação foram validados ao vivo contra o `GET /actions` de um nó
real em 2026-08-05 — as 29 ações do Instagram conferem.
"""

import pytest

from olimpo.integrations.actions import INSTAGRAM, NON_RETRYABLE
from olimpo.integrations.orchestrator import (
    _sanitize,
    build_params,
    read_evidence,
    to_task_status,
)

#: Catálogo real do serviço, conforme GET /actions.
CATALOGO_REAL = {
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


class TestStatusMapping:
    def test_conclusao_vira_verified(self):
        assert to_task_status("done") == "verified"

    def test_falha_vira_failed(self):
        assert to_task_status("failed") == "failed"

    def test_unknown_nao_vira_falha(self):
        """`unknown` é timeout ou restart no meio: a postagem PODE ter ido ao
        ar. Marcar como falha dispara retry e duplica o post."""
        assert to_task_status("unknown") == "running"

    def test_status_desconhecido_mantem_em_voo(self):
        assert to_task_status("estado_novo") == "running"


class TestEvidence:
    def test_publicacao_confirmada_e_verified(self):
        level, ev = read_evidence(
            {"status": "done", "ok": True, "data": {"published": True, "verified": True}}
        )
        assert level == "verified"
        assert ev["verified"] is True

    def test_acao_sem_recibo_e_apenas_reported(self):
        """`like` e `comment` devolvem data vazio — não há prova de efeito."""
        level, _ = read_evidence({"status": "done", "ok": True, "data": {}})
        assert level == "reported"

    def test_unknown_pede_verificacao_humana(self):
        level, ev = read_evidence({"status": "unknown", "ok": True, "data": {}})
        assert level == "unknown"
        assert "timeout" in ev["reason"]

    def test_falha_nao_produz_evidencia(self):
        level, _ = read_evidence({"status": "failed", "ok": False, "error": "x"})
        assert level == "none"

    def test_like_verificado_por_pixel(self):
        level, _ = read_evidence(
            {"status": "done", "ok": True, "data": {"result": "liked"}}
        )
        assert level == "verified"


class TestParams:
    def test_publicacao_exige_publish_explicito(self):
        """Sem publish=true o serviço monta tudo e PARA na tela de legenda —
        e é fácil concluir que postou."""
        p = build_params("instagram", "publish_post", {"media_refs": ["m://a.jpg"], "caption": "oi"})
        assert p["publish"] is True
        assert p["media"] == ["m://a.jpg"]

    def test_sanitiza_token_de_duplo_hifen(self):
        """O autodroid trunca o texto no primeiro token `--` e não há escape:
        a caption perderia tudo dali em diante, ou o serviço recusa com 400."""
        p = build_params("instagram", "comment", {"target_url": "u", "text": "top --demais ontem"})
        assert "--demais" not in p["text"]
        assert p["text"] == "top ontem"

    def test_hifen_simples_passa(self):
        assert _sanitize("bem-vinda ao treino") == "bem-vinda ao treino"

    def test_recon_pede_apenas_metadados(self):
        """Sem no_video o serviço baixa o vídeo para o device, e não existe
        rota HTTP para buscar o arquivo de volta."""
        p = build_params("instagram", "extract_content", {"target_url": "https://x/reel/abc"})
        assert p["no_video"] is True


class TestClientGuards:
    @pytest.mark.asyncio
    async def test_recusa_acao_nao_qualificada(self):
        from olimpo.integrations.orchestrator import OrchestratorClient

        client = OrchestratorClient("http://x:8078", dry_run=True)
        with pytest.raises(ValueError, match="não qualificada"):
            await client.create_task(account="@a", action="post-feed", params={"x": 1})
