# Changelog

All notable changes to lzt-flow. Format loosely follows [Keep a Changelog](https://keepachangelog.com);
this project uses a single-tenant, wave-based history — see `ARCHITECTURE.md` for the design record.

## [0.3.0] — 2026-07-21

### Added
- **Presets declare their own fields.** A preset is a Pydantic model on the server; `GET
  /panel/presets/list` returns its JSON Schema and the panel renders the form from it. Adding a
  preset is a backend change with no frontend edit, and the same model validates the deploy body —
  the form and the validation cannot drift apart because they are one declaration.
- **Accounts show who they are.** Nickname, balance and currency are fetched from the marketplace
  and stored; `POST /accounts/{id}/refresh` re-reads them. An account list is no longer a list of
  opaque ids.
- **A failed run says why.** The failure reason is recorded on the run and on the step that raised
  it, and the history panel shows it. Four separate paths previously lost it — a forked branch's
  cause was swallowed by its exception group, and a purchase that timed out reported plain failure.
- **A flow registry** — browse official flows, install one, and export any flow to a file.
- **The panel works on a phone.** Every tab is reachable, the editor fits, and the type scale has a
  14px floor.

### Changed
- **A registered token is verified before it is stored.** Registration calls the marketplace; a
  token it does not accept is refused at the door instead of failing on first use.
- **A timed-out purchase is `PurchaseOutcomeUnknown`, not a failure.** `fast-buy` is a
  non-idempotent POST taking 28-31s, so a timeout says nothing about whether the marketplace
  completed it. It now says exactly that, and warns against a blind retry.
- **The purchase timeout is carried per call.** It applies on both the pinned and the pooled path
  without widening the shared client, so a purchase gets 120s and ordinary reads keep their default.
  Requires pylzt >= 0.2.0.
- **A currency the marketplace could not have sent is refused** rather than stored — it overflowed
  its column on Postgres while passing silently on SQLite.

### Fixed
- Branching flows lost their edges when saved: an output port was looked up by bare node type
  instead of its catalog key, so `true`/`false` and loop-body edges vanished on reload.
- The thread-bump preset could not be filled in and so could not be deployed.
- The autobump preset offered a reprice checkbox that could not work.

### Migration
- `0011_account_profile_and_run_error` — account profile columns and the run/step failure reason.

## [0.2.0] — 2026-07-18

### Added
- **Owner-only `.py` plugin runtime** (`app/plugin_runtime/`). A trusted, in-process plugin advertised
  via the `lzt_flow.plugins` entry point contributes node types, an API router, bot handlers, and
  typed lifecycle hooks (`PRE_INIT → POST_INIT → SHUTDOWN`). A `PluginManager` discovers plugins at
  process start and applies each plugin's slice to whichever of the three processes (API / worker /
  bot) consumes it. No sandbox by design — `pip install` + restart, never over the API.
- **Install plugins from the bot.** `discover()` gains a second source: `.system/plugins/<name>/`
  folders installed from a trusted git-hosted catalog (`LZT_FLOW_PLUGIN_INDEX_URL`). The bot's
  `/plugins` menu installs / updates / removes; declared `requirements` are `pip`-installed once at
  install time (never at boot). A broken folder plugin — including a node-key collision with a
  built-in — is quarantined, not fatal; entry-point plugins stay fail-closed. Auto-update and
  new-version alerts are toggleable (off by default), alert text configurable in `texts.toml`.
- **Full inline-button Telegram bot.** `/flows`, `/nodes`, `/modules`, `/plugins` open paginated
  inline lists → item cards → actions (run a flow and view its logs, install a module/plugin).
- **Flow secret inputs** — a node input `{"env": "NAME"}` resolves a host env var by name at each
  access (never compiled into the FlowIR), guarded by an allow-list prefix (`LZT_FLOW_FLOW_ENV_PREFIX`).

### Changed
- The bot is now button-driven end to end: errors flow through an `ErrorHandlerMiddleware`, handlers
  return a `TelegramMethod` (answer-update), and `FlowApiClient` parses every response into a typed
  DTO at the boundary.

### Config
- New settings: `LZT_FLOW_PLUGIN_DIR`, `LZT_FLOW_PLUGIN_INDEX_URL`, `LZT_FLOW_PLUGIN_UPDATE_INTERVAL_S`,
  `LZT_FLOW_PLUGIN_TEXTS_PATH`, `LZT_FLOW_FLOW_ENV_PREFIX`. See `.env.example`.
- No database migration — plugin state lives in `.system/plugins/state.json`, not the DB.

## [0.1.0]

Initial release: the flow engine (compile → run → resume), node catalog, official module registry,
scheduled/event triggers, the account/token pool, and the FastAPI + arq worker + Telegram bot
processes. See `ARCHITECTURE.md`.
