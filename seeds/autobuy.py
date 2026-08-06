"""The autobuy sniper, built once and instantiated per market category.

**Why code and not 21 JSON files.** The per-category templates differ in exactly three things — the
name, the default category and the default filters — across a twelve-node graph. Shipping twenty
copies of that graph would mean every fix to the money path had to land twenty times, and the
nineteenth copy that missed it would look identical in a directory listing. The graph lives here
once; the categories are data.

The graph, and why each node is there:

    cap    budget // max_price      how many lots the budget covers, floored
      -> search   filtered search, price-capped server-side
      -> take     cut the result to `cap` — THIS is the spend ceiling
      -> loop     fan out over what survived
           body -> buy      fast_buy, dry_run by default
                -> bought   purchased?
                -> tg_lot   is Telegram even configured?
                -> lot_text + notify
           after -> tg_run  is Telegram even configured?
                 -> run_text + summary

Two things in there are load-bearing and were both learned the hard way:

``cap`` is the budget gate, and it must be integer division. A per-iteration ``stop_condition`` on
``loop`` does not work — the runtime evaluates a stop condition before the fan-out, against the
loop's own output, so a budget of 1000 with twenty lots at 300 bought all twenty.

The Telegram nodes sit behind ``bot_token != ""``. Without that gate a run with no Telegram
configured fails on its LAST node, after the purchases, and reports itself failed — observed against
a live stand: ``egress blocked: host=api.telegram.org``.
"""

from __future__ import annotations

from app.domain.flow_engine.spec import (
    FlowMaturity,
    FlowSpec,
    InputSpec,
    NodeSpec,
    ParamControl,
    ParamOption,
    ParamSpec,
)
from app.domain.market.categories import SearchableCategory, label_for

# Safe for every category: both names are in the 18 filters all 21 share, and both narrow toward
# what a sniper usually wants — a fresh auto-registered account that is not on hold.
_DEFAULT_FILTERS = '{"origin": "autoreg", "nsb": true}'

_CURRENCIES = (("rub", "Рубли"), ("usd", "Доллары"), ("eur", "Евро"))


def _params(category: SearchableCategory) -> list[ParamSpec]:
    return [
        ParamSpec(
            key="max_price",
            label="Цена за лот, до",
            control=ParamControl.NUMBER,
            default=300,
            minimum=1,
            group="Деньги",
            description=(
                "Потолок цены одного лота. Фильтрует маркет при поиске и проверяется ещё раз "
                "в момент покупки — продавец может переставить цену между этими двумя моментами."
            ),
        ),
        ParamSpec(
            key="budget",
            label="Бюджет на прогон",
            control=ParamControl.NUMBER,
            default=1000,
            minimum=1,
            group="Деньги",
            description=(
                "Сколько всего можно потратить за один запуск. Число лотов = бюджет / цена "
                "за лот, с округлением вниз. Бюджет меньше одного лота — прогон завершится, "
                "ничего не купив."
            ),
        ),
        ParamSpec(
            key="currency",
            label="Валюта",
            control=ParamControl.SELECT,
            default="rub",
            group="Деньги",
            options=[ParamOption(value=value, label=text) for value, text in _CURRENCIES],
        ),
        ParamSpec(
            key="dry_run",
            label="Холостой прогон",
            control=ParamControl.TOGGLE,
            default=True,
            group="Деньги",
            description=(
                "Включено — флоу проходит целиком и показывает, что купил бы, но деньги не уходят. "
                "Выключать после того, как посмотрели прогон."
            ),
        ),
        ParamSpec(
            key="category",
            label="Категория",
            control=ParamControl.CATEGORY,
            default=category.value,
            group="Поиск",
        ),
        ParamSpec(
            key="filters",
            label="Фильтры",
            control=ParamControl.TEXTAREA,
            default=_DEFAULT_FILTERS,
            required=False,
            group="Поиск",
            description="JSON-объект фильтров выбранной категории. Форма на канвасе их раскрывает.",
        ),
        ParamSpec(
            key="bot_token",
            label="Токен Telegram-бота",
            control=ParamControl.TEXT,
            default="",
            required=False,
            group="Уведомления",
            description="Пусто — уведомления просто не отправляются, прогон при этом успешен.",
        ),
        ParamSpec(
            key="chat_id",
            label="Чат для уведомлений",
            control=ParamControl.TEXT,
            default="",
            required=False,
            group="Уведомления",
        ),
    ]


def _nodes() -> list[NodeSpec]:
    tg_configured = {
        "left": InputSpec(literal="{{vars.bot_token}}"),
        "op": InputSpec(literal="ne"),
        "right": InputSpec(literal=""),
    }
    return [
        NodeSpec(
            id="cap",
            type="logic.math",
            inputs={
                # idiv, not div: `logic.take` refuses a fractional count, so `1000 / 300` failed
                # every run — while `900 / 300` passed, hiding the defect behind round numbers.
                "op": InputSpec(literal="idiv"),
                "a": InputSpec(literal="{{vars.budget}}"),
                "b": InputSpec(literal="{{vars.max_price}}"),
            },
            edges={"next": "search"},
        ),
        NodeSpec(
            id="search",
            type="market.search",
            inputs={
                "max_price": InputSpec(literal="{{vars.max_price}}"),
                "category": InputSpec(literal="{{vars.category}}"),
                "filters": InputSpec(literal="{{vars.filters}}"),
            },
            edges={"next": "take"},
        ),
        NodeSpec(
            id="take",
            type="logic.take",
            inputs={
                "items": InputSpec(ref="search.item_ids"),
                "count": InputSpec(ref="cap.result"),
            },
            edges={"next": "loop"},
        ),
        NodeSpec(
            id="loop",
            type="logic.for_each_lot",
            inputs={"item_ids": InputSpec(ref="take.items")},
            # "after", not "next": the runtime continues past a fan-out on the `after` edge.
            edges={"body": "buy", "after": "tg_run"},
        ),
        NodeSpec(
            id="buy",
            type="market.fast_buy",
            inputs={
                # `loop.item_id` is produced by the runtime per iteration; it is not in
                # for_each_lot's declared output_schema, and referencing it is the shipped pattern.
                "item_id": InputSpec(ref="loop.item_id"),
                "max_price": InputSpec(literal="{{vars.max_price}}"),
                "max_price_currency": InputSpec(literal="{{vars.currency}}"),
                "dry_run": InputSpec(literal="{{vars.dry_run}}"),
            },
            edges={"next": "bought"},
        ),
        NodeSpec(
            id="bought",
            type="logic.condition",
            inputs={
                "left": InputSpec(ref="buy.purchased"),
                "op": InputSpec(literal="eq"),
                "right": InputSpec(literal=True),
            },
            edges={"true": "tg_lot"},
        ),
        NodeSpec(
            id="tg_lot",
            type="logic.condition",
            inputs=dict(tg_configured),
            edges={"true": "lot_text"},
        ),
        NodeSpec(
            id="lot_text",
            type="logic.string_concat",
            inputs={
                "a": InputSpec(literal="Куплен лот "),
                "b": InputSpec(ref="buy.item_id"),
                "c": InputSpec(ref="buy.price"),
            },
            edges={"next": "notify"},
        ),
        NodeSpec(
            id="notify",
            type="tg.send_message",
            inputs={
                "bot_token": InputSpec(literal="{{vars.bot_token}}"),
                "chat_id": InputSpec(literal="{{vars.chat_id}}"),
                "text": InputSpec(ref="lot_text.result"),
            },
        ),
        NodeSpec(
            id="tg_run",
            type="logic.condition",
            inputs=dict(tg_configured),
            edges={"true": "run_text"},
        ),
        NodeSpec(
            id="run_text",
            type="logic.string_concat",
            inputs={
                "a": InputSpec(literal="Автобай: рассмотрено лотов "),
                "b": InputSpec(ref="take.count"),
            },
            edges={"next": "summary"},
        ),
        NodeSpec(
            id="summary",
            type="tg.send_message",
            inputs={
                "bot_token": InputSpec(literal="{{vars.bot_token}}"),
                "chat_id": InputSpec(literal="{{vars.chat_id}}"),
                # `take`/`buy` only. A summary reading `search.count` raises "not yet produced"
                # after a resume — AFTER the money moved.
                "text": InputSpec(ref="run_text.result"),
            },
        ),
    ]


def autobuy_spec(category: SearchableCategory) -> FlowSpec:
    """One sniper, aimed at one category.

    ``testnet`` is on and ``maturity`` is experimental: the filter surface is reflected out of
    pylzt's signatures, and a signature proves a filter's NAME and TYPE — not that the marketplace
    narrows anything by it. Until that is checked against the live market, the first run of a
    shipped template should not be able to spend money.
    """
    return FlowSpec(
        name=f"Автобай {label_for(category)} — снайпер",
        entry_node_id="cap",
        params=_params(category),
        nodes=_nodes(),
        maturity=FlowMaturity.EXPERIMENTAL,
        testnet=True,
    )


def autobuy_specs() -> list[FlowSpec]:
    """One sniper per searchable category — 21 flows from one graph."""
    return [autobuy_spec(category) for category in SearchableCategory]
