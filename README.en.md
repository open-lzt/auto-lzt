<p align="right"><b>English</b> · <a href="README.md">Русский</a></p>

# auto-lzt

A server-side no-code automation engine for [lzt.market](https://lzt.market). A task is described as a graph of nodes rather than a script: "find Steam accounts under 500 and buy them", "bump my listings every 4 hours".

**The repository is called `auto-lzt`, while the package, the CLI and the docs are called `lzt-flow`.** That's a leftover from a rename, not two different things.

```bash
uv sync --extra dev
uv run python dev.py --demo
```

## Development without Docker

`dev.py` brings everything up on SQLite, fakeredis and a mock market — no token, no database, no real money.

```bash
uv sync --extra dev
uv run python dev.py            # API on 127.0.0.1:8000
uv run python dev.py --demo     # the same plus demo data
bash scripts/smoke.sh           # end-to-end: create → compile → run
pnpm --dir frontend dev         # the flow canvas, a separate process
```

## Production

```bash
scripts/install.sh    # Docker + compose, .env from .env.example, migrations, start
scripts/update.sh     # pull, migrate, restart
scripts/backup.sh     # database dump
scripts/restore.sh    # load a dump back
```

`LZT_FLOW_MASTER_KEY` is required by the API process — an empty value is rejected at startup rather than surfacing on the first token access.

```bash
LZT_FLOW_MASTER_KEY=$(python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())")
```

## CLI

The package installs two commands: `lzt-flow` for operator tasks and `lzt-flow-validate` for checking a module.

Global flags go **before** the subcommand:

```bash
lzt-flow --api http://127.0.0.1:8000 --json list
```

| Flag | What it does |
|---|---|
| `--api URL` | API address, default `http://127.0.0.1:8000` |
| `--api-key KEY` | key if it isn't in the environment — visible in `ps` and shell history |
| `--env-file PATH` | where to read `.env` from, default `.env` |
| `--json` | machine-readable output |

| Command | What it does |
|---|---|
| `status` | service health and market mode |
| `modules` | modules available in the catalog |
| `list` | flows: id, name, whether compiled |
| `install <MODULE> [--param K=V] [--account ID]` | create a flow from a module |
| `params <FLOW_ID>` | a flow's declared parameters |
| `run <FLOW_ID> [--param K=V] [--watch] [--no-dry-run]` | run it; `dry_run` is on by default |
| `runs [--flow ID]` | recent runs |
| `trace <RUN_ID\|FLOW_ID>` | per-node trace |
| `accounts [add --token --label]` | list accounts or add one |

`run` without `--no-dry-run` buys nothing. Turn the dry run off once you've read the trace.

## REST API

| Prefix | What it covers |
|---|---|
| `/flows` | create, update, compile, invoke, triggers, export |
| `/runs` | run status, trace, event stream |
| `/accounts` | market tokens |
| `/modules` · `/plugins` | module and plugin catalogs, installation |
| `/catalog` | available node types and pylzt dynamic methods |
| `/tasks` | schedules |
| `/panel` · `/auth` | web panel |

The full cycle:

```bash
API=http://127.0.0.1:8000

FLOW=$(curl -sX POST $API/flows/create -H 'Content-Type: application/json' -d '{
  "name": "demo",
  "entry_node_id": "search",
  "nodes": [{"id":"search","type":"market.search","inputs":{"category":{"literal":"steam"}}}]
}' | jq -r .flow_id)

curl -sX POST $API/flows/$FLOW/compile
RUN=$(curl -sX POST $API/runs/create -H 'Content-Type: application/json' \
  -d "{\"flow_id\":\"$FLOW\",\"params\":{}}" | jq -r .run_id)

curl -s $API/runs/$RUN/get
curl -s $API/runs/$RUN/trace
```

Attach a schedule:

```bash
curl -sX POST $API/flows/$FLOW/triggers/create -H 'Content-Type: application/json' \
  -d '{"kind":"schedule","schedule_cron":"*/30 * * * *"}'
```

Errors arrive as `{code, message, request_id}` — look the `request_id` up in the logs.

## Ready-made flows and plugins

| Catalog | What's in it | Who publishes |
|---|---|---|
| [lzt-flows](https://github.com/open-lzt/lzt-flows) | flow graphs, which are data | any author, via PR |
| [lzt-plugins](https://github.com/open-lzt/lzt-plugins) | executable `.py` running inside the engine process with your tokens, no sandbox | the stand owner only, from the bot menu |

## Configuration

Prefix `LZT_FLOW_`. The essentials:

| Variable | Default | What it is |
|---|---|---|
| `LZT_FLOW_MASTER_KEY` | — | Fernet key for token encryption, required |
| `LZT_FLOW_DATABASE_URL` | `postgresql+asyncpg://lzt:lzt@localhost:5432/lztflow` | Postgres |
| `LZT_FLOW_REDIS_URL` | `redis://localhost:6379/0` | Redis for the queue |
| `LZT_FLOW_API_KEY` | empty | shared secret for `X-API-Key`; empty disables the check |
| `LZT_FLOW_EGRESS_ALLOWED_HOSTS` | empty | where request nodes may go; empty means nowhere |
| `LZT_FLOW_DEFAULT_TENANT_ID` | `00000000-…-0001` | default tenant |
| `LZT_FLOW_BOT_ENABLED` | `0` | Telegram control bot |
| `LZT_FLOW_BOT_TOKEN` · `LZT_FLOW_BOT_ADMIN_IDS` | — | @BotFather token and admin ids |
| `LZT_FLOW_PLUGIN_DIR` | `.system/plugins` | where plugins are unpacked |
| `LZT_FLOW_PLUGIN_INDEX_URL` | the `lzt-plugins` catalog | where the plugin list comes from |
| `LZT_FLOW_WORKER_ID` | `worker-1` | worker name |

An empty `LZT_FLOW_EGRESS_ALLOWED_HOSTS` is closed, not open: a request node with no allowlist goes nowhere.

The event engine has its own `LZT_`-prefixed variables — they are separate, and `LZT_TOKEN_ENC_KEY` is not the same thing as `LZT_FLOW_MASTER_KEY`. The full list is in `.env.example`.

## Development

```bash
uv run ruff check .
uv run mypy app --strict
uv run pytest -q          # e2e and live excluded by default
uv run pytest -m e2e
```

Docs: [ARCHITECTURE.en.md](ARCHITECTURE.en.md) · [flow design](docs/flow-design-guide.md) · [modules](docs/modules.en.md) · [plugins](docs/plugins.en.md) · [runbooks](docs/runbooks/README.md) · [AI-agent docs](docs/for_ai/)

## Ecosystem

[pylzt](https://github.com/open-lzt/pylzt) — market SDK · [lzt-eventus](https://github.com/open-lzt/lzt-eventus) — events · [lzt-testnet](https://github.com/open-lzt/lzt-testnet) — mock market · [lzt-mcp](https://github.com/open-lzt/lzt-mcp) — server for AI agents · [the whole stand](https://github.com/open-lzt/open-lzt)

## License

[MIT](LICENSE)
