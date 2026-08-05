"""Instrução textual que explica ao modelo o PAPEL de cada imagem de referência.

Porte literal de ``src/utils/refInstruction.js`` da máquina de influencers.

Comentário original do topo do arquivo, traduzido:

    Instrução de tratamento de referências para o caminho de geração fal/OpenAI.
    Espelha a lógica de dentro de ``higgsfieldGenerate.generateThreeImages``, mas
    endereça as imagens em linguagem natural ("a primeira/segunda imagem de
    referência") porque esses providers recebem um ARRAY de imagens em vez das
    tags MCP ``@image1``/``@image2`` do Higgsfield.

    As referências precisam ser passadas rosto-primeiro, depois estilo — casando
    com a redação "primeira/segunda". Notas do usuário têm prioridade sobre a
    extração padrão, exatamente como no caminho do Higgsfield.

Cada string aqui codifica uma lição aprendida na marra e NÃO deve ser reescrita:

* a referência facial é **geometria** (proporção de olhos, largura de mandíbula,
  ponte do nariz) — quando existe descrição textual, o tom de pele, cabelo e cor
  dos olhos vêm do TEXTO, não da foto;
* a referência de estilo é pose/roupa/paleta/cena — o modelo é explicitamente
  proibido de copiar o rosto de quem aparece nela;
* referências extras são a MESMA pessoa em outros ângulos, nunca outra pessoa.
"""

from __future__ import annotations

from .tipos import Referencia

__all__ = [
    "montar_instrucao_a_partir_de_referencias",
    "montar_instrucao_de_referencias",
    "montar_instrucao_posicional",
]


def _bloco_de_rosto(ref: str, *, face_note: str, tem_descricao: bool) -> str:
    """Frase que descreve o papel da imagem de identidade.

    Três caminhos, na ordem de precedência do original:

    1. nota escrita pelo usuário → usa só aquilo dela;
    2. existe descrição física em texto → a referência vira GEOMETRIA e o texto
       manda no tom de pele/cabelo/olhos (é a lição mais cara deste arquivo);
    3. nada de texto → a referência manda em tudo.
    """
    if face_note:
        sufixo = " Use the text description for all other identity attributes." if tem_descricao else ""
        return f'{ref}: use specifically "{face_note}" from this reference.{sufixo}'
    if tem_descricao:
        return (
            f"{ref} is a facial geometry reference — match the face proportions "
            "(eye spacing, jaw width, nose bridge, face shape) but defer to the text description "
            "for skin tone, hair, eye color, and identity. Ignore its clothing, background, and lighting."
        )
    return (
        f"{ref} is the appearance reference — faithfully recreate this person's face, "
        "skin tone, hair, eye color, and overall look exactly as shown."
    )


def _bloco_de_estilo(ref: str, *, style_note: str) -> str:
    """Frase que descreve o papel da imagem de estilo.

    O "do NOT copy the face" em caixa alta é proposital: sem ele o modelo
    troca a identidade da persona pela pessoa que aparece na referência.
    """
    if style_note:
        return (
            f'{ref}: use specifically "{style_note}" from this reference. '
            "Do not copy the face or identity of any person in it."
        )
    return (
        f"{ref} is a visual style reference — do NOT copy the face or identity of any person in it. "
        "Match the pose and body positioning, outfit aesthetic (silhouette, layering, fabric, styling), "
        "color palette, scene and background, lighting mood, and overall photographic vibe."
    )


def montar_instrucao_de_referencias(
    *,
    face_ref: str | None = None,
    style_ref: str | None = None,
    physical_desc: str = "",
    face_ref_note: str | None = None,
    style_ref_note: str | None = None,
    extra_count: int = 0,
) -> str:
    """Porte de ``buildRefInstructionPlain``.

    Devolve o trecho que é CONCATENADO ao final do prompt principal — por isso
    começa com um espaço quando não é vazio (idêntico ao original).

    Args:
        face_ref: imagem de identidade (só a presença importa, não o conteúdo).
        style_ref: imagem de estilo/pose.
        physical_desc: descrição física em texto; se existir, muda o papel da
            referência facial de "aparência" para "geometria".
        face_ref_note: nota do usuário sobre o que usar da referência facial.
        style_ref_note: nota do usuário sobre o que usar da referência de estilo.
        extra_count: quantas fotos de referência adicionais entram no array.
    """
    tem_descricao = bool((physical_desc or "").strip())
    face_note = (face_ref_note or "").strip()
    style_note = (style_ref_note or "").strip()

    # Fotos de referência extras = a MESMA pessoa em outros ângulos/iluminações.
    # Elas reforçam a trava de identidade; o modelo NÃO pode lê-las como pessoas
    # diferentes — daí o "never a different person" no fim da frase.
    #
    # NOTA: o verbo fica em "show" mesmo com extra_count == 1 (concordância
    # errada no original). Preservado literalmente — o prompt é o que foi
    # testado, não o que é gramaticalmente correto.
    if extra_count > 0:
        plural = "s" if extra_count > 1 else ""
        extra = (
            f" {extra_count} additional reference image{plural} show the SAME person from other "
            "angles and lighting — use them together with the identity reference to lock the face "
            "exactly (same bone structure, features, skin tone). They are the same individual, "
            "never a different person."
        )
    else:
        extra = ""

    if face_ref and style_ref:
        rosto = _bloco_de_rosto(
            "The first reference image", face_note=face_note, tem_descricao=tem_descricao
        )
        estilo = _bloco_de_estilo("The second reference image", style_note=style_note)
        return f" {rosto} {estilo}{extra}"

    if face_ref:
        rosto = _bloco_de_rosto(
            "The reference image", face_note=face_note, tem_descricao=tem_descricao
        )
        return f" {rosto}{extra}"

    if style_ref:
        estilo = _bloco_de_estilo("The reference image", style_note=style_note)
        return (
            f" {estilo} The subject's face and identity come entirely from "
            f"the text description above.{extra}"
        )

    if extra_count > 0:
        return extra

    return ""


#: Papéis que contam como "referência de identidade" (a primeira imagem).
_PAPEIS_DE_ROSTO = frozenset({"face"})
#: Papéis que contam como "referência de estilo" (a segunda imagem).
_PAPEIS_DE_ESTILO = frozenset({"wardrobe", "style"})


def montar_instrucao_a_partir_de_referencias(
    referencias: list[Referencia],
    *,
    physical_desc: str = "",
    face_ref_note: str | None = None,
    style_ref_note: str | None = None,
) -> str:
    """Adaptador: deriva os argumentos a partir do array de `Referencia`.

    A ORDEM do array é o contrato — rosto primeiro, estilo depois. Tudo que não
    é rosto nem estilo (close-ups, fotos de perfil extras) conta como
    ``extra_count``, que é exatamente o papel delas: reforçar a identidade sem
    deslocar quem é "a primeira" e quem é "a segunda" imagem.

    Não muda a semântica de :func:`montar_instrucao_de_referencias` — só evita
    que o chamador remonte os mesmos argumentos à mão.
    """
    face_ref: str | None = None
    style_ref: str | None = None
    extras = 0
    for ref in referencias:
        if ref.papel in _PAPEIS_DE_ROSTO and face_ref is None:
            face_ref = ref.url
        elif ref.papel in _PAPEIS_DE_ESTILO and style_ref is None:
            style_ref = ref.url
        else:
            extras += 1

    return montar_instrucao_de_referencias(
        face_ref=face_ref,
        style_ref=style_ref,
        physical_desc=physical_desc,
        face_ref_note=face_ref_note,
        style_ref_note=style_ref_note,
        extra_count=extras,
    )


# =============================================================================
# Instrução posicional — quando cada imagem tem um papel diferente
# =============================================================================
# `montar_instrucao_de_referencias` cobre o caso de CRIAR um rosto: uma
# referência facial, uma de estilo, e ângulos extras da mesma pessoa.
#
# As âncoras derivadas e o figurino são outro caso. Ali as imagens têm papéis
# heterogêneos — identidade, layout, peça de roupa —, e o prompt dizia "the
# reference image" no singular enquanto o código mandava três. Com linguagem
# singular o modelo não tem como saber que a terceira imagem é uma blusa e
# não uma pessoa, e o resultado é a blusa influenciando o rosto.

#: Escrito por extenso porque o texto vai direto para o modelo. Os provedores
#: não entendem `@image3`; entendem "the third reference image".
_ORDINAIS = (
    "first", "second", "third", "fourth", "fifth",
    "sixth", "seventh", "eighth", "ninth", "tenth",
)

#: Singular e plural escritos separadamente, não derivados por substituição.
#: Concordância errada num prompt não é detalhe de estilo: "use IT to confirm"
#: quando são três imagens diz ao modelo para usar uma só, e ele obedece.
_INSTRUCAO_POR_PAPEL: dict[str, tuple[str, str]] = {
    "identity": (
        "the identity reference — replicate this exact person: facial bone "
        "structure, eye shape and colour, nose, lips, skin tone and texture, "
        "hairline. This face is the subject's identity and must not drift",
        "identity references — replicate this exact person: facial bone "
        "structure, eye shape and colour, nose, lips, skin tone and texture, "
        "hairline. This face is the subject's identity and must not drift",
    ),
    "layout": (
        "the layout reference — reproduce its panel arrangement, framing and "
        "body proportions. Do NOT copy its clothing",
        "layout references — reproduce their panel arrangement, framing and "
        "body proportions. Do NOT copy their clothing",
    ),
    "outfit": (
        "the current-outfit reference — the clothing the subject is wearing",
        "current-outfit references — the clothing the subject is wearing",
    ),
    "garment": (
        "a garment reference — reproduce this exact clothing item on the "
        "subject. Copy ONLY the garment: ignore the face, body and background "
        "of whoever is shown wearing it",
        "garment references — reproduce these exact clothing items on the "
        "subject. Copy ONLY the garments: ignore the face, body and background "
        "of whoever is shown wearing them",
    ),
    "product": (
        "a product reference — reproduce this exact object. Ignore any person "
        "shown holding it",
        "product references — reproduce these exact objects. Ignore any person "
        "shown holding them",
    ),
    "extra": (
        "the SAME person photographed from another angle or in different "
        "lighting — use it to confirm bone structure and body proportions. It "
        "is the same individual, never a different person",
        "the SAME person photographed from other angles or in different "
        "lighting — use them to confirm bone structure and body proportions. "
        "They are the same individual, never different people",
    ),
}


def montar_instrucao_posicional(papeis: list[str]) -> str:
    """Diz, por posição, o que o modelo deve fazer com cada imagem.

    `papeis` vem NA MESMA ORDEM do array de referências enviado ao provider.
    Descrever a segunda imagem enquanto se envia outra na segunda posição é
    pior que não descrever nada: o modelo segue o texto.

    Papéis repetidos são agrupados ("The third and fourth are garment
    references"), porque enumerar cinco vezes a mesma frase dilui o resto do
    prompt — e prompt longo demais faz o modelo ignorar o começo.
    """
    if not papeis:
        return ""

    # Agrupa posições consecutivas de mesmo papel.
    grupos: list[tuple[str, list[int]]] = []
    for indice, papel in enumerate(papeis):
        if grupos and grupos[-1][0] == papel:
            grupos[-1][1].append(indice)
        else:
            grupos.append((papel, [indice]))

    partes: list[str] = []
    for papel, posicoes in grupos:
        formas = _INSTRUCAO_POR_PAPEL.get(papel)
        if not formas:
            continue
        nomes = [
            _ORDINAIS[i] if i < len(_ORDINAIS) else f"{i + 1}th" for i in posicoes
        ]
        if len(nomes) == 1:
            partes.append(f"The {nomes[0]} reference image is {formas[0]}.")
        else:
            lista = ", ".join(nomes[:-1]) + f" and {nomes[-1]}"
            partes.append(f"The {lista} reference images are {formas[1]}.")

    return " REFERENCE IMAGES — " + " ".join(partes)
