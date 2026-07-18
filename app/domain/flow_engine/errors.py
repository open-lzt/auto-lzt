"""flow_engine typed errors. Carry args, not pre-formatted text.

``CompileError``/``EntityNotFound``/``FlowNotCompiled`` are HTTP-surfaced (subclass ``AppError`` so
the one envelope handler maps them). ``RunFailed``/``RunAlreadyClaimed``/``PathResolutionError``/
``UnknownDynamicMethod``/``DynamicMethodArgMismatch`` are worker control-flow — never an HTTP
response, always surfaced via ``runtime.py``'s blanket wrap into ``RunFailed`` — so they stay plain
exceptions.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from app.core.exceptions import AppError, ErrorCode

if TYPE_CHECKING:
    from app.domain.flow_engine.model import RunId


class FlowInvokeTimeout(AppError):
    """A synchronous ``POST /flows/{id}/invoke`` exceeded the whole-flow wall-clock ceiling — the
    run keeps executing on the worker; the caller should switch to the async run path or poll."""

    status_code = 504
    code = ErrorCode.FLOW_INVOKE_TIMEOUT

    def __init__(self, run_id: str, timeout_s: int) -> None:
        super().__init__(f"flow invoke exceeded {timeout_s}s ceiling (run {run_id})")
        self.run_id = run_id
        self.timeout_s = timeout_s

    @property
    def client_message(self) -> str:
        return "Flow took too long to run synchronously; use the async run endpoint."


class ParamValidationError(AppError):
    """A provided flow parameter failed validation against its ``ParamSpec`` (required-missing,
    wrong type, or out of the declared min/max range) at the run trust boundary."""

    status_code = 400
    code = ErrorCode.VALIDATION_ERROR

    def __init__(self, key: str, reason: str) -> None:
        super().__init__(f"param {key!r}: {reason}")
        self.key = key
        self.reason = reason

    @property
    def client_message(self) -> str:
        return f"Invalid parameter '{self.key}': {self.reason}"


class CompileError(AppError):
    """A Flow failed static validation (dangling edge / missing input / cycle / unknown type) and
    must never reach the runtime."""

    status_code = 400
    code = ErrorCode.COMPILE_ERROR

    def __init__(self, reason: str, node_id: str | None = None) -> None:
        super().__init__(f"compile error: {reason}" + (f" (node {node_id})" if node_id else ""))
        self.reason = reason
        self.node_id = node_id

    @property
    def client_message(self) -> str:
        return f"Invalid flow: {self.reason}"


class ImportValidationError(AppError):
    """A flow-import upload failed shape validation (schema_version mismatch / malformed
    FlowSpec) before any node could be blamed — gate 1 of the wave-04 three-gate import."""

    status_code = 400
    code = ErrorCode.IMPORT_VALIDATION_ERROR

    def __init__(self, reason: str) -> None:
        super().__init__(f"import validation failed: {reason}")
        self.reason = reason

    @property
    def client_message(self) -> str:
        return f"Invalid flow file: {self.reason}"


class DryRunFailed(AppError):
    """A flow-import's mocked dry-run (gate 3) raised — the offending node is always known,
    unlike a CompileError which can be structural."""

    status_code = 400
    code = ErrorCode.DRY_RUN_FAILED

    def __init__(self, node_id: str, cause: str) -> None:
        super().__init__(f"dry-run failed at node {node_id}: {cause}")
        self.node_id = node_id
        self.cause = cause

    @property
    def client_message(self) -> str:
        return f"Flow failed a dry-run check at node {self.node_id}"


class CompositeCycleError(CompileError):
    """A composite template references itself, directly or transitively through another
    template — rejected before any further recursion (wave-05)."""

    def __init__(self, chain: tuple[str, ...]) -> None:
        super().__init__(f"composite cycle detected: {' -> '.join(chain)}")
        self.chain = chain


class CompositeDepthExceeded(CompileError):
    """A non-cyclic but very deep composite-inlining chain hit the depth cap — defense-in-depth
    against pathological compile-time blowup (wave-05)."""

    def __init__(self, depth: int, max_depth: int) -> None:
        super().__init__(f"composite inlining depth {depth} exceeds cap of {max_depth}")
        self.depth = depth
        self.max_depth = max_depth


class UnknownTemplate(CompileError):
    """A `custom.<template_id>` node references a template id that doesn't exist for the
    compiling tenant (wave-05) — either a typo or a cross-tenant id (D2-5, opus-review: the
    lookup is bound to the compiling tenant, so a foreign id looks unknown, never leaks)."""

    def __init__(self, template_id: str, node_id: str) -> None:
        super().__init__(f"unknown composite template '{template_id}'", node_id)
        self.template_id = template_id


class RunFailed(Exception):
    """A run aborted while executing a step (loading, a node raised, or invalid graph state)."""

    def __init__(self, run_id: RunId, step: str, cause: str) -> None:
        super().__init__(f"run {run_id} failed at step {step}: {cause}")
        self.run_id = run_id
        self.step = step
        self.cause = cause


class EntityNotFound(AppError):
    """A requested flow / flow_ir / run does not exist for the tenant."""

    status_code = 404
    code = ErrorCode.NOT_FOUND

    def __init__(self, entity: str, entity_id: str) -> None:
        super().__init__(f"{entity} {entity_id} not found")
        self.entity = entity
        self.entity_id = entity_id

    @property
    def client_message(self) -> str:
        return f"{self.entity} not found"


class FlowNotCompiled(AppError):
    """A run was requested for a flow that has no compiled FlowIR yet."""

    status_code = 409
    code = ErrorCode.FLOW_NOT_COMPILED

    def __init__(self, flow_id: str) -> None:
        super().__init__(f"flow {flow_id} has no compiled IR; call /compile first")
        self.flow_id = flow_id

    @property
    def client_message(self) -> str:
        return "Flow has no compiled version"


class RunAlreadyClaimed(Exception):
    """Another executor owns this run (optimistic-lock version mismatch). The loser exits cleanly
    with no side-effects — this is the mutual-exclusion guard, not an error condition."""

    def __init__(self, run_id: RunId, expected_version: int) -> None:
        super().__init__(f"run {run_id} already claimed (expected version {expected_version})")
        self.run_id = run_id
        self.expected_version = expected_version


class PathResolutionError(Exception):
    """A ``PortRef.path`` could not be walked against the resolved port value — not valid JSON,
    missing key, index out of range, or a type mismatch (e.g. indexing a dict)."""

    def __init__(self, path: str, segment_index: int, reason: str) -> None:
        super().__init__(f"path '{path}' failed at segment {segment_index}: {reason}")
        self.path = path
        self.segment_index = segment_index
        self.reason = reason


class UnknownDynamicMethod(Exception):
    """A ``DynamicMethodNode``'s ``_facade``/``_method`` inputs don't resolve to a real, public
    callable on the live pylzt Client."""

    def __init__(self, facade: str, method: str) -> None:
        super().__init__(f"unknown dynamic method '{facade}.{method}'")
        self.facade = facade
        self.method = method


class NodeTimeoutError(Exception):
    """A node's `execute()` exceeded its declared `timeout_s` (wave-06)."""

    def __init__(self, node_id: str, timeout_s: int) -> None:
        super().__init__(f"node {node_id} exceeded its {timeout_s}s timeout")
        self.node_id = node_id
        self.timeout_s = timeout_s


class MaxStepsExceededError(Exception):
    """A run's step-execution budget (Settings.max_steps_per_run) was exhausted — the backstop
    against an unbounded `stop_condition: goto` loop or a runaway self-loop (D2-2, opus-review)."""

    def __init__(self, run_id: object, max_steps: int) -> None:
        super().__init__(f"run {run_id} exceeded max_steps_per_run={max_steps}")
        self.run_id = run_id
        self.max_steps = max_steps


class MathDomainError(Exception):
    """A ``MathNode`` division/modulo op was asked to divide by zero."""

    def __init__(self, op: str, a: float, b: float, reason: str) -> None:
        super().__init__(f"math op '{op}'({a}, {b}) failed: {reason}")
        self.op = op
        self.a = a
        self.b = b
        self.reason = reason


class WaitTimeoutError(Exception):
    """A ``WaitUntilNode`` never saw its condition resolve true before ``timeout_s`` elapsed."""

    def __init__(self, node_id: str, timeout_s: int) -> None:
        super().__init__(f"node {node_id} timed out waiting {timeout_s}s for its condition")
        self.node_id = node_id
        self.timeout_s = timeout_s


class NoMatchingCase(Exception):
    """A ``SwitchNode``'s resolved value matched none of its declared cases — fails loud rather
    than silently falling through to a default edge (no default edge exists by design)."""

    def __init__(self, node_id: str, value: object, cases: tuple[str, ...]) -> None:
        super().__init__(f"node {node_id}: value {value!r} matched none of cases {cases!r}")
        self.node_id = node_id
        self.value = value
        self.cases = cases


class EventDecodeError(Exception):
    """A wire-format run-event payload (wave-07) failed to decode into a known ``RunEvent``
    variant — surfaced loud rather than silently dropped, so a malformed publish is caught by
    a test, not swallowed. Not HTTP-surfaced: it fires deep inside a Redis-backed generator, not a
    request handler."""

    def __init__(self, raw_payload: str) -> None:
        super().__init__(f"malformed run event payload: {raw_payload!r}")
        self.raw_payload = raw_payload


class DynamicMethodArgMismatch(Exception):
    """A ``DynamicMethodNode``'s wired kwargs don't match the resolved method's real signature."""

    def __init__(
        self, facade: str, method: str, missing: tuple[str, ...], unexpected: tuple[str, ...]
    ) -> None:
        super().__init__(
            f"'{facade}.{method}' arg mismatch: missing={missing!r} unexpected={unexpected!r}"
        )
        self.facade = facade
        self.method = method
        self.missing = missing
        self.unexpected = unexpected
