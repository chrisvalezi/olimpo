"""Mídia: identidade visual das personas e conteúdo gerado.

Os arquivos ficam no armazenamento do Olimpo, não no CDN do provider. Não é
preciosismo: as URLs do fal expiram e o CDN de onde essas imagens vieram vai
ser desligado. Uma imagem de identidade perdida não se regenera — não existe
como pedir "o mesmo rosto de antes".
"""

from __future__ import annotations

import pathlib
from typing import Any, Literal
from uuid import UUID

from fastapi import APIRouter, File, Form, HTTPException, Query, UploadFile
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field
from sqlalchemy import text

from olimpo.core.db import session_scope
from olimpo.media import modelos
from olimpo.media.servico import PedidoDeFoto
from olimpo.prompts import photo_studio
from olimpo.media import armazenamento, conversao, identidade as identidade_svc
from olimpo.workers.tasks_geracao import enfileirar_foto, enfileirar_identidade

router = APIRouter(prefix="/v1/media", tags=["mídia"])

RAIZ = pathlib.Path("/app/media")

#: Ordem em que as âncoras de identidade entram no array de referências.
#: É contrato semântico do prompt: a primeira é o rosto, a segunda é a roupa.
#: Reordenar faz o modelo copiar a roupa da referência facial.
ORDEM_IDENTIDADE = [
    ("face_asset_id", "face", "rosto"),
    ("character_sheet_asset_id", "character_sheet", "folha de personagem"),
    ("close_up_face_asset_id", "close_up_face", "close de rosto"),
    ("close_up_detail_asset_id", "close_up_detail", "cartela de detalhe"),
]


@router.get("/{asset_id}/arquivo")
async def servir(asset_id: UUID) -> FileResponse:
    """Serve o arquivo. Cache longo: o nome vem do hash do conteúdo, então
    um arquivo nunca muda — se mudasse, teria outro id."""
    async with session_scope() as s:
        caminho = (await s.execute(
            text("SELECT storage_path FROM media_assets WHERE id = :id"),
            {"id": asset_id},
        )).scalar()
    if not caminho:
        raise HTTPException(404, "mídia não encontrada")

    arquivo = RAIZ / caminho
    # O caminho vem do banco, mas confiar nele sem checar permitiria que uma
    # linha adulterada lesse qualquer arquivo do container.
    try:
        arquivo.resolve().relative_to(RAIZ.resolve())
    except ValueError:
        raise HTTPException(400, "caminho inválido") from None
    if not arquivo.exists():
        raise HTTPException(404, "arquivo ausente no armazenamento")

    return FileResponse(
        arquivo, headers={"Cache-Control": "public, max-age=31536000, immutable"}
    )


@router.get("")
async def listar(
    persona_id: UUID | None = None,
    role: str | None = None,
    kind: str | None = None,
    page: int = Query(1, ge=1),
    per_page: int = Query(60, ge=1, le=200),
) -> dict[str, Any]:
    where, params = [], {"limit": per_page, "offset": (page - 1) * per_page}
    if persona_id:
        where.append("m.persona_id = :persona_id")
        params["persona_id"] = persona_id
    if role:
        where.append("m.role = CAST(:role AS media_role)")
        params["role"] = role
    if kind:
        where.append("m.kind = CAST(:kind AS media_kind)")
        params["kind"] = kind
    clause = f"WHERE {' AND '.join(where)}" if where else ""

    async with session_scope() as s:
        rows = (await s.execute(text(f"""
            SELECT m.id, m.kind, m.role, m.label, m.source, m.provider, m.model,
                   m.aspect_ratio, m.width, m.height, m.bytes, m.created_at,
                   m.prompt, p.name AS persona_name
              FROM media_assets m
              LEFT JOIN personas p ON p.id = m.persona_id
              {clause}
             ORDER BY m.created_at DESC
             LIMIT :limit OFFSET :offset
        """), params)).mappings().all()
        total = (await s.execute(
            text(f"SELECT count(*) FROM media_assets m {clause}"), params
        )).scalar() or 0

    return {
        "media": [dict(r) for r in rows],
        "total": total, "page": page,
        "pages": max(1, -(-total // per_page)),
    }


@router.get("/personas/{persona_id}/identidade")
async def identidade(persona_id: UUID) -> dict[str, Any]:
    """As âncoras de identidade da persona, na ordem que a geração usa.

    Quantas existem importa mais que quais: com as quatro, o rosto se mantém
    entre gerações. Faltando a primeira, não há de onde ancorar.
    """
    async with session_scope() as s:
        p = (await s.execute(text("""
            SELECT id, name, face_asset_id, character_sheet_asset_id,
                   close_up_face_asset_id, close_up_detail_asset_id,
                   reference_asset_ids
              FROM personas WHERE id = :id
        """), {"id": persona_id})).mappings().first()
        if not p:
            raise HTTPException(404, "persona não encontrada")

        ids = [p[coluna] for coluna, _, _ in ORDEM_IDENTIDADE if p[coluna]]
        detalhes = {}
        if ids:
            for r in (await s.execute(text("""
                SELECT id, role, storage_path, width, height, bytes
                  FROM media_assets WHERE id = ANY(:ids)
            """), {"ids": ids})).mappings().all():
                detalhes[str(r["id"])] = dict(r)

    ancoras = []
    for posicao, (coluna, papel, rotulo) in enumerate(ORDEM_IDENTIDADE, start=1):
        asset_id = p[coluna]
        ancoras.append({
            # A posição é o que o prompt referencia como "@imageN".
            "posicao": posicao,
            "papel": papel,
            "rotulo": rotulo,
            "asset_id": str(asset_id) if asset_id else None,
            "detalhe": detalhes.get(str(asset_id)) if asset_id else None,
        })

    completas = sum(1 for a in ancoras if a["asset_id"])
    return {
        "persona": {"id": str(p["id"]), "name": p["name"]},
        "ancoras": ancoras,
        "completas": completas,
        "extras": [str(i) for i in (p["reference_asset_ids"] or [])],
        # Sem a primeira âncora não há identidade a preservar: qualquer
        # geração produz uma pessoa diferente.
        "pode_gerar_com_identidade": bool(p["face_asset_id"]),
    }


# =============================================================================
# Geração
# =============================================================================

class PedidoDeGeracao(BaseModel):
    """O que a tela envia. Tudo o mais vem da persona."""

    local: str = "coffee-shop"
    hora: str = "afternoon"
    pose: str = "front"
    stance: str = "standing"
    expressao: str = "natural"
    olhar: str = "at-camera"
    aspect_ratio: str = "9:16"
    quantidade: int = Field(default=1, ge=1, le=8)
    wardrobe_item_id: UUID | None = None
    wardrobe_text: str = ""
    hairstyle_text: str = ""
    brand_id: UUID | None = None
    prop_text: str = ""
    modelo: str | None = None
    resolucao: str | None = None
    #: Semente fixa reproduz o mesmo prompt. Serve para comparar variações
    #: mudando um parâmetro por vez — algo que a origem não permitia.
    seed: int | None = None


@router.get("/opcoes")
async def opcoes() -> dict[str, Any]:
    """Os valores que a tela oferece, vindos do próprio motor de prompt.

    Não são listas escritas à mão na interface: sair de sincronia com o
    motor produziria um pedido que gera prompt vazio no meio.
    """
    return {
        "locais": list(photo_studio.LOCATION_LABEL.keys()),
        "horas": list(photo_studio.TIME_ATMO.keys()),
        "poses": sorted(photo_studio.POSE_MAP.keys()),
        "expressoes": list(photo_studio.EXPRESSION_MAP.keys()),
        # Cada motor com o que aceita e se está utilizável. A tela precisa
        # das três coisas juntas: oferecer um modelo sem chave transforma a
        # escolha num job que morre no worker dez segundos depois.
        "modelos": [
            {
                "id": m.id,
                "label": m.label,
                "provider": m.provider,
                "resolucoes": list(m.resolucoes),
                "aspectos": list(m.aspectos_aceitos),
                "motivo": (motivo := modelos.motivo_de_indisponibilidade(m)),
                "disponivel": motivo is None,
            }
            for m in modelos.IMAGEM.values()
        ],
        "aspect_ratios": ["9:16", "4:5", "1:1", "16:9", "3:4"],
    }


@router.post("/personas/{persona_id}/gerar", status_code=202)
async def gerar(persona_id: UUID, body: PedidoDeGeracao) -> dict[str, Any]:
    """Enfileira uma geração. Devolve o job para acompanhar.

    Não espera pelo resultado: uma imagem leva de vinte segundos a alguns
    minutos, e segurar a conexão é como se perde trabalho já pago quando o
    processo reinicia.
    """
    async with session_scope() as s:
        persona = (await s.execute(text("""
            SELECT name, face_asset_id FROM personas WHERE id = :id
        """), {"id": persona_id})).mappings().first()
    if not persona:
        raise HTTPException(404, "persona não encontrada")
    if not persona["face_asset_id"]:
        raise HTTPException(
            400,
            f"{persona['name']} não tem referência de rosto. Sem ela cada "
            "geração produz uma pessoa diferente — gere o rosto antes.",
        )

    job_id = await enfileirar_foto(PedidoDeFoto(
        persona_id=persona_id, **body.model_dump()
    ))
    return {"job_id": str(job_id), "status": "queued"}


@router.get("/jobs/{job_id}")
async def estado_do_job(job_id: UUID) -> dict[str, Any]:
    async with session_scope() as s:
        job = (await s.execute(text("""
            SELECT g.*, p.name AS persona_name
              FROM generation_jobs g
              LEFT JOIN personas p ON p.id = g.persona_id
             WHERE g.id = :id
        """), {"id": job_id})).mappings().first()
    if not job:
        raise HTTPException(404, "job não encontrado")
    return dict(job)


@router.get("/jobs")
async def listar_jobs(
    persona_id: UUID | None = None,
    status: str | None = None,
    limite: int = Query(50, ge=1, le=200),
) -> dict[str, Any]:
    where, params = [], {"limite": limite}
    if persona_id:
        where.append("g.persona_id = :persona_id")
        params["persona_id"] = persona_id
    if status:
        where.append("g.status = CAST(:status AS generation_status)")
        params["status"] = status
    clause = f"WHERE {' AND '.join(where)}" if where else ""

    async with session_scope() as s:
        rows = (await s.execute(text(f"""
            SELECT g.id, g.persona_id, g.kind, g.status, g.progress, g.model,
                   g.route, g.error, g.created_at, g.finished_at,
                   g.result_asset_ids, p.name AS persona_name
              FROM generation_jobs g
              LEFT JOIN personas p ON p.id = g.persona_id
              {clause}
             ORDER BY g.created_at DESC LIMIT :limite
        """), params)).mappings().all()
    return {"jobs": [dict(r) for r in rows]}


# =============================================================================
# Upload e identidade visual
# =============================================================================

#: Tipos aceitos. Vídeo entra pelo mesmo caminho porque o armazenamento é o
#: mesmo — o que muda é o papel.
#:
#: HEIC/HEIF não está aqui porque não é ARMAZENADO: é convertido para JPEG
#: na entrada e o que chega ao armazenamento já é JPEG. Ver
#: `media/conversao.py`.
MIMES_ACEITOS = {
    "image/jpeg", "image/png", "image/webp", "image/gif",
    "video/mp4", "video/quicktime", "video/webm",
}
TAMANHO_MAXIMO = 200 * 1024 * 1024


@router.post("/upload", status_code=201)
async def enviar(
    arquivo: UploadFile = File(...),
    persona_id: UUID | None = Form(None),
    papel: str = Form("reference"),
    rotulo: str | None = Form(None),
) -> dict[str, Any]:
    """Recebe um arquivo e cria o asset.

    É por aqui que uma persona ganha rosto quando o operador já tem a foto —
    o caminho alternativo é gerar variações e escolher uma.
    """
    conteudo = await arquivo.read()
    if not conteudo:
        raise HTTPException(400, "arquivo vazio")
    if len(conteudo) > TAMANHO_MAXIMO:
        raise HTTPException(413, f"arquivo acima de {TAMANHO_MAXIMO // 1024 // 1024} MB")

    # Foto de iPhone chega em HEIC. A conversão vem ANTES da checagem de
    # tipo de propósito: o navegador manda o mime errado com frequência
    # (`application/octet-stream`, ou vazio), então quem decide é o byte.
    mime = arquivo.content_type or ""
    try:
        conteudo, mime, convertido = conversao.normalizar(
            conteudo, mime, arquivo.filename or ""
        )
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc

    # Quem manda são os BYTES, não o que o cliente declara — nas duas
    # direções:
    #
    #   declarou errado  `curl` manda `application/octet-stream` para toda
    #                    extensão que não conhece, e um webp válido era
    #                    recusado pelo envelope em vez de pelo conteúdo.
    #   declarou de mais renomear um PDF para `.jpg` fazia o cliente anunciar
    #                    `image/jpeg`, e como o tipo estava na lista, entrava.
    #                    A falha só apareceria na geração, quando o provider
    #                    não decodificasse — longe daqui e sem explicação.
    # Não reconhecer é motivo de recusa, não motivo para voltar a confiar no
    # que o cliente disse: um arquivo cujos bytes não são de nenhum formato
    # conhecido não vira imagem por ser chamado de `.jpg`.
    detectado = conversao.detectar_mime(conteudo)
    mime = detectado or ""
    if mime not in MIMES_ACEITOS:
        raise HTTPException(
            400,
            f"o conteúdo deste arquivo é {detectado or 'de tipo não reconhecido'}"
            + (f" (o envio dizia {arquivo.content_type})"
               if arquivo.content_type and detectado
               and arquivo.content_type != detectado else "")
            + f". Aceitos: {', '.join(sorted(MIMES_ACEITOS))}",
        )

    asset_id = await armazenamento.guardar(
        conteudo, persona_id=persona_id, role=papel,
        kind="video" if mime.startswith("video/") else "image",
        mime=mime, label=rotulo or arquivo.filename, source="upload",
    )

    # Enviar direto para uma âncora é o fluxo natural de quem já tem a foto.
    if persona_id and papel in identidade_svc.COLUNA_POR_ANCORA:
        await identidade_svc.definir_ancora(persona_id, papel, asset_id)

    return {"asset_id": str(asset_id), "papel": papel, "convertido": convertido}


class ReferenciaEntrada(BaseModel):
    """Uma foto que guia a geração, com o papel dela declarado."""

    asset_id: UUID
    #: face — recria este rosto · style — copie a pose, roupa e luz, NUNCA o
    #: rosto · extra — a MESMA pessoa em outro ângulo, reforça a identidade.
    papel: Literal["face", "style", "extra"] = "face"
    #: Sobrescreve a regra padrão. "use o cabelo desta" é mais específico
    #: que qualquer instrução genérica.
    nota: str = ""


class PedidoDeAncora(BaseModel):
    modelo: str | None = None
    #: Vazio usa o padrão da âncora, que não é escolha estética — a folha
    #: de personagem precisa de largura para os 4 painéis.
    aspect_ratio: str | None = None
    resolucao: str | None = None
    quantidade: int = Field(default=3, ge=1, le=4)
    seed: int | None = None
    #: Quantas quiser. A ordem é reorganizada no serviço — rosto, estilo,
    #: extras — porque a instrução fala em "a primeira" e "a segunda".
    referencias: list[ReferenciaEntrada] = []


@router.post("/personas/{persona_id}/ancoras/{ancora}", status_code=202)
async def gerar_ancora(
    persona_id: UUID, ancora: str, body: PedidoDeAncora | None = None
) -> dict[str, Any]:
    """Gera uma âncora de identidade.

    `face` propõe variações para o operador escolher, e aceita fotos de
    referência — quantas ele tiver. As demais derivam do rosto, usando-o
    como referência; é essa cadeia que faz todas serem da mesma pessoa.
    """
    body = body or PedidoDeAncora()
    if ancora not in identidade_svc.COLUNA_POR_ANCORA:
        raise HTTPException(
            400, f"âncora desconhecida: {ancora}. "
                 f"Use uma de {sorted(identidade_svc.COLUNA_POR_ANCORA)}"
        )

    async with session_scope() as s:
        p = (await s.execute(text("""
            SELECT name, face_asset_id, physical_desc FROM personas WHERE id = :id
        """), {"id": persona_id})).mappings().first()
    if not p:
        raise HTTPException(404, "persona não encontrada")

    if ancora == "face" and not (p["physical_desc"] or "").strip() and not body.referencias:
        raise HTTPException(
            400,
            f"{p['name']} não tem descrição física nem foto de referência. "
            "Uma das duas é a identidade — sem nenhuma, o modelo inventa uma "
            "pessoa diferente a cada geração.",
        )
    if ancora != "face" and not p["face_asset_id"]:
        raise HTTPException(
            400,
            f"{p['name']} não tem rosto definido. A folha de personagem e os "
            "closes derivam dele — sem o rosto, cada um sairia de uma pessoa "
            "diferente.",
        )

    job_id = await enfileirar_identidade(
        persona_id, ancora, modelo=body.modelo, quantidade=body.quantidade,
        seed=body.seed, aspect_ratio=body.aspect_ratio,
        resolucao=body.resolucao,
        referencias=[r.model_dump(mode="json") for r in body.referencias],
    )
    return {"job_id": str(job_id), "status": "queued", "ancora": ancora}


@router.delete("/personas/{persona_id}/ancoras/{ancora}", status_code=200)
async def aposentar_ancora(persona_id: UUID, ancora: str) -> dict[str, Any]:
    """Desliga a âncora sem apagar a imagem.

    Quem testa outra geração não quer perder a anterior: o asset continua na
    biblioteca, marcado como referência, e pode ser reescolhido depois. Só o
    vínculo é desfeito.

    Tirar o rosto derruba a identidade inteira — as outras três derivam
    dele —, então isso é dito em vez de acontecer em silêncio.
    """
    if ancora not in identidade_svc.COLUNA_POR_ANCORA:
        raise HTTPException(400, f"âncora desconhecida: {ancora}")
    coluna = identidade_svc.COLUNA_POR_ANCORA[ancora]

    async with session_scope() as s:
        antigo = (await s.execute(
            text(f"SELECT {coluna} FROM personas WHERE id = :p"), {"p": persona_id}
        )).scalar()
        if not antigo:
            return {"ok": True, "ja_estava_vazia": True}

        await s.execute(
            text(f"UPDATE personas SET {coluna} = NULL WHERE id = :p"),
            {"p": persona_id},
        )
        # Volta a ser candidata: continua na biblioteca e pode ser reescolhida.
        await s.execute(text("""
            UPDATE media_assets SET role = 'reference' WHERE id = :a
        """), {"a": antigo})

    return {
        "ok": True,
        "asset_liberado": str(antigo),
        "aviso": (
            "as outras âncoras derivam do rosto — sem ele, gerar qualquer uma "
            "produz outra pessoa"
        ) if ancora == "face" else None,
    }


@router.post("/personas/{persona_id}/ancoras/{ancora}/definir")
async def definir_ancora(
    persona_id: UUID, ancora: str, asset_id: UUID
) -> dict[str, Any]:
    """Escolhe qual asset é a âncora. É como uma variação vira o rosto."""
    if ancora not in identidade_svc.COLUNA_POR_ANCORA:
        raise HTTPException(400, f"âncora desconhecida: {ancora}")
    await identidade_svc.definir_ancora(persona_id, ancora, asset_id)
    return {"ok": True, "ancora": ancora, "asset_id": str(asset_id)}


# =============================================================================
# Fotos de referência
# =============================================================================
# Diferentes das quatro âncoras: não têm posição fixa no prompt nem papel
# semântico. São ângulos e corpo inteiro que entram como reforço em TODA
# geração — quanto mais a personagem aparece, menos o modelo inventa. Por
# isso vivem numa lista, não em colunas.

@router.post("/personas/{persona_id}/referencias/{asset_id}", status_code=200)
async def anexar_referencia(persona_id: UUID, asset_id: UUID) -> dict[str, Any]:
    """Junta uma foto ao acervo de referência da persona.

    `array_append` com guarda de duplicata em vez de append cego: a mesma
    foto duas vezes na lista é a mesma URL duas vezes no array de
    referências, e alguns modelos contam referências para decidir peso.
    """
    async with session_scope() as s:
        if not (await s.execute(
            text("SELECT 1 FROM media_assets WHERE id = :a"), {"a": asset_id}
        )).first():
            raise HTTPException(404, "asset não encontrado")

        atual = (await s.execute(text("""
            UPDATE personas
               SET reference_asset_ids = CASE
                     WHEN :a = ANY(COALESCE(reference_asset_ids, '{}'))
                       THEN reference_asset_ids
                     ELSE array_append(COALESCE(reference_asset_ids, '{}'), :a)
                   END
             WHERE id = :p
         RETURNING reference_asset_ids
        """), {"a": asset_id, "p": persona_id})).scalar()
        if atual is None:
            raise HTTPException(404, "persona não encontrada")

        # Marcar o papel é o que faz a galeria separar identidade de
        # conteúdo — e o que permite reconstruir se a lista for perdida.
        await s.execute(text("""
            UPDATE media_assets
               SET role = 'reference', persona_id = COALESCE(persona_id, :p)
             WHERE id = :a
        """), {"a": asset_id, "p": persona_id})

    return {"ok": True, "referencias": [str(i) for i in atual]}


@router.delete("/personas/{persona_id}/referencias/{asset_id}", status_code=200)
async def remover_referencia(persona_id: UUID, asset_id: UUID) -> dict[str, Any]:
    """Tira a foto do acervo. A imagem continua na biblioteca."""
    async with session_scope() as s:
        atual = (await s.execute(text("""
            UPDATE personas
               SET reference_asset_ids =
                     array_remove(COALESCE(reference_asset_ids, '{}'), :a)
             WHERE id = :p
         RETURNING reference_asset_ids
        """), {"a": asset_id, "p": persona_id})).scalar()
        if atual is None:
            raise HTTPException(404, "persona não encontrada")

    return {"ok": True, "referencias": [str(i) for i in atual]}


class PedidoDeFigurino(BaseModel):
    """Peça a peça, como na origem.

    Separar importa: o modelo trata cada peça como um item a reproduzir, não
    como uma descrição geral. "camiseta branca, jeans largo, tênis branco"
    produz resultado diferente de "look casual".
    """

    nome: str = ""
    top: str = ""
    bottom: str = ""
    calcado: str = ""
    cabelo: str = ""
    #: Sobrescreve os campos acima.
    texto_livre: str = ""
    modelo: str | None = None
    aspect_ratio: str | None = None
    resolucao: str | None = None
    #: Fotos de peças reais que a personagem deve vestir.
    referencias_extras: list[UUID] = []


@router.post("/personas/{persona_id}/figurinos", status_code=202)
async def gerar_figurino(persona_id: UUID, body: PedidoDeFigurino) -> dict[str, Any]:
    """Cria um figurino — a mesma personagem com outra roupa."""
    if not any([body.top, body.bottom, body.calcado, body.cabelo, body.texto_livre]):
        raise HTTPException(
            400, "descreva ao menos uma peça, ou use o texto livre"
        )
    job_id = await enfileirar_identidade(
        persona_id, "wardrobe", **body.model_dump(mode="json")
    )
    return {"job_id": str(job_id), "status": "queued"}


@router.get("/personas/{persona_id}/figurinos")
async def listar_figurinos(persona_id: UUID) -> dict[str, Any]:
    async with session_scope() as s:
        rows = (await s.execute(text("""
            SELECT w.id, w.name, w.description, w.asset_id, w.sort_order
              FROM wardrobe_items w WHERE w.persona_id = :p
             ORDER BY w.sort_order, w.created_at
        """), {"p": persona_id})).mappings().all()
    return {"figurinos": [dict(r) for r in rows]}


@router.delete("/{asset_id}", status_code=204)
async def apagar(asset_id: UUID) -> None:
    """Remove o registro. O arquivo fica — outro asset pode apontar para ele
    (dedupe por hash), e apagar às cegas quebraria o outro."""
    async with session_scope() as s:
        usado = (await s.execute(text("""
            SELECT count(*) FROM personas
             WHERE face_asset_id = :a OR character_sheet_asset_id = :a
                OR close_up_face_asset_id = :a OR close_up_detail_asset_id = :a
        """), {"a": asset_id})).scalar() or 0
        if usado:
            raise HTTPException(
                409,
                "este asset é âncora de identidade de uma persona. Trocar a "
                "âncora antes de apagar evita deixá-la sem referência.",
            )
        await s.execute(text("DELETE FROM media_assets WHERE id = :a"), {"a": asset_id})


# =============================================================================
# Marcas
# =============================================================================

marcas_router = APIRouter(prefix="/v1/brands", tags=["marcas"])


@marcas_router.get("")
async def listar_marcas(persona_id: UUID | None = None) -> dict[str, Any]:
    """Marcas disponíveis.

    Sem `persona_id`, devolve o catálogo geral mais as exclusivas de cada
    persona. Com ele, só as que aquela persona pode usar: as dela e as
    globais.
    """
    where = ""
    params: dict[str, Any] = {}
    if persona_id:
        where = "WHERE b.persona_id IS NULL OR b.persona_id = :p"
        params["p"] = persona_id

    async with session_scope() as s:
        rows = (await s.execute(text(f"""
            SELECT b.id, b.name, b.category, b.persona_id, b.default_mode,
                   b.product_sheet_asset_id, b.asset_ids, b.active,
                   p.name AS persona_name
              FROM brands b LEFT JOIN personas p ON p.id = b.persona_id
              {where}
             ORDER BY b.name
        """), params)).mappings().all()
    return {"brands": [dict(r) for r in rows]}


@marcas_router.post("", status_code=201)
async def criar_marca(
    name: str, category: str | None = None, persona_id: UUID | None = None,
    default_mode: str = "holding",
) -> dict[str, Any]:
    """Cadastra uma marca. As fotos do produto entram depois, por upload.

    `default_mode` decide como o produto entra na cena — segurado na mão ou
    vestido no corpo. Não dá para inferir da imagem, e a instrução no prompt
    muda completamente.
    """
    if default_mode not in ("holding", "wearing"):
        raise HTTPException(400, "modo deve ser 'holding' ou 'wearing'")
    async with session_scope() as s:
        row = (await s.execute(text("""
            INSERT INTO brands (name, category, persona_id, default_mode)
            VALUES (:n, :c, :p, :m) RETURNING *
        """), {"n": name, "c": category, "p": persona_id, "m": default_mode}
        )).mappings().one()
    return dict(row)


@marcas_router.post("/{brand_id}/fotos")
async def anexar_foto(brand_id: UUID, asset_id: UUID) -> dict[str, Any]:
    """Anexa uma foto do produto à marca."""
    async with session_scope() as s:
        await s.execute(text("""
            UPDATE brands
               SET asset_ids = (SELECT array_agg(DISTINCT x)
                                  FROM unnest(asset_ids || :a::uuid) x)
             WHERE id = :b
        """), {"a": asset_id, "b": brand_id})
        await s.execute(text("""
            UPDATE media_assets SET role = 'brand_product' WHERE id = :a
        """), {"a": asset_id})
    return {"ok": True}
