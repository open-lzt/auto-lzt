"""API-key gate for mutating endpoints, and the tenant resolution that rides on it.

A presented key names a TENANT. That makes this module the front door for identity, not only for
authorization: ``core/tenant.py`` reads what is resolved here rather than resolving a second time.

Fails CLOSED: with no key configured and no key rows seeded, mutations are blocked unless
``settings.allow_unauthenticated`` is explicitly set (the loopback-dev escape hatch).

Reads are NOT uniformly open, despite the name of this module. Only the routers a canvas needs
before anyone has authenticated stay public — ``catalog``, ``health``, ``auth/required``,
``panel/tabs``. Everything that reveals what THIS installation runs is gated whether it mutates or
not: runs, tasks, composites, a flow's triggers and its status all carry operator data (schedules,
active account counts, node graphs), so a read of them is as much an operator surface as a write.
A router is therefore either ``dependencies=protect(...)`` or deliberately public with a comment —
no third pattern, and "it's a GET" is not a reason.

MIGRATION WINDOW, stated because it is a security-relevant temporary: while ``api_keys`` is empty,
the configured ``settings.api_key`` still authenticates and maps to ``default_tenant_id``. That is
what keeps an existing single-tenant install working across the upgrade instead of locking its
operator out. It ends the moment the table has a row — seeding is therefore a one-way door, and the
0019 revision seeds it in the same transaction that creates the table.
"""

from __future__ import annotations

import hmac
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from uuid import UUID

from fastapi import Depends, Request, params

from app.core.config import Settings, get_settings
from app.core.exceptions import Unauthorized
from app.domain.account.model import TenantId
from app.domain.api_key.crypto import fingerprint_api_key
from app.domain.api_key.errors import ApiKeyNotFound, TenantNotFound
from app.domain.api_key.model import ApiKey, ApiKeyId, Tenant
from app.domain.api_key.repo import ApiKeyRepository

_API_KEY_HEADER = "X-API-Key"


@dataclass(slots=True, frozen=True)
class Principal:
    """Who is making this request — the single object every downstream consumer reads.

    Carries the resolved ROWS, not copies of selected fields: a route that needs the key's label or
    the tenant's plan reaches through here instead of going back to the database, and adding a
    column to either table does not mean threading a new argument through the dependency chain.

    Identity only — this says WHO, never HOW MUCH. Usage policy (per-tenant budgets, plans,
    metering) is not part of this project; a deployment that meters wraps these dependencies with
    its own. Keeping the two apart is what lets the same resolution serve a self-host that meters
    nothing and a deployment that meters everything.

    ``api_key`` is None on the migration-window path — there is no key row to point at, and a
    fabricated id would show up in logs as a key nobody can find.
    """

    tenant: Tenant
    api_key: ApiKey | None = None

    @property
    def tenant_id(self) -> TenantId:
        return self.tenant.id

    @property
    def api_key_id(self) -> ApiKeyId | None:
        return self.api_key.id if self.api_key is not None else None


def _repo(request: Request) -> ApiKeyRepository:
    return ApiKeyRepository(request.app.state.sessionmaker)


async def principal_dep(request: Request, settings: Settings = Depends(get_settings)) -> Principal:
    """Resolve the caller from ``X-API-Key`` and charge the request against its budget.

    THE one resolution point. Everything else — ``protect()``, ``tenant_id_dep``, any route wanting
    the key or the plan — depends on THIS callable, which is what makes the work happen once:
    FastAPI caches a dependency's result per request per callable (``use_cache`` defaults to True),
    so a request whose route asks for both the gate and the tenant id runs one lookup and one rate
    check. That framework-native cache is deliberately the ONLY caching here; an earlier draft also
    memoized on ``request.state``, which was a second mechanism answering a question the first had
    already answered.

    The rate check sits in a dependency rather than in middleware because middleware runs before
    routing and would meter the public canvas endpoints too — the ones deliberately left open so a
    panel can render before anyone has a key.
    """
    provided = request.headers.get(_API_KEY_HEADER, "")
    repo = _repo(request)
    return (
        await _resolve_from_table(repo, provided, settings)
        if await _is_seeded(request, repo)
        else _legacy_principal(provided, settings)
    )


async def _is_seeded(request: Request, repo: ApiKeyRepository) -> bool:
    """Whether ``api_keys`` has rows — latched ON for the process once it ever does.

    A COUNT on every authenticated request would be a query per request forever, so the answer is
    cached. Cached in ONE direction on purpose: seeding is irreversible by design, and a cache that
    could fall back to False would silently reopen the env-var credential path on an installation
    whose operator had already moved off it — the one transition this module must never make
    backwards. While the table is still empty the count does run per request; that window is a
    pre-migration or dev box, and paying a COUNT there buys noticing the seed the moment it lands.
    """
    if getattr(request.app.state, "api_keys_seeded", False):
        return True
    seeded = await repo.count_keys() > 0
    if seeded:
        request.app.state.api_keys_seeded = True
    return seeded


async def _resolve_from_table(
    repo: ApiKeyRepository, provided: str, settings: Settings
) -> Principal:
    if not provided:
        raise ApiKeyNotFound("no X-API-Key header")
    key = await repo.find_by_hash(fingerprint_api_key(settings.master_key, provided))
    if key is None:
        raise ApiKeyNotFound("no active key matches the presented digest")
    tenant = await repo.get_tenant(key.tenant_id)
    if tenant is None:
        raise TenantNotFound(key.tenant_id)
    return Principal(tenant=tenant, api_key=key)


def _legacy_principal(provided: str, settings: Settings) -> Principal:
    """The pre-``api_keys`` path: one configured key, one tenant, no metering.

    Reached ONLY while the table is empty. That is the whole answer to "why does
    ``settings.api_key`` still decide anything" — once a key row exists this function is never
    called again, and the env var stops meaning anything at all. Here it is the only credential
    there is, so it keeps its old two jobs: absent + hatch off means fail closed, present means the
    presented key must equal it.
    """
    default = Principal(
        tenant=Tenant(
            id=TenantId(UUID(settings.default_tenant_id)),
            plan="self_host",
            created_at=datetime.now(UTC),
        )
    )
    if not settings.api_key:
        if settings.allow_unauthenticated:
            return default
        raise Unauthorized()
    # Constant-time so a wrong key can't be timing-probed.
    if not hmac.compare_digest(provided, settings.api_key):
        raise Unauthorized()
    return default


async def require_api_key(principal: Principal = Depends(principal_dep)) -> Principal:
    """The gate, as a name that reads correctly in ``dependencies=protect()``.

    Returns the principal rather than None so it is usable in a signature too — but a route that
    wants the object should say ``Depends(principal_dep)`` outright; this name exists for the gate
    position, where the return value is discarded by FastAPI anyway.
    """
    return principal


def protect(*filters: Callable[..., object]) -> list[params.Depends]:
    """Require the X-API-Key gate — drop it into any ``dependencies=``, on one route or a whole
    router::

        router = APIRouter(prefix="/flows", dependencies=protect())   # every route needs the key
        @router.get("/list", dependencies=protect())                  # just this route
        @router.post("/wipe", dependencies=protect(require_admin))     # key + extra check(s)

    Always enforces the key; pass extra FastAPI dependencies to layer more checks (roles/scopes).
    A route is therefore either ``dependencies=protect(...)`` (closed) or deliberately public — no
    third pattern to hunt for. (FastAPI binds a route at its ``@router`` line, so there is no
    ``@protect`` decorator to stack — the ``dependencies=`` list is the framework's own way, and
    the simplest thing that reads at a glance.)
    """
    return [Depends(require_api_key), *(Depends(f) for f in filters)]
