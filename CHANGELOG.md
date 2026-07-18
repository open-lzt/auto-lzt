# Changelog

All notable changes to lzt-flow. Format loosely follows [Keep a Changelog](https://keepachangelog.com);
this project uses a single-tenant, wave-based history — see `ARCHITECTURE.md` for the design record.

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
