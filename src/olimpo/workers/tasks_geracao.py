"""Execução da geração de mídia.

O estado vive em `generation_jobs`; a execução é do Celery. Na origem havia
uma fila própria com um `setInterval` de 4s dentro do processo web — e um
job síncrono que morresse no meio ficava preso em `running` para sempre, com
o crédito já gasto. Aqui isso desaparece: ack tardio devolve a task à fila
se o worker morrer, e o retry é do broker.

O que NÃO é automático e por isso está explícito abaixo: geração de imagem
com referência **não pode ser repetida cegamente**. Cada tentativa custa
dinheiro e produz uma imagem diferente. O retry só vale para falha de rede
antes de o provider aceitar o pedido.
"""

from __future__ import annotations

import asyncio
import json
from typing import Any
from uuid import UUID

import structlog
from sqlalchemy import text

from olimpo.core.db import engine, session_scope
from olimpo.media.servico import PedidoDeFoto, gerar_foto
from olimpo.workers.app import app

log = structlog.get_logger(__name__)


def _rodar(corrotina: Any) -> Any:
    """Ponte entre o Celery (síncrono) e o serviço (assíncrono).

    O `dispose` no fim não é zelo: o pool de conexões do SQLAlchemy fica
    preso ao event loop que o criou, e `asyncio.run` cria um loop NOVO a
    cada task. Sem descartar o pool, a segunda task do mesmo processo recebe
    conexões de um loop já morto e falha com "attached to a different loop".

    O sintoma é traiçoeiro porque a PRIMEIRA task sempre funciona.
    """
    async def com_limpeza() -> Any:
        try:
            return await corrotina
        finally:
            await engine.dispose()

    return asyncio.run(com_limpeza())


async def _marcar(job_id: UUID, **campos: Any) -> None:
    if not campos:
        return
    sets = []
    params: dict[str, Any] = {"id": job_id}
    for chave, valor in campos.items():
        if chave == "status":
            sets.append("status = CAST(:status AS generation_status)")
        elif chave in {"request", "result_asset_ids"}:
            sets.append(f"{chave} = :{chave}")
        else:
            sets.append(f"{chave} = :{chave}")
        params[chave] = valor
    async with session_scope() as s:
        await s.execute(
            text(f"UPDATE generation_jobs SET {', '.join(sets)} WHERE id = :id"), params
        )


@app.task(
    name="olimpo.workers.tasks.produce_image",
    bind=True,
    acks_late=True,
    # Só uma repetição, e ela existe para falha de REDE antes do provider
    # aceitar. Repetir depois disso paga duas vezes pela mesma foto.
    max_retries=1,
    default_retry_delay=15,
)
def produzir_imagem(self: Any, job_id: str) -> dict[str, Any]:
    """Executa um job de geração de imagem."""
    identificador = UUID(job_id)

    async def executar() -> dict[str, Any]:
        async with session_scope() as s:
            job = (await s.execute(text("""
                SELECT id, persona_id, status, request, attempts
                  FROM generation_jobs WHERE id = :id
            """), {"id": identificador})).mappings().first()

        if not job:
            return {"erro": "job não encontrado"}
        # Um job já concluído chegando de novo significa que o worker morreu
        # DEPOIS de terminar e antes do ack. Repetir aqui seria gerar de novo
        # e pagar de novo.
        if job["status"] in ("done", "cancelled"):
            log.info("geracao.ja_concluida", job_id=job_id, status=job["status"])
            return {"ignorado": job["status"]}

        async with session_scope() as s:
            await s.execute(text("""
                UPDATE generation_jobs
                   SET status = 'running', progress = 10, started_at = now(),
                       attempts = attempts + 1
                 WHERE id = :id
            """), {"id": identificador})

        pedido_bruto = dict(job["request"] or {})
        pedido = PedidoDeFoto(
            persona_id=job["persona_id"],
            **{k: v for k, v in pedido_bruto.items()
               if k in PedidoDeFoto.__dataclass_fields__ and k != "persona_id"},
        )

        try:
            assets = await gerar_foto(pedido, job_id=identificador)
        except Exception as exc:
            async with session_scope() as s:
                await s.execute(text("""
                    UPDATE generation_jobs
                       SET status = 'error', error = :erro, finished_at = now()
                     WHERE id = :id
                """), {"id": identificador, "erro": str(exc)[:1000]})
            log.error("geracao.falhou", job_id=job_id, erro=str(exc))
            raise

        async with session_scope() as s:
            await s.execute(text("""
                UPDATE generation_jobs
                   SET status = 'done', progress = 100, finished_at = now(),
                       result_asset_ids = :assets, error = NULL
                 WHERE id = :id
            """), {"id": identificador, "assets": assets})

        log.info("geracao.concluida", job_id=job_id, assets=len(assets))
        return {"assets": [str(a) for a in assets]}

    return _rodar(executar())


@app.task(name="olimpo.workers.tasks.produce_reconcile_generation")
def reconciliar_geracao() -> dict[str, int]:
    """Resgata jobs presos.

    Um job em `running` há muito tempo significa worker morto no meio. Não
    reenfileiramos automaticamente: a geração pode ter acontecido do lado do
    provider, e refazer custaria de novo. Vai para erro, com o motivo dito, e
    quem decide é humano.
    """
    async def executar() -> dict[str, int]:
        async with session_scope() as s:
            presos = (await s.execute(text("""
                UPDATE generation_jobs
                   SET status = 'error',
                       error = 'preso em running por mais de 30 min — o worker '
                               'provavelmente morreu no meio. A geração PODE ter '
                               'acontecido no provider: verifique antes de repetir.',
                       finished_at = now()
                 WHERE status = 'running' AND started_at < now() - interval '30 minutes'
                RETURNING id
            """))).fetchall()
        if presos:
            log.warning("geracao.presos_resgatados", quantidade=len(presos))
        return {"resgatados": len(presos)}

    return _rodar(executar())


async def enfileirar_foto(pedido: PedidoDeFoto) -> UUID:
    """Cria o job e manda para a fila. Devolve o id para acompanhar."""
    # `slots=True` no dataclass elimina o __dict__ — os campos vêm da
    # declaração, não da instância.
    corpo = {
        campo: (str(valor) if isinstance(valor, UUID) else valor)
        for campo in PedidoDeFoto.__dataclass_fields__
        if campo != "persona_id"
        and (valor := getattr(pedido, campo)) is not None
    }
    async with session_scope() as s:
        job_id = (await s.execute(text("""
            INSERT INTO generation_jobs (persona_id, kind, status, request, model)
            VALUES (:persona_id, 'image', 'queued', CAST(:request AS jsonb), :model)
            RETURNING id
        """), {
            "persona_id": pedido.persona_id,
            "request": json.dumps(corpo, default=str),
            "model": pedido.modelo,
        })).scalar()

    produzir_imagem.apply_async(args=[str(job_id)], queue="production")
    return job_id


# =============================================================================
# Identidade visual
# =============================================================================

@app.task(name="olimpo.workers.tasks.produce_identity", acks_late=True, max_retries=0)
def produzir_ancora(job_id: str) -> dict[str, Any]:
    """Gera uma âncora derivada (folha de personagem ou close).

    Sem retry: cada tentativa custa e produz uma imagem diferente. Falhou,
    o operador decide se tenta de novo.
    """
    from olimpo.media import identidade

    identificador = UUID(job_id)

    async def executar() -> dict[str, Any]:
        async with session_scope() as s:
            job = (await s.execute(text("""
                SELECT persona_id, status, request FROM generation_jobs WHERE id = :id
            """), {"id": identificador})).mappings().first()
        if not job or job["status"] in ("done", "cancelled"):
            return {"ignorado": True}

        pedido = job["request"] or {}
        async with session_scope() as s:
            await s.execute(text("""
                UPDATE generation_jobs SET status = 'running', progress = 10,
                       started_at = now(), attempts = attempts + 1
                 WHERE id = :id
            """), {"id": identificador})

        try:
            alvo = pedido.get("ancora")
            if alvo == "face":
                res = await identidade.propor_rostos(
                    job["persona_id"],
                    quantidade=int(pedido.get("quantidade", 3)),
                    seed=pedido.get("seed"), modelo=pedido.get("modelo"),
                    aspect_ratio=pedido.get("aspect_ratio") or "9:16",
                    resolucao=pedido.get("resolucao"),
                    referencias_entrada=[
                        identidade.ReferenciaDeEntrada(
                            asset_id=UUID(r["asset_id"]),
                            papel=r.get("papel", "face"),
                            nota=r.get("nota", ""),
                        )
                        for r in (pedido.get("referencias") or [])
                    ],
                )
                assets = [r.asset_id for r in res]
            elif alvo == "wardrobe":
                assets = [await identidade.gerar_figurino(
                    job["persona_id"], nome=pedido.get("nome", ""),
                    top=pedido.get("top", ""), bottom=pedido.get("bottom", ""),
                    calcado=pedido.get("calcado", ""),
                    cabelo=pedido.get("cabelo", ""),
                    texto_livre=pedido.get("texto_livre", ""),
                    modelo=pedido.get("modelo"),
                    aspect_ratio=pedido.get("aspect_ratio"),
                    resolucao=pedido.get("resolucao"),
                    referencias_extras=[
                        UUID(x) for x in (pedido.get("referencias_extras") or [])
                    ],
                )]
            else:
                res = await identidade.gerar_ancora(
                    job["persona_id"], alvo, modelo=pedido.get("modelo"),
                    aspect_ratio=pedido.get("aspect_ratio"),
                    resolucao=pedido.get("resolucao"),
                )
                assets = [res.asset_id]
        except Exception as exc:
            async with session_scope() as s:
                await s.execute(text("""
                    UPDATE generation_jobs SET status = 'error', error = :e,
                           finished_at = now() WHERE id = :id
                """), {"id": identificador, "e": str(exc)[:1000]})
            log.error("identidade.falhou", job_id=job_id, erro=str(exc))
            raise

        async with session_scope() as s:
            await s.execute(text("""
                UPDATE generation_jobs SET status = 'done', progress = 100,
                       finished_at = now(), result_asset_ids = :a, error = NULL
                 WHERE id = :id
            """), {"id": identificador, "a": assets})
        return {"assets": [str(a) for a in assets]}

    return _rodar(executar())


async def enfileirar_identidade(
    persona_id: UUID, ancora: str, **opcoes: Any
) -> UUID:
    """Cria o job de identidade e manda para a fila."""
    async with session_scope() as s:
        job_id = (await s.execute(text("""
            INSERT INTO generation_jobs (persona_id, kind, status, request, model)
            VALUES (:p, 'image', 'queued', CAST(:req AS jsonb), :modelo)
            RETURNING id
        """), {
            "p": persona_id,
            "req": json.dumps({"ancora": ancora, **opcoes}, default=str),
            "modelo": opcoes.get("modelo"),
        })).scalar()

    produzir_ancora.apply_async(args=[str(job_id)], queue="production")
    return job_id
