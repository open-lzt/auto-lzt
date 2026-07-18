<p align="right"><a href="index.en.md">English</a> · <b>Русский</b></p>

# Карта для AI-агентов — lzt-flow

Прочтите это перед тем, как открывать исходники. Каждый пакет ниже поставляет собственный
`_MODULE_AUTO.md` (сгенерированные, сжатые сигнатуры) — читайте сначала его; открывайте `.py`
исходник, только если доку устарела, неоднозначна, или вам нужен control-flow. Полное
повествовательное описание архитектуры: [../../ARCHITECTURE.md](../../ARCHITECTURE.md).
Расширение движка: [../plugins.md](../plugins.md) (написание узла, capabilities, гард денег,
egress-забор) и [../modules.md](../modules.md) (реестр и что доказывает и не доказывает его
чексумма).

## Раскладка

| Пакет | Что владеет |
|---|---|
| `app/api/` | FastAPI-роутеры — тонкие хендлеры, сервисы/репозитории через `Depends`. См. `app/api/_MODULE_AUTO.md`. |
| `app/core/` | Настройки, аутентификация, общее дерево `AppError`, логирование. См. `app/core/_MODULE_AUTO.md`. |
| `app/db/` | Асинхронный engine/sessionmaker, контракты `BaseRepo`/`BaseSessionmakerRepo`, ORM-модели. См. `app/db/_MODULE_AUTO.md`. |
| `app/domain/account/` | Тенанты, аккаунты маркетплейса, envelope-шифрование токенов, `TokenPool` на тенант. См. `app/domain/account/_MODULE_AUTO.md`. |
| `app/domain/flow_engine/` | FlowSpec → компилятор → IR → типизированные ошибки; здесь живут резолвер путей (`path.py`) и контракт узла (`base_node.py`). См. `app/domain/flow_engine/_MODULE_AUTO.md`. |
| `app/domain/catalog/` | Каталог узлов (`registry.py`) и все конкретные узлы (`nodes/`), включая `DynamicMethodNode` на рефлексии. См. `app/domain/catalog/_MODULE_AUTO.md`. |
| `app/domain/market/` | Адаптер/сервис маркетплейса поверх `pylzt` (bump, reprice, relist, list-lots). См. `app/domain/market/_MODULE_AUTO.md`. |
| `app/domain/triggers/` | Определения триггеров по расписанию/событию, привязанные к скомпилированному флоу. См. `app/domain/triggers/_MODULE_AUTO.md`. |
| `app/domain/scheduler/` | Обвязка APScheduler, превращающая триггер `SCHEDULE` в периодический запуск. См. `app/domain/scheduler/_MODULE_AUTO.md`. |
| `app/domain/events/` | Встроенный event-роутер `lzt-eventus` для триггеров `EVENT`. См. `app/domain/events/_MODULE_AUTO.md`. |
| `app/worker/` | Statefull-интерпретатор (`runtime.py`), обвязка arq-джобов, реестр узлов. См. `app/worker/_MODULE_AUTO.md`. |
| `frontend/src/canvas/` | Канвас авторинга на React Flow. См. `frontend/src/canvas/_MODULE_AUTO.md`. |

## Инварианты, которые агент не должен ломать

- **Слои не пропускаются**: `api → service/repo → orm`. Роут никогда не держит бизнес-логику.
- **Один engine** (`app/db/`) — ни одна фича не строит собственный `sessionmaker`.
- **`BaseRepo`** — сессия на запрос; **`BaseSessionmakerRepo`** — сессия на вызов (нужно для
  двухфазного коммита / оптимистичной блокировки движка флоу). Не схлопывайте их в одно.
- **`StepResultDTO.output`** — только плоские JSON-примитивы: вложенная структура кодируется в JSON
  в один строковый ключ, никогда не передаётся как сырой `dict`/`list`.
- Узлы с денежным/побочным эффектом вызывают `ctx.deps.guard.check_and_set(...)` до эффекта —
  история crash-resume воркера зависит от того, что каждый такой узел это делает.
- HTTP — только `POST`/`GET`; хендлер бросает типизированный `AppError`, никогда не собирает ответ
  вручную.
