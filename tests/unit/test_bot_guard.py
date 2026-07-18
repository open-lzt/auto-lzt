"""The bot's authorization and throttle (T3.1 / R-14).

The test that matters enumerates the dispatcher's OWN handlers rather than a list of commands
written here: naming them would prove nothing about the command somebody adds next month, and
"somebody added a handler and forgot the guard" is the entire failure being defended against. That
is also why the guard is middleware rather than a decorator, and this file is what turns that from
a comment into a fact.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

import pytest
from aiogram import Dispatcher
from aiogram.types import Chat, Message, TelegramObject, User

from app.bot.config import BotSettings
from app.bot.main import _UNGUARDED_OBSERVERS, build_dispatcher
from app.bot.middleware.admin_guard import AdminGuard
from app.bot.middleware.rate_limit import RateLimit

ADMIN_ID = 111
STRANGER_ID = 999


@pytest.fixture
def replies(monkeypatch: pytest.MonkeyPatch) -> list[str]:
    """Capture what the bot says, with no Bot and no network.

    A duck-typed stand-in does not work here, and that is worth knowing rather than working around:
    the middleware dispatches on ``isinstance(event, Message)``, so a lookalike is silently neither
    replied to nor refused with a reply. The event has to be a real Message; only ``answer`` is
    doubled — which is the process boundary anyway.
    """
    captured: list[str] = []

    async def _answer(self: Message, text: str, **_kw: Any) -> None:
        captured.append(text)

    monkeypatch.setattr(Message, "answer", _answer)
    return captured


def _message(text: str = "/start") -> Message:
    return Message(message_id=1, date=datetime.now(UTC), chat=Chat(id=1, type="private"), text=text)


def _settings() -> BotSettings:
    return BotSettings(
        token="123:FAKE",  # type: ignore[arg-type]
        admin_ids=frozenset({ADMIN_ID}),
        enabled=True,
        api_key="k",
    )


def _user(user_id: int) -> User:
    return User(id=user_id, is_bot=False, first_name="T")


async def _call_guard(user_id: int | None, event: TelegramObject) -> bool:
    """Run the guard exactly as the dispatcher would. True if the handler was reached."""
    reached = False

    async def handler(_event: TelegramObject, _data: dict[str, Any]) -> None:
        nonlocal reached
        reached = True

    data: dict[str, Any] = {} if user_id is None else {"event_from_user": _user(user_id)}
    await AdminGuard(frozenset({ADMIN_ID}))(handler, event, data)
    return reached


@pytest.fixture(scope="module")
def dispatcher() -> Dispatcher:
    """One dispatcher for the module.

    aiogram's Routers are module-level singletons and refuse to attach to a second Dispatcher, so
    build_dispatcher cannot be called twice in a process. In production it is called exactly once,
    which makes this a property of the framework rather than a defect — but the tests must share.
    """
    return build_dispatcher(_settings(), api=object())  # type: ignore[arg-type]


async def test_an_admin_reaches_the_handler(replies: list[str]) -> None:
    assert await _call_guard(ADMIN_ID, _message()) is True


async def test_a_stranger_never_reaches_the_handler(replies: list[str]) -> None:
    assert await _call_guard(STRANGER_ID, _message()) is False


async def test_an_event_with_no_user_is_refused(replies: list[str]) -> None:
    """Fail closed: an event the guard cannot attribute to a person is not one to wave through."""
    assert await _call_guard(None, _message()) is False


async def test_the_refusal_does_not_reveal_what_this_bot_is(replies: list[str]) -> None:
    """A stranger learning they have found an lzt-flow admin panel has learned the one thing worth
    knowing."""
    await _call_guard(STRANGER_ID, _message())

    reply = " ".join(replies).lower()
    assert reply, "a stranger got no answer at all"
    for leak in ("lzt", "admin", "админ", "flow", "доступ", "forbidden"):
        assert leak not in reply, f"the refusal leaks {leak!r}: {reply!r}"


def test_the_guard_is_registered_on_the_dispatcher_not_on_handlers(dispatcher: Dispatcher) -> None:
    """A decorator is a guard you have to remember. As middleware it applies to a handler because
    the handler exists — which is what makes the enumeration test below meaningful."""
    for observer in (dispatcher.message, dispatcher.callback_query):
        kinds = [type(m) for m in observer.middleware]
        assert AdminGuard in kinds, f"{observer} has no AdminGuard"
        assert RateLimit in kinds, f"{observer} has no RateLimit"


def _all_message_handlers(dispatcher: Dispatcher) -> list[Any]:
    found: list[Any] = []
    routers: list[Any] = [dispatcher]
    while routers:
        router = routers.pop()
        found.extend(router.message.handlers)
        routers.extend(router.sub_routers)
    return found


def test_no_handler_can_be_reached_except_through_the_guarded_observer(
    dispatcher: Dispatcher,
) -> None:
    """Enumerates the dispatcher's real routers rather than a list written here, so a handler added
    later is covered without anyone remembering to extend this test. Every message handler is
    reached only through dispatcher.message, whose middleware chain the test above pins — there is
    no per-handler flag that could exempt one."""
    handlers = _all_message_handlers(dispatcher)

    assert handlers, "no handlers found — this test would pass vacuously"
    assert len(handlers) >= 6, (
        f"expected the /start /help /nodes /node /flows /run set, got {len(handlers)}"
    )
    assert AdminGuard in [type(m) for m in dispatcher.message.middleware]


def test_the_throttle_runs_before_the_authorization_check(dispatcher: Dispatcher) -> None:
    """A flood should be dropped before it costs an authorization check per event — otherwise the
    throttle protects everything except itself."""
    kinds = [type(m) for m in dispatcher.message.middleware]

    assert kinds.index(RateLimit) < kinds.index(AdminGuard)


async def test_a_flood_from_one_user_is_throttled(replies: list[str]) -> None:
    """R-14. An admin holding a button down and an admin whose account was taken over look the same
    from here; neither should become a burst against the API and the marketplace behind it."""
    limiter = RateLimit(max_events=3, window_s=60.0)
    passed = 0

    async def handler(_e: TelegramObject, _d: dict[str, Any]) -> None:
        nonlocal passed
        passed += 1

    event = _message("/nodes")
    for _ in range(10):
        await limiter(handler, event, {"event_from_user": _user(ADMIN_ID)})

    assert passed == 3
    assert len(replies) == 7  # the rest were told to slow down, not silently dropped


async def test_the_throttle_is_per_user(replies: list[str]) -> None:
    """One noisy admin must not lock out the others."""
    limiter = RateLimit(max_events=2, window_s=60.0)
    reached: list[int] = []

    async def handler(_e: TelegramObject, data: dict[str, Any]) -> None:
        reached.append(data["event_from_user"].id)

    event = _message("/nodes")
    for _ in range(5):
        await limiter(handler, event, {"event_from_user": _user(ADMIN_ID)})
    await limiter(handler, event, {"event_from_user": _user(222)})

    assert reached.count(ADMIN_ID) == 2
    assert reached.count(222) == 1


async def test_the_window_slides_so_a_throttle_is_not_a_ban(
    replies: list[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    """A throttle that never forgets is a ban with extra steps.

    The clock is moved rather than slept through. A version of this test that slept for the real
    window passed and then failed on the next run — a timing-dependent test is a test that
    eventually lies, and a flaky one gets ignored, which is worse than not having it.
    """
    now = 1000.0
    monkeypatch.setattr("app.bot.middleware.rate_limit.time.monotonic", lambda: now)

    limiter = RateLimit(max_events=1, window_s=10.0)
    passed = 0

    async def handler(_e: TelegramObject, _d: dict[str, Any]) -> None:
        nonlocal passed
        passed += 1

    event = _message("/nodes")
    await limiter(handler, event, {"event_from_user": _user(ADMIN_ID)})
    await limiter(handler, event, {"event_from_user": _user(ADMIN_ID)})
    assert passed == 1, "the second event inside the window should be throttled"

    now += 11.0  # past the window
    await limiter(handler, event, {"event_from_user": _user(ADMIN_ID)})
    assert passed == 2, "the throttle must forget once the window has passed"


def test_every_observer_that_could_carry_a_handler_is_guarded(dispatcher: Dispatcher) -> None:
    """Not just message and callback_query. aiogram routes edited_message, inline_query,
    my_chat_member and twenty more to their OWN observers — a @router.message() handler does not
    see an edited message. Guarding only the two in use makes the guard's reach depend on a list
    nobody updates when they add a handler, which is precisely the "somebody forgot" failure the
    middleware exists to make impossible."""
    unguarded = [
        name
        for name, observer in dispatcher.observers.items()
        if name not in _UNGUARDED_OBSERVERS
        and AdminGuard not in [type(m) for m in observer.middleware]
    ]

    assert not unguarded, f"observers reachable without the guard: {unguarded}"


def test_no_handler_of_ours_sits_on_an_observer_the_guard_skips(dispatcher: Dispatcher) -> None:
    """The other half: `update` and `error` are deliberately unguarded, so none of OUR handlers may
    live there. If one does, the exemption stops being a reasoned gap and becomes a hole.

    aiogram's own ``Dispatcher._listen_update`` is expected on `update` — it is the root plumbing
    that fans an update out to the per-type observers, which are guarded. Exempting it by identity
    rather than by counting handlers keeps the test honest: a handler *we* add there still fails.
    """
    framework_owned = {"Dispatcher._listen_update"}
    routers: list[Any] = [dispatcher]
    offenders: list[str] = []
    while routers:
        router = routers.pop()
        for name in _UNGUARDED_OBSERVERS:
            observer = router.observers.get(name)
            for handler in getattr(observer, "handlers", []):
                qualname = getattr(handler.callback, "__qualname__", str(handler.callback))
                if qualname not in framework_owned:
                    offenders.append(f"{name}: {qualname}")
        routers.extend(router.sub_routers)

    assert not offenders, f"our handlers on an unguarded observer: {offenders}"


def test_one_throttle_budget_per_user_not_one_per_event_type(dispatcher: Dispatcher) -> None:
    """A fresh RateLimit() per observer gives each user a separate budget per event type, so the
    same person gets max_events messages AND max_events callbacks — twice the limit anyone
    configured, and the button-masher case is exactly a callback flood."""
    limiters = {
        id(m)
        for observer in dispatcher.observers.values()
        for m in observer.middleware
        if isinstance(m, RateLimit)
    }

    assert len(limiters) == 1, f"{len(limiters)} separate throttle budgets, expected one shared"


async def test_a_stranger_cannot_grow_the_throttle_table_without_bound() -> None:
    """The throttle runs BEFORE the authorization check, so its keys come from strangers. Anything
    an unauthenticated party can grow without limit is a memory-exhaustion primitive; the table has
    to have a ceiling, and the ceiling has to hold when every entry is too fresh to evict."""
    limiter = RateLimit(max_events=5, window_s=600.0, max_tracked_users=50)

    async def handler(_e: TelegramObject, _d: dict[str, Any]) -> None:
        pass

    event = _message("/nodes")
    for stranger_id in range(500):
        await limiter(handler, event, {"event_from_user": _user(stranger_id)})

    assert len(limiter._hits) <= 50, f"table grew to {len(limiter._hits)} from 500 strangers"


async def test_a_full_table_does_not_become_a_permanent_lockout(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The ceiling's failure mode if eviction were broken: once the table filled, every new user
    would be dropped silently — forever. A stranger flood would end as a lasting denial of service
    against the admins, which is a worse outcome than the memory growth the ceiling prevents.

    So: fill it, let the windows pass, and a real admin arriving next must get in.
    """
    now = 1000.0
    monkeypatch.setattr("app.bot.middleware.rate_limit.time.monotonic", lambda: now)
    limiter = RateLimit(max_events=5, window_s=10.0, max_tracked_users=50)
    reached: list[int] = []

    async def handler(_e: TelegramObject, data: dict[str, Any]) -> None:
        reached.append(data["event_from_user"].id)

    event = _message("/nodes")
    for stranger_id in range(50):
        await limiter(handler, event, {"event_from_user": _user(stranger_id)})
    assert len(limiter._hits) == 50, "the table should be full"

    now += 11.0  # every stranger's window has now passed
    await limiter(handler, event, {"event_from_user": _user(ADMIN_ID)})

    assert ADMIN_ID in reached, "a full table locked out a user whose predecessors had all expired"
    assert len(limiter._hits) == 1, "expired entries were not evicted to make room"


async def test_a_returning_users_stale_window_is_dropped(monkeypatch: pytest.MonkeyPatch) -> None:
    """No husk per user who ever sent one message: an entry that prunes to empty is removed rather
    than kept as an empty deque keyed forever."""
    now = 1000.0
    monkeypatch.setattr("app.bot.middleware.rate_limit.time.monotonic", lambda: now)
    limiter = RateLimit(max_events=5, window_s=10.0)

    async def handler(_e: TelegramObject, _d: dict[str, Any]) -> None:
        pass

    event = _message("/nodes")
    await limiter(handler, event, {"event_from_user": _user(ADMIN_ID)})
    assert len(limiter._hits[ADMIN_ID]) == 1

    now += 11.0
    await limiter(handler, event, {"event_from_user": _user(ADMIN_ID)})

    assert len(limiter._hits[ADMIN_ID]) == 1, "the stale hit was carried into the new window"


async def test_merely_looking_a_stranger_up_does_not_allocate_their_entry() -> None:
    """`defaultdict[key]` inserts on READ. With one, the lookup that decides whether to throttle
    would itself create the entry — so the table would grow from strangers even if the code never
    intended to track them."""
    limiter = RateLimit(max_events=5, window_s=600.0)

    async def handler(_e: TelegramObject, _d: dict[str, Any]) -> None:
        pass

    await limiter(handler, _message("/nodes"), {})  # an event with no user attached

    assert not limiter._hits, "an unattributable event allocated a throttle entry"


def test_a_bot_with_no_admins_is_not_configured() -> None:
    """The worst outcome is a bot that answers everyone, so a token alone must not be enough."""
    assert not BotSettings(token="123:FAKE", admin_ids=frozenset(), enabled=True).is_configured()  # type: ignore[arg-type]
    assert not BotSettings(token="", admin_ids=frozenset({1}), enabled=True).is_configured()  # type: ignore[arg-type]
    assert not BotSettings(token="123:F", admin_ids=frozenset({1}), enabled=False).is_configured()  # type: ignore[arg-type]
    assert BotSettings(token="123:F", admin_ids=frozenset({1}), enabled=True).is_configured()  # type: ignore[arg-type]


def test_admin_ids_parse_from_a_plain_env_string() -> None:
    """Operators write LZT_FLOW_BOT_ADMIN_IDS=111,222 in .env, not a JSON array."""
    assert BotSettings(token="x", admin_ids="111,222", enabled=True).admin_ids == {111, 222}  # type: ignore[arg-type]


def test_the_token_is_a_secret_and_does_not_print() -> None:
    """It reaches logs and tracebacks otherwise, and it is a credential that can message every user
    the bot has ever seen."""
    settings = BotSettings(token="123:REAL-SECRET", admin_ids=frozenset({1}), enabled=True)  # type: ignore[arg-type]

    assert "REAL-SECRET" not in repr(settings)
    assert "REAL-SECRET" not in str(settings.token)
    assert settings.token.get_secret_value() == "123:REAL-SECRET"
