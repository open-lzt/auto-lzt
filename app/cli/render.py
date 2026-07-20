"""Human-readable table vs ``--json`` — the one place a command's result becomes stdout."""

from __future__ import annotations

import json
from collections.abc import Mapping, Sequence

from pydantic import BaseModel

Row = Mapping[str, object]


def models_to_rows(models: Sequence[BaseModel]) -> list[Row]:
    return [m.model_dump(mode="json") for m in models]


def print_json(rows: Sequence[Row] | Row) -> None:
    print(json.dumps(rows, indent=2, ensure_ascii=False, default=str))


def print_table(rows: Sequence[Row], columns: Sequence[str]) -> None:
    """Fixed-width columns, no color/unicode box-drawing — must stay readable piped/redirected."""
    if not rows:
        print("(none)")
        return
    values = [{col: _fmt(row.get(col)) for col in columns} for row in rows]
    widths = {col: max(len(col), *(len(v[col]) for v in values)) for col in columns}
    print("  ".join(col.upper().ljust(widths[col]) for col in columns))
    for v in values:
        print("  ".join(v[col].ljust(widths[col]) for col in columns))


def _fmt(value: object) -> str:
    return "" if value is None else str(value)
