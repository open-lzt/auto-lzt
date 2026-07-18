"""A full runtime plugin, written the way an owner would write one.

It contributes all three surfaces at once — a node type, a FastAPI router, an aiogram router — and
a POST_INIT / SHUTDOWN pair that flips module-level flags so a test can prove the lifecycle ran.
The manager applies only the surfaces the current process consumes; the plugin appends all of them
unconditionally.

Public surface only: nothing here reaches into an underscore-prefixed name. If a plugin had to, the
extension point would not really be one.
"""

from __future__ import annotations

from aiogram import Router as BotRouter
from fastapi import APIRouter

from app.core.schema import BaseSchema
from app.domain.catalog.capabilities import NodeCapability
from app.domain.catalog.registry import NodeCategory, NodeRegistration, NodeType
from app.domain.flow_engine.base_node import BaseNode, RunContext
from app.domain.flow_engine.dtos import StepResultDTO
from app.plugin_runtime import (
    PluginLoadContext,
    PluginLoadedContext,
    PluginReadyContext,
)

# Observable lifecycle state — a test asserts these flip.
LIFECYCLE: dict[str, bool] = {"started": False, "stopped": False}


class PingInput(BaseSchema):
    pass


class PingOutput(BaseSchema):
    pong: str


class PingNode(BaseNode):
    node_type = "demo.runtime_ping"
    required_inputs = ()

    async def execute(self, ctx: RunContext) -> StepResultDTO:
        return StepResultDTO(node_id=ctx.node.id, output={"pong": "pong"})


_NODE = NodeRegistration(
    node_type=NodeType(
        key=PingNode.node_type,
        category=NodeCategory.LOGIC,
        input_schema=PingInput,
        output_schema=PingOutput,
        idempotent=True,
        capabilities=frozenset({NodeCapability.PURE}),
    ),
    impl=PingNode,
    # No origin — the manager stamps it from the entry-point name.
)

api_router = APIRouter()


@api_router.get("/plugins/demo-runtime/ping")
async def _http_ping() -> dict[str, str]:
    return {"pong": "from-plugin"}


bot_router = BotRouter(name="demo-runtime")


@bot_router.message()
async def _bot_ping(message: object) -> None:
    """Presence is what the wiring test checks; the body is irrelevant to it."""


def _register(ctx: PluginLoadContext) -> PluginLoadedContext:
    loaded = PluginLoadedContext()
    loaded.nodes.append(_NODE)
    loaded.api_routers.append(api_router)
    loaded.bot_routers.append(bot_router)
    return loaded


async def _start(ctx: PluginReadyContext) -> None:
    LIFECYCLE["started"] = True


async def _stop(ctx: PluginReadyContext) -> None:
    LIFECYCLE["stopped"] = True


PRE_INIT = [_register]
POST_INIT = [_start]
SHUTDOWN = [_stop]
