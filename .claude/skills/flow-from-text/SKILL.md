---
name: flow-from-text
description: Turn a plain-text description of a lzt.market automation into a valid lzt-flow FlowSpec JSON. Use when the user describes an automation in words ("поднимай мои лоты каждый час", "покупай steam-аккаунты дешевле 500р") and wants a ready-to-run flow. Reads the live catalog, drafts the FlowSpec, validates via compile + dry-run, and self-repairs before returning.
---

# flow-from-text — text → FlowSpec

Produce a **valid** `FlowSpec` JSON from a text brief. Never hand back a flow you have not
validated: the compiler + dry-run are the contract, not your confidence.

## Pipeline (do every step, in order)

1. **Fetch the live vocabulary.** `GET /catalog` returns every node type and its `input_schema`
   (JSON Schema per input). This is the ONLY source of node types — never invent a `type`, never
   rely on memory. If a needed capability has no node, say so; do not fabricate one.
2. **Map the brief to a graph.** Decompose the automation into the shape
   *trigger → fetch/filter → action → notify*. One node = one job. Wire data with
   `{{node_id.port}}` refs; hardcode only what is genuinely constant.
3. **Surface the tunables as `params`.** Any value the user will want to change (a category, a
   price threshold, a delay, a count) becomes a `ParamSpec` in `FlowSpec.params`, referenced from
   the node input as the literal string `"{{vars.<key>}}"`. Do NOT scatter these as raw literals
   across nodes — that is exactly what the parameter surface exists to avoid.
4. **Validate.** `POST /flows/create` then `POST /flows/{id}/compile`. On a `COMPILE_ERROR`
   envelope, read `message` (it names the node + reason) and patch — a dangling edge, a missing
   required input, an unknown type, a cycle. Repeat until compile succeeds.
5. **Dry-run.** Fire a dry-run (the import path runs it automatically; or run the flow against the
   testnet). A clean dry-run means every node executed against synthetic data. Fix any
   `DRY_RUN_FAILED` the same way.
6. **Return** the final FlowSpec JSON + a one-paragraph plain-language summary of what it does and
   which params the user can tune.

## FlowSpec shape (frozen contract — see app/domain/flow_engine/spec.py)

```jsonc
{
  "name": "human-readable name",
  "entry_node_id": "<id of the first node>",
  "params": [
    { "key": "item_id", "label": "ID лота", "control": "number",
      "required": true, "default": 321 }
  ],
  "nodes": [
    { "id": "bump", "type": "market.bump",
      "inputs": { "item_id": { "literal": "{{vars.item_id}}" } },
      "edges": {} }
  ]
}
```

- **`InputSpec`** is exactly one of `{"literal": ...}` or `{"ref": "node.port"}`. A literal string
  `"{{vars.KEY}}"` is a flow-variable reference resolved from the run's params at fire time.
- **`ParamControl`** ∈ `text · number · slider · toggle · select · account_picker ·
  category_picker · delay`. Use `slider`/`delay` with `minimum`/`maximum`/`step`; `select` needs
  `options`; `category_picker` values are pylzt category slugs.
- **`edges`** map a named output to the next node id (`{"next": "..."}`, `{"body": "..."}` for a
  loop's iteration body). `on_error` points at a fallback node. Node ids match `^\w+$`.
- Fan-out: `logic.for_each_lot` takes `item_ids` (a JSON-int-list string, e.g. from
  `logic.get_my_lots.item_ids`) and walks its `"body"` edge once per lot, exposing `item_id`.

## Rules

- **Live catalog only.** Types and input names come from `GET /catalog`, never from this file.
- **Validate before returning.** Compile + dry-run are mandatory; a flow that hasn't passed both
  is not a deliverable.
- **Tunables → params, not scattered literals.** The user configures the flow from one settings
  menu.
- **Fail honestly.** If the brief needs a capability the catalog lacks, say what's missing instead
  of inventing a node.

## Examples

`examples/*.json` are committed, compile-and-dry-run-verified FlowSpecs (see
`tests/skills/test_flow_from_text.py`). Read them for the exact wire shape:
`bump-one-lot` (param → node), `threshold-math` (slider param), `two-step-math` (ref chaining).

See also: `docs/flow-design-guide.md` for the design patterns behind these choices.
