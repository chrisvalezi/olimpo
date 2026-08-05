"""A instrução que diz ao modelo o que é cada imagem de referência.

Existe porque os prompts das âncoras derivadas e do figurino falavam em
"the reference image" no singular enquanto o código mandava três. Com
linguagem singular o modelo não tem como saber que a terceira imagem é uma
blusa e não uma pessoa — e a blusa acaba influenciando o rosto.

O que precisa estar certo aqui é o **casamento entre ordinal e posição**.
Descrever a segunda imagem enquanto se envia outra na segunda posição é pior
que não descrever nada: o modelo segue o texto.
"""

from __future__ import annotations

import pytest

from olimpo.prompts.referencias import montar_instrucao_posicional as montar


def test_sem_papeis_nao_gera_texto() -> None:
    """Prompt não deve ganhar cauda vazia quando não há o que explicar."""
    assert montar([]) == ""


def test_ordinal_acompanha_a_posicao() -> None:
    """Cada papel é anunciado na posição em que a imagem é enviada."""
    texto = montar(["identity", "layout", "garment"])
    assert "The first reference image is the identity reference" in texto
    assert "The second reference image is the layout reference" in texto
    assert "The third reference image is a garment reference" in texto


def test_agrupa_posicoes_consecutivas() -> None:
    """Repetir a mesma frase cinco vezes dilui o resto do prompt."""
    texto = montar(["identity", "garment", "garment", "garment"])
    assert "The second, third and fourth reference images are" in texto
    assert texto.count("garment reference") == 1


def test_plural_concorda() -> None:
    """"use IT to confirm" com três imagens manda usar uma só, e o modelo
    obedece. A concordância é instrução, não estilo."""
    plural = montar(["identity", "extra", "extra"])
    assert "use them to confirm" in plural
    assert "They are the same individual" in plural
    assert " use it to confirm" not in plural

    singular = montar(["identity", "extra"])
    assert "use it to confirm" in singular
    assert "use them to confirm" not in singular


def test_garment_no_plural_fala_em_pecas() -> None:
    texto = montar(["identity", "garment", "garment"])
    assert "these exact clothing items" in texto
    assert "this exact clothing item" not in texto


def test_papel_desconhecido_nao_desloca_os_seguintes() -> None:
    """Papel sem instrução é omitido — mas a imagem continua sendo enviada,
    então quem vem depois mantém o ordinal.

    Renumerar aqui seria descrever a imagem errada.
    """
    texto = montar(["identity", "inexistente", "garment"])
    assert "The third reference image is a garment reference" in texto
    assert "second" not in texto


@pytest.mark.parametrize("papel", ["identity", "layout", "outfit", "garment",
                                   "product", "extra"])
def test_todo_papel_usado_no_codigo_tem_texto(papel: str) -> None:
    """Um papel sem instrução vira silêncio: a imagem é enviada e o modelo
    decide sozinho o que fazer com ela."""
    assert montar([papel]).strip(), papel


def test_garment_manda_ignorar_quem_veste() -> None:
    """O ponto todo da referência de peça.

    Sem isso o modelo copia a pessoa da foto do produto, e a persona vira
    outra — que é o defeito exato que a instrução existe para evitar.
    """
    texto = montar(["identity", "garment"])
    assert "ignore the face" in texto.lower()
