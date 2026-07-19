"""Application settings, loaded from environment (never a secret in the repo)."""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """lzt-flow's own configuration surface.

    NB: lzt-eventus's ``EngineConfig`` (LZT_TOKENS, LZT_TOKEN_ENC_KEY, cadences, advisory lock)
    is a *separate* config surface added in Wave 5 (decisions #21/#24) — polling tokens live
    there, action tokens live in the accounts table. Do not merge the two.
    """

    model_config = SettingsConfigDict(env_prefix="LZT_FLOW_", env_file=".env", extra="ignore")

    database_url: str = Field(
        default="postgresql+asyncpg://lzt:lzt@localhost:5432/lztflow",
        description="Async SQLAlchemy DSN (asyncpg driver).",
    )
    redis_url: str = Field(default="redis://localhost:6379/0")

    # Master key for at-rest token encryption. Base64-urlsafe 32 bytes (Fernet-compatible seed).
    master_key: str = Field(default="", description="Envelope master key; empty fails loud at use.")

    # Shared secret for mutating endpoints. Send it as X-API-Key on every mutation. When empty the
    # gate fails CLOSED (mutations blocked) unless allow_unauthenticated is explicitly set.
    api_key: str = Field(default="", description="X-API-Key required on mutations.")
    allow_unauthenticated: bool = Field(
        default=False,
        description="Loopback-dev escape hatch: allow mutations with no api_key set. Off by "
        "default so a missing key fails closed instead of silently opening mutations.",
    )

    # Identity written to Run.claimed_by so a stuck-run reaper (Wave 5) can tell executors apart.
    worker_id: str = Field(default="worker-1", description="This worker instance's id.")

    # The worker embeds the lzt-eventus EventEngine in-process (Decision #16 — no separate daemon).
    # Set 0 in a deployment that runs eventus as its OWN service: the embedded engine would block
    # forever on the Postgres advisory lock the standalone engine already holds, so the worker would
    # never finish starting. Off => the worker runs only arq + the APScheduler leader.
    embed_eventus: bool = Field(default=True)

    # Default tenant for single-tenant self-host (multi-tenant resolved from auth in Phase 2).
    default_tenant_id: str = Field(default="00000000-0000-0000-0000-000000000001")

    # Wave-03 run-history retention (FP-1): a long-lived scheduled flow must not unbounded-grow
    # run_traces. Row-cap is enforced inline at write time; the day-based window is pruned by a
    # periodic job.
    # An SSE connection is held, not completed, so this bounds a resource rather than a rate. Set
    # from the deployment's connection pool, not guessed: every open stream holds one.
    max_concurrent_streams: int = Field(
        default=50, description="Maximum simultaneously open SSE streams across the process."
    )

    run_trace_retention_days: int = Field(default=30)
    run_trace_max_rows_per_run: int = Field(default=5000)

    # Wave-06 safety backstops: a per-run step-execution budget (guards against an unbounded
    # stop_condition:goto loop or runaway self-loop, D2-2) and a conservative cap on batch-node
    # children until pylzt's real execute_batch limit is confirmed (wave-06 Risks).
    max_steps_per_run: int = Field(default=10_000)
    batch_max_children: int = Field(default=50)

    # Wave-04 synchronous flow invoke: whole-flow wall-clock ceiling for POST /flows/{id}/invoke.
    # Long flows should use the async POST /runs path; invoke is for short request-scoped runs.
    flow_invoke_timeout_s: int = Field(default=60)

    # Phase-3 testnet integration: base URL for a sandbox/testnet market API. None (default)
    # means production pylzt behavior is unchanged; set to opt into testnet mode.
    market_base_url: str | None = Field(default=None)

    # The complete set of hosts a request node may reach. EMPTY BY DEFAULT: an unconfigured
    # deployment must reach nothing, so that forgetting to configure the fence fails closed rather
    # than silently opening this host's private network to third-party flow modules. Bootstrap adds
    # api.telegram.org. Comma-separated in the environment.
    egress_allowed_hosts: frozenset[str] = Field(default=frozenset())

    @field_validator("egress_allowed_hosts", mode="before")
    @classmethod
    def _split_hosts(cls, value: object) -> object:
        """Accept "a.com, b.com" from .env — pydantic would otherwise demand JSON for a set."""
        if isinstance(value, str):
            return frozenset(part.strip() for part in value.split(",") if part.strip())
        return value

    # Allow-list prefix for a flow's {"env": NAME} inputs. A flow — untrusted registry-published
    # data — may only read host env vars whose name starts with this, so it cannot name
    # LZT_FLOW_MASTER_KEY or AWS_SECRET_ACCESS_KEY and have the engine hand it over. Must be
    # non-empty: an empty prefix would turn {"env": ...} into an arbitrary host-environment read.
    flow_env_prefix: str = Field(default="FLOW_")

    @field_validator("flow_env_prefix")
    @classmethod
    def _prefix_non_empty(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("flow_env_prefix must be non-empty (empty = arbitrary host-env read)")
        return value

    # Owner-only plugin runtime (folder source). plugin_dir holds bot-installed plugins as
    # <name>/{manifest.json, plugin.py}; the runtime scans it at start (shared across the 3
    # processes on a single-host deploy — D-8). plugin_index_url is the trusted git-hosted catalog
    # of installable plugins ("" disables the install UI). The update loop lives in the bot process.
    plugin_dir: Path = Field(default=Path(".system/plugins"))
    plugin_index_url: str = Field(default="")
    # GitHub PAT (repo read scope) for a PRIVATE plugin catalog; empty for a public one.
    plugin_index_token: str = Field(default="")
    plugin_update_interval_s: int = Field(default=3600)
    # Override the bundled plugin-notification texts (plugin_runtime/texts.toml); None = bundled.
    plugin_texts_path: Path | None = Field(default=None)


@lru_cache
def get_settings() -> Settings:
    return Settings()
