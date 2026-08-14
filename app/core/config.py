"""Application settings, loaded from environment (never a secret in the repo)."""

from __future__ import annotations

import base64
import binascii
from functools import lru_cache
from pathlib import Path
from typing import Annotated

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, NoDecode, SettingsConfigDict

_MASTER_KEY_BYTES = 32
GENERATE_KEY_HINT = (
    'Generate one: python -c "from cryptography.fernet import '
    'Fernet;print(Fernet.generate_key().decode())"'
)


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

    @field_validator("master_key")
    @classmethod
    def _master_key_shape(cls, value: str) -> str:
        """Exactly 32 bytes of base64-urlsafe, or empty.

        The trap: this key is NOT stretched. ``EnvelopeCipher`` runs it through HKDF, and HKDF is
        one hash — it derives a per-tenant key, it does not add work. A passphrase here means the
        whole token table is an offline dictionary attack away, and nothing in the app would ever
        notice. So the shape is enforced where the value enters the process.

        Empty is still accepted here because a worker/bot deployment that never touches account
        tokens has nothing to configure; the API refuses to start on it (``app.main``).
        """
        value = value.strip()
        if not value:
            return ""
        try:
            raw = base64.urlsafe_b64decode(value)
            # Decoding alone let the passphrase through, which is the one thing this validator
            # exists to stop. `urlsafe_b64decode` silently skips characters outside the alphabet
            # instead of refusing them, and 43 arbitrary characters plus a padding `=` then decode
            # to exactly 32 bytes — so the length check below saw a well-shaped key. Re-encoding is
            # the real test: only a canonical encoding of these bytes gives back the string it came
            # from, which also rejects every non-alphabet character the decoder swallowed.
            if base64.urlsafe_b64encode(raw).decode() != value:
                raise ValueError("not a canonical base64-urlsafe encoding")
        except (binascii.Error, ValueError) as exc:
            raise ValueError(
                f"LZT_FLOW_MASTER_KEY must be base64-urlsafe encoded {_MASTER_KEY_BYTES} bytes. "
                "A passphrase is not a key — HKDF derives, it does not stretch. "
                + GENERATE_KEY_HINT
            ) from exc
        if len(raw) != _MASTER_KEY_BYTES:
            raise ValueError(
                f"LZT_FLOW_MASTER_KEY decodes to {len(raw)} bytes, expected exactly "
                f"{_MASTER_KEY_BYTES}. A passphrase is not a key — HKDF derives, it does not "
                "stretch. " + GENERATE_KEY_HINT
            )
        return value

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

    # Must stay BELOW the idle timeout of whatever sits in front of the app (nginx
    # proxy_read_timeout, an ELB idle timeout, a corporate proxy) — those reap a quiet socket, and
    # the heartbeat is the only thing that stops them. A different deployment fronts a different
    # intermediary, so this is configuration rather than a constant.
    stream_heartbeat_s: float = Field(
        default=15.0, description="Seconds of silence before an SSE stream emits a keepalive."
    )

    # Recovery sweep for Runs whose row committed but whose arq push never landed
    # (``triggers/firing.sweep_stale_pending_runs``). The grace must stay comfortably above the arq
    # pickup latency of a healthy worker — set below it, the sweep re-enqueues runs that are merely
    # queued — and that latency is a property of the deployment, not of the code.
    pending_sweep_grace_s: int = Field(default=300)
    pending_sweep_batch_limit: int = Field(default=200)

    run_trace_retention_days: int = Field(default=30)
    run_trace_max_rows_per_run: int = Field(default=5000)

    # Wave-06 safety backstops: a per-run step-execution budget (guards against an unbounded
    # stop_condition:goto loop or runaway self-loop, D2-2) and a conservative cap on batch-node
    # children until pylzt's real execute_batch limit is confirmed (wave-06 Risks).
    # Ceiling on runs one tenant may have queued or executing at once; 0 disables it. Same shape as
    # max_concurrent_streams above and for the same job: stop one caller from taking the whole
    # worker. A flat number per installation on purpose — a per-tenant SCHEDULE of caps is usage
    # policy, which belongs to whoever operates a deployment, not to the engine.
    max_concurrent_runs_per_tenant: int = Field(default=0)
    max_steps_per_run: int = Field(default=10_000)
    batch_max_children: int = Field(default=50)

    # Wave-04 synchronous flow invoke: whole-flow wall-clock ceiling for POST /flows/{id}/invoke.
    # Long flows should use the async POST /runs path; invoke is for short request-scoped runs.
    flow_invoke_timeout_s: int = Field(default=60)

    # Phase-3 testnet integration: base URL for a sandbox/testnet market API. None (default)
    # means production pylzt behavior is unchanged; set to opt into testnet mode.
    market_base_url: str | None = Field(default=None)

    # Where a flow compiled with `testnet=True` sends its marketplace calls. NOT the same knob as
    # `market_base_url` above: that one redirects the WHOLE deployment, so using it to try an
    # autobuy safely would also redirect every live automation the operator already runs. This one
    # is per-flow and leaves the rest on the real market.
    #
    # Defaults to lzt-testnet's own documented address, so a shipped testnet template works against
    # `scripts/run.sh` in that repo without configuration. Nothing connects until a testnet flow
    # actually runs, and if the mock is not up the failure names this port.
    market_testnet_base_url: str | None = Field(default="http://127.0.0.1:8765")

    @field_validator("market_base_url", "market_testnet_base_url", mode="before")
    @classmethod
    def _blank_is_unset(cls, value: object) -> object:
        """An empty env var means "not configured", not "configure me with an empty URL".

        Rendered config files write `LZT_FLOW_MARKET_BASE_URL=` when there is no testnet to point
        at, and pydantic read that as the string `""` — a value, so the default never applied. The
        SDK then built requests against an empty base and failed with "Request URL is missing an
        'http://' or 'https://' protocol", which names the symptom three layers away from the cause.
        """
        return None if isinstance(value, str) and not value.strip() else value

    # The complete set of hosts a request node may reach. EMPTY BY DEFAULT: an unconfigured
    # deployment must reach nothing, so that forgetting to configure the fence fails closed rather
    # than silently opening this host's private network to third-party flow modules. Bootstrap adds
    # api.telegram.org. Comma-separated in the environment.
    # NoDecode: pydantic-settings JSON-decodes a complex type (frozenset) inside the env source,
    # BEFORE any validator runs — so without it `api.telegram.org` dies as invalid JSON and the
    # validator below never sees the string it exists to split.
    egress_allowed_hosts: Annotated[frozenset[str], NoDecode] = Field(default=frozenset())

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

    # The one switch that decides whether this installation executes code it did not ship. OFF
    # means no .py plugin is imported from either source, and the whole install/update surface is
    # gone with it — flows and the built-in nodes are untouched, since those never travel through
    # the plugin runtime. Exists because a plugin runs in-process with the tokens and the money:
    # that is the owner's own risk on a self-host, and somebody else's on a hosted deployment.
    plugins_enabled: bool = Field(default=True)
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
