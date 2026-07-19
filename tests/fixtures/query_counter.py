"""Counts the SQL statements a block of code issues.

A scaling claim is either asserted or it is marketing. A benchmark would measure this host on this
day; a statement count measures the shape of the access pattern, which is the thing that actually
degrades when the data grows. So the projection tests assert "the count does not change between 20
rows and 500 rows" rather than "it took N milliseconds".

Hooks ``before_cursor_execute``, so transaction control (BEGIN/COMMIT) is not counted — those do not
round-trip through a cursor. What is counted is what the application asked the database to do.
"""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass, field
from typing import Any

from sqlalchemy import event
from sqlalchemy.ext.asyncio import AsyncEngine


@dataclass(slots=True)
class QueryCount:
    statements: list[str] = field(default_factory=list)

    @property
    def total(self) -> int:
        return len(self.statements)

    def __str__(self) -> str:
        """Rendered into the assertion message — a bare "expected 1, got 4" tells you the test
        broke but not which query snuck in, and that is the whole diagnosis."""
        return "\n".join(f"  {i + 1}. {s.strip()[:160]}" for i, s in enumerate(self.statements))


@contextmanager
def count_queries(engine: AsyncEngine) -> Iterator[QueryCount]:
    counter = QueryCount()

    def _on_execute(
        _conn: Any, _cursor: Any, statement: str, _params: Any, _ctx: Any, _many: bool
    ) -> None:
        counter.statements.append(statement)

    sync_engine = engine.sync_engine
    event.listen(sync_engine, "before_cursor_execute", _on_execute)
    try:
        yield counter
    finally:
        event.remove(sync_engine, "before_cursor_execute", _on_execute)
