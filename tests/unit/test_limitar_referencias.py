"""Corte de referências quando não cabem no modelo.

Existe porque o teto **não é uniforme** e o padrão é o mais apertado: o
FLUX.2 aceita quatro imagens, e uma persona completa com produto e fotos de
acervo manda oito. Passar do teto é `422` no provider — que só aparece no
worker, depois do job enfileirado, com uma mensagem sobre `image_urls` que
não diz nada a quem pediu uma foto.

Duas coisas precisam estar certas, e as duas quebram em silêncio:

  **quem sobrevive** — largar o produto que o operador escolheu produz
  exatamente a foto que ele não pediu, e ninguém vê erro nenhum.

  **a ordem entre os sobreviventes** — é o contrato do prompt. Reordenar ao
  cortar faz "wearing the outfit from the second reference image" apontar
  para o rosto, e o modelo copia a roupa da referência facial.
"""

from __future__ import annotations

from olimpo.media.servico import limitar_referencias

#: Uma persona completa, com produto e três fotos de acervo — o caso que
#: estourou o FLUX na prática.
COMPLETA = [
    ("face", "a"), ("wardrobe", "b"), ("close_up_face", "c"),
    ("close_up_detail", "d"), ("product", "e"),
    ("extra", "f"), ("extra", "g"), ("extra", "h"),
]


def test_nao_corta_quando_cabe() -> None:
    mantidas, descartados = limitar_referencias(COMPLETA, 10)
    assert mantidas == COMPLETA
    assert descartados == []


def test_rosto_nunca_sai() -> None:
    """Sem o rosto não há identidade — a geração produziria outra pessoa."""
    for teto in range(1, 9):
        mantidas, _ = limitar_referencias(COMPLETA, teto)
        assert mantidas[0][0] == "face", teto


def test_flux_mantem_o_que_e_o_pedido() -> None:
    """Com quatro vagas: identidade, roupa, produto e o close de rosto.

    Roupa e produto SÃO o pedido — largá-los devolve outra foto. Os closes e
    os ângulos extras melhoram o resultado, mas a foto continua certa sem
    eles.
    """
    mantidas, descartados = limitar_referencias(COMPLETA, 4)
    papeis = [p for p, _ in mantidas]
    assert "face" in papeis
    assert "wardrobe" in papeis
    assert "product" in papeis
    assert descartados.count("extra") == 3
    assert "close_up_detail" in descartados


def test_ordem_original_e_preservada() -> None:
    """O contrato do prompt é posicional.

    Cortar não pode reordenar: o texto diz "the second reference image", e o
    array precisa concordar.
    """
    for teto in range(1, 9):
        mantidas, _ = limitar_referencias(COMPLETA, teto)
        posicoes = [COMPLETA.index(r) for r in mantidas]
        assert posicoes == sorted(posicoes), teto


def test_extras_saem_antes_dos_closes() -> None:
    """As fotos de acervo são as mais dispensáveis: reforçam a identidade,
    mas as âncoras já a carregam."""
    mantidas, _ = limitar_referencias(COMPLETA, 6)
    papeis = [p for p, _ in mantidas]
    assert "close_up_detail" in papeis
    assert papeis.count("extra") == 1


def test_papel_desconhecido_e_o_primeiro_a_sair() -> None:
    """Um papel novo que ninguém classificou não pode empurrar o rosto para
    fora — o desconhecido é o menos confiável, não o mais."""
    refs = [("face", "a"), ("misterioso", "b"), ("wardrobe", "c")]
    mantidas, descartados = limitar_referencias(refs, 2)
    assert [p for p, _ in mantidas] == ["face", "wardrobe"]
    assert descartados == ["misterioso"]


def test_lista_vazia_nao_estoura() -> None:
    assert limitar_referencias([], 4) == ([], [])
