"""Os endpoints do fal batem com o catálogo real.

Um endpoint errado não falha na tela nem no teste de tipo: o pedido é
aceito, vira job, sai da fila e só então o fal responde 404. O operador vê
"erro" dez segundos depois, sem dizer o quê.

Foi exatamente o que aconteceu com o Seedream v5 Pro — portado com
`fal-ai/` na frente porque todos os vizinhos têm, e a origem documentava em
comentário que aquele não tem.
"""

from __future__ import annotations

import pytest

from olimpo.media import modelos


def _endpoints() -> list[tuple[str, str]]:
    """Só os do fal.

    A regra de prefixo é do catálogo do fal. Um modelo da OpenAI declara o
    nome do modelo (`gpt-image-2`), não um caminho de endpoint — exigir
    `fal-ai/` dele seria aplicar a regra do lugar errado.
    """
    saida = [(m.id, m.edit) for m in modelos.IMAGEM.values() if m.provider == "fal"]
    saida += [
        (m.id, m.t2i) for m in modelos.IMAGEM.values()
        if m.provider == "fal" and m.t2i
    ]
    saida += [
        (m.id, m.endpoint) for m in modelos.VIDEO.values() if m.provider == "fal"
    ]
    return saida


def test_modelo_openai_nao_finge_ser_endpoint_fal() -> None:
    """O que a OpenAI recebe é nome de modelo, não caminho.

    Mandar `fal-ai/gpt-image-2` para a API da OpenAI é um 400 de modelo
    inexistente — e a distinção é fácil de perder porque os dois campos se
    chamam `edit`.
    """
    for m in modelos.IMAGEM.values():
        if m.provider != "openai":
            continue
        assert "/" not in m.edit, f"{m.id}: {m.edit!r} parece caminho, não modelo"
        assert not m.edit.startswith("fal-ai"), m.id


@pytest.mark.parametrize(("ident", "endpoint"), _endpoints())
def test_prefixo_fal_ai_e_deliberado(ident: str, endpoint: str) -> None:
    """Ou o endpoint tem o prefixo, ou está na lista dos que não têm.

    Sem isso, "padronizar os endpoints" é uma limpeza que parece correta e
    derruba dois modelos silenciosamente.
    """
    if endpoint in modelos.SEM_PREFIXO_FAL:
        assert not endpoint.startswith("fal-ai/"), (
            f"{ident} está declarado como sem prefixo mas tem: {endpoint}"
        )
        return

    assert endpoint.startswith("fal-ai/"), (
        f"{ident} usa {endpoint!r}, que não tem o prefixo `fal-ai/` e também "
        f"não está em SEM_PREFIXO_FAL. Se o fal realmente serve esse caminho, "
        f"declare lá — com a URL que você testou."
    )


def test_sem_prefixo_nao_tem_orfao() -> None:
    """Todo item da lista corresponde a um modelo vivo.

    Uma entrada órfã vira documentação errada: alguém lê que o caminho é
    sem prefixo e aplica no modelo errado.
    """
    usados = {e for _, e in _endpoints()}
    orfaos = modelos.SEM_PREFIXO_FAL - usados
    assert not orfaos, f"em SEM_PREFIXO_FAL mas em nenhum modelo: {sorted(orfaos)}"


def test_aspectos_sempre_existem() -> None:
    """Nenhum modelo oferece lista de proporções vazia.

    A tela desabilitaria o botão sem dizer por quê — não há formato para
    escolher, então não há como gerar.
    """
    for m in modelos.IMAGEM.values():
        assert m.aspectos_aceitos, f"{m.id} não aceita proporção nenhuma"
