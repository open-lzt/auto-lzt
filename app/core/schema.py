"""BaseSchema — the one Pydantic base every DTO in this project inherits from.

Single extension point for shared model config (e.g. per-field aliasing conventions) without
each DTO re-declaring it. NOT `strict=True`: tried it project-wide, but Pydantic v2 strict mode
validates FastAPI request bodies in python-mode (Starlette hands over an already-parsed dict, not
raw JSON bytes), and python-mode strict rejects `str -> UUID` — every UUID path/body param would
422 despite the wire value being the correct JSON string representation. Revisit as a per-field
`Field(strict=True)` opt-in on specific DTOs if a concrete need shows up, not a blanket default.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from typing import Annotated, Any

from pydantic import BaseModel, BeforeValidator


class Widget(StrEnum):
    """Как поле рисуется в конструкторе сценариев. Закрытый список — фронтенд умеет только это."""

    TEXT = "text"
    NUMBER = "number"
    BOOL = "bool"
    SWITCH = "switch"
    SELECT = "select"
    SECRET = "secret"
    FILTERS = "filters"


@dataclass(frozen=True, slots=True)
class XUI:
    """Подсказка интерфейсу для поля схемы.

    Раньше писалась словарём прямо в `json_schema_extra={"x-ui": {"widget": "number"}}`. Словарь
    принимает что угодно: опечатка в ключе или несуществующий виджет — валидный словарь, схема
    собирается, поле молча рисуется не тем контролом или не рисуется вовсе. Замерено на этом
    движке: `"widget": "textarea"` — виджета с таким именем нет ни в одном экране.

    Пишется так: ``Field(..., json_schema_extra=XUI(Widget.NUMBER).extra())``.
    """

    widget: Widget

    def extra(self) -> dict[str, Any]:
        """Форма, которую ждёт pydantic. Ключ `x-` — соглашение JSON Schema о своих полях."""
        return {"x-ui": {"widget": self.widget.value}}


def _reject_bool(value: object) -> object:
    """`True`/`False` — это `int` в Python, и pydantic принимает их как `1`/`0`.

    Для числового порта узла это всегда дефект, и дефект молчаливый: `item_id=True` уедет как
    **лот номер 1**, `count=True` обрежет список до одного элемента. Проверка стояла рукописной в
    трёх узлах (`take`, `bump`, `bump_thread`) и снята оттуда в пользу этого типа.
    """
    if isinstance(value, bool):
        raise ValueError("must be a number, not a boolean")
    return value


NumericPort = Annotated[int, BeforeValidator(_reject_bool)]
"""`int` для порта узла: принимает `3` и `3.0`, отвергает `True`.

`3.0` обязан проходить — `logic.math` типизирует любой результат как `float`, поэтому счёт,
вычисленный на холсте, приезжает дробным по типу и целым по значению.
"""

FractionalPort = Annotated[float, BeforeValidator(_reject_bool)]
"""То же для порта, у которого дробная часть осмысленна: цена, процент, потолок.

Заведён после того, как ревью нашло дыру: `NumericPort` закрыл целочисленные порты, а `float`-овые
остались голыми, и `reprice.price=True` уезжал как цена **1**. Снятые при типизации `as_price` и
`_as_float` отвергали `bool` явно; тип возвращает эту гарантию всем таким портам разом.
"""


class BaseSchema(BaseModel):
    pass


class EmptyInput(BaseSchema):
    """Shared input schema for a node wired with no inputs — one class instead of a per-node
    ``class XInput(BaseSchema): pass``. ``BaseNode.input_schema`` defaults to this."""
