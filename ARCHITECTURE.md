# Architecture — lzt-flow

lzt-flow is a **server-side, no-code automation engine** for the lzt.market marketplace. You
describe a flow ("bump my lots on a schedule"), press Deploy, and close the tab — the flow keeps
running 24/7 without your machine. This document contrasts that design with a typical
client-side no-code builder, then places lzt-flow inside the wider `lzt-*` platform.

---

## 1. Contrast — client-side builder vs lzt-flow

| Dimension | Typical client-side no-code builder | **lzt-flow** |
|---|---|---|
| **I/O** | Runs in the browser tab; the automation dies when the tab closes or the laptop sleeps | Server-side worker; the flow runs unattended 24/7, survives tab close and host restart |
| **Data between layers** | Untyped JSON blobs / `dict` passed hand-to-hand between steps | Typed DTOs at every boundary; the flow compiles to an immutable, validated **FlowIR** before it can run |
| **Errors** | Silent failure or a toast that vanishes; no record of what broke | Typed error hierarchy carrying args, structured logs with `request_id`, fail-loud at the trust boundary |
| **Storage** | Browser `localStorage` / ephemeral memory | Durable Postgres (flows, runs, per-step state) + Redis (queues, locks, rate budgets) |
| **Tokens** | Marketplace token held in the browser or stored in plaintext | Envelope-encrypted per tenant at rest (Fernet/AES-GCM); the DB only ever holds ciphertext |
| **Execution** | Ephemeral; a crash loses all progress and restarts from zero | Stateful runtime with **idempotent steps** and **resume-after-restart** — a re-picked run continues from its last committed step, never double-acts |
| **Model / catalog** | Wide generic block catalog (16+ blocks) that still needs the user to wire raw HTTP | Focused domain nodes wrapping the typed `pylzt` SDK (bump, reprice, get-my-lots, for-each-account …) — the value is depth, not width |
| **Layers** | Monolithic frontend, no server contract | `Handler → Service → Repository → Model`, DTOs on boundaries, standing on the reusable `lzt-*` ecosystem |

The single proof this design exists to deliver: **a seller trusts a *server* flow with their
account and gets a 24/7 autopilot that runs without their PC.**

---

## 2. Runtime shape (this repo)

```
                        ┌──────────────┐
   React Flow canvas ──▶│  FastAPI app │  POST /flows · /compile · POST /runs · GET /runs/{id}
   (frontend/)          │  (app/api)   │  GET /flows · /flows/{id}/status · /health
                        └──────┬───────┘
                               │  Handler → Service → Repo → Model, DTOs at each edge
              ┌────────────────┼─────────────────────────────┐
              ▼                ▼                              ▼
        ┌───────────┐   ┌──────────────┐            ┌──────────────────┐
        │ Postgres  │   │    Redis     │            │  worker process  │
        │ flows/    │   │ arq queue,   │            │  (python -m      │
        │ runs/     │   │ locks, rate  │            │   app.worker)    │
        │ ir/state  │   │ budgets      │            │  arq + APScheduler│
        └───────────┘   └──────────────┘            │  + embedded       │
                                                     │  lzt-eventus      │
                                                     └──────────────────┘
```

- **One worker process** supervises the arq run-executor, the APScheduler `on-schedule` leader,
  and the embedded `lzt-eventus` `on-event` engine under a single graceful SIGTERM (Decision #16 —
  no second daemon, no extra network seam). Run only one replica: the scheduler is a single leader
  guarded by a Postgres advisory lock.
- **Two isolated config surfaces** (Decision #24): lzt-flow's own settings use the `LZT_FLOW_*`
  prefix and hold action-token encryption (`LZT_FLOW_MASTER_KEY`); the embedded event engine uses
  the `LZT_*` prefix with its **own** poll-token key (`LZT_TOKEN_ENC_KEY`). They never merge.
- **Two schema chains**: lzt-flow's own tables via Alembic (`alembic upgrade head`); lzt-eventus's
  tables via `ensure_eventus_schema()` (create_all, checkfirst) at worker startup.

---

## 3. Platform layers — lzt-flow as one consumer of the `lzt-*` ecosystem

lzt-flow is not a monolith vibe-coded in ten days — it **stands on reusable ecosystem
infrastructure**. The layered view below is also the reframing statement: `open-lzt` → `open-market`,
where lzt-flow is *one of many* consumers of the platform rather than its apex.

| Layer | Name | Responsibility | State in this plan |
|---|---|---|---|
| **L0** | **Transport** — `pylzt` | Typed async SDK over lzt.market/lolzteam: OpenAPI codegen (202 methods, 230 models), token pool + per-RateClass limiter, proxy pool with circuit breaker, cursor pagination, typed error hierarchy | ✅ **built** (reused, not rewritten) |
| **L1** | **Credential Custodian** | Per-tenant token vault + envelope encryption; scoped delegation ("act as account X" without exposing the raw token) | 🔶 **seam now** (envelope + `tenant_id` in place; vault-as-a-service is Phase 2) |
| **L2** | **Data Fabric** | Durable Postgres model (flows, runs, IR, per-step state) + Redis (queues/locks/budgets) | ✅ **built** |
| **L3** | **Event Fabric** — `lzt-eventus` | Self-hosted event engine over `pylzt`: polls the catalog, diffs into 38 typed `DomainEvent`s, durable Postgres log, catch-up bus with per-consumer cursor, webhook delivery (HMAC + retry + DLQ), **embeddable in-process** | ✅ **built** (reused, embedded in the worker) |
| **L4** | **Action Gateway** | Idempotent single entry point for mutations, mirroring the event fabric | ⏳ **Phase 2** (no second consumer today — deferred, not seamed, to avoid premature indirection) |
| **L5** | **Runtime** | IR compiler + stateful step interpreter: idempotent steps, resume-after-restart, optimistic locking; triggers `on-schedule` / `on-event` / `manual` | ✅ **built** (lzt-flow's own domain) |
| **L6** | **Consumers** | lzt-flow's no-code canvas **plus** bots / plugins / third-party scripts that attach to the same layers | ✅ flow built; other consumers are the `open-market` vector |

**Legend:** ✅ built · 🔶 seam cut now (clean internal contract, service extraction is Phase 2)
· ⏳ deferred to Phase 2 (second consumer does not exist yet).

The reuse story matters twice: (1) it shows the flow engine sits on shared, already-built
ecosystem infrastructure (`pylzt` L0 transport, `lzt-eventus` L3 event fabric), so the ten-day
build is a *thin domain layer over mature plumbing*, not a from-scratch monolith; (2) it names
`open-lzt → open-market` — a unifying layer over marketplace SDKs (lolz as the reference
implementation, with `starvell-sdk` / playerok / FunPay as future adapters behind the same
`BaseMarketplace` seam) — as a deliberate architectural vector, not an accident.

---

## 3a. Sandbox-run vs. the existing dry-run gate

Two different safety mechanisms exist side by side, and they are **not** the same thing:

- **Sandbox-run** (`LZT_FLOW_MARKET_BASE_URL`, see [README.md](./README.md#sandbox-testing-against-a-fake-market-backend)):
  real code executes for real, but against a fake backend (`lzt-testnet`) instead of
  `prod-api.lzt.market`. Use case: local dev / manual end-to-end testing with a safe fake API
  that still exercises the real HTTP call path.
- **Dry-run gate** (`app/domain/flow_engine/dryrun.py`, unchanged by this plan): the real backend
  is never touched at all — no HTTP call happens, only structural flow validation runs. Use case:
  CI / pre-flight checks before a flow is allowed to run for real.

They compose rather than overlap: a sandboxed flow (pointed at `lzt-testnet`) can still be passed
through the existing dry-run structural gate first, unchanged — this plan does not replace or
interact with that gate's logic, it only adds a second, independent way to redirect where real
calls land.

## 4. Security & correctness floor (never cut)

Types on boundaries · DTOs between layers · idempotency on mutate endpoints (`fast-buy` is not
natively idempotent → the node carries its own key) · envelope token encryption per tenant ·
structured logging with `request_id` · fail-loud on invariant violations. These are correctness,
not scale — they hold regardless of the CUT-list (see `.plans/lzt-flow/00-overview.md`).

See also: [README.md](./README.md) for quickstart and the demo.
