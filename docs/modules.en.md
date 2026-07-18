<p align="right"><b>English</b> · <a href="modules.md">Русский</a></p>

# Modules — ready-made flows and node packs

A module is a directory in [open-lzt/lzt-flows](https://github.com/open-lzt/lzt-flows). Installed
from the bot:

```
/modules            — what's available
/import bump-daily  — install
```

There are two kinds, and the difference between them isn't format — it's trust.

| | `kind: flow` | `kind: python` |
|---|---|---|
| What it is | a graph of nodes the engine already has | a pack of new nodes |
| Contains | `module.yaml`, `flow.json` | `module.yaml`, `pyproject.toml`, `*.py` |
| Who publishes | **anyone** | **only the repo owner** |
| How it's installed | `/import` in the bot — the API validates and stores it | by hand on the machine: `pip install` + worker restart |
| Why | the worst a graph can do is what its nodes can do, and the validator checks all of them | this is someone else's code in your worker, with your tokens and your money |

**The API never installs code.** `/import` for `kind: python` is rejected before a single byte is
downloaded. Otherwise installing a pack would be remote code execution available to anyone holding
an API key — and that key belongs to the bot, which is one hijacked Telegram account away from
being compromised.

## Publishing a flow

Add yourself to `authors.yml` once — in a **separate PR**. CI rejects a PR that touches both
`authors.yml` and a module: a claim of identity and the flow it vouches for don't get checked in
one glance.

Then a PR with `modules/<name>/`:

**`module.yaml`**
```yaml
schema_version: 1
name: bump-daily
version: 1.0.0
author: your-github-handle    # must match the PR's author
description: Bumps all of the account's lots.
requires_nodes:
  - logic.get_my_lots
  - logic.for_each_lot
  - market.bump
```

**`flow.json`** — the graph. Validate it locally with the same validator CI uses:

```bash
uv run lzt-flow-validate modules/bump-daily
```

Passes — it installs. Fails — you see why in the PR, not later. It's the same function; there
aren't two implementations, because they'd drift, and on the day they drift CI would pass what the
backend runs anyway.

Don't touch `index.json` — it's rebuilt on main. A PR touching it is rejected.

## Why a module gets rejected

| Reason | What happened |
|---|---|
| `unknown_node` | references a node the engine doesn't have |
| `forbidden_capability` | uses a node with `reflective` — an arbitrary marketplace API call |
| `compile_failed` | the graph doesn't pass the **real** compiler |
| `code_in_module` | a `.py` file in `kind: flow`, or a stray file in a pack |
| `bad_name` | the name isn't `[a-z0-9-]` — it becomes a path segment |
| `checksum_mismatch` | the bytes aren't the ones that were reviewed |

Filtered by **capability**, not by name: a reflective node added a month later gets caught on its
own, with no need to edit a list.

## Being honest about trust

**`sha256` is transport integrity.** It proves the flow you have is byte-for-byte the one that was
reviewed. It is **not a signature** and says nothing about whether the author can be trusted.

The only thing protecting you from a hostile module is that a maintainer read it before merging.
`flow.json` is plain JSON — read it yourself, everything the module will do is visible in it.

**CI is not a boundary.** For a fork's PR, GitHub takes the workflow from the merge of head into
base — the PR's author can edit `validate.yml` in the same PR and strip the check. They don't get
secrets or a write token, but they do strip the check. Code on main is held by **CODEOWNERS +
branch protection**: a file lands there because the owner clicked merge. The CI check exists so a
reviewer understands why a PR is suspicious before reading four hundred lines.

The backend doesn't trust CI either: on import, a module is **re-validated from scratch** against
*this* process's node registry. CI validated against whatever set the runner had at merge time;
you may have a different one — a node was removed, a plugin was deleted. The registry knows what
was safe *there and then*; only your process knows what's runnable *here and now*. Otherwise it's
a TOCTOU with a paid action at the end.

## If the registry is unreachable

The list will be **empty**, not stale. An empty catalog is a visibly degraded interface. A
stale-but-unverified catalog looks like it's working and offers modules whose integrity nobody
checked. A silent failure is the dangerous one.

The bot will say "no modules **or** the registry is unreachable" — it deliberately doesn't
distinguish the two cases.

## Publishing a node pack

Repo owner only. A directory with `module.yaml` (`kind: python`), `pyproject.toml`, and a
package. Examples: `modules/notify-pack/`, `modules/pricing-pack/`.

For how to write the nodes themselves — see [Plugins](plugins.en.md). Installed on the machine:

```bash
pip install "lzt-flow-notify-pack @ git+https://github.com/open-lzt/lzt-flows.git#subdirectory=modules/notify-pack"
sudo systemctl restart open-lzt-flow-worker open-lzt-flow-api
```

Don't forget to open the host for nodes that need network access:

```bash
LZT_FLOW_EGRESS_ALLOWED_HOSTS=api.telegram.org,discord.com
```

## See also

- [Plugins](plugins.en.md) — how to write a node
- [Flow design](flow-design-guide.en.md) — how to build a graph
- [Architecture](../ARCHITECTURE.md)
