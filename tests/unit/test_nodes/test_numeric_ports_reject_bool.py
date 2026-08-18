"""`True` — это `int` в Python, и pydantic принимает его как `1`. На числовом порту это дефект.

Файл заведён по находке ревью: типизация узлов сняла рукописные `as_price`/`_as_float`, которые
отвергали `bool` явно, а типы на их месте закрыли только ЦЕЛОЧИСЛЕННЫЕ порты. Дробные остались
голыми, и `reprice.price=True` уезжал на маркет как цена **1**.

Тест держит границу для обоих типов сразу, потому что дыра была ровно в шве между ними.
"""

from __future__ import annotations

import pytest
from pydantic import ValidationError
from pylzt.types import Currency

from app.domain.catalog.nodes.bump import BumpInput
from app.domain.catalog.nodes.math import MathInput
from app.domain.catalog.nodes.reprice import RepriceInput
from app.domain.catalog.nodes.search import SearchInput
from app.domain.catalog.nodes.take import TakeInput
from app.domain.market.categories import SearchableCategory


def _reprice(**overrides: object) -> RepriceInput:
    return RepriceInput.model_validate({"item_id": 5, "currency": Currency.RUB, **overrides})


@pytest.mark.parametrize(
    ("label", "build"),
    [
        # Дробные порты — та самая дыра, ради которой файл существует.
        ("reprice.price", lambda: _reprice(price=True)),
        ("reprice.decay_pct", lambda: _reprice(decay_pct=True, current_price=10)),
        ("reprice.current_price", lambda: _reprice(decay_pct=5, current_price=True)),
        (
            "search.max_price",
            lambda: SearchInput(category=SearchableCategory.STEAM, max_price=True),
        ),
        ("math.a", lambda: MathInput(op="add", a=True, b=1)),
        ("math.b", lambda: MathInput(op="add", a=1, b=True)),
        # Целочисленные — закрыты `NumericPort`, проверяются здесь же, чтобы шов не разъехался.
        ("bump.item_id", lambda: BumpInput(item_id=True)),
        ("take.count", lambda: TakeInput(items="[]", count=True)),
    ],
)
def test_a_boolean_is_not_a_number(label: str, build: object) -> None:
    """`item_id=True` — это лот номер один, `price=True` — цена в одну единицу валюты. Обе беды
    молчаливые: узел отработает успешно и сделает не то."""
    with pytest.raises(ValidationError):
        build()  # type: ignore[operator]


@pytest.mark.parametrize(
    ("label", "build", "expected"),
    [
        ("reprice.price целое", lambda: _reprice(price=100).price, 100),
        (
            "reprice.decay дробный",
            lambda: _reprice(decay_pct=2.5, current_price=100).decay_pct,
            2.5,
        ),
        ("math.a дробный", lambda: MathInput(op="add", a=2.5, b=1).a, 2.5),
        # `3.0` обязан проходить: `logic.math` типизирует любой результат как float, поэтому счёт,
        # вычисленный на холсте, приезжает дробным по типу и целым по значению.
        ("take.count из math", lambda: TakeInput(items="[]", count=3.0).count, 3),
    ],
)
def test_real_numbers_still_pass(label: str, build: object, expected: object) -> None:
    """Обратная сторона: запрет на `bool` не имеет права зацепить обычные числа."""
    assert build() == expected  # type: ignore[operator]
