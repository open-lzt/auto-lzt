"""FlowService / RunService — orchestrate flow authoring, compilation, and run creation."""

from __future__ import annotations

from collections.abc import Awaitable, Callable, Sequence
from datetime import UTC, datetime
from uuid import uuid4

from app.domain.account.model import TenantId
from app.domain.catalog.registry import NodeRegistry
from app.domain.flow_engine.compiler import compile_flow
from app.domain.flow_engine.errors import (
    DuplicatePresetFlow,
    EntityNotFound,
    FlowNotCompiled,
    UnknownTemplate,
)
from app.domain.flow_engine.model import (
    Flow,
    FlowId,
    FlowIR,
    FlowTemplate,
    Run,
    RunId,
    RunStatus,
    TemplateId,
)
from app.domain.flow_engine.params import JsonValue, resolve_params
from app.domain.flow_engine.repo import (
    FlowIrRepository,
    FlowRepository,
    FlowTemplateRepository,
    RunRepository,
)
from app.domain.flow_engine.spec import FlowSpec, ParamSpec
from app.domain.triggers.repo import TriggerRepository


class FlowService:
    def __init__(
        self,
        flow_repo: FlowRepository,
        ir_repo: FlowIrRepository,
        registry: NodeRegistry,
        trigger_repo: TriggerRepository,
    ) -> None:
        self._flows = flow_repo
        self._irs = ir_repo
        self._registry = registry
        self._triggers = trigger_repo

    async def create(
        self, tenant_id: TenantId, spec: FlowSpec, *, source_preset_key: str | None = None
    ) -> Flow:
        return await self._flows.create(
            tenant_id, spec.name, spec, source_preset_key=source_preset_key
        )

    async def deploy_from_preset(self, tenant_id: TenantId, key: str, spec: FlowSpec) -> Flow:
        """Create the tenant's automation for this preset, or overwrite the one already there.

        Enabling a preset twice used to leave TWO flows, each with its own schedule, both firing —
        which on the autobuy preset means paying twice. The panel shows one form per preset, so a
        second press is an edit, and this is where that reading is enforced.
        """
        existing = await self._flows.get_by_preset(tenant_id, key)
        if existing is not None:
            return await self.update(tenant_id, existing.id, spec)
        try:
            return await self.create(tenant_id, spec, source_preset_key=key)
        except DuplicatePresetFlow:
            # Lost a race with a concurrent deploy — a double-click on the enable button, or two
            # open tabs. Both requests wanted this preset's automation to match their form, so the
            # loser applies its own spec as an edit instead of returning an error nobody can act on.
            winner = await self._flows.get_by_preset(tenant_id, key)
            if winner is None:  # pragma: no cover — the row exists, we just collided with it
                raise
            return await self.update(tenant_id, winner.id, spec)

    async def list(self, tenant_id: TenantId) -> list[Flow]:
        return await self._flows.list(tenant_id)

    async def is_compiled(self, tenant_id: TenantId, flow_id: FlowId) -> bool:
        """Whether this flow has a compiled IR yet — a flow authored but never sent through
        `/compile` cannot be run (`POST /runs/create` needs an IR to enqueue against)."""
        return await self._irs.get_latest_for_flow(tenant_id, flow_id) is not None

    async def get(self, tenant_id: TenantId, flow_id: FlowId) -> Flow:
        flow = await self._flows.get(tenant_id, flow_id)
        if flow is None:
            raise EntityNotFound("flow", str(flow_id))
        return flow

    async def update(self, tenant_id: TenantId, flow_id: FlowId, spec: FlowSpec) -> Flow:
        flow = await self._flows.update_spec(tenant_id, flow_id, spec.name, spec)
        if flow is None:
            raise EntityNotFound("flow", str(flow_id))
        return flow

    async def rename(self, tenant_id: TenantId, flow_id: FlowId, name: str) -> None:
        if not await self._flows.rename(tenant_id, flow_id, name):
            raise EntityNotFound("flow", str(flow_id))

    async def delete(self, tenant_id: TenantId, flow_id: FlowId) -> None:
        """Delete the flow AND the triggers that fire it.

        There is no foreign key between the two tables, so nothing removes the triggers on our
        behalf. Left behind, a schedule row keeps its APScheduler job and the flow the operator
        deleted goes on firing — with a purchase preset, spending money against a flow that no
        longer exists in the panel. The rows go first: a delete that fails halfway must leave the
        flow unfireable rather than fireable-but-invisible.
        """
        await self._triggers.delete_by_flow(tenant_id, flow_id)
        if not await self._flows.delete(tenant_id, flow_id):
            raise EntityNotFound("flow", str(flow_id))

    async def compile(self, tenant_id: TenantId, flow_id: FlowId) -> FlowIR:
        flow = await self._flows.get(tenant_id, flow_id)
        if flow is None:
            raise EntityNotFound("flow", str(flow_id))
        ir = compile_flow(flow, self._registry.node_classes())
        await self._irs.create(tenant_id, ir)
        return ir


_DRY_RUN_PARAM = "dry_run"


def _default_to_dry_run(
    declared: Sequence[ParamSpec], params: dict[str, JsonValue] | None
) -> dict[str, JsonValue]:
    """A flow that declares ``dry_run`` starts dry unless the CALLER says otherwise, in words.

    Here rather than in each client, because "no params given" reached this service from three of
    them and only the CLI forced the flag: the bot's run button sent ``{}``, so a flow whose node
    carried ``dry_run=false`` bought for real on one tap, with nothing on screen saying so. An
    explicit ``dry_run=false`` in the request is still honoured — that is the deliberate live run.
    """
    given = dict(params or {})
    if any(p.key == _DRY_RUN_PARAM for p in declared) and _DRY_RUN_PARAM not in given:
        given[_DRY_RUN_PARAM] = True
    return given


class RunService:
    def __init__(
        self,
        ir_repo: FlowIrRepository,
        run_repo: RunRepository,
        enqueue: Callable[[RunId], Awaitable[None]],
        flow_repo: FlowRepository,
    ) -> None:
        self._irs = ir_repo
        self._runs = run_repo
        self._enqueue = enqueue
        self._flows = flow_repo

    async def prepare_run(
        self,
        tenant_id: TenantId,
        flow_id: FlowId,
        run_key: str | None,
        params: dict[str, JsonValue] | None = None,
    ) -> Run:
        """Validate params and persist a PENDING run WITHOUT enqueuing it — the shared core of the
        async ``create_run`` (which then enqueues) and synchronous invoke (which runs it inline)."""
        ir = await self._irs.get_latest_for_flow(tenant_id, flow_id)
        if ir is None:
            raise FlowNotCompiled(str(flow_id))
        flow = await self._flows.get(tenant_id, flow_id)
        if flow is None:  # pragma: no cover — an IR exists ⇒ its flow exists
            raise EntityNotFound("flow", str(flow_id))
        flow_vars = resolve_params(flow.spec.params, _default_to_dry_run(flow.spec.params, params))

        key = run_key or f"manual:{uuid4()}"
        now = datetime.now(UTC)
        await self._runs.create_if_absent(
            Run(
                id=RunId(uuid4()),
                flow_id=flow_id,
                flow_ir_id=ir.id,
                tenant_id=tenant_id,
                run_key=key,
                status=RunStatus.PENDING,
                current_node_id=None,
                version=0,
                claimed_by=None,
                claimed_at=None,
                created_at=now,
                updated_at=now,
                vars=flow_vars,
            )
        )
        stored = await self._runs.get_by_key(tenant_id, flow_id, key)
        if stored is None:  # pragma: no cover — the row exists by construction after DO NOTHING
            raise EntityNotFound("run", key)
        return stored

    async def create_run(
        self,
        tenant_id: TenantId,
        flow_id: FlowId,
        run_key: str | None,
        params: dict[str, JsonValue] | None = None,
    ) -> Run:
        stored = await self.prepare_run(tenant_id, flow_id, run_key, params)
        await self._enqueue(stored.id)
        return stored

    async def get(self, run_id: RunId) -> Run:
        run = await self._runs.get(run_id)
        if run is None:
            raise EntityNotFound("run", str(run_id))
        return run


class TemplateService:
    """Composite ("function") block CRUD (wave-05). ``create`` compiles the template as a
    standalone graph first (own entry/nodes, no template_lookup — templates don't reference
    other templates at creation-validation time beyond what the compiler's own inlining already
    supports recursively at USE time) so a broken internal graph is rejected before it's ever
    inlined anywhere."""

    def __init__(self, templates: FlowTemplateRepository, registry: NodeRegistry) -> None:
        self._templates = templates
        self._registry = registry

    async def create(self, tenant_id: TenantId, template: FlowTemplate) -> FlowTemplate:
        self._validate_standalone(template)
        return await self._templates.create(tenant_id, template)

    async def get(self, tenant_id: TenantId, template_id: TemplateId) -> FlowTemplate:
        template = await self._templates.get(tenant_id, template_id)
        if template is None:
            raise EntityNotFound("composite", str(template_id))
        return template

    async def list(self, tenant_id: TenantId) -> list[FlowTemplate]:
        return await self._templates.list(tenant_id)

    def _validate_standalone(self, template: FlowTemplate) -> None:
        """Compiles the template's own sub-graph as if it were a flow, to catch dangling edges /
        unknown node types / cycles at creation time — reuses the exact same compiler, not a
        parallel validator."""
        placeholder = Flow(
            id=FlowId(template.id),
            tenant_id=template.tenant_id,
            name=template.name,
            version=1,
            spec=FlowSpec(
                name=template.name,
                nodes=list(template.nodes),
                entry_node_id=template.entry_node_id,
            ),
            created_at=template.created_at,
        )

        def _no_nested_templates(_template_id: object) -> None:
            """A composite's own internal graph is catalog nodes only — no template-of-templates
            at creation time (a real tenant-bound lookup for nested USE, as opposed to creation
            validation, is exactly what compile_flow's real callers already provide)."""
            raise UnknownTemplate("(nested composite references are not allowed)", "")

        compile_flow(
            placeholder, self._registry.node_classes(), template_lookup=_no_nested_templates
        )
