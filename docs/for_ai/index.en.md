<p align="right"><b>English</b> · <a href="index.md">Русский</a></p>

# AI-agent map — lzt-flow

Read this before opening source. Each package below ships its own `_MODULE_AUTO.md`
(generated, compressed signatures) — read that first; open the `.py` source only if
the doc is stale, ambiguous, or you need control-flow. Full narrative design: [../../ARCHITECTURE.md](../../ARCHITECTURE.md).
Extending the engine: [../plugins.en.md](../plugins.en.md) (writing a node, capabilities, the
money guard, the egress fence) and [../modules.en.md](../modules.en.md) (the registry and what its
checksum does and does not prove).

## Layout

| Package | What it owns |
|---|---|
| `app/api/` | FastAPI routers — thin handlers, `Depends`-injected services/repos. See `app/api/_MODULE_AUTO.md`. |
| `app/core/` | Settings, auth, the shared `AppError` tree, logging. See `app/core/_MODULE_AUTO.md`. |
| `app/db/` | Async engine/sessionmaker, `BaseRepo`/`BaseSessionmakerRepo` contracts, ORM models. See `app/db/_MODULE_AUTO.md`. |
| `app/domain/account/` | Tenants, marketplace accounts, envelope token encryption, the per-tenant `TokenPool`. See `app/domain/account/_MODULE_AUTO.md`. |
| `app/domain/flow_engine/` | Flow spec → compiler → IR → typed errors; the path resolver (`path.py`) and node contract (`base_node.py`) live here. See `app/domain/flow_engine/_MODULE_AUTO.md`. |
| `app/domain/catalog/` | The node catalog (`registry.py`) and every concrete node (`nodes/`), including the reflection-based `DynamicMethodNode`. See `app/domain/catalog/_MODULE_AUTO.md`. |
| `app/domain/market/` | The `pylzt`-backed marketplace adapter/service (bump, reprice, relist, list-lots). See `app/domain/market/_MODULE_AUTO.md`. |
| `app/domain/triggers/` | Schedule/event trigger definitions attached to a compiled flow. See `app/domain/triggers/_MODULE_AUTO.md`. |
| `app/domain/scheduler/` | APScheduler wiring that turns a `SCHEDULE` trigger into a periodic run. See `app/domain/scheduler/_MODULE_AUTO.md`. |
| `app/domain/events/` | The embedded `lzt-eventus` event router for `EVENT` triggers. See `app/domain/events/_MODULE_AUTO.md`. |
| `app/worker/` | The stateful interpreter (`runtime.py`), arq job wiring, the node registry. See `app/worker/_MODULE_AUTO.md`. |
| `frontend/src/canvas/` | The React Flow authoring canvas. See `frontend/src/canvas/_MODULE_AUTO.md`. |

## Invariants an agent must not break

- **Layers don't skip**: `api → service/repo → orm`. A route never holds business logic.
- **One engine** (`app/db/`) — no feature builds its own `sessionmaker`.
- **`BaseRepo`** is session-per-request; **`BaseSessionmakerRepo`** is session-per-call (needed for
  the flow engine's two-phase commit / optimistic locking). Don't collapse the two.
- **`StepResultDTO.output`** is flat JSON-primitives only — a nested structure is JSON-encoded into
  one string key, never passed as a raw `dict`/`list`.
- Money-adjacent/side-effecting nodes call `ctx.deps.guard.check_and_set(...)` before their effect —
  the worker's crash-resume story depends on every such node doing this.
- HTTP is `POST`/`GET` only; a handler raises a typed `AppError`, never builds a response by hand.
