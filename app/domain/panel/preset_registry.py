"""The presets the panel offers, each DECLARING its own parameter surface.

A preset is a form that authors a flow. What that form contains is stated here, once, as a
Pydantic model — the same mechanism a node uses to declare its inputs. That one choice is what
makes the frontend generic: the model renders to JSON Schema for ``AutoForm`` AND validates the
deploy request body, so a field cannot exist in the form without existing in validation, and
adding a preset touches no frontend file at all.

The alternative, briefly tried and removed: a bespoke request DTO plus a bespoke React screen per
preset. That duplicated the interval list into the client, re-typed the market's category enum by
hand in TypeScript, and made "add a preset" a four-file change across two languages.

``build`` stays a pure function (``presets.py``) — this module adds the declaration, not logic.
"""

from __future__ import annotations

import re
from collections.abc import Callable
from dataclasses import dataclass
from enum import StrEnum
from typing import Any, Final
from uuid import UUID

from pydantic import Field, field_validator

from app.core.exceptions import AppError, ErrorCode
from app.core.schema import BaseSchema
from app.domain.flow_engine.spec import FlowSpec
from app.domain.market.categories import SearchableCategory
from app.domain.panel.presets import (
    AutobumpSettings,
    AutobuySettings,
    ThreadBumpSettings,
    build_autobump_flow,
    build_autobuy_flow,
    build_thread_bump_flow,
)


class SchedulePreset(StrEnum):
    """The intervals a preset form offers.

    Server-side so the set is one fact: these five used to be a literal in the panel, which meant
    a sixth interval was a frontend change and the server had no idea which schedules it was
    actually offering. Values ARE cron expressions — the trigger takes them unchanged.
    """

    EVERY_15_MIN = "*/15 * * * *"
    EVERY_30_MIN = "*/30 * * * *"
    HOURLY = "0 * * * *"
    EVERY_4_HOURS = "0 */4 * * *"
    DAILY_9AM = "0 9 * * *"


# Pydantic types `json_schema_extra` as a dict of JSON values; without the annotation mypy infers
# these literals far more narrowly (`dict[str, dict[str, str]]`) and rejects them at every use.
_JsonDict = dict[str, Any]

# A cron expression is not a label. ONE mapping, read by two surfaces: the form's picker (through
# `x-ui.options`) and the task list (through `schedule_label` on the task DTO). Keeping it single
# is the point — the same five intervals were previously a literal in the client, and the task
# cards showed raw cron because nothing on that path knew the words.
SCHEDULE_LABELS: Final[dict[str, str]] = {
    SchedulePreset.EVERY_15_MIN.value: "Каждые 15 минут",
    SchedulePreset.EVERY_30_MIN.value: "Каждые 30 минут",
    SchedulePreset.HOURLY.value: "Каждый час",
    SchedulePreset.EVERY_4_HOURS.value: "Каждые 4 часа",
    # The zone is named because this is the only option promising a WALL-CLOCK hour, and the
    # scheduler runs every expression in UTC. An operator at UTC+3 picking a bare «9:00» would
    # get 12:00 their time — a three-hour lie in a tool whose whole job is doing things at the
    # right moment. The intervals above need no zone: four hours is four hours in any of them.
    SchedulePreset.DAILY_9AM.value: "Раз в день, в 9:00 UTC",
}


def schedule_label(cron: str) -> str:
    """The human name for a schedule, or the cron itself when it is not one of ours.

    A flow edited on the canvas can carry any cron, and inventing a phrase for an arbitrary
    expression would be a lie — showing it verbatim is the honest fallback.
    """
    return SCHEDULE_LABELS.get(cron, cron)


_SCHEDULE_UI: _JsonDict = {
    "x-ui": {
        "widget": "select",
        # Last in the form. It lives on the base class (so the deploy route can read it typed),
        # and Pydantic puts base fields first — which would open every preset with «Как часто».
        "order": 100,
        "options": [{"value": value, "label": label} for value, label in SCHEDULE_LABELS.items()],
    }
}

_ACCOUNTS_UI: _JsonDict = {"x-ui": {"widget": "account_ref"}}
_CATEGORY_UI: _JsonDict = {"x-ui": {"widget": "category_picker"}}
_THREADS_UI: _JsonDict = {"x-ui": {"widget": "textarea"}}

# Whitespace, commas and semicolons all separate ids — the field asks for "one per line, comma or
# space separated", so all three have to work in one paste.
_THREAD_SEPARATORS: Final = re.compile(r"[\s,;]+")
# lzt thread links are `/threads/<id>/` or `/threads/<slug>.<id>/`; take the number that follows
# the segment, not merely the last number in the URL (a trailing `/page-2` is not the thread).
_THREAD_URL_ID: Final = re.compile(r"/threads/(?:[^/]*?\.)?(\d+)")


class PresetParams(BaseSchema):
    """What every preset asks for, at minimum.

    ``schedule_cron`` lives on the base because the DEPLOY path needs it: the trigger is attached
    by the route, not by the graph builder, so the route has to be able to read the schedule off
    any preset's parameters without knowing which preset it is holding. Subclasses override the
    default, never the name.
    """

    schedule_cron: SchedulePreset = Field(
        default=SchedulePreset.EVERY_30_MIN, title="Как часто", json_schema_extra=_SCHEDULE_UI
    )


class AutobumpParams(PresetParams):
    # Ordering note (applies to every preset below): Pydantic emits base-class fields FIRST, so
    # `schedule_cron` — declared on PresetParams so the deploy route can read it typed — would
    # open every form with «Как часто». That asks WHEN before WHO or WHAT, which is the wrong
    # order to think in. `x-ui.order` (see _SCHEDULE_UI) pushes it last without moving the field
    # off the base class and re-duplicating it into all three subclasses.
    accounts: list[UUID] = Field(
        min_length=1,
        title="Аккаунты",
        description="Лоты каждого выбранного аккаунта поднимаются отдельно.",
        json_schema_extra=_ACCOUNTS_UI,
    )
    max_bumps: int = Field(
        default=20,
        ge=1,
        le=1000,
        title="Лотов за один запуск",
        description="Ограничение на один запуск — общий темп задаёт расписание.",
    )
    # No `reprice` field, deliberately. It shipped as a checkbox that deployed a task failing on
    # EVERY fire: `market.reprice` needs `price`, or `decay_pct` together with `current_price`, and
    # the preset wired neither — `AutobumpSettings.reprice_price` was never assigned, so the only
    # branch that could have supplied one was unreachable.
    #
    # It cannot simply be wired up either: nothing in this graph knows what a lot currently costs.
    # `get_my_lots` yields `item_ids` and `count`, `for_each_lot` yields `count` — no price
    # anywhere, so `decay_pct` (lower each lot by a percentage, which is what the label implies)
    # has no input to work from. The one satisfiable shape is a single fixed price applied to
    # every lot, which would flatten a seller's whole price list to one number on the first fire.
    #
    # `market.reprice` stays in the catalog: on the canvas an author can wire a price from a source
    # that has one. The preset offers the feature again when the graph can carry per-lot prices.


class ThreadBumpParams(PresetParams):
    schedule_cron: SchedulePreset = Field(
        default=SchedulePreset.EVERY_4_HOURS, title="Как часто", json_schema_extra=_SCHEDULE_UI
    )
    accounts: list[UUID] = Field(
        min_length=1,
        title="Аккаунты",
        description="Тему поднимает тот аккаунт, который её создал.",
        json_schema_extra=_ACCOUNTS_UI,
    )
    threads: list[int] = Field(
        min_length=1,
        title="ID тем",
        description="По одному в строке, через запятую или пробел. Ссылку можно вставить целиком.",
        json_schema_extra=_THREADS_UI,
    )

    @field_validator("threads", mode="before")
    @classmethod
    def _parse_threads(cls, value: object) -> object:
        """Parse the textarea's free text into ids — the field's own description is its contract.

        The widget is a textarea, so the panel sends ONE string. Without this, `list[int]` rejected
        every shape the description promises — «12345», «12345, 67890», one per line, and a pasted
        URL — with "Input should be a valid list". The whole «Поднятие тем» preset could not be
        deployed: no text an operator could type into the field would validate.

        Fails loudly on a token it cannot read rather than dropping it. Silently skipping an
        unparseable id would bump fewer threads than the operator listed and report success.
        """
        if not isinstance(value, str):
            return value  # a real list from an API client — leave it to the int coercion below
        ids: list[int] = []
        for token in _THREAD_SEPARATORS.split(value):
            if not token:
                continue
            if token.isdigit():
                ids.append(int(token))
                continue
            # A pasted link: the id is the first number after /threads/. Anchored on that segment
            # rather than "last number in the string", which would read `/page-2` as the thread.
            match = _THREAD_URL_ID.search(token)
            if match is None:
                raise ValueError(f"не похоже на ID темы или ссылку: {token!r}")
            ids.append(int(match.group(1)))
        return ids


class AutobuyParams(PresetParams):
    schedule_cron: SchedulePreset = Field(
        default=SchedulePreset.HOURLY, title="Как часто", json_schema_extra=_SCHEDULE_UI
    )
    category: SearchableCategory = Field(
        default=SearchableCategory.STEAM,
        title="Категория",
        description="Раздел маркета, в котором искать.",
        json_schema_extra=_CATEGORY_UI,
    )
    max_price: float = Field(
        gt=0,
        default=500,
        title="Цена до",
        description="Фильтрует сам маркет — лот дороже сюда не попадёт.",
    )
    count: int = Field(default=1, ge=1, le=100, title="Лотов за один запуск")
    # Default True at every layer — form, request body and builder. This flow spends money, so
    # the safe value has to be the one you get by not thinking about it.
    dry_run: bool = Field(
        default=True,
        title="Холостой прогон",
        description="Включено — флоу только сообщает, что купил бы. Деньги не тратятся.",
    )
    accounts: list[UUID] = Field(
        default_factory=list,
        title="Аккаунты",
        description="Пусто — покупка от любого свободного аккаунта.",
        json_schema_extra=_ACCOUNTS_UI,
    )


def _build_autobump(name: str, params: AutobumpParams) -> FlowSpec:
    return build_autobump_flow(
        name,
        AutobumpSettings(
            accounts=tuple(params.accounts),
            schedule_cron=params.schedule_cron.value,
            max_bumps=params.max_bumps,
            # Always False: the form no longer offers it (see AutobumpParams) because the graph
            # has no per-lot price to reprice against.
            reprice=False,
        ),
    )


def _build_thread_bump(name: str, params: ThreadBumpParams) -> FlowSpec:
    return build_thread_bump_flow(
        name,
        ThreadBumpSettings(
            accounts=tuple(params.accounts),
            threads=tuple(params.threads),
            schedule_cron=params.schedule_cron.value,
        ),
    )


def _build_autobuy(name: str, params: AutobuyParams) -> FlowSpec:
    return build_autobuy_flow(
        name,
        AutobuySettings(
            category=params.category.value,
            max_price=params.max_price,
            count=params.count,
            schedule_cron=params.schedule_cron.value,
            dry_run=params.dry_run,
            accounts=tuple(params.accounts),
        ),
    )


@dataclass(slots=True, frozen=True)
class PresetSpec:
    """One preset: how it is named, what it asks for, and what graph it produces.

    ``params`` is both the form and the request validator. ``build`` receives an already-validated
    instance of it, so a builder never sees an unchecked value.
    """

    key: str
    title: str
    icon: str  # a symbol that exists in @open-lzt/ui's sprite
    params: type[PresetParams]
    build: Callable[[str, Any], FlowSpec]
    default_name: str


class UnknownPreset(AppError):
    """Deploy asked for a preset key this build does not ship."""

    status_code = 404
    code = ErrorCode.NOT_FOUND

    def __init__(self, key: str) -> None:
        super().__init__(f"unknown preset {key!r}")
        self.key = key

    @property
    def client_message(self) -> str:
        return "Такого пресета нет"


class DuplicatePresetKey(Exception):
    """Two presets claimed one key — fails at import, like the node and tab registries."""

    def __init__(self, key: str) -> None:
        super().__init__(f"preset key {key!r} declared twice")
        self.key = key


BUILTIN_PRESETS: Final[tuple[PresetSpec, ...]] = (
    PresetSpec(
        key="autobump",
        title="Поднятие",
        icon="zap",
        params=AutobumpParams,
        build=_build_autobump,
        default_name="Поднятие",
    ),
    PresetSpec(
        key="thread-bump",
        title="Поднятие тем",
        icon="message",
        params=ThreadBumpParams,
        build=_build_thread_bump,
        default_name="Поднятие тем",
    ),
    PresetSpec(
        key="autobuy",
        title="Автобай",
        icon="wallet",
        params=AutobuyParams,
        build=_build_autobuy,
        default_name="Автобай",
    ),
)


def _by_key() -> dict[str, PresetSpec]:
    presets: dict[str, PresetSpec] = {}
    for preset in BUILTIN_PRESETS:
        if preset.key in presets:
            raise DuplicatePresetKey(preset.key)
        presets[preset.key] = preset
    return presets


PRESETS_BY_KEY: Final[dict[str, PresetSpec]] = _by_key()


def get_preset(key: str) -> PresetSpec:
    preset = PRESETS_BY_KEY.get(key)
    if preset is None:
        raise UnknownPreset(key)
    return preset
