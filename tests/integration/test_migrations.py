"""The alembic chain actually runs — every other integration test builds its schema straight
from `Base.metadata.create_all`, so `alembic upgrade head` is never exercised and a broken
migration (bad `down_revision`, a syntax error only Postgres's dialect catches, a trigger that
doesn't survive a re-run) would ship invisibly.
"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import pytest
from alembic.config import Config
from alembic.script import ScriptDirectory

REPO_ROOT = Path(__file__).resolve().parents[2]


def test_migration_chain_has_exactly_one_head() -> None:
    """Revision-graph integrity, no DB needed: a `down_revision` typo or a second branch left by
    a concurrent migration author would surface here as 0 or 2+ heads instead of 1."""
    cfg = Config(str(REPO_ROOT / "alembic.ini"))
    script_dir = ScriptDirectory.from_config(cfg)

    heads = script_dir.get_heads()

    assert len(heads) == 1
    # Walking base->head must not raise (would on a cycle or a dangling down_revision).
    reachable = {rev.revision for rev in script_dir.walk_revisions()}
    # Every revision FILE has to be on that walk. `len(revisions) >= 11` could not fail on any real
    # defect: a revision orphaned onto its own branch (the exact mistake a second author makes)
    # leaves the count untouched and the migration simply never runs.
    on_disk = {path.stem for path in (REPO_ROOT / "alembic" / "versions").glob("[0-9]*.py")}
    assert {rev for rev in on_disk} == reachable


@pytest.mark.pg
def test_alembic_upgrade_downgrade_upgrade_on_real_postgres(pg_dsn: str) -> None:
    """`upgrade head` -> `downgrade base` -> `upgrade head` against a real, empty-to-start
    Postgres — the actual boot path a fresh prod deploy runs, not `create_all`.

    Destructive on the target database (downgrade base drops every lzt-flow table): point
    LZT_FLOW_DATABASE_URL at a disposable Postgres, the same contract the `pg` marker already
    states in pyproject.toml.
    """
    env = {**os.environ, "LZT_FLOW_DATABASE_URL": pg_dsn}

    def alembic(*args: str) -> None:
        result = subprocess.run(
            [sys.executable, "-m", "alembic", *args],
            cwd=REPO_ROOT,
            env=env,
            capture_output=True,
            text=True,
            timeout=120,
            check=False,
        )
        assert result.returncode == 0, result.stdout + result.stderr

    alembic("upgrade", "head")
    alembic("downgrade", "base")
    alembic("upgrade", "head")
