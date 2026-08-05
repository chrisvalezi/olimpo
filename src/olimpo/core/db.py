"""Sessão de banco (SQLAlchemy async).

O Postgres é a única fonte da verdade do sistema — inclusive do agendamento
(ADR-0004). Redis é fila, lock e cache: nada que doa perder.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from sqlalchemy.ext.asyncio import (
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

from olimpo.core.config import settings

engine = create_async_engine(
    settings.database_url,
    echo=False,
    pool_size=10,
    max_overflow=20,
    # Conexão ociosa reciclada antes que o Postgres a derrube — evita o
    # clássico "server closed the connection unexpectedly" no worker.
    pool_recycle=1800,
    pool_pre_ping=True,
)

SessionFactory = async_sessionmaker(
    engine, class_=AsyncSession, expire_on_commit=False, autoflush=False
)


@asynccontextmanager
async def session_scope() -> AsyncIterator[AsyncSession]:
    """Sessão transacional: commit no sucesso, rollback na exceção."""
    session = SessionFactory()
    try:
        yield session
        await session.commit()
    except Exception:
        await session.rollback()
        raise
    finally:
        await session.close()


async def get_session() -> AsyncIterator[AsyncSession]:
    """Dependência do FastAPI."""
    async with session_scope() as session:
        yield session


async def healthcheck() -> bool:
    from sqlalchemy import text

    try:
        async with SessionFactory() as session:
            await session.execute(text("SELECT 1"))
        return True
    except Exception:
        return False
