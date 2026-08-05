"""Tradução entre o contrato do android-orchestrator e o estado interno.

Este mapeamento é segurança, não conveniência: interpretar errado um status
externo leva a republicar um post que já foi publicado.
"""

from olimpo.integrations.orchestrator import ACTION_MAP, build_payload, to_task_status


class TestStatusMapping:
    def test_conclusao_vira_verified(self):
        for external in ("done", "success", "verified", "DONE"):
            assert to_task_status(external) == "verified"

    def test_falha_vira_failed(self):
        for external in ("failed", "error", "ERROR"):
            assert to_task_status(external) == "failed"

    def test_status_desconhecido_mantem_em_voo(self):
        """Estado que não reconhecemos NÃO pode virar 'failed'.

        Marcar como falha dispara retry, e retry de uma publicação que já
        aconteceu produz post duplicado. Manter em voo deixa o reconciliador
        decidir — o erro barato é esperar, não republicar.
        """
        for unknown in ("weird_state", "", "paused_by_operator"):
            assert to_task_status(unknown) == "running"

    def test_cancelamento_e_preservado(self):
        assert to_task_status("cancelled") == "cancelled"


class TestActionMap:
    def test_todo_tipo_de_task_executavel_tem_acao(self):
        """Um task_type sem entrada aqui falharia só na hora do dispatch,
        em produção, com a task já agendada."""
        executable = {
            "publish_post", "publish_story", "publish_reels", "like", "comment",
            "follow", "unfollow", "save", "share", "watch_reels", "view_stories",
            "send_dm", "reply_comment", "extract_content", "collect_metrics",
            "collect_interactions", "monitor_check",
        }
        assert executable <= ACTION_MAP.keys()


class TestPayload:
    def test_publicacao_carrega_midia_e_caption(self):
        payload = build_payload(
            "publish_post",
            {"media_refs": ["media://a.jpg"], "caption": "bom dia",
             "hashtags": ["corrida"], "audio_ref": None},
        )
        assert payload["media"] == ["media://a.jpg"]
        assert payload["caption"] == "bom dia"

    def test_comentario_precisa_de_alvo_e_texto(self):
        payload = build_payload(
            "comment", {"target_url": "https://instagram.com/p/X", "text": "top"}
        )
        assert payload == {"url": "https://instagram.com/p/X", "text": "top"}

    def test_watch_reels_tem_default_seguro(self):
        """Sem mínimo de segundos, "assistir" vira scroll instantâneo — que é
        exatamente o padrão que denuncia automação."""
        payload = build_payload("watch_reels", {"segment": "fitness"})
        assert payload["min_seconds"] >= 5
        assert payload["count"] >= 1

    def test_tipo_desconhecido_passa_o_payload_adiante(self):
        original = {"algo": "novo"}
        assert build_payload("tipo_futuro", original) == original
