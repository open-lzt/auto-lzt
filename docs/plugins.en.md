<p align="right"><b>English</b> · <a href="plugins.md">Русский</a></p>

# Plugins — how to add your own node

A node is added by installing a package. Not a folder someone scans, not a path in a config —
`pip install`, and that's it. So a node can't appear in the engine without someone installing a
package that provides it.

## A minimal plugin

Two files.

**`pyproject.toml`** — the entire install lives here:

```toml
[project]
name = "lzt-flow-my-pack"
version = "1.0.0"
requires-python = ">=3.12"
dependencies = ["lzt-flow"]

[project.entry-points."lzt_flow.nodes"]
my_pack = "lzt_flow_my_pack.nodes:REGISTRATIONS"

[build-system]
requires = ["hatchling"]
build-backend = "hatchling.build"

[tool.hatch.build.targets.wheel]
packages = ["lzt_flow_my_pack"]
```

**`lzt_flow_my_pack/nodes.py`**:

```python
from pydantic import Field

from app.core.schema import BaseSchema
from app.domain.catalog.capabilities import NodeCapability
from app.domain.catalog.registry import NodeCategory, NodeRegistration, NodeType
from app.domain.flow_engine.base_node import BaseNode, RunContext
from app.domain.flow_engine.dtos import StepResultDTO


class ShoutInput(BaseSchema):
    text: str = Field(title="Text", json_schema_extra={"ui": "text"})


class ShoutOutput(BaseSchema):
    shouted: str


class ShoutNode(BaseNode):
    node_type = "demo.shout"          # the key a flow references the node by
    required_inputs = ("text",)       # the compiler checks that this port is wired

    async def execute(self, ctx: RunContext) -> StepResultDTO:
        text = str(ctx.resolve_input("text"))
        return StepResultDTO(node_id=ctx.node.id, output={"shouted": text.upper()})


REGISTRATIONS = [
    NodeRegistration(
        node_type=NodeType(
            key=ShoutNode.node_type,
            category=NodeCategory.LOGIC,
            input_schema=ShoutInput,
            output_schema=ShoutOutput,
            idempotent=True,
            capabilities=frozenset({NodeCapability.PURE}),
        ),
        impl=ShoutNode,
    )
]
```

```bash
uv pip install -e .
# restart the worker and API — the registry is built at startup
```

That's it. The node shows up in `GET /catalog/list`, gets a form in the web canvas and in the bot
with no edits to either, and is compiled and run by the same interpreter as the built-ins.

A live example exercised in tests: `tests/fixtures/plugin_pkg/`.

## A full plugin — the runtime (nodes + routers + handlers + lifecycle)

The node-plugin above (`lzt_flow.nodes`) adds **only a node type**. If you need more — your own
API router, your own bot handler, a background task, access to redis/DB at startup — that's a
**full plugin**: a separate entry point group `lzt_flow.plugins`, brought up by the runtime
manager (`app/plugin_runtime/`).

The trust story is the same — none. Both a node pack and a full plugin are **owner-only code**:
installed via `pip install` + restart, never through the API. A full plugin can just do more (see
"What these rules do NOT give you" below — it applies to it in full).

A plugin declares a **module** (no `:attr`), and on that module — three optional
list-constants of lifecycle hooks:

```toml
[project.entry-points."lzt_flow.plugins"]
my_plugin = "my_pkg.plugin"
```

```python
# my_pkg/plugin.py
from __future__ import annotations

from app.plugin_runtime import (
    PluginLoadContext, PluginLoadedContext, PluginProcess, PluginReadyContext,
)

def _register(ctx: PluginLoadContext) -> PluginLoadedContext:
    loaded = PluginLoadedContext()
    loaded.nodes.append(MY_NODE_REGISTRATION)          # applied in API and WORKER
    if ctx.process is PluginProcess.API:
        loaded.api_routers.append(my_api_router)       # applied only in API
    loaded.bot_routers.append(my_bot_router)           # applied only in BOT
    return loaded

async def _start(ctx: PluginReadyContext) -> None:
    # redis/sessionmaker exist in API and WORKER, but are None in BOT (the bot is an API client).
    ctx.spawn(_my_background_loop(ctx), "my-plugin-loop")

async def _stop(ctx: PluginReadyContext) -> None:
    ...

PRE_INIT  = [_register]   # sync: registration before the process starts
POST_INIT = [_start]      # async: live handles (redis/DB), background tasks via ctx.spawn
SHUTDOWN  = [_stop]       # async: best-effort cleanup; an error is logged, doesn't crash shutdown
```

Lifecycle: `discover()` (import, fail-closed) → `pre_init()` (collect contributions, filter by
process) → `post_init()` (live handles) → `shutdown()` (cancel tasks + SHUTDOWN). A plugin
declares **all** its surfaces; the manager applies only the ones the current process needs, out of
three:

| Process | What gets applied |
|---|---|
| API (`app/main.py`, in lifespan) | `nodes` + `api_routers` |
| WORKER (`app/worker/arq_settings.py`) | `nodes` |
| BOT (`app/bot/main.py`) | `bot_routers` |

A plugin's nodes fold into the same `NodeRegistry` via `build_registry(extra_registrations=...)`,
so a plugin that takes a built-in node's key crashes startup with `DuplicateNodeType` just the
same. Discovery runs in **lifespan/startup**, not on `app.main` import: `ep.load()` executes
someone else's code, and that belongs at process startup, not at any `import` (alembic, scripts,
tests).

Like a node pack: **no hot-reload** — a plugin is only visible after a restart. A full plugin can
be installed two ways: `pip install` by hand (entry point `lzt_flow.plugins`) **or from the bot** —
from a folder, see below. Live example: `tests/fixtures/plugin_runtime_pkg/`.

## Installing from the bot (the `.system/plugins/` folder)

The second runtime source is a folder. The owner installs plugins **from the bot**, with no
shell: the bot shows a catalog from a trusted git repository, a button hits the API, and the API
downloads the plugin into `.system/plugins/<name>/`. Same manager, same lifecycle — `discover()`
just also scans the folder.

**Layout** of `.system/plugins/<name>/`:
```
manifest.json   # {schema_version, name, version, description, entry, requirements}
plugin.py       # the entry module with PRE_INIT/POST_INIT/SHUTDOWN (plugin files at the archive root)
```
`requirements` are the plugin's pip dependencies. Installed **once, at install time** (in the API
endpoint, under a lock), not at startup: the three processes (API/worker/bot) share one venv, and
a parallel `pip` at startup would corrupt site-packages. Startup only **verifies** that the
dependencies import.

**Catalog** — `plugins.json` at `LZT_FLOW_PLUGIN_INDEX_URL` (empty → install-from-bot is
disabled). A trusted repository owned by you, **separate from `lzt-flows`** (which holds
FLOW-module data): reference — [`open-lzt/lzt-plugins`](https://github.com/open-lzt/lzt-plugins).
Each entry: `name`, `version`, `description`, `source_url` (a zip archive), `requirements`. The
downloaded archive is unpacked with protection against zip-slip and symlink entries. For a private
catalog, set `LZT_FLOW_PLUGIN_INDEX_TOKEN` (a GitHub PAT, repo read scope); note that a private
`raw.githubusercontent` redirects to a different host and httpx drops the header — for a private
GitHub catalog it's simpler to use a public repo or an `api.github.com/.../contents` URL.

**The bot** — `/plugins` opens an inline menu: available + installed, a card with
`Install/Update/Remove`, a settings screen with **Auto-update** and **New-version alerts** toggles
(both off by default). The update check lives in the bot process (only it has the Bot and the
admin id): with auto-update on, a new version downloads into the folder; with alerts, a
notification arrives (text configurable in `app/plugin_runtime/texts.toml`). Everything is applied
only after a restart.

**Fail-closed vs. quarantine.** A broken plugin from the bot (a malformed manifest, a missing
dependency, an import error, **or a node-key collision with a built-in**) doesn't crash the
process — it's logged, skipped, and marked "broken" in the bot. Otherwise a broken plugin would
lock out the very API/bot that's supposed to remove it. Entry-point plugins (a deliberate
`pip install` in a shell) stay fail-closed: a key collision crashes startup, because an ambiguous
set of nodes can't be served.

**Limitation.** The layout assumes the three processes share a disk (single-host self-host); a
multi-container deployment needs a shared `.system/plugins/` on a shared volume.

## The schema IS the UI

Nobody writes a form separately. It's derived from your Pydantic model:

```python
class BumpInput(BaseSchema):
    item_id: int = Field(title="Lot", json_schema_extra={"ui": "lot_ref"})
```

`title` is the label, `ui` is the control. The `ui` vocabulary is closed:

| `ui` | What it renders | What arrives in `resolve_input` |
|---|---|---|
| `lot_ref` | a lot picker | `int` |
| `account_ref` | an account picker | `str` (a UUID, validated) |
| `text` | a text field | `str` |
| `number` | a number | `int` or `float` |
| `bool` | yes/no | `bool` |
| `select` | a list | `str` (for a `StrEnum`, options come from it automatically) |
| `secret` | masked, never echoed in chat | `str` |

An unknown `ui` degrades to a text field rather than breaking the form — otherwise a plugin could
disable the bot by inventing a control.

Remove a field from the model — it disappears from the form. Add one — it appears. There's nothing
to edit in the bot or the frontend: they carry no knowledge of your nodes.

## Capabilities — mandatory

`capabilities` isn't a checkbox label. The module validator filters on it, and it's how an
operator sees what a node does **before** wiring it up.

| Capability | When |
|---|---|
| `PURE` | does nothing external |
| `MARKET_READ` | reads the marketplace |
| `MARKET_MUTATE` | changes lots |
| `MONEY` | **spends money** |
| `NETWORK_EGRESS` | reaches the network |
| `REFLECTIVE` | calls an arbitrary API method by name |

An empty set is forbidden: it doesn't distinguish "provably does nothing" from "nobody declared
it," and the filter would let the latter through. If a node does nothing — say so with `PURE`.

`REFLECTIVE` is in `FORBIDDEN_CAPABILITIES`. A module using such a node is rejected.

### If a node spends money

Declare `MONEY` **and** take the guard before the effect:

```python
async def execute(self, ctx: RunContext) -> StepResultDTO:
    first = await ctx.deps.guard.check_and_set(ctx.idempotency_key)
    if not first:
        raise RunFailed(ctx.run_id, ctx.node.id, "already executed; verify manually")
    result = await ctx.deps.market.bump(item_id, account)   # the effect — AFTER the guard
    ...
```

Why: the two-phase `RunStep` commit protects against concurrent execution, but **not** against a
crash between the effect and the commit. Without the guard, resume would repeat the paid action.

And don't fake success on a retry. `market.relist`, on a detected retry, fails loudly rather than
returning a made-up id — because a fake id would poison anything downstream that reads
`${relist.item_id}`. Failing loudly is the honest price of dealing with money.

A contract test checks this by an AST walk: a node with `MONEY` whose module has no
`check_and_set` fails the build.

## Nodes that reach the network

Derive from `BaseRequestNode`. Not from `BaseNode` with `httpx` inside.

```python
class SendMessageNode(BaseRequestNode):
    node_type = "tg.send_message"
    required_inputs = ("bot_token", "chat_id", "text")

    def build_request(self, ctx: RunContext) -> RequestSpec:
        token = str(ctx.resolve_input("bot_token"))
        return RequestSpec(
            url=f"https://api.telegram.org/bot{token}/sendMessage",
            method=HttpMethod.POST,
            headers={"Content-Type": "application/json"},
            json_body={"chat_id": ..., "text": ...},
            timeout_s=10.0,
        )

    def parse_response(self, ctx, status, body) -> StepResultDTO:
        ...
```

`execute()` there is final. It owns the egress policy, retries, backoff, and timeout — you get
them whether you wanted them or not. You only implement `build_request` and `parse_response`.

**Build the URL, don't accept one.** A node that takes a URL from the flow is an SSRF primitive
with a friendly name, and the fence is the only thing standing between a third-party module and
your internal network. Need a different host — write a different node.

The host must be in `LZT_FLOW_EGRESS_ALLOWED_HOSTS`, or the request never leaves. The list is
**empty by default**: an unconfigured fence must let nothing through, not everything.

429s and 5xxs ("later, but not never") are retried with jitter. 4xxs are not retried: the endpoint
is telling you the request itself is wrong, and a retry would just burn the rate limit.

## Rules you cannot talk your way around

**A plugin can never replace a built-in node.** Claim `market.bump` — the process fails to start,
`DuplicateNodeType` names both sides. No last-wins: a package that silently swapped out a money
node would take over every flow on the deployment, without a single error in the logs.

Origin is stamped by the loader from the entry point, not the plugin. Otherwise a hostile package
could call itself `builtin` and pin the blame on someone else.

**A load error stops startup.** A process that silently drops a broken plugin serves a set of
nodes nobody declared: a flow using that node fails at runtime, holding money, instead of failing
at load time. Refusing to start is louder and cheaper.

## What these rules do NOT give you

Said plainly, because comments in the code used to say the opposite.

**A plugin is code.** `ep.load()` imports your module — that is, it executes arbitrary Python. A
plugin can do `BumpNode.execute = ...` — there's no collision, origin stays `builtin`, and every
flow now runs through someone else's code. The registry protects against **conflicting
registration**, not against a hostile package.

Same with the fence: it holds **modules** (data, where a URL is untrusted input). Against a
plugin, it's powerless — `import socket` is always available.

This is defensible: `pip install` is an administrator action, and installing a plugin means
trusting its author exactly as much as you trust the engine itself. It's only wrong to assume
isolation exists.

## Verification

```bash
uv run pytest tests/integration/test_plugin_nodes.py -q
```

A real fixture distribution is installed there, and the tests go the whole path: discovery via
entry point → catalog → compile → **an actual run**. No test patches a global to sneak a node
in — a registry that only extends via a patch isn't extensible, it's just mutable.

Verify your own node the same way: build a flow with it and run it through the real
`execute_run`. A node that's in the catalog but doesn't run is a worse lie than not having it.

## See also

- [Modules and the registry](modules.en.md) — how to publish, the two kinds, the trust model
- [Flow design](flow-design-guide.en.md) — how to build a graph from text
- [Architecture](../ARCHITECTURE.md) — where a node lives in the bigger picture
