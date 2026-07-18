# AGENTS.md — lzt-flow project conventions

Tool-agnostic agent instructions for this repo. `CLAUDE.md` mirrors this file for Claude Code.

## Toolchain (run from repo root, all must stay green)
```
uv run ruff check .          # lint
uv run ruff format .         # format (then --check in CI)
uv run mypy app              # strict; tests are not mypy-gated
uv run pytest -q             # -m 'not live' by default (in pyproject addopts)
```
Deps: `uv sync --extra dev`. Add a runtime dep to `[project.dependencies]`, a dev dep to
`[project.optional-dependencies].dev`, then re-sync with `--extra dev`.

## Layering (strict — never skip a layer)
`api/` (Transport: extract DTO → call Service → return) → `domain/<area>/service.py` (all logic,
DI via constructor) → `domain/<area>/repo.py` (`BaseRepo[TDoc, TId]`, tenant-scoped) →
`domain/<area>/model.py` (dataclass, imports nothing from app). ORM tables live in
`db/models/<entity>.py` (one file per entity, re-exported from `db/models/__init__.py`) — repos
import from there, not from a single `db/repos` module. One module = one job.

## Conventions
- **Every file** starts with a one-line module docstring + `from __future__ import annotations`.
- **IDs**: `TenantId`/`AccountId` are `NewType(..., UUID)` in `app/domain/account/model.py`.
- **Domain models**: `@dataclass(slots=True, frozen=True)`; datetimes are UTC tz-aware
  (`datetime.now(UTC)`); money would be `Decimal` (none in the domain yet).
- **DTOs at HTTP edge**: Pydantic `BaseModel`; results/internal DTOs: frozen dataclass.
- **Typed errors**: carry args not formatted text (`TokenInvalid(account_id)`,
  `MarketApiError(status, body)`); chain with `raise X(...) from e`; never silence.
- **Error envelope**: domain exceptions map to `ErrorEnvelope{code: ErrorCode, message, request_id}`
  at the boundary in `app/core/errors.py` (`register_error_handlers`). Add new `ERR-XXXX` codes to
  `ErrorCode` and a handler there — never leak a raw exception to the client.
- **Repos**: subclass `BaseRepo[TDoc, TId]`; EVERY method takes `tenant_id: TenantId` explicitly.
  ORM in `app/db/models/<entity>.py` (all tables carry `tenant_id`). No raw SQL outside a repo.
- **pylzt access**: ONLY through `app/domain/market/adapter.py` (`MarketAdapter`). No other
  module imports pylzt. The adapter maps `AuthFailed→TokenInvalid`, `TransportError→MarketApiError`,
  and must never let a token reach a log/error message.
- **structlog**: `structlog.get_logger()`; `request_id` is bound by middleware; no PII/secrets;
  no f-strings in log args (pass kwargs). `logger.exception(...)` in a caught handler.
- **Imports**: module-level only (top of file); no local imports to dodge cycles.
- **Comments**: only for a non-obvious "why" the code can't show. No section-divider banners,
  no restating the next line.

## Migrations
- Alembic async is set up (`alembic/env.py` reads `Base.metadata` + Settings DSN). Add a new
  revision file `alembic/versions/000N_<slug>.py` with `down_revision` pointing at the prior head.
  Verify offline with `uv run alembic upgrade head --sql` (no DB needed).
- A SECOND alembic chain exists for lzt-eventus (its own tables) — do not merge the two chains.

## Tests
- `tests/conftest.py` provides an autouse `_test_env` (master key + debug token) and pulls in the
  `mock_lzt` fixture (`tests/fixtures/mock_lzt_server.py`) which respx-mocks `prod-api.lzt.market`
  / `prod-api.lolz.live` so no live token is needed. Unit tests mock the adapter/collaborator;
  integration tests drive the FastAPI app via `httpx.ASGITransport` + `asgi_lifespan.LifespanManager`.
- Live tests: `@pytest.mark.live` (skipped by default; needs `LZT_LIVE_TOKEN`).
- `get_settings` is `lru_cache`d — call `get_settings.cache_clear()` when patching env in a test.

## Config
- lzt-flow settings: `app/core/config.py`, prefix `LZT_FLOW_`, `pydantic-settings`.
- lzt-eventus's `EngineConfig` (prefix `LZT_`, incl. `LZT_TOKEN_ENC_KEY`) is a SEPARATE surface —
  do not merge the two.

## Docker
- `docker-compose.yml` has `postgres` + `redis` + `api`. A process that needs its own service
  (worker, scheduler, frontend) extends it. Ports bound to `127.0.0.1` for infra.

## Definition of done per change
All of: ruff clean, ruff format clean, mypy app clean, pytest green (non-live), any new
migration applies offline (`alembic upgrade head --sql`). Then commit.

## Repo housekeeping
- `_MODULE.md` / `_MODULE_AUTO.md` (auto-generated module docs) and `.plans/` (planning
  artifacts) are gitignored — local-only, never committed.
