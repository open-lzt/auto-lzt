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

> **Renamed.** The GitHub repository was renamed from `lzt-flow` to **`auto-lzt`**. The package, module, CLI, and almost all documentation still use the name **`flow`** / `lzt-flow` — it's the same project, nothing to change.

**lzt-flow** is a self-hosted automation engine: describe a flow, press Deploy, close the tab —
the flow keeps running on the server, 24/7, without your machine.

**Authoring is text.** You install a ready-made module from the [official
registry](https://github.com/open-lzt/lzt-flows) and run it from Telegram; the engine itself is
extended by installing a Python package — a **node pack** (one new node type) or a **full
[plugin](docs/plugins.en.md)** (nodes + API routes + bot handlers + lifecycle), which the owner installs
straight from the Telegram bot's `/plugins` menu. The bot is button-driven end to end: browse flows,
run one and read its logs, install modules and plugins. The visual canvas ships in this repo but is
**off by default** — see *Deferred, not dead* below.

[Architecture](ARCHITECTURE.md) · [Plugins](docs/plugins.en.md) · [Modules](docs/modules.en.md) · [Flows](docs/flow-design-guide.en.md) · [AI-agent docs](docs/for_ai/) · [Issues](../../issues)

The reference flow this project is built around: **on-schedule → get-my-lots → for-each-account →
bump + reprice.** A seller wires it once on the canvas, hits Deploy, and the flow bumps their lots
on schedule from the server — surviving a closed browser tab and a worker restart, resuming from
the last committed step, never double-acting.

> A canvas demo GIF belongs here — see [docs/DEMO.en.md](docs/DEMO.en.md) for how to capture one.

## Quickstart

### No-Docker dev mode (fastest — SQLite + fakeredis + mock market)

Zero external services. Runs the whole core loop in-process.

```bash
uv sync --extra dev
uv run python dev.py                 # dev API on http://127.0.0.1:8000
pnpm --dir frontend dev              # React Flow canvas on http://localhost:5173
```

Prove the core loop end to end in one command:

```bash
uv run python dev.py --demo          # drives one bump flow -> prints FINAL run -> {... 'status': 'completed'}
# or:  bash scripts/smoke.sh         # same gate, exits 0/1 (this is what CI runs)
```

### Docker production path

```bash
scripts/install.sh                   # docker + compose, .env from .env.example, migrations, stack up
# API on http://localhost:8000 · canvas on http://localhost:5173 · health at /health
```

Put a reverse proxy (nginx/Caddy) in front to terminate HTTPS — never leave a deployment with real
marketplace tokens on bare HTTP.

## Operating lzt-flow

| Action | Command |
|---|---|
| Status / health | `curl http://localhost:8000/health` |
| Logs | `docker compose logs -f api worker` |
| Update | `scripts/update.sh` — pulls, migrates, restarts |
| Backup / restore | `scripts/backup.sh` / `scripts/restore.sh` (Postgres) |
| Manage | canvas UI at `http://localhost:5173` — author, deploy, and monitor flows |
| Remove | `docker compose down -v` (drops containers + volumes; no dedicated uninstall script yet) |

## Configuration

Copy `.env.example` → `.env` and fill the secrets. Two **separate** config surfaces (by design):

- `LZT_FLOW_*` — lzt-flow's own settings, incl. `LZT_FLOW_MASTER_KEY` (envelope key for account
  tokens).
- `LZT_*` — the embedded `lzt-eventus` engine (only needed for the `on-event` trigger path), incl.
  `LZT_TOKENS` (poll tokens) and `LZT_TOKEN_ENC_KEY` — a key **distinct** from
  `LZT_FLOW_MASTER_KEY`.

### Sandbox testing against a fake market backend

`LZT_FLOW_MARKET_BASE_URL` (unset by default, real `prod-api.lzt.market`) points every
**ephemeral** `MarketAdapter` construction at a different market backend instead — for running
lzt-flow against a fake API during local dev/manual testing, without touching the real
marketplace. It is a different thing from the dry-run gate below: with this var set, real code
still executes real HTTP calls, just against a fake server.

Boot the fake server from the sibling `lzt-testnet` repo (see its own README for the full
quickstart) and point `dev.py` at it:

```bash
cd ../lzt-testnet && scripts/run.sh          # starts the mock market on 127.0.0.1:8765
# in this repo:
LZT_FLOW_MARKET_BASE_URL=http://127.0.0.1:8765 uv run python dev.py --demo
```

**Known gap**: the pooled-client construction path (`MarketAdapter(client=...)`, used where a
`httpx.AsyncClient` is shared/injected) does **not** honor this env var — only the ephemeral,
per-call construction path does. This is a documented limitation, not a silent one.

## Examples

Two independent ways to work with an already-running instance — pick by integration surface,
not as a progression.

### REST API — author, compile, and fire a flow imperatively
For scripting a one-off run or wiring your own tooling directly against the API.

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
`X-API-Key` is required on the mutating calls above once `LZT_FLOW_API_KEY` is set (default empty,
fine for a loopback-only self-host).

#### Parameters & synchronous invoke

A flow can declare a **parameter surface** — a flat set of tunables (a delay, a count, a category)
edited from one settings form instead of hunting values inside individual blocks. Declare them under
`params` and reference each from a node input as the literal `"{{vars.<key>}}"`:

```jsonc
{
  "name": "bump-one",
  "entry_node_id": "bump",
  "params": [{ "key": "item_id", "label": "Lot ID", "control": "number", "required": true, "default": 1 }],
  "nodes": [{ "id": "bump", "type": "market.bump", "inputs": { "item_id": { "literal": "{{vars.item_id}}" } } }]
}
```

Both `POST /runs/create` and `POST /flows/{id}/invoke` accept a `params` body validated against the
declared surface. `invoke` runs the flow **synchronously** and returns its terminal output — bounded
by `LZT_FLOW_FLOW_INVOKE_TIMEOUT_S` (default `60`); use the async `/runs/create` path for long flows.

```bash
curl -sS -X POST "http://localhost:8000/flows/<flow_id>/invoke" \
  -H 'Content-Type: application/json' -d '{"params": {"item_id": 123456}}'
# -> {"run_id": "...", "status": "completed", "output": {...}}
```

### Triggers — attach a schedule so the flow fires without any further call
For the actual autopilot use case: no imperative call after setup, the flow runs itself.

```bash
# examples/03_attach_schedule_trigger.sh
curl -sS -X POST "http://localhost:8000/flows/<flow_id>/triggers/create" \
  -H 'Content-Type: application/json' \
  -d '{"kind": "schedule", "schedule_cron": "*/30 * * * *"}'

curl -sS "http://localhost:8000/flows/<flow_id>/status"
# -> {"running": true, "active_accounts": N, "last_run_at": "..."}
```

## Architecture

lzt-flow is a thin domain layer over the reusable `lzt-*` ecosystem — `pylzt` (transport) and
`lzt-eventus` (event fabric) are **reused, not rewritten**. See **[ARCHITECTURE.md](ARCHITECTURE.md)**
for the full contrast table (vs. a client-side flow builder) and the runtime shape diagram.

Designing a flow from a text description? See the **[flow design guide](docs/flow-design-guide.en.md)**
and the `flow-from-text` skill (`.claude/skills/flow-from-text/`), which turns a plain-language
brief into a compile-and-dry-run-verified `FlowSpec`.

> `pylzt` and `lzt-eventus` (org `open-lzt`) are separate private repos pinned by SHA in
> `pyproject.toml` — cloning this repo alone is not enough to build until you have read access.

### A note on module trust

A module's `sha256` in the registry's `index.json` is **transport integrity**: it proves the flow
you install is byte-for-byte the flow that was reviewed. It is **not a signature** and says nothing
about whether its author is trustworthy. The only thing standing between you and a hostile module
is that a maintainer read it before merging — and that a module is *data*, so you can read it too.

## Node plugins

A node is added by installing a distribution that advertises the `lzt_flow.nodes` entry point:

```toml
# in your package's pyproject.toml
[project.entry-points."lzt_flow.nodes"]
my_pack = "my_pack.nodes:REGISTRATIONS"
```

`pip install` is the entire install step. There is no plugin directory to scan and no path to
configure, so a node cannot appear without somebody having installed a package that provides it.
The node then shows up in `GET /catalog/list`, gets a form in the web canvas and in the bot with no
edits to either, and is compiled and run by the same interpreter as the built-ins.

Two rules a plugin cannot talk its way around:

- **It may never shadow a built-in.** A plugin claiming `market.bump` fails the boot with
  `DuplicateNodeType` naming both sides. It never wins by load order — a package that silently
  replaced a money node would have every flow on the stand calling its code, with nothing to see.
- **It cannot reach the network unpoliced.** A request node derives from `BaseRequestNode`, whose
  `execute()` is final and goes through `deps.http`, which cannot be constructed without an
  `EgressPolicy`. There is no seam to opt out of.

Every node declares what it can do (`NodeCapability`), and that declaration is what the module
validator filters on — see `app/domain/catalog/capabilities.py`.

## Outbound requests

Nodes that talk to something other than the marketplace go through an egress fence
(`app/domain/egress/`). It is `https` only, matches an **exact** allow-list that is **empty by
default**, judges the *resolved address* rather than the hostname, and connects to the address it
checked — so `2130706433`, `0177.0.0.1` and `::ffff:127.0.0.1` are all recognised as loopback and
DNS rebinding has nothing to re-resolve. Redirects are refused rather than followed.

```bash
LZT_FLOW_EGRESS_ALLOWED_HOSTS=api.telegram.org   # comma-separated; empty means "reach nothing"
```

Unconfigured means unreachable, on purpose: this process sits next to a Redis holding the
money-idempotency guards and the job queue, so `http://redis:6379` in a community module would
otherwise be code execution in the worker.

## Deferred, not dead

The visual canvas and the composite-block authoring UI are behind `VITE_BUILDER_ENABLED`, **off by
default**. The code stays in the tree deliberately — this is a deferral, not a removal, and a
`/cleanup` pass that concludes `AuthoringMode` and the deploy path are dead code would delete a
working feature. Build with the flag on to get it back:

```bash
VITE_BUILDER_ENABLED=1 npm run build
```

Flagging the UI off is a **product decision, not a security boundary**: the mutating endpoints are
still there and still gated by the API key. What makes the preview build honest is that it ships no
key to hide behind the buttons — the key is typed by an operator at runtime into sessionStorage.

## Community

See [docs/](docs/) for the architecture writeup and demo notes, and [AGENTS.md](AGENTS.md) /
[CLAUDE.md](CLAUDE.md) for the layering + coding conventions a PR is expected to match. Before
opening one: `uv sync --extra dev && uv run ruff check . && uv run mypy app --strict && uv run pytest -q`
must all pass. `tests/e2e/` spins up a real `dev.py` subprocess over a real port — opt in with
`uv run pytest -m e2e` (excluded from the default run, slower). Use [issues](../../issues) for
bugs and feature requests.

<a href="https://github.com/open-lzt"><img src="https://github.com/open-lzt.png" width="48" height="48" style="border-radius:50%" alt="open-lzt"/></a>

## License

[MIT](LICENSE) © 2026 open-lzt
