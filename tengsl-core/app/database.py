"""Асинхронный движок и сессии SQLAlchemy."""

from __future__ import annotations

from collections.abc import AsyncGenerator

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.config import settings

engine = create_async_engine(settings.database_url, pool_pre_ping=True)
async_session_factory = async_sessionmaker(engine, expire_on_commit=False)


async def get_session() -> AsyncGenerator[AsyncSession, None]:
    """FastAPI-зависимость для получения сессии БД."""
    async with async_session_factory() as session:
        yield session
