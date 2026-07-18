"""tg.send_message — the alert node: how a flow tells its operator something happened.

The bot token is a flow input rather than a setting because a flow may legitimately alert through a
different bot than the admin bot, and because a module that ships with a token baked in should be
impossible. It is marked ``ui: secret`` so the form masks it and never echoes it back.

The URL is built here, never accepted: a node that took a URL would be an SSRF primitive with a
friendly name, and the egress fence would be the only thing left standing between a community
module and this host's network. Wanting to reach a different host means writing a different node.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from pydantic import Field

from app.core.schema import BaseSchema
from app.domain.catalog.nodes.base_request import BaseRequestNode, HttpMethod, RequestSpec
from app.domain.flow_engine.base_node import RunContext
from app.domain.flow_engine.dtos import StepResultDTO
from app.domain.flow_engine.errors import RunFailed

TELEGRAM_HOST = "api.telegram.org"
_TIMEOUT_S = 10.0
_HTTP_OK = 200


class SendMessageInput(BaseSchema):
    bot_token: str = Field(
        title="Токен бота",
        description="Токен бота, от имени которого уйдёт уведомление.",
        json_schema_extra={"ui": "secret"},
    )
    chat_id: str = Field(title="Чат", json_schema_extra={"ui": "text"})
    text: str = Field(title="Текст", min_length=1, json_schema_extra={"ui": "text"})


class SendMessageOutput(BaseSchema):
    message_id: int
    chat_id: str


def _as_str(value: object, port: str) -> str:
    if value is None or isinstance(value, bool):
        raise ValueError(f"{port} must be a string, got {value!r}")
    return str(value)


class SendMessageNode(BaseRequestNode):
    node_type = "tg.send_message"
    required_inputs = ("bot_token", "chat_id", "text")
    batchable = True

    def build_request(self, ctx: RunContext) -> RequestSpec:
        token = _as_str(ctx.resolve_input("bot_token"), "bot_token")
        return RequestSpec(
            # The token sits in the path because that is Telegram's API shape. It therefore must
            # never be logged: RequestSpec is not logged anywhere, and errors below quote the
            # response, not the URL.
            url=f"https://{TELEGRAM_HOST}/bot{token}/sendMessage",
            method=HttpMethod.POST,
            headers={"Content-Type": "application/json"},
            json_body={
                "chat_id": _as_str(ctx.resolve_input("chat_id"), "chat_id"),
                "text": _as_str(ctx.resolve_input("text"), "text"),
            },
            timeout_s=_TIMEOUT_S,
        )

    def parse_response(
        self, ctx: RunContext, status: int, body: Mapping[str, Any]
    ) -> StepResultDTO:
        if status != _HTTP_OK or not body.get("ok"):
            raise RunFailed(
                ctx.run_id,
                ctx.node.id,
                f"telegram refused the message: status={status} "
                f"description={body.get('description', '(none)')!r}",
            )
        result = body.get("result")
        message_id = result.get("message_id") if isinstance(result, dict) else None
        chat = result.get("chat") if isinstance(result, dict) else None
        chat_id = chat.get("id") if isinstance(chat, dict) else None
        return StepResultDTO(
            node_id=ctx.node.id,
            output={"message_id": int(message_id or 0), "chat_id": str(chat_id or "")},
        )
