"""AccountService — orchestrates token encryption, persistence, and pool invalidation."""

from __future__ import annotations

from datetime import UTC, datetime
from uuid import uuid4

import structlog
from sqlalchemy.exc import IntegrityError

from app.core.exceptions import Conflict
from app.domain.account.crypto import EnvelopeCipher
from app.domain.account.errors import AccountInUse, AccountNotFound
from app.domain.account.model import Account, AccountId, AccountStatus, TenantId
from app.domain.account.pool import TokenPool
from app.domain.account.repo import AccountRepository
from app.domain.market.adapter import MarketAdapter

log = structlog.get_logger()


class AccountService:
    def __init__(
        self,
        repo: AccountRepository,
        cipher: EnvelopeCipher,
        pool: TokenPool,
        market_base_url: str | None = None,
    ) -> None:
        self._repo = repo
        self._cipher = cipher
        self._pool = pool
        self._market_base_url = market_base_url

    async def refresh_profile(self, tenant_id: TenantId, account_id: AccountId) -> Account:
        """Fetch this account's nickname and balance from the marketplace and store them.

        Deliberately a PINNED adapter (one token), not ``pool.acquire()``: the pooled Client
        round-robins across every account of the tenant, so ``profile_get`` through it would
        return whichever account the rotation happened to land on — each account's balance
        would be some other account's. The one call that must speak as a specific credential
        cannot go through the pool.
        """
        account = await self._repo.get(tenant_id, account_id)
        if account is None:
            raise AccountNotFound(account_id)
        token = self._cipher.decrypt(account.encrypted_token, tenant_id)
        adapter = MarketAdapter(token=token, account_id=account_id, base_url=self._market_base_url)
        profile = await adapter.profile()
        return await self._repo.save_profile(
            tenant_id,
            account_id,
            username=profile.username,
            balance=profile.balance,
            currency=profile.currency,
            synced_at=datetime.now(UTC),
        )

    async def add_account(self, tenant_id: TenantId, token: str) -> Account:
        # Ask the marketplace before storing it ACTIVE. A token that is never checked joins the
        # rotation pool anyway and fails at the first call that matters — which is how a
        # throwaway token silently became the account an autobuy run picked, then died on
        # TokenInvalid mid-run. Refusing here costs one request and moves the failure to the
        # moment a human is watching.
        await MarketAdapter(token=token, base_url=self._market_base_url).verify_token()

        account = Account(
            id=AccountId(uuid4()),
            tenant_id=tenant_id,
            encrypted_token=self._cipher.encrypt(token, tenant_id),
            created_at=datetime.now(UTC),
            status=AccountStatus.ACTIVE,
            token_hash=self._cipher.fingerprint_token(token),
        )
        await self._repo.create(tenant_id, account)
        await self._pool.invalidate(tenant_id)
        try:
            return await self.refresh_profile(tenant_id, account.id)
        except Exception:  # noqa: BLE001 — enrichment boundary: fetching the nickname and
            # balance is a nicety, storing the credential is the operation. A narrow
            # `except (TokenInvalid, MarketApiError)` looked right and was not: an upstream
            # whose response shape drifts raises something else entirely, and adding an account
            # then failed outright — the enrichment taking the operation down with it.
            # The account lands with blank profile fields and a «Обновить» button, which reads
            # as "not fetched yet" rather than pretending a balance of zero.
            log.warning("account_profile_unavailable_on_add", account_id=str(account.id))
            return account

    async def reactivate(self, tenant_id: TenantId, account_id: AccountId) -> None:
        await self._repo.update_status(tenant_id, account_id, AccountStatus.ACTIVE)
        await self._pool.invalidate(tenant_id)

    async def list_accounts(self, tenant_id: TenantId) -> list[Account]:
        return await self._repo.list(tenant_id)

    async def set_label(
        self, tenant_id: TenantId, account_id: AccountId, label: str | None
    ) -> Account:
        try:
            return await self._repo.set_label(tenant_id, account_id, label)
        except KeyError as exc:
            raise AccountNotFound(account_id) from exc
        except IntegrityError as exc:
            raise Conflict(
                f"tenant {tenant_id} already has an account labeled {label!r}",
                client_message="Этот ярлык уже используется",
            ) from exc

    async def delete_account(self, tenant_id: TenantId, account_id: AccountId) -> None:
        """Refuses (never cascades) when a live-scheduled flow still pins this account.

        The check and the delete both run through ``self._repo``, which holds the ONE session
        that ``get_account_service`` opens for the whole request (session_scope commits only
        after the handler returns) — so they already share one uncommitted transaction and a
        trigger inserted mid-request can't slip past the check. No extra sessionmaker needed.
        """
        blocking = await self._repo.flows_referencing(tenant_id, account_id)
        if blocking:
            raise AccountInUse(account_id, blocking)
        deleted = await self._repo.delete(tenant_id, account_id)
        if not deleted:
            raise AccountNotFound(account_id)
        await self._pool.invalidate(tenant_id)
