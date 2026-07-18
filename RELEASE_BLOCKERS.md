# Release Blockers — v0.2.0

Owner-only actions that must clear before lzt-flow can be released publicly. Everything in the repo
is release-ready (lint + types + 524 tests green, CI config correct, version/lock/env/changelog
done); these two are external and cannot be resolved from inside the codebase.

Status legend: 🔴 blocking · 🟡 should-do · ✅ cleared.

---

## 🔴 B-1 — Private dependencies not published

`pyproject.toml` depends on two first-party packages resolved from private git repos via
`[tool.uv.sources]`:

| Dependency | Source | Used for |
|---|---|---|
| `pylzt` | `git+https://github.com/open-lzt/pylzt.git@main` | marketplace transport SDK |
| `lzt-eventus[engine]` | `git+https://github.com/open-lzt/lzt-eventus.git@main` | on-event polling engine |

Until these repos are public (or the packages published to an index), a fresh `uv sync` on any
machine without git access to `open-lzt` fails — including public CI and any external evaluator.

**To clear:** publish `open-lzt/pylzt` and `open-lzt/lzt-eventus` (public repo or package index),
then re-pin `[tool.uv.sources]` to the published SHA/tag and `uv lock`.

**Current stance:** everything stays private for now (owner decision), so this is knowingly deferred,
not overlooked.

---

## 🔴 B-2 — GitHub Actions not running

`.github/workflows/ci.yml` is correct (ruff · ruff format · mypy · pytest+cov · frontend · smoke),
but the workflow returns `startup_failure` in ~0s with no annotations — the usual cause is Actions
minutes exhausted on a private repo, i.e. an account/billing issue, not a config one.

**To clear (owner, needs `user` billing scope):**
```
gh auth refresh -h github.com -s user
gh api users/zlexdev/settings/billing/actions   # confirm a live quota
```
Until that returns a live quota, CI is green-by-config but does not actually run. Runs pass locally
via `uv run ruff check . && uv run ruff format --check . && uv run mypy app && uv run pytest`.

---

## 🟡 B-3 — No demo asset

`README.md` and `docs/DEMO.md` reference a canvas demo GIF placeholder. Capture one before a public
launch (see `docs/DEMO.md`).

## 🟡 B-4 — Tag the release

After `master` is on origin with 0.2.0, tag it: `git tag v0.2.0 && git push origin v0.2.0`.
