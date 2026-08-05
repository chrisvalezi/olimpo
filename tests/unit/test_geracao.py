"""Ordem das referências de identidade.

Este é o teste mais importante do subsistema de geração, e o que ele protege
não falha com erro: falha com uma pessoa levemente diferente.

Não existe semente, LoRA nem embedding por trás da consistência facial. Ela
depende de as imagens serem reenviadas NA ORDEM certa, porque as tags do
prompt (`@image1`, `@image2`…) apontam para posições do array. Se a lista
compactar e alguém indexar por posição fixa, a roupa passa a ser copiada da
referência de rosto — e a única evidência é o resultado parecer "quase" a
personagem.
"""

from __future__ import annotations

import pytest

from olimpo.media.providers.base import GenerationRequest, MediaKind, Route
from olimpo.media.providers.gateway import GatewayImageProvider
from olimpo.media.servico import ContextoDaPersona
from olimpo.prompts.tipos import ORDEM_REFERENCIAS, Persona


def ctx(*papeis: str) -> ContextoDaPersona:
    """Contexto com os papéis informados, na ordem informada."""
    from uuid import uuid4
    return ContextoDaPersona(
        persona=Persona(name="X"),
        referencias=[(p, uuid4()) for p in papeis],
        tem_rosto="face" in papeis,
    )


class TestOrdemDasReferencias:
    def test_a_ordem_canonica_comeca_pelo_rosto(self):
        """Se a primeira posição não for o rosto, não há identidade a
        preservar — as travas do prompt apontam para a imagem errada."""
        assert ORDEM_REFERENCIAS[0] == "face"
        assert ORDEM_REFERENCIAS[1] == "wardrobe"

    def test_papel_viaja_junto_do_asset(self):
        """A posição sozinha não diz o que a imagem é. Guardar o papel junto
        é o que permite resolver por papel em vez de por índice."""
        c = ctx("face", "wardrobe", "close_up_face")
        assert [p for p, _ in c.referencias] == ["face", "wardrobe", "close_up_face"]
        assert len(c.ids) == 3

    def test_ausencia_compacta_sem_perder_o_papel(self):
        """Uma persona sem folha de personagem tem o close de rosto na
        segunda posição do ARRAY — e é assim que o motor atribui as tags.
        O que não pode é alguém continuar achando que a posição 1 é a roupa."""
        c = ctx("face", "close_up_face", "close_up_detail")
        por_papel = dict(c.referencias)

        assert "wardrobe" not in por_papel
        # A segunda posição do array é o close de rosto…
        assert c.referencias[1][0] == "close_up_face"
        # …mas quem pergunta por "roupa" recebe nada, em vez de receber o
        # close de rosto por engano.
        assert por_papel.get("wardrobe") is None

    def test_extras_ficam_no_fim(self):
        """Fotos extras entram por último de propósito: colocá-las no meio
        deslocaria as posições das tags de todas as âncoras seguintes."""
        c = ctx("face", "wardrobe", "close_up_face", "close_up_detail",
                "product", "extra", "extra")
        papeis = [p for p, _ in c.referencias]
        assert papeis[-2:] == ["extra", "extra"]
        assert papeis.index("product") < papeis.index("extra")

    def test_sem_rosto_a_persona_nao_gera_com_identidade(self):
        assert ctx("wardrobe", "close_up_face").tem_rosto is False
        assert ctx("face").tem_rosto is True


class TestEscolhaDeProvider:
    """O gateway só expõe texto→imagem. Assim que existe referência — que é
    o caso normal de uma persona já criada — o caminho é o direto."""

    def test_gateway_atende_texto_puro(self):
        req = GenerationRequest(kind=MediaKind.IMAGE, prompt="uma foto")
        assert GatewayImageProvider().supports(req)

    def test_gateway_recusa_com_referencia(self):
        req = GenerationRequest(
            kind=MediaKind.IMAGE, prompt="uma foto",
            reference_images=["https://x/rosto.jpg"],
        )
        assert not GatewayImageProvider().supports(req)
        assert req.needs_direct

    @pytest.mark.parametrize("tipo", [MediaKind.VIDEO, MediaKind.AUDIO])
    def test_gateway_recusa_video_e_voz(self, tipo):
        req = GenerationRequest(kind=tipo, prompt="x")
        assert not GatewayImageProvider().supports(req)
        assert req.needs_direct

    def test_rota_e_registrada(self):
        """Saber por onde a geração passou é o que permite atribuir custo e
        latência depois."""
        assert GatewayImageProvider().route is Route.GATEWAY


class TestTraducaoDeTags:
    """`@image1` não significa nada para o fal. A tradução para ordinal
    posicional é o que preserva o roteamento entre as referências."""

    def test_traduz_para_ordinal(self):
        from olimpo.media.modelos import limpar_prompt
        saida = limpar_prompt("Foto de @image1 vestindo a roupa de @image2")
        assert "the first reference image" in saida
        assert "the second reference image" in saida
        assert "@image" not in saida

    def test_aceita_as_duas_grafias(self):
        """O photo studio usa `@image1`; o motor de vídeo usa `@image_1`."""
        from olimpo.media.modelos import limpar_prompt
        assert "the first reference image" in limpar_prompt("@image_1 aparece")
        assert "the first reference image" in limpar_prompt("@image1 aparece")

    def test_tag_sem_numero_nao_engole_a_numerada(self):
        """A ordem das substituições importa: tratar `@image_` antes de
        `@image_2` deixaria um '2' solto no texto."""
        from olimpo.media.modelos import limpar_prompt
        saida = limpar_prompt("@image_2 e @image_")
        assert "the second reference image" in saida
        assert "2" not in saida.replace("the second reference image", "")
