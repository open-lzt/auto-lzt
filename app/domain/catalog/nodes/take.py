"""TakeNode — the first N entries of a JSON id list, so a fan-out can be bounded.

Exists because ``for-each-lot`` fans out over everything it is given and no shipped node could cut
a list down first. That made "bump at most N lots per fire" inexpressible as a graph, which the
autobump preset needs and which any other fan-out will want the moment a seller has 500 lots.

Deliberately a generic list primitive rather than an autobump-shaped node: it knows nothing about
lots, bumps or schedules, so `get-my-lots -> take -> for-each-lot` and any future
`something-that-lists -> take -> loop` are the same shape.
"""

from __future__ import annotations

import json

from pydantic import Field, field_validator

from app.core.schema import BaseSchema
from app.domain.catalog.capabilities import PURE, NodeCategory
from app.domain.flow_engine.base_node import BaseNode, RunContext
from app.domain.flow_engine.dtos import StepResultDTO


class TakeInput(BaseSchema):
    items: str = Field(
        title="Список",
        description="JSON-массив — обычно выход get_my_lots.",
        json_schema_extra={"x-ui": {"widget": "text"}},
    )
    # `int` в мягком режиме принимает и `3.0`, и это НЕСУЩЕЕ свойство, а не мелочь: `logic.math`
    # типизирует любой результат как `float`, поэтому вычисленный на холсте счёт (автозакупка
    # считает `budget // max_price`) приезжает как `3.0`. Отказ от него ломал бы граф ровно там,
    # где счёт выведен, а не вписан руками — то есть незаметно. `3.5` при этом отвергается,
    # и это тоже намеренно: дробный счёт означает ошибку в формуле.
    # НЕ ставить сюда `strict=True`.
    count: int = Field(
        ge=0,
        title="Сколько взять",
        # Ноль — законное значение, а не ошибка: он означает «бюджета не хватает и на один лот по
        # этому потолку». Прогон при этом зелёный и пустой; расписанная автозакупка, падающая на
        # каждом запуске до падения цены, — это тревога, на которую никто не может отреагировать.
        description="Сколько первых элементов оставить. Ноль — не брать ничего.",
        json_schema_extra={"x-ui": {"widget": "number"}},
    )

    @field_validator("count", mode="before")
    @classmethod
    def _reject_bool(cls, value: object) -> object:
        """`True` — это `int` в Python, и pydantic его принимает как `1`.

        Здесь это недопустимо: `items[:True]` молча вернул бы ОДИН элемент вместо отказа на явно
        неверном входе. Проверка стояла в снятом `_as_count` и восстановлена здесь — схема мягче
        рукописной проверки ровно в этом месте.
        """
        if isinstance(value, bool):
            raise ValueError("count must be a number, not a boolean")
        return value

    @field_validator("items")
    @classmethod
    def _must_be_json_list(cls, value: str) -> str:
        if not isinstance(json.loads(value), list):
            raise ValueError("items must be a JSON array")
        return value


class TakeOutput(BaseSchema):
    items: str  # JSON-encoded, same element type as the input — feeds a for-each node
    count: int
    # True when the input was longer than the cap. The preset does not branch on it, but a flow that
    # wants to notify "N lots were skipped this fire" has no other way to know it happened.
    truncated: bool


class TakeNode(BaseNode):
    node_type = "logic.take"
    category = NodeCategory.LOGIC
    idempotent = True
    capabilities = PURE
    input_schema = TakeInput
    output_schema = TakeOutput
    required_inputs = ("items", "count")

    async def execute(self, ctx: RunContext[TakeInput]) -> StepResultDTO:
        # Форма `items` (строка, разбираемая в JSON-массив) и границы `count` проверены схемой при
        # сборке контекста — здесь остаётся сам разбор, потому что дальше нужен список, а по
        # проводу едет строка.
        items = json.loads(ctx.inputs.items)
        kept = items[: ctx.inputs.count]
        return StepResultDTO(
            node_id=ctx.node.id,
            output={
                "items": json.dumps(kept),
                "count": len(kept),
                "truncated": len(items) > len(kept),
            },
        )
