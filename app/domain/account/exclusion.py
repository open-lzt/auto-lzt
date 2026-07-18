"""Account exclusion — the single durable path that takes a token out of rotation.

pylzt already auto-quarantines a token on the first ``AuthFailed`` inside ``lease()`` and owns
retry / timeout / rate-limit internally, so lzt-flow adds only the durable half pylzt can't do:
persist ``AccountStatus.EXCLUDED`` to Postgres (the source of truth for every pool rebuild), then
best-effort quarantine the live in-memory pool so the token stops rotating before the next rebuild.

Any ``AuthFailed`` surfaced from the adapter maps to ``TokenInvalid`` and calls ``exclude_account``
directly (auth failures are immediate and unambiguous — they carry the offending account_id).
"""

from __future__ import annotations

from datetime import UTC, datetime

import structlog
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.db.base import session_scope
from app.domain.account.events import TokenInvalidEvent
from app.domain.account.model import AccountId, AccountStatus, TenantId
from app.domain.account.pool import TokenPool
from app.domain.account.repo import AccountRepository

log = structlog.get_logger()


class AccountExcluder:
    """Postgres-first exclusion: durable status write, then best-effort in-memory quarantine."""

    def __init__(self, sessionmaker: async_sessionmaker[AsyncSession], pool: TokenPool) -> None:
        self._sessionmaker = sessionmaker
        self._pool = pool

    async def exclude_account(self, tenant_id: TenantId, account_id: AccountId) -> None:
        # 1. durable fact first — Postgres is the source of truth for pool (re)builds.
        async with session_scope(self._sessionmaker) as session:
            await AccountRepository(session).update_status(
                tenant_id, account_id, AccountStatus.EXCLUDED
            )
        # 2. sync the runtime pool so the token stops rotating before the next rebuild.
        self._pool.quarantine_account(tenant_id, account_id)
        # 3. emit the fact (logged until an event bus exists).
        event = TokenInvalidEvent(
            account_id=account_id, tenant_id=tenant_id, occurred_at=datetime.now(UTC)
        )
        log.warning(
            "account_excluded",
            account_id=str(event.account_id),
            tenant_id=str(event.tenant_id),
        )
