<p align="right"><b>English</b> · <a href="README.md">Русский</a></p>

<p align="center">
  <strong>lzt-flow</strong>
</p>

<p align="center">
  <strong>Server-side, no-code automation for the lzt.market marketplace</strong>
</p>

<p align="center">
  <a href="https://github.com/open-lzt/auto-lzt/actions/workflows/ci.yml"><img src="https://img.shields.io/github/actions/workflow/status/open-lzt/auto-lzt/ci.yml?branch=main&style=for-the-badge" alt="CI status"></a>
  <a href="LICENSE"><img src="https://img.shields.io/badge/License-MIT-yellow.svg?style=for-the-badge" alt="License"></a>
</p>

> **About the name.** The GitHub repository was renamed from `lzt-flow` to **`auto-lzt`**.
> The package, module, CLI, and almost all docs still say **`flow`** / `lzt-flow`.
> Same project. Nothing to change on your side.

Describe a flow → press Deploy → close the tab.

The flow keeps running **on the server**. 24/7, without your machine.

**Flow** — a chain of steps. For example: on schedule → get my lots → for each account →
bump the lot + reprice it.

**Node** — one step in that chain. `market.bump` is a node.

That is the reference flow this whole project is built around.
A seller wires it once, hits Deploy — and their lots get bumped from the server, on schedule.

It survives a closed browser tab. It survives a worker restart: it resumes from the last committed
step and **never performs an action twice**.

[Architecture](ARCHITECTURE.en.md) · [Plugins](docs/plugins.en.md) · [Modules](docs/modules.en.md) · [Flows](docs/flow-design-guide.en.md) · [Runbooks](docs/runbooks/README.en.md) · [AI-agent docs](docs/for_ai/) · [Issues](../../issues)

> A canvas demo GIF belongs here — see [docs/DEMO.en.md](docs/DEMO.en.md) for how to capture one.

## Authoring is text, not a mouse

Ready-made flows live in the [official registry](https://github.com/open-lzt/lzt-flows).

What you install from there is a **module** — a finished flow packaged as **data**, not code.
Install from the registry, run it from Telegram.

The engine itself is extended differently — by installing a Python package. Two shapes:

- **node pack** — one new node type;
- **[plugin](docs/plugins.en.md)** — nodes + API routes + bot handlers + lifecycle.

The owner installs either one straight from the Telegram bot's `/plugins` menu.

The bot is button-driven end to end: browse flows, run one and read its logs, install modules
and plugins.

The visual canvas ships in this repo too, but it is **off by default** —
why exactly, see *Deferred, not dead* below.

## Quickstart

### No-Docker dev mode

The fastest path: SQLite + fakeredis + mock market.

Zero external services, the whole core loop in one process.

```bash
uv sync --extra dev
uv run python dev.py                 # dev API on http://127.0.0.1:8000
pnpm --dir frontend dev              # React Flow canvas on http://localhost:5173
```

Verify the loop end to end with one command:

```bash
uv run python dev.py --demo          # runs one bump flow -> prints FINAL run -> {... 'status': 'completed'}
# or:  bash scripts/smoke.sh         # same gate, exit 0/1 (this is what CI runs)
```

### Production via Docker

```bash
scripts/install.sh                   # docker + compose, .env from .env.example, migrations, stack up
# API on http://localhost:8000 · canvas on http://localhost:5173 · health on /health
```

Put a reverse proxy (nginx/Caddy) in front of it for HTTPS.

Never leave a deploy holding real marketplace tokens on bare HTTP.

## Operating it

| Action | Command |
|---|---|
| Status / health | `curl http://localhost:8000/health` |
| Logs | `docker compose logs -f api worker` |
| Update | `scripts/update.sh` — pull, migrate, restart |
| Backup / restore | `scripts/backup.sh` / `scripts/restore.sh` (Postgres) |
| Manage | canvas UI on `http://localhost:5173` |
| Remove | `docker compose down -v` (containers + volumes; no dedicated uninstall script) |

## Configuration

Copy `.env.example` → `.env` and fill in the secrets.

Here is the trap people fall into: there are **two** config surfaces, and they are different.
Deliberately.

- `LZT_FLOW_*` — lzt-flow's own settings.
  This is where `LZT_FLOW_MASTER_KEY` lives — the envelope key that encrypts account tokens.

- `LZT_*` — the embedded `lzt-eventus` engine, needed only for the `on-event` trigger path.
  This is where `LZT_TOKENS` (polling tokens) and `LZT_TOKEN_ENC_KEY` live.

**FACT to memorise:** `LZT_TOKEN_ENC_KEY` ≠ `LZT_FLOW_MASTER_KEY`. Two different keys.

### Running against a fake market

`LZT_FLOW_MARKET_BASE_URL` points market calls at a different backend.
Unset by default — meaning the real `prod-api.lzt.market`.

What it's for: testing locally without touching the real marketplace.

Kill the wrong model right away: this is **not** a dry run. The code still performs real HTTP
requests — just against a fake server.

Bring one up from the neighbouring `lzt-testnet` repo (full quickstart in its own README):

```bash
cd ../lzt-testnet && scripts/run.sh          # mock market on 127.0.0.1:8765
# in this repo:
LZT_FLOW_MARKET_BASE_URL=http://127.0.0.1:8765 uv run python dev.py --demo
```

**Known gap.** Only the ephemeral `MarketAdapter` construction path — the one built per call —
reads that variable.

The pool-client path (`MarketAdapter(client=...)`, where an `httpx.AsyncClient` is shared) does
**not**. A documented limitation, not a silent one.

## Examples

Two independent ways to work with a running instance.

Not a progression from one to the other — pick by integration surface.

### REST API — drive a flow imperatively

For scripting a one-off run, or your own automation on top of the API.

```bash
# examples/01_rest_flow.sh
curl -sS -X POST http://localhost:8000/flows/create \
  -H 'Content-Type: application/json' \
  -d '{
        "name": "bump-once",
        "entry_node_id": "bump",
        "nodes": [{"id": "bump", "type": "market.bump", "inputs": {"item_id": {"literal": 123456}}}]
      }'
# -> {"flow_id": "..."}

curl -sS -X POST "http://localhost:8000/flows/<flow_id>/compile"
# -> {"flow_ir_id": "...", "node_count": 1}

curl -sS -X POST http://localhost:8000/runs/create \
  -H 'Content-Type: application/json' -d '{"flow_id": "<flow_id>"}'
# -> {"run_id": "...", "status": "pending"}

curl -sS "http://localhost:8000/runs/<run_id>/get"
# -> {"run_id": "...", "status": "completed"}
```

`X-API-Key` is required on the mutating calls above as soon as `LZT_FLOW_API_KEY` is set.
It is empty by default — fine for a loopback-only self-host.

#### Params and synchronous invoke

A flow can declare a **param surface** — a flat set of settings: delay, count, category.

What it buys you: editing them in one settings form instead of hunting values inside individual
nodes.

Declare them under `params`, then reference each from a node input as the literal
`"{{vars.<key>}}"`:

```jsonc
{
  "name": "bump-one",
  "entry_node_id": "bump",
  "params": [{ "key": "item_id", "label": "Lot ID", "control": "number", "required": true, "default": 1 }],
  "nodes": [{ "id": "bump", "type": "market.bump", "inputs": { "item_id": { "literal": "{{vars.item_id}}" } } }]
}
```

Both endpoints accept a `params` body — `POST /runs/create` and `POST /flows/{id}/invoke` —
and both validate it against the declared surface.

The difference: `invoke` runs the flow **synchronously** and returns its final output.

Hence its ceiling: `LZT_FLOW_FLOW_INVOKE_TIMEOUT_S`, `60` seconds by default.
For long flows use the async `/runs/create` path.

```bash
curl -sS -X POST "http://localhost:8000/flows/<flow_id>/invoke" \
  -H 'Content-Type: application/json' -d '{"params": {"item_id": 123456}}'
# -> {"run_id": "...", "status": "completed", "output": {...}}
```

### Triggers — so the flow fires itself

The actual autopilot case: after setup there are no imperative calls at all.

```bash
# examples/03_attach_schedule_trigger.sh
curl -sS -X POST "http://localhost:8000/flows/<flow_id>/triggers/create" \
  -H 'Content-Type: application/json' \
  -d '{"kind": "schedule", "schedule_cron": "*/30 * * * *"}'

curl -sS "http://localhost:8000/flows/<flow_id>/status"
# -> {"running": true, "active_accounts": N, "last_run_at": "..."}
```

## Architecture

lzt-flow is a thin domain layer over the `lzt-*` ecosystem.

`pylzt` (transport) and `lzt-eventus` (event fabric) are **reused, not rewritten**.

The full contrast table (vs. a client-side flow builder) and the runtime shape diagram live in
**[ARCHITECTURE.en.md](ARCHITECTURE.en.md)**.

> `pylzt` and `lzt-eventus` live in separate public repos under the
> [`open-lzt`](https://github.com/open-lzt) org and are wired in as git dependencies in
> `pyproject.toml`. No need to clone them yourself — `uv sync` pulls them in.

Designing a flow from a text description? See the **[flow design guide](docs/flow-design-guide.en.md)**
and the `flow-from-text` skill (`.claude/skills/flow-from-text/`) — it turns a plain-language brief
into a `FlowSpec` verified by compile and dry run.

### What a module's `sha256` actually equals

Every module in the registry's `index.json` carries a `sha256`. It is easy to read it wrong.

What it means: **transport integrity**. The flow you install is byte-for-byte the flow that was
reviewed.

What it does **not** mean: a signature. It says nothing about its author.

Exactly one thing stands between you and a hostile module — a maintainer read it before merging.
Plus this: a module is *data*, so you can read it too.

## Node plugins

A new node arrives by installing a distribution that advertises the `lzt_flow.nodes` entry point:

```toml
# in your package's pyproject.toml
[project.entry-points."lzt_flow.nodes"]
my_pack = "my_pack.nodes:REGISTRATIONS"
```

`pip install` is the entire install step.

No plugin folder to scan, no path to configure. Hence the property that matters: a node **cannot
appear on its own** — somebody installed the package providing it.

From then on it behaves like a built-in: it shows up in `GET /catalog/list`, gets a form in the
canvas and in the bot with no edit to either, and compiles and executes on the same interpreter.

Two rules a plugin cannot get around:

**1. It can never shadow a built-in node.**
A plugin claiming `market.bump` fails startup with `DuplicateNodeType`, naming both sides.
Load order never wins it.

Why so strict: a package that quietly shadowed a money node would make every flow on the
deployment call its code — leaving no trace.

**2. It cannot reach the network unchecked.**
A request node inherits from `BaseRequestNode`, whose `execute()` is final.
It goes through `deps.http`, which cannot be constructed without an `EgressPolicy`. No way around.

Every node declares its capabilities (`NodeCapability`) — and that declaration is what the module
validator filters on, see `app/domain/catalog/capabilities.py`.

## Outbound requests

`EgressPolicy` from the previous section — here is what it does.

Anything talking to something other than the marketplace goes through the egress fence
(`app/domain/egress/`):

- `https` only;
- checked against an **exact** allow-list, which is **empty by default**;
- the *resolved address* is evaluated, not the hostname;
- the connection goes to exactly the address that was checked.

Two consequences. `2130706433`, `0177.0.0.1` and `::ffff:127.0.0.1` all read as loopback.
And DNS rebinding has nothing to re-resolve: the address is already pinned.

Redirects are rejected, not followed.

```bash
LZT_FLOW_EGRESS_ALLOWED_HOSTS=api.telegram.org   # comma-separated; empty means "can't reach anything"
```

Unconfigured means unreachable — deliberately.

Why empty by default: this process sits next to Redis, and Redis holds the money-idempotency guards
and the task queue. Without the fence, `http://redis:6379` in a community module would be code
execution in the worker.

## Deferred, not dead

The visual canvas and the composite-block authoring UI sit behind `VITE_BUILDER_ENABLED`,
**off by default**.

Off — not removed. The code stays in the tree deliberately: this is a **deferral**.

Which sets a trap for anyone tidying the repo: from the outside, `AuthoringMode` and the
deploy path look like dead code. They are not dead — they are flagged off. Don't cut them.

Build with the flag on to get them back:

```bash
VITE_BUILDER_ENABLED=1 npm run build
```

Second trap: the flag is a **product decision, not a security boundary**.
The mutating endpoints are still there and still gated by the API key — hiding the buttons
hides the buttons, nothing more.

What makes the preview build honest is something else: it **ships no key** to hide behind
those buttons. The key is typed in by an operator at runtime and lives in sessionStorage.

## Community

The architecture writeup and demo notes are in [docs/](docs/).

The layering and coding conventions a PR is expected to match are in [AGENTS.md](AGENTS.md) /
[CLAUDE.md](CLAUDE.md).

Before opening a PR, all of this has to pass:

```bash
uv sync --extra dev && uv run ruff check . && uv run mypy app --strict && uv run pytest -q
```

`tests/e2e/` is excluded from the default run — it spawns a real `dev.py` subprocess on a real port
and is therefore slower. Opt in with `uv run pytest -m e2e`.

Bugs and feature requests go to [issues](../../issues).

<a href="https://github.com/open-lzt"><img src="https://github.com/open-lzt.png" width="48" height="48" style="border-radius:50%" alt="open-lzt"/></a>

## License

[MIT](LICENSE) © 2026 open-lzt
