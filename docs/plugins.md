<p align="right"><a href="plugins.en.md">English</a> · <b>Русский</b></p>

# Плагины — как добавить свой узел

Узел добавляется установкой пакета. Не папкой, которую кто-то сканирует, не путём в конфиге —
`pip install`, и всё. Поэтому узел не может появиться в движке без того, чтобы кто-то поставил
пакет, который его даёт.

## Минимальный плагин

Два файла.

**`pyproject.toml`** — здесь вся установка:

```toml
[project]
name = "lzt-flow-my-pack"
version = "1.0.0"
requires-python = ">=3.12"
dependencies = ["lzt-flow"]

[project.entry-points."lzt_flow.nodes"]
my_pack = "lzt_flow_my_pack.nodes:REGISTRATIONS"

[build-system]
requires = ["hatchling"]
build-backend = "hatchling.build"

[tool.hatch.build.targets.wheel]
packages = ["lzt_flow_my_pack"]
```

**`lzt_flow_my_pack/nodes.py`**:

```python
from pydantic import Field

from app.core.schema import BaseSchema
from app.domain.catalog.capabilities import NodeCapability
from app.domain.catalog.registry import NodeCategory, NodeRegistration, NodeType
from app.domain.flow_engine.base_node import BaseNode, RunContext
from app.domain.flow_engine.dtos import StepResultDTO


class ShoutInput(BaseSchema):
    text: str = Field(title="Текст", json_schema_extra={"ui": "text"})


class ShoutOutput(BaseSchema):
    shouted: str


class ShoutNode(BaseNode):
    node_type = "demo.shout"          # ключ, по которому флоу ссылается на узел
    required_inputs = ("text",)       # компилятор проверит, что порт подключён

    async def execute(self, ctx: RunContext) -> StepResultDTO:
        text = str(ctx.resolve_input("text"))
        return StepResultDTO(node_id=ctx.node.id, output={"shouted": text.upper()})


REGISTRATIONS = [
    NodeRegistration(
        node_type=NodeType(
            key=ShoutNode.node_type,
            category=NodeCategory.LOGIC,
            input_schema=ShoutInput,
            output_schema=ShoutOutput,
            idempotent=True,
            capabilities=frozenset({NodeCapability.PURE}),
        ),
        impl=ShoutNode,
    )
]
```

```bash
uv pip install -e .
# перезапустите воркер и API — реестр собирается на старте
```

Всё. Узел появится в `GET /catalog/list`, получит форму в веб-канвасе и в боте без единой правки
там, и будет скомпилирован и запущен тем же интерпретатором, что и встроенные.

Живой пример, который гоняется в тестах: `tests/fixtures/plugin_pkg/`.

## Полный плагин — рантайм (узлы + роутеры + хендлеры + lifecycle)

Узел-плагин выше (`lzt_flow.nodes`) добавляет **только тип узла**. Если нужно больше — свой
API-роутер, свой бот-хендлер, фоновая задача, доступ к redis/БД на старте — это **полный плагин**:
отдельная группа entry point `lzt_flow.plugins`, которую поднимает менеджер рантайма
(`app/plugin_runtime/`).

Разница по доверию — никакой. И узел-пак, и полный плагин это **owner-only код**: ставится
`pip install` + рестарт, никогда не через API. Полный плагин просто может больше (см. «Чего эти
правила НЕ дают» ниже — она применима к нему в полной мере).

Плагин объявляет **модуль** (без `:attr`), а на модуле — три необязательных списка-константы
хуков жизненного цикла:

```toml
[project.entry-points."lzt_flow.plugins"]
my_plugin = "my_pkg.plugin"
```

```python
# my_pkg/plugin.py
from __future__ import annotations

from app.plugin_runtime import (
    PluginLoadContext, PluginLoadedContext, PluginProcess, PluginReadyContext,
)

def _register(ctx: PluginLoadContext) -> PluginLoadedContext:
    loaded = PluginLoadedContext()
    loaded.nodes.append(MY_NODE_REGISTRATION)          # применяется в API и WORKER
    if ctx.process is PluginProcess.API:
        loaded.api_routers.append(my_api_router)       # применяется только в API
    loaded.bot_routers.append(my_bot_router)           # применяется только в BOT
    return loaded

async def _start(ctx: PluginReadyContext) -> None:
    # redis/sessionmaker есть в API и WORKER, но None в BOT (бот — клиент API).
    ctx.spawn(_my_background_loop(ctx), "my-plugin-loop")

async def _stop(ctx: PluginReadyContext) -> None:
    ...

PRE_INIT  = [_register]   # sync: регистрация до старта процесса
POST_INIT = [_start]      # async: живые хендлы (redis/БД), фоновые задачи через ctx.spawn
SHUTDOWN  = [_stop]       # async: best-effort очистка; ошибка логируется, не роняет остановку
```

Жизненный цикл: `discover()` (импорт, fail-closed) → `pre_init()` (собрать вклад, отфильтровать по
процессу) → `post_init()` (живые хендлы) → `shutdown()` (отмена задач + SHUTDOWN). Плагин объявляет
**все** поверхности; менеджер применяет только те, что нужны текущему процессу из трёх:

| Процесс | Что применяется |
|---|---|
| API (`app/main.py`, в lifespan) | `nodes` + `api_routers` |
| WORKER (`app/worker/arq_settings.py`) | `nodes` |
| BOT (`app/bot/main.py`) | `bot_routers` |

Узлы плагина сворачиваются в тот же `NodeRegistry` через `build_registry(extra_registrations=...)`,
поэтому плагин, занявший ключ встроенного узла, так же роняет старт `DuplicateNodeType`. Обнаружение
крутится в **lifespan/startup**, а не в импорте `app.main`: `ep.load()` — это выполнение чужого
кода, ему место на старте процесса, а не на любом `import` (alembic, скрипты, тесты).

Как и у узла-пака: **нет hot-reload** — плагин виден только после рестарта. Полный плагин можно
поставить двумя путями: `pip install` руками (entry point `lzt_flow.plugins`) **или из бота** — из
папки, см. ниже. Живой пример: `tests/fixtures/plugin_runtime_pkg/`.

## Установка из бота (папка `.system/plugins/`)

Второй источник рантайма — папка. Владелец ставит плагины **из бота**, без шелла: бот показывает
каталог из доверенного git-репозитория, по кнопке дёргает API, а тот скачивает плагин в
`.system/plugins/<name>/`. Тот же менеджер, тот же lifecycle — просто `discover()` сканирует ещё и
папку.

**Раскладка** `.system/plugins/<name>/`:
```
manifest.json   # {schema_version, name, version, description, entry, requirements}
plugin.py       # entry-модуль с PRE_INIT/POST_INIT/SHUTDOWN (файлы плагина в корне архива)
```
`requirements` — pip-зависимости плагина. Ставятся **один раз при install** (в API-эндпойнте, под
локом), а не на старте: три процесса (API/worker/bot) делят один venv, и параллельный `pip` на
старте испортил бы site-packages. Старт только **проверяет**, что зависимости импортируются.

**Каталог** — `plugins.json` по адресу `LZT_FLOW_PLUGIN_INDEX_URL` (пусто → установка из бота
выключена). Доверенный репозиторий владельца, **отдельный от `lzt-flows`** (там FLOW-модули-данные):
референс — [`open-lzt/lzt-plugins`](https://github.com/open-lzt/lzt-plugins). Каждая запись: `name`,
`version`, `description`, `source_url` (zip-архив), `requirements`. Скачивание архива распаковывается
с защитой от zip-slip и symlink-записей. Приватный каталог — задайте `LZT_FLOW_PLUGIN_INDEX_TOKEN`
(GitHub PAT, repo read); учтите, что приватный `raw.githubusercontent` редиректит на другой хост и
httpx роняет заголовок — для приватного GitHub-каталога проще публичный репо или `api.github.com/.../contents`-URL.

**Бот** — `/plugins` открывает инлайн-меню: доступные + установленные, карточка с
`Установить/Обновить/Удалить`, экран настроек с тумблерами **Автообновление** и **Алерты о новых
версиях** (оба по умолчанию выключены). Проверка обновлений живёт в процессе бота (только у него есть
Bot и admin-id): при включённом авто-обновлении новая версия скачивается в папку, при алертах —
приходит уведомление (текст настраивается в `app/plugin_runtime/texts.toml`). Применяется всё —
после рестарта.

**Fail-closed vs карантин.** Сломанный плагин из бота (битый манифест, отсутствующая зависимость,
ошибка импорта **или коллизия ключа узла со встроенным**) не роняет процесс — он логируется,
пропускается и помечается «сломан» в боте. Иначе битый плагин заблокировал бы сам API/бот, через
который его удаляют. Entry-point-плагины (осознанный `pip install` в шелле) остаются fail-closed:
их коллизия ключа роняет старт, потому что неоднозначный набор узлов обслуживать нельзя.

**Ограничение.** Раскладка предполагает общий диск у трёх процессов (single-host self-host);
multi-container деплою нужна общая `.system/plugins/` на shared volume.

## Схема — это и есть UI

Форму никто не пишет отдельно. Она выводится из вашей Pydantic-модели:

```python
class BumpInput(BaseSchema):
    item_id: int = Field(title="Лот", json_schema_extra={"ui": "lot_ref"})
```

`title` — подпись, `ui` — контрол. Словарь `ui` закрыт:

| `ui` | Что рисует | Что приходит в `resolve_input` |
|---|---|---|
| `lot_ref` | выбор лота | `int` |
| `account_ref` | выбор аккаунта | `str` (UUID, проверяется) |
| `text` | строка | `str` |
| `number` | число | `int` или `float` |
| `bool` | да/нет | `bool` |
| `select` | список | `str` (для `StrEnum` варианты берутся сами) |
| `secret` | маскируется, не эхо-ится в чат | `str` |

Незнакомый `ui` деградирует до текстового поля, а не роняет форму — иначе плагин мог бы выключить
бота, выдумав контрол.

Удалили поле из модели — оно исчезло из формы. Добавили — появилось. Править бота или фронт не
нужно: в них нет знания о ваших узлах.

## Возможности (capabilities) — обязательны

`capabilities` — не метка для галочки. По ней фильтрует валидатор модулей и по ней оператор видит,
что узел делает, **до** того как его подключит.

| Возможность | Когда |
|---|---|
| `PURE` | не делает ничего наружу |
| `MARKET_READ` | читает маркет |
| `MARKET_MUTATE` | меняет лоты |
| `MONEY` | **тратит деньги** |
| `NETWORK_EGRESS` | ходит в сеть |
| `REFLECTIVE` | вызывает произвольный метод API по имени |

Пустой набор запрещён: он не отличает «доказуемо ничего не делает» от «никто не объявил», и
фильтр пропустил бы второе. Если узел ничего не делает — скажите это словом `PURE`.

`REFLECTIVE` — в `FORBIDDEN_CAPABILITIES`. Модуль, использующий такой узел, отклоняется.

### Если узел тратит деньги

Объявите `MONEY` **и** возьмите гард перед эффектом:

```python
async def execute(self, ctx: RunContext) -> StepResultDTO:
    first = await ctx.deps.guard.check_and_set(ctx.idempotency_key)
    if not first:
        raise RunFailed(ctx.run_id, ctx.node.id, "уже выполнено; сверьте вручную")
    result = await ctx.deps.market.bump(item_id, account)   # эффект — ПОСЛЕ гарда
    ...
```

Почему так: двухфазный коммит `RunStep` защищает от одновременного выполнения, но **не** от падения
между эффектом и коммитом. Без гарда resume повторит платное действие.

И не подделывайте успех на повторе. `market.relist` при обнаруженном повторе падает, а не
возвращает выдуманный id — потому что фальшивый id отравит всё, что ниже читает
`${relist.item_id}`. Громко упасть — честная цена за деньги.

Контрактный тест проверяет это AST-обходом: узел с `MONEY`, в модуле которого нет
`check_and_set`, роняет сборку.

## Узлы, которые ходят в сеть

Наследуйтесь от `BaseRequestNode`. Не от `BaseNode` с `httpx` внутри.

```python
class SendMessageNode(BaseRequestNode):
    node_type = "tg.send_message"
    required_inputs = ("bot_token", "chat_id", "text")

    def build_request(self, ctx: RunContext) -> RequestSpec:
        token = str(ctx.resolve_input("bot_token"))
        return RequestSpec(
            url=f"https://api.telegram.org/bot{token}/sendMessage",
            method=HttpMethod.POST,
            headers={"Content-Type": "application/json"},
            json_body={"chat_id": ..., "text": ...},
            timeout_s=10.0,
        )

    def parse_response(self, ctx, status, body) -> StepResultDTO:
        ...
```

`execute()` там финальный. Он владеет egress-политикой, ретраями, backoff'ом и таймаутом — вы их
получаете, хотели или нет. Реализуете только `build_request` и `parse_response`.

**Стройте URL, не принимайте его.** Узел, берущий URL из флоу, — это SSRF-примитив с приветливым
именем, и забор остаётся единственным, что стоит между чужим модулем и вашей внутренней сетью.
Нужен другой хост — напишите другой узел.

Хост должен быть в `LZT_FLOW_EGRESS_ALLOWED_HOSTS`, иначе запрос не уйдёт. Список **пуст по
умолчанию**: ненастроенный забор обязан не пускать никуда, а не пускать всюду.

Ретраятся 429 и 5xx («позже, но не никогда») с джиттером. 4xx не ретраится: endpoint говорит, что
не так сам запрос, и повтор только сожжёт лимит.

## Правила, которые вам не обойти

**Плагин не может подменить встроенный узел.** Заявите `market.bump` — процесс не стартует,
`DuplicateNodeType` назовёт обе стороны. Никакого last-wins: пакет, тихо подменивший денежный
узел, увёл бы все флоу стенда на свой код без единой ошибки в логах.

Origin проставляет загрузчик из entry point, а не плагин. Иначе враждебный пакет назвался бы
`builtin` и свалил вину на кого-то другого.

**Ошибка загрузки останавливает старт.** Процесс, молча выкинувший сломанный плагин, обслуживает
набор узлов, которого никто не объявлял: флоу с этим узлом упадёт в рантайме, держа деньги, вместо
загрузки. Отказ стартовать — громче и дешевле.

## Чего эти правила НЕ дают

Скажем прямо, потому что комментарии в коде когда-то говорили обратное.

**Плагин — это код.** `ep.load()` импортирует ваш модуль, то есть выполняет произвольный Python.
Плагин может сделать `BumpNode.execute = ...` — коллизии не будет, origin останется `builtin`, и
все флоу пойдут через чужой код. Реестр защищает от **конфликтующей регистрации**, а не от
враждебного пакета.

Так же и с забором: он держит **модули** (данные, где URL — недоверенный ввод). Против плагина
он бессилен — `import socket` есть всегда.

Это защитимо: `pip install` — действие администратора, и ставя плагин, вы доверяете его автору
ровно так же, как доверяете самому движку. Недопустимо только считать, что изоляция есть.

## Проверка

```bash
uv run pytest tests/integration/test_plugin_nodes.py -q
```

Там установлен настоящий дистрибутив-фикстура, и тесты проходят весь путь: обнаружение через entry
point → каталог → компиляция → **реальный запуск**. Ни один тест не патчит глобал, чтобы протащить
узел, — реестр, который расширяется только патчем, не расширяемый, а просто изменяемый.

Свой узел проверяйте так же: соберите флоу с ним и прогоните через настоящий `execute_run`. Узел,
который есть в каталоге, но не запускается, — ложь хуже, чем его отсутствие.

## См. также

- [Модули и реестр](modules.md) — как опубликовать, две разновидности, модель доверия
- [Проектирование флоу](flow-design-guide.md) — как из текста собрать граф
- [Архитектура](../ARCHITECTURE.md) — где узел живёт в общей картине
