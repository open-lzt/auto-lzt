"""Request nodes and the transport under them (T2.2 / T2.3).

The claim being tested is structural, and it is narrower than "a node cannot reach the network":
a node reaches the network through ``deps.http``, ``deps.http`` cannot exist without a policy, and
``BaseRequestNode.execute`` is final, so there is no seam a node author opts out of by accident.
A plugin that writes ``import httpx`` is not covered by any test here and cannot be — it is code in
this process. What the fence holds is a *module*: data, whose reach is its nodes' reach. So the
interesting tests are the ones where a node TRIES to get around it — not the happy path.

respx mocks at the httpx transport, i.e. the process boundary (D-14). Everything above it — the
policy, the retry loop, the interpreter — is real.
"""

from __future__ import annotations

import inspect
from datetime import UTC, datetime
from ipaddress import ip_address
from types import SimpleNamespace
from typing import Any
from uuid import uuid4

import httpcore
import httpx
import pytest
import respx

from app.domain.account.model import TenantId
from app.domain.catalog.nodes.base_request import BaseRequestNode
from app.domain.catalog.nodes.telegram.send_message import TELEGRAM_HOST, SendMessageNode
from app.domain.egress.policy import EgressBlockReason, EgressPolicy, ResolvedTarget
from app.domain.egress.transport import (
    HttpMethod,
    PolicedHttpTransport,
    RequestSpec,
    build_transport,
)
from app.domain.flow_engine import env_input
from app.domain.flow_engine.compiler import compile_flow
from app.domain.flow_engine.errors import RunFailed
from app.domain.flow_engine.model import Flow, FlowId, RunStatus
from app.domain.flow_engine.spec import FlowSpec, InputSpec, NodeSpec
from app.worker.runtime import execute_run
from tests.fixtures.flow_fakes import (
    FakeFlowIrStore,
    FakeGuard,
    FakeMarket,
    FakeRunRepo,
    FakeRunStepRepo,
    build_node_deps,
    build_run,
    node_classes,
)

ALLOWED = frozenset({TELEGRAM_HOST})
TOKEN = "123456:FAKE-TOKEN"
CHAT = "-100500"
TELEGRAM_IP = "149.154.167.220"


def _ok_payload(message_id: int = 42) -> dict[str, Any]:
    return {"ok": True, "result": {"message_id": message_id, "chat": {"id": CHAT}}}


def _alert_flow() -> Flow:
    spec = FlowSpec(
        name="alert",
        nodes=[
            NodeSpec(
                id="n1",
                type="tg.send_message",
                inputs={
                    "bot_token": InputSpec(literal=TOKEN),
                    "chat_id": InputSpec(literal=CHAT),
                    "text": InputSpec(literal="лот продан"),
                },
            )
        ],
        entry_node_id="n1",
    )
    return Flow(
        id=FlowId(uuid4()),
        tenant_id=TenantId(uuid4()),
        name=spec.name,
        version=1,
        spec=spec,
        created_at=datetime.now(UTC),
    )


class _StubTransport:
    """A transport that answers from a script, so a node's retry behaviour can be driven without
    pretending to be a network. It still goes through BaseRequestNode's real execute()."""

    def __init__(self, *responses: tuple[int, dict[str, Any]] | Exception) -> None:
        self._responses = list(responses)
        self.calls: list[RequestSpec] = []

    async def request(self, spec: RequestSpec) -> tuple[int, dict[str, Any]]:
        self.calls.append(spec)
        answer = self._responses[min(len(self.calls) - 1, len(self._responses) - 1)]
        if isinstance(answer, Exception):
            raise answer
        return answer


async def _run_alert(
    http: Any, flow: Flow | None = None
) -> tuple[RunFailed | None, FakeRunStepRepo, Any]:
    """Run the alert flow, returning the failure rather than raising it.

    ``execute_run`` propagates RunFailed instead of returning RunStatus.FAILED — the worker's job
    wrapper is what turns it into a status. These tests are about what the node did, so the failure
    is a value here; ``None`` means the run completed.
    """
    flow = flow if flow is not None else _alert_flow()
    ir = compile_flow(flow, node_classes())
    runs, steps, flows = FakeRunRepo(), FakeRunStepRepo(), FakeFlowIrStore(ir)
    run = build_run(ir)
    await runs.create_if_absent(run)
    try:
        status = await execute_run(
            run.id,
            runs=runs,
            steps=steps,
            flows=flows,
            registry=node_classes(),
            node_deps=build_node_deps(FakeMarket(), FakeGuard(), http=http),
            worker_id="w1",
        )
    except RunFailed as exc:
        return exc, steps, run
    assert status is RunStatus.COMPLETED
    return None, steps, run


def test_execute_is_final_so_no_node_can_skip_the_policy() -> None:
    """The structural claim, asserted structurally. Egress policy, retry and timeout live in
    BaseRequestNode.execute; a subclass overriding it would silently opt out of all three, and
    nothing else in the codebase would notice."""
    for cls in _request_node_subclasses():
        assert "execute" not in vars(cls), (
            f"{cls.__name__} overrides execute() — that bypasses the egress fence, the retry loop "
            f"and the timeout. Implement build_request/parse_response instead."
        )


def _request_node_subclasses() -> list[type[BaseRequestNode]]:
    from app.domain.catalog.registry import BUILTIN_REGISTRATIONS

    return [
        reg.impl
        for reg in BUILTIN_REGISTRATIONS
        if inspect.isclass(reg.impl) and issubclass(reg.impl, BaseRequestNode)
    ]


def test_a_transport_cannot_be_built_without_a_policy() -> None:
    """The other half of the claim: there is no no-policy constructor to reach for."""
    signature = inspect.signature(PolicedHttpTransport.__init__)
    policy_param = signature.parameters["policy"]
    assert policy_param.default is inspect.Parameter.empty


def test_httpcore_keys_connection_reuse_on_the_url_origin_and_ignores_sni() -> None:
    """Pins the dependency behaviour build_transport works around, so the workaround is not
    cargo-cult once httpcore changes.

    We pin the IP into the URL (R-3), so the origin httpcore keys on names an ADDRESS. If reuse
    ignores sni_hostname — as it does here — then two allow-listed hosts on one IP share a
    connection, and the second host's certificate is never verified. If this test ever fails,
    httpcore started keying on the SNI and build_transport can have its keep-alive back.
    """
    same_ip_different_host = httpcore.Origin(b"https", b"93.184.216.34", 443)
    connection = httpcore.AsyncHTTPConnection(same_ip_different_host)

    assert connection.can_handle_request(same_ip_different_host), (
        "reuse is decided by origin alone; the SNI a connection was built with is not consulted"
    )


def test_the_production_transport_never_reuses_a_pooled_connection() -> None:
    """The consequence of the test above: with the pool unable to tell two hosts on one IP apart,
    the only safe pool is no pool. A regression here is silent — requests keep working, they just
    ride a TLS session that proved a different name."""
    transport = build_transport(EgressPolicy(frozenset()))

    # Asserted on the pool httpcore actually consults, not on the Limits object we passed in: the
    # bug would be a client built without the limit, and a constructor argument that never reached
    # the pool would read as configured while pooling exactly as before. Default here is 20.
    assert transport._client._transport._pool._max_keepalive_connections == 0  # type: ignore[attr-defined]


async def test_a_node_aimed_at_redis_is_refused_and_the_run_fails() -> None:
    """The scenario the whole fence exists for, driven through the real interpreter: a module names
    an internal URL, and the run fails loudly instead of the worker's Redis being reachable."""
    policy = EgressPolicy(frozenset())  # default deployment: allow nothing
    transport = PolicedHttpTransport(policy, httpx.AsyncClient())

    failure, _, _ = await _run_alert(transport)

    assert failure is not None
    assert EgressBlockReason.HOST_NOT_ALLOWED.value in str(failure)


async def test_a_429_is_retried_and_then_succeeds() -> None:
    http = _StubTransport(
        (429, {"ok": False, "parameters": {"retry_after": 0}}), (200, _ok_payload())
    )
    failure, _, _ = await _run_alert(http)

    assert failure is None
    assert len(http.calls) == 2


async def test_a_5xx_is_retried() -> None:
    http = _StubTransport((503, {"ok": False}), (200, _ok_payload()))
    failure, _, _ = await _run_alert(http)

    assert failure is None
    assert len(http.calls) == 2


async def test_a_400_is_not_retried() -> None:
    """A 4xx says the request itself is wrong. Repeating it cannot fix it and only burns the rate
    limit — the thing that causes the next 429."""
    http = _StubTransport((400, {"ok": False, "description": "chat not found"}))
    failure, _, _ = await _run_alert(http)

    assert failure is not None
    assert len(http.calls) == 1


async def test_a_transport_failure_is_retried_to_the_cap_then_fails() -> None:
    http = _StubTransport(httpx.ConnectError("boom"))
    failure, _, _ = await _run_alert(http)

    assert failure is not None
    assert len(http.calls) == SendMessageNode.max_attempts


async def test_an_egress_block_is_never_retried() -> None:
    """The fence's verdict will not change on a second try, and retrying would turn one refused
    request into a scan of the internal network."""
    from app.domain.egress.policy import EgressBlocked

    http = _StubTransport(EgressBlocked("evil.internal", "127.0.0.1", EgressBlockReason.LOOPBACK))
    failure, _, _ = await _run_alert(http)

    assert failure is not None
    assert len(http.calls) == 1


async def test_a_hostile_retry_after_cannot_park_the_worker(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """An endpoint asking us to wait an hour is either broken or hostile; either way a worker slot
    is not theirs to hold."""
    slept: list[float] = []

    async def _record(seconds: float) -> None:
        slept.append(seconds)

    monkeypatch.setattr("app.domain.catalog.nodes.base_request.asyncio.sleep", _record)
    http = _StubTransport(
        (429, {"ok": False, "parameters": {"retry_after": 86400}}), (200, _ok_payload())
    )
    failure, _, _ = await _run_alert(http)

    assert failure is None
    assert slept == [60.0]


class _PinnedPolicy(EgressPolicy):
    """The real policy with the resolver replaced — DNS is the process boundary, everything else
    (scheme check, allow-list, address classification, the pinning contract) stays real."""

    async def resolve_and_check(self, url: str) -> ResolvedTarget:
        host = self.check_host(url)
        return ResolvedTarget(url=url, host=host, ip=ip_address(TELEGRAM_IP))


@respx.mock
async def test_the_alert_lands_through_the_real_transport() -> None:
    """Note the URL respx has to mock: the request goes to the ADDRESS, not the name.

    That is not an artefact of the test — it is the anti-rebinding rule (R-3) visible from the
    outside. The transport connects to the address the policy cleared and carries the hostname in
    the Host header and in TLS, so the name is never resolved a second time.
    """
    route = respx.post(f"https://{TELEGRAM_IP}/bot{TOKEN}/sendMessage").mock(
        return_value=httpx.Response(200, json=_ok_payload(7))
    )
    transport = PolicedHttpTransport(_PinnedPolicy(ALLOWED), httpx.AsyncClient())

    failure, steps, run = await _run_alert(transport)

    assert failure is None
    assert route.called
    assert route.calls.last.request.headers["Host"] == TELEGRAM_HOST
    step = await steps.get_step(run.id, "n1", None)
    assert step is not None and step.result is not None
    assert step.result.output["message_id"] == 7


@respx.mock
async def test_a_redirect_is_refused_rather_than_followed() -> None:
    """R-4: a Location is a fresh URL chosen by the same untrusted source. Following it would
    re-open everything the fence just closed — the classic "allowed host redirects to 169.254".
    """
    respx.post(f"https://{TELEGRAM_IP}/bot{TOKEN}/sendMessage").mock(
        return_value=httpx.Response(302, headers={"Location": "https://169.254.169.254/latest"})
    )
    transport = PolicedHttpTransport(_PinnedPolicy(ALLOWED), httpx.AsyncClient())

    failure, _, _ = await _run_alert(transport)

    assert failure is not None
    assert EgressBlockReason.REDIRECT_ATTEMPTED.value in str(failure)


class _CountingStream(httpx.AsyncByteStream):
    """A body that reports how much of it was actually pulled.

    Counting the chunks is the only way to tell a bounded READ from a bounded REPORT: both return
    the same "response_too_large" to the caller, and only one of them declined to take the bytes.
    """

    def __init__(self, chunk: bytes, count: int) -> None:
        self._chunk = chunk
        self._count = count
        self.pulled = 0

    async def __aiter__(self) -> Any:
        for _ in range(self._count):
            self.pulled += 1
            yield self._chunk


def _spec(url: str) -> RequestSpec:
    return RequestSpec(url=url, method=HttpMethod.POST, headers={}, json_body=None, timeout_s=5.0)


@respx.mock
async def test_an_endless_body_is_dropped_mid_download_not_after_it() -> None:
    """The fence's premise is that the far side may be hostile, and an allow-listed host is not a
    healthy one. `len(response.content) > MAX` reads the whole body and THEN calls it too large —
    which reports a bound while enforcing none, and hands a worker's memory to whoever answers.

    128 MB here if the cap is a report rather than a read.
    """
    stream = _CountingStream(b"x" * 64 * 1024, 2_000)
    respx.post(f"https://{TELEGRAM_IP}/big").mock(return_value=httpx.Response(200, stream=stream))
    transport = PolicedHttpTransport(_PinnedPolicy(ALLOWED), httpx.AsyncClient())

    status, body = await transport.request(_spec(f"https://{TELEGRAM_HOST}/big"))

    assert status == 200
    assert body["error"] == "response_too_large"
    assert stream.pulled < 32, f"read {stream.pulled} chunks of an endless body before stopping"


@respx.mock
async def test_a_declared_oversize_body_is_not_read_at_all() -> None:
    """When the endpoint says how big it is, believe it and take nothing. The streamed cap below
    still has to exist for the header that is absent, chunked, or lying."""
    stream = _CountingStream(b"x" * 1024, 10)
    respx.post(f"https://{TELEGRAM_IP}/declared").mock(
        return_value=httpx.Response(
            200, headers={"Content-Length": str(64 * 1024 * 1024)}, stream=stream
        )
    )
    transport = PolicedHttpTransport(_PinnedPolicy(ALLOWED), httpx.AsyncClient())

    _, body = await transport.request(_spec(f"https://{TELEGRAM_HOST}/declared"))

    assert body["error"] == "response_too_large"
    assert body["bytes"] == 64 * 1024 * 1024
    assert stream.pulled == 0, "a body we were told was oversized was read anyway"


@respx.mock
async def test_a_body_under_the_cap_still_parses() -> None:
    """The half that a too-eager cap would break: streaming must not cost us ordinary responses."""
    respx.post(f"https://{TELEGRAM_IP}/fine").mock(
        return_value=httpx.Response(200, json={"ok": True, "result": {"message_id": 5}})
    )
    transport = PolicedHttpTransport(_PinnedPolicy(ALLOWED), httpx.AsyncClient())

    status, body = await transport.request(_spec(f"https://{TELEGRAM_HOST}/fine"))

    assert status == 200
    assert body["result"]["message_id"] == 5


@respx.mock
async def test_a_non_json_body_is_reported_not_raised() -> None:
    """An endpoint returning HTML is the node's problem to interpret, not a reason to crash a run
    that may be holding money."""
    respx.post(f"https://{TELEGRAM_IP}/html").mock(
        return_value=httpx.Response(200, text="<html>502 Bad Gateway</html>")
    )
    transport = PolicedHttpTransport(_PinnedPolicy(ALLOWED), httpx.AsyncClient())

    _, body = await transport.request(_spec(f"https://{TELEGRAM_HOST}/html"))

    assert body["error"] == "not_json"
    assert "502 Bad Gateway" in body["text"]


async def test_the_node_builds_its_url_and_never_accepts_one() -> None:
    """A node taking a URL would be an SSRF primitive with a friendly name, leaving the fence as
    the only thing between a community module and this host's network."""
    schema = SendMessageNode  # the input model is what a module can fill in
    from app.domain.catalog.nodes.telegram.send_message import SendMessageInput

    assert "url" not in SendMessageInput.model_fields
    assert "host" not in SendMessageInput.model_fields
    assert schema.node_type == "tg.send_message"


async def test_the_bot_token_never_reaches_the_error_text() -> None:
    """The token sits in Telegram's URL path, so an error that quoted the URL would put a
    credential in the run trace — which /trace hands to anyone with the API key."""
    http = _StubTransport((400, {"ok": False, "description": "chat not found"}))
    failure, _, _ = await _run_alert(http)

    assert failure is not None
    # RunFailed's text is what the worker logs and what /trace can surface, so this is the string
    # that must not carry a credential.
    assert TOKEN not in str(failure)
    assert "chat not found" in str(failure)  # the useful part still gets through


def _alert_flow_env() -> Flow:
    """The alert flow, but the bot token arrives via {"env": ...} — resolved at access, never
    compiled into the IR."""
    spec = FlowSpec(
        name="alert",
        nodes=[
            NodeSpec(
                id="n1",
                type="tg.send_message",
                inputs={
                    "bot_token": InputSpec(env="FLOW_BOT_TOKEN"),
                    "chat_id": InputSpec(literal=CHAT),
                    "text": InputSpec(literal="лот продан"),
                },
            )
        ],
        entry_node_id="n1",
    )
    return Flow(
        id=FlowId(uuid4()),
        tenant_id=TenantId(uuid4()),
        name=spec.name,
        version=1,
        spec=spec,
        created_at=datetime.now(UTC),
    )


async def test_an_env_sourced_token_reaches_no_trace_no_log_no_error_text(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The env form exists so a credential lives by name, not by value. Prove the value appears in
    none of the surfaces a leak would ride out on: the IR, the step result, and the failure text."""
    monkeypatch.setattr(env_input, "get_settings", lambda: SimpleNamespace(flow_env_prefix="FLOW_"))
    monkeypatch.setenv("FLOW_BOT_TOKEN", TOKEN)

    flow = _alert_flow_env()
    ir = compile_flow(flow, node_classes())
    assert TOKEN not in repr(ir)  # the value is not in the compiled IR — only the name is

    http = _StubTransport((400, {"ok": False, "description": "chat not found"}))
    failure, steps, _ = await _run_alert(http, flow)

    assert failure is not None
    assert TOKEN not in str(failure)
    assert TOKEN not in repr(list(steps._steps.values()))  # nor in any persisted step surface


async def test_a_telegram_level_failure_is_a_run_failure_not_a_silent_success() -> None:
    """``200 {"ok": false}`` is Telegram refusing. A node that returned success there would report
    an alert that never arrived."""
    http = _StubTransport((200, {"ok": False, "description": "bot was blocked by the user"}))
    failure, _, _ = await _run_alert(http)
    assert failure is not None


def test_send_message_declares_egress_and_is_not_money() -> None:
    """Sending the same alert twice is noise, not a loss — so no guard, and the capability says
    what it actually does: reach the network."""
    from app.domain.catalog.capabilities import NodeCapability
    from tests.fixtures.flow_fakes import builtin_registry

    caps = builtin_registry().get("tg.send_message").capabilities
    assert caps == frozenset({NodeCapability.NETWORK_EGRESS})


async def test_the_node_sets_a_timeout_on_its_own_request() -> None:
    """A request with no timeout holds a worker slot until the OS gives up. Asserted on the spec
    the node actually builds, not on a RequestSpec this test constructs — the latter would only
    prove the dataclass stores what it is handed."""
    http = _StubTransport((200, _ok_payload()))
    failure, _, _ = await _run_alert(http)

    assert failure is None
    spec = http.calls[0]
    assert spec.timeout_s > 0
    assert spec.method is HttpMethod.POST
    assert spec.url.startswith(f"https://{TELEGRAM_HOST}/")
