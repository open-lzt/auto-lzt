"""BaseRepo ABC (tenant-scoped by construction) + async session factory."""

from __future__ import annotations

from abc import ABC, abstractmethod
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Any, Generic, TypeVar

from sqlalchemy.dialects.postgresql import insert as _pg_insert
from sqlalchemy.dialects.sqlite import insert as _sqlite_insert
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)
from sqlalchemy.orm import DeclarativeBase

from app.domain.account.model import TenantId


def dialect_insert(sessionmaker: async_sessionmaker[AsyncSession]) -> Any:
    """The right INSERT construct for the bound engine — both Postgres (prod) and SQLite (dev)
    expose ``.on_conflict_do_nothing(index_elements=...)`` with the same API."""
    bind = sessionmaker.kw.get("bind")
    name = getattr(getattr(bind, "dialect", None), "name", "postgresql")
    return _sqlite_insert if name == "sqlite" else _pg_insert


TDoc = TypeVar("TDoc")
TId = TypeVar("TId")


class Base(DeclarativeBase):
    """Declarative base for all lzt-flow ORM models."""


def make_engine(database_url: str) -> AsyncEngine:
    return create_async_engine(database_url, pool_pre_ping=True)


def make_sessionmaker(engine: AsyncEngine) -> async_sessionmaker[AsyncSession]:
    return async_sessionmaker(engine, expire_on_commit=False)


class BaseRepo(ABC, Generic[TDoc, TId]):
    """CRUD behind an ABC. EVERY read/write takes tenant_id explicitly — there is no
    tenant-unscoped path even with a single tenant (closes the missing-user_id-filter killer)."""

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    @abstractmethod
    async def get(self, tenant_id: TenantId, id_: TId) -> TDoc | None: ...

    @abstractmethod
    async def list(self, tenant_id: TenantId) -> list[TDoc]: ...

    @abstractmethod
    async def create(self, tenant_id: TenantId, doc: TDoc) -> TDoc: ...

    @abstractmethod
    async def update(self, tenant_id: TenantId, doc: TDoc) -> TDoc: ...


class BaseSessionmakerRepo(ABC, Generic[TDoc, TId]):
    """Second repo lineage, session-per-call (not session-per-request like ``BaseRepo``).

    Holds a ``sessionmaker`` and opens its own ``session_scope`` per method, so each operation
    commits independently. Required by the flow engine's two-phase commit / optimistic-locking
    (Run.claim/touch each need their own immediately-visible transaction) — do not collapse this
    into ``BaseRepo``'s single-session model. No abstract CRUD methods are forced here: Flow/Run/
    Trigger repos have genuinely different method shapes (claim/touch vs create/list_by_flow), so
    a uniform signature would be a fake abstraction rather than a real shared contract.
    """

    def __init__(self, sessionmaker: async_sessionmaker[AsyncSession]) -> None:
        self._sm = sessionmaker


@asynccontextmanager
async def session_scope(
    sessionmaker: async_sessionmaker[AsyncSession],
) -> AsyncIterator[AsyncSession]:
    """A transactional session scope: commit on success, rollback on error."""
    async with sessionmaker() as session:
        try:
            yield session
            await session.commit()
        except Exception:
            await session.rollback()
            raise
