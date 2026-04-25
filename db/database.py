"""
Database engine and async session factory.

`init_db()` is called once at application startup to create tables if they
don't exist. Schema is intentionally simple — this is a single-user bot.
"""

from __future__ import annotations

import logging
from contextlib import asynccontextmanager
from typing import AsyncIterator

from sqlalchemy.ext.asyncio import (
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

from config import settings
from db.models import Base

logger = logging.getLogger(__name__)

_engine = create_async_engine(
    settings.DATABASE_URL,
    pool_pre_ping=True,
    pool_size=5,
    max_overflow=5,
    echo=False,
    future=True,
)

_SessionFactory = async_sessionmaker(
    _engine,
    class_=AsyncSession,
    expire_on_commit=False,
    autoflush=False,
)


async def init_db() -> None:
    """Create tables if they don't exist. Safe to call repeatedly."""
    async with _engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    logger.info("Database initialised — tables ensured.")


async def shutdown_db() -> None:
    """Dispose of the connection pool cleanly on shutdown."""
    await _engine.dispose()
    logger.info("Database connection pool disposed.")


@asynccontextmanager
async def session_scope() -> AsyncIterator[AsyncSession]:
    """
    Async context manager yielding a session that commits on success
    and rolls back on exception.
    """
    session = _SessionFactory()
    try:
        yield session
        await session.commit()
    except Exception:
        await session.rollback()
        raise
    finally:
        await session.close()
