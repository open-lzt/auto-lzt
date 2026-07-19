<p align="right"><b>English</b> · <a href="flow-design-guide.md">Русский</a></p>

# Designing a flow from text — a guide

How to turn a plain-language description of an automation into a working `FlowSpec` for
lzt-flow. A companion document to the `flow-from-text` skill (which does this automatically;
here are the principles, for doing it deliberately — by hand, or to check the skill's result).

## 1. Break the task into the canonical skeleton

Almost any marketplace/forum automation fits the shape:

```
trigger → fetch/filter → action → notify
```

- **Trigger** — what starts the flow: an event (`new lot`, `message`), a schedule (`every hour`),
  or a manual run. On the canvas the trigger is a separate node marking the entry point.
- **Fetch/filter** — get the data and drop what's not needed (`logic.get_my_lots`, `logic.compare`,
  `logic.condition`, `logic.switch`).
- **Action** — what it's all for (`market.bump`, `market.reprice`, `market.relist`, a purchase,
  forum operations via `pylzt.dynamic_call`).
- **Notify** — an optional final step (a message/log).

One node = one job. If a node can't be described without an "and" — it's two nodes.

## 2. Data flows through `ref`, constants through `literal`

- `{"ref": "fetch.item_ids"}` — the input takes the `item_ids` output of node `fetch`.
- `{"literal": 500}` — a hardcoded value.
- `{"literal": "{{vars.threshold}}"}` — a value from a **flow parameter** (see §3).

The path can go deeper: `{"ref": "node.port[0].field"}` (see `path.py`).

Secrets go through `env`, never through `literal`:

- `{"env": "FLOW_BOT_TOKEN"}` — the input takes the value of a host environment variable **by
  name**. The value is read on **every access** at runtime, not at compile time: only the name
  ever ends up in the compiled `FlowIR`, in `flow.json`, and in the trace — never the secret
  itself. Rotating the token doesn't require rebuilding the flow.
- Only the prefix from the `LZT_FLOW_FLOW_ENV_PREFIX` setting (default `FLOW_`) is allowed — a
  flow cannot name `LZT_FLOW_MASTER_KEY` or `AWS_SECRET_ACCESS_KEY` and get them. A name outside
  the prefix or an unset variable → `RunFailed` (fail-closed), never an empty string: an empty
  token silently turns into an unauthenticated request.
- Never commit a token to the registry — publish the flow with `{"env": "FLOW_…"}` and set the
  actual value in the host environment.

⚠️ Reading on every access means the value can **change mid-run** (rotation between two nodes).
That's correct for tokens; not for a value the flow compares against itself (in that case, read it
once into a node and pass it via `ref`).

## 3. Configurable values → parameters, not scattered literals

Anything a user will want to change — a category, a price threshold, a delay, a count — gets
pulled out into `FlowSpec.params` as a `ParamSpec` and wired into a node as the literal
`"{{vars.<key>}}"`. Then the flow has **one settings menu** ("Threshold: 500", "Category: steam"),
instead of values hidden across different blocks.

Controls (`ParamControl`): `text · number · slider · toggle · select · account_picker ·
category_picker · delay`. Give a slider `minimum/maximum/step`; `select` — `options`;
`category_picker` operates on pylzt category slugs (`steam`, `fortnite`, …).

## 4. Branches and loops

- **Condition**: `logic.condition` / `logic.compare` → different `edges` by result.
- **N-way**: `logic.switch` with a typed list of cases.
- **Loop over lots**: `logic.get_my_lots` → `logic.for_each_lot` (input `item_ids` — a JSON string
  of an int list), body via the `"body"` edge, `item_id` available on each iteration.
- **Loop over accounts**: `logic.for_each_account`.
- **Errors**: a node's `on_error` points to a fallback node — never swallow a failure silently.

## 5. Always validate

An unvalidated FlowSpec isn't a result. Two gates:

1. **compile** (`POST /flows/{id}/compile`) — static check: dangling edges, missing required
   inputs, unknown types, cycles. The error comes back as a typed envelope with the node name and
   reason — fix from that.
2. **dry-run** — a run through the real interpreter on synthetic data, no network or DB. Catches
   what the compiler can't see (value formats, domains).

## 6. Common scenarios (templates)

| I want | Skeleton |
|---|---|
| Bump one lot | `market.bump(item_id={{vars.item_id}})` |
| Bump all my lots | `get_my_lots → for_each_lot → bump(item_id=loop.item_id)` |
| Buy by filter | `trigger(new lot) → compare(price < {{vars.max}}) → buy` |
| Reprice by threshold | `get_my_lots → for_each_lot → reprice` |

More ready-made scenarios — in the template library `seeds/templates/`.

See also: the `flow-from-text` skill (`.claude/skills/flow-from-text/SKILL.md`) and its examples
`examples/*.json`.

## See also

- [Plugins](plugins.en.md) — if the node you need doesn't exist, add your own
- [Modules](modules.en.md) — how to publish a ready-made flow to the official registry
- [Architecture](../ARCHITECTURE.md) — how a flow is compiled and executed
