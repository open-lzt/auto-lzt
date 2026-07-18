"""Flat result DTO produced by a node. Kept dependency-free so both model.py (RunStep.result) and
base_node.py (RunContext output) can import it without a cycle."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(slots=True, frozen=True)
class StepResultDTO:
    """A node's output. JSON-primitive-only (no nested Any) so it round-trips through JSONB and is
    safe to feed the next node's inputs."""

    node_id: str
    output: dict[str, str | int | float | bool | None]
