"""BaseRequestNode — the shape of every node that talks to something other than the marketplace.

``execute()`` is final: subclasses supply ``build_request`` and ``parse_response`` and never touch
the wire. That is not tidiness, it is the security boundary. Egress policy, retry, backoff, timeout
and the idempotency claim all live in one place, so a plugin author writing a request node gets
them whether they wanted them or not, and cannot write a node that skips them (R-2).

Retry is on transport failure and on the statuses that mean "later, not never" (429, 5xx). A 4xx is
the endpoint saying the request itself is wrong; repeating it just burns the rate limit.

Deviation from the frozen contract, which spells ``parse_response(status, body)``: a StepResultDTO
carries the node id it came from, and a RunFailed carries the run and node it failed in. Neither is
derivable from a status code and a body, so ``ctx`` is passed through. Stashing it on ``self``
instead would have kept the signature and traded it for per-instance mutable state on a node the
interpreter may reuse.
"""

from __future__ import annotations

import asyncio
import random
from abc import ABC, abstractmethod
from collections.abc import Mapping
from typing import Any, ClassVar

import httpx
import structlog

from app.domain.catalog.capabilities import EGRESS, NodeCapability
from app.domain.egress.policy import EgressBlocked
from app.domain.egress.transport import HttpMethod, RequestSpec
from app.domain.flow_engine.base_node import BaseNode, RunContext
from app.domain.flow_engine.dtos import StepResultDTO
from app.domain.flow_engine.errors import RunFailed

__all__ = ["BaseRequestNode", "HttpMethod", "RequestSpec"]

log = structlog.get_logger()


class BaseRequestNode(BaseNode, ABC):
    node_type: ClassVar[str]
    capabilities: ClassVar[frozenset[NodeCapability]] = EGRESS
    max_attempts: ClassVar[int] = 3
    backoff_base_s: ClassVar[float] = 0.5
    retry_on_status: ClassVar[frozenset[int]] = frozenset({429, 500, 502, 503, 504})

    @abstractmethod
    def build_request(self, ctx: RunContext) -> RequestSpec: ...

    @abstractmethod
    def parse_response(
        self, ctx: RunContext, status: int, body: Mapping[str, Any]
    ) -> StepResultDTO: ...

    async def execute(self, ctx: RunContext) -> StepResultDTO:
        """FINAL — do not override. Owns egress policy, retry/backoff, timeout and the idempotency
        claim, none of which a subclass may opt out of."""
        spec = self.build_request(ctx)
        last_status: int | None = None
        last_body: Mapping[str, Any] = {}

        for attempt in range(1, self.max_attempts + 1):
            try:
                status, body = await ctx.deps.http.request(spec)
            except EgressBlocked as exc:
                # Never retried: the fence's verdict will not change on a second try, and retrying
                # would turn one refused request into a scan.
                raise RunFailed(
                    ctx.run_id,
                    ctx.node.id,
                    f"egress blocked: host={exc.host} ip={exc.ip} reason={exc.reason.value}",
                ) from exc
            except (httpx.TimeoutException, httpx.TransportError) as exc:
                if attempt == self.max_attempts:
                    raise RunFailed(ctx.run_id, ctx.node.id, f"request failed: {exc!r}") from exc
                await self._backoff(attempt)
                continue

            if status not in self.retry_on_status or attempt == self.max_attempts:
                return self.parse_response(ctx, status, body)

            last_status, last_body = status, body
            log.info(
                "request_node_retry",
                node_type=self.node_type,
                node_id=ctx.node.id,
                status=status,
                attempt=attempt,
            )
            await self._backoff(attempt, body)

        # Unreachable: the loop either returns or raises on its final attempt. Kept so a future
        # edit to the loop bounds fails loudly rather than falling off the end returning None.
        return self.parse_response(ctx, last_status or 0, last_body)

    async def _backoff(self, attempt: int, body: Mapping[str, Any] | None = None) -> None:
        """Exponential, with jitter. Jitter matters more than the curve here: without it, a
        fan-out that hits one 429 retries in lockstep and re-creates the burst that caused it
        (R-10). An endpoint's own ``retry_after`` wins — it knows better than the formula."""
        retry_after = _retry_after(body)
        if retry_after is not None:
            await asyncio.sleep(retry_after)
            return
        delay = self.backoff_base_s * (2 ** (attempt - 1))
        await asyncio.sleep(delay * (0.5 + random.random()))  # noqa: S311 — jitter, not crypto


def _retry_after(body: Mapping[str, Any] | None) -> float | None:
    """Telegram-style ``{"parameters": {"retry_after": 30}}``, capped so a hostile or broken
    endpoint cannot park a worker slot for an hour by naming a huge number."""
    if not body:
        return None
    params = body.get("parameters")
    value = params.get("retry_after") if isinstance(params, dict) else None
    if not isinstance(value, int | float) or isinstance(value, bool) or value <= 0:
        return None
    return min(float(value), 60.0)
