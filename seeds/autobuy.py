"""The autobuy sniper, built once and instantiated per market category.

**Why code and not 21 JSON files.** The per-category templates differ in exactly three things — the
name, the default category and the default filters — across a twelve-node graph. Shipping twenty
copies of that graph would mean every fix to the money path had to land twenty times, and the
nineteenth copy that missed it would look identical in a directory listing. The graph lives here
once; the categories are data.

The graph, and why each node is there:

    search   filtered search, price-capped server-side
      -> take     cut to `max_lots` — a CANDIDATE cap, not the spend ceiling
      -> loop     fan out over what survived
           body -> buy      fast_buy, dry_run by default — THIS is the spend ceiling
                -> bought   purchased?
                -> tg_lot   is Telegram even configured?
                -> lot_text + notify
           after -> tg_run  is Telegram even configured?
                 -> run_text + summary

Two things in there are load-bearing and were both learned the hard way:

**The budget gate counts money, not candidates, and that took two attempts.** It began as
``cap = budget // max_price`` feeding ``take``, which limited how many lots were LOOKED AT: a lot
refused with 403 burned a slot while moving nothing, so three contested lots in a row bought
nothing with the budget untouched and lot #4 in the same page. Refusal is the normal outcome on
cheap lots — measured 3 of 5 against prod. The gate now lives on ``buy``, which sums what the run
actually paid (the `purchases` ledger, keyed by run) and refuses the next lot that will not fit.

A ``stop_condition`` on ``loop`` still does not work — the runtime evaluates it before the fan-out
against the loop's own output, so a budget of 1000 with twenty lots at 300 bought all twenty. On
``buy``, inside the body, it is evaluated right after that step commits; the fan-out is sequential
and checks its abort flag per item, so the first refusal ends the loop.

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
    StopConditionSpec,
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
                "Сколько всего можно потратить за один запуск. Считается ПОТРАЧЕННОЕ: лот, по "
                "которому маркет отказал, денег не стоит и бюджет не расходует. Как только "
                "следующий лот в остаток не влезает, прогон останавливается."
            ),
        ),
        ParamSpec(
            key="max_lots",
            label="Сколько лотов смотреть",
            control=ParamControl.NUMBER,
            default=40,
            minimum=1,
            group="Деньги",
            description=(
                "Верхняя граница числа кандидатов из выдачи, а не числа покупок — тратой "
                "управляет бюджет. Одна страница поиска даёт 40 лотов; больше смотреть незачем."
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
                # A CANDIDATE cap, not the spend ceiling. It used to be `budget // max_price`, and
                # that conflated "how many I can afford" with "how many I will look at": a lot
                # refused with 403 burned a slot while moving no money, so three contested lots in
                # a row bought nothing with the budget untouched and lot #4 sitting in the same
                # page. Refusal is the NORMAL outcome on cheap lots — measured 3 of 5 live. The
                # money gate now lives on `buy`, which counts what was SPENT.
                "count": InputSpec(literal="{{vars.max_lots}}"),
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
                "run_budget": InputSpec(literal="{{vars.budget}}"),
                "dry_run": InputSpec(literal="{{vars.dry_run}}"),
            },
            # The spend ceiling, and the reason it works here where it did not on the loop: a
            # stop_condition on `loop` is evaluated BEFORE the fan-out against the loop's own
            # output, so a budget of 1000 with twenty lots at 300 bought all twenty. On a node
            # INSIDE the body it is evaluated right after that step commits, and the fan-out is
            # sequential with an abort flag checked per item — so the first refusal ends the loop.
            stop_condition=StopConditionSpec(
                output_key="budget_exhausted", equals=True, action="abort"
            ),
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

    ``testnet`` is on and ``maturity`` is experimental.

    The PLUMBING is verified against the live marketplace (2026-08-06, read-only searches): a
    control of two identical searches churned zero lots, so set differences mean something; then
    ``pmin=1000`` returned forty lots all at or above 1000 against a baseline where every lot cost
    1; ``title=cs`` returned forty of forty titles containing "cs"; ``origin=autoreg`` and
    ``origin=brute`` returned disjoint pages; and the two combined narrowed forty lots to one.
    Filters reach the marketplace and the marketplace applies them.

    What is still unverified is each of the other ~1000 filters individually — a signature gives a
    name and a type, never a guarantee that this particular name narrows anything. Five checked out
    of 1012 is evidence about the mechanism, not about the surface, so a shipped template still
    starts unable to spend: aimed at the mock, dry_run on, labelled experimental.
    """
    return FlowSpec(
        name=f"Автобай {label_for(category)} — снайпер",
        entry_node_id="search",
        params=_params(category),
        nodes=_nodes(),
        maturity=FlowMaturity.EXPERIMENTAL,
        testnet=True,
    )


def autobuy_specs() -> list[FlowSpec]:
    """One sniper per searchable category — 21 flows from one graph."""
    return [autobuy_spec(category) for category in SearchableCategory]
