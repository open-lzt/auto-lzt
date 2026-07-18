"""AutoReplyNode — graceful degrade for the unresolved facade gap (00-decisions.md #19).

Confirmed via direct introspection of the installed ``pylzt`` client (not assumed): the forum
facade exposes ``conversations_start(user_id)`` (no message body — only opens/returns a
conversation), ``conversations_messages_edit(conversation_id, message_id, message_body)`` (edits an
*existing* message, cannot create a new one) and ``chatbox_post_message(room_id, message)`` (the
public chat room, not a private reply). No method posts a new message into an existing private
conversation. Rather than crash the killer bump-autopilot flow over a cosmetic feature, this node
degrades: it structured-logs the gap once and returns a successful, clearly-marked no-op.
"""

from __future__ import annotations

import structlog
from pydantic import Field

from app.core.schema import BaseSchema
from app.domain.flow_engine.base_node import BaseNode, RunContext
from app.domain.flow_engine.dtos import StepResultDTO

log = structlog.get_logger()


class AutoReplyInput(BaseSchema):
    conversation_id: int = Field(
        title="Диалог",
        description="Идентификатор диалога, в который отправляется ответ.",
        json_schema_extra={"ui": "number"},
        gt=0,
    )
    message: str = Field(title="Текст ответа", json_schema_extra={"ui": "text"}, min_length=1)


class AutoReplyOutput(BaseSchema):
    skipped: bool
    reason: str


class AutoReplyNode(BaseNode):
    node_type = "forum.auto_reply"
    required_inputs = ("conversation_id", "message")

    async def execute(self, ctx: RunContext) -> StepResultDTO:
        first = await ctx.deps.guard.check_and_set(ctx.idempotency_key)
        if not first:
            return StepResultDTO(
                node_id=ctx.node.id, output={"deduplicated": True, "skipped": True}
            )

        log.warning(
            "auto_reply.skipped_facade_gap",
            node_id=ctx.node.id,
            run_id=str(ctx.run_id),
            reason="no pylzt method posts a new message into an existing conversation",
        )
        return StepResultDTO(node_id=ctx.node.id, output={"skipped": True, "reason": "facade_gap"})
