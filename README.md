<p align="right"><a href="README.en.md">English</a> · <b>Русский</b></p>

# auto-lzt

Серверный движок no-code автоматизаций для [lzt.market](https://lzt.market). Задача описывается графом узлов, а не скриптом: «искать Steam дешевле 500 и покупать», «поднимать лоты каждые 4 часа».

**Репозиторий называется `auto-lzt`, а пакет, CLI и документация — `lzt-flow`.** Это следы переименования, не две разные вещи.

```bash
uv sync --extra dev
uv run python dev.py --demo
```

## Разработка без Docker

`dev.py` поднимает всё на SQLite, fakeredis и мок-маркете — ни токена, ни базы, ни реальных денег.

```bash
uv sync --extra dev
uv run python dev.py            # API на 127.0.0.1:8000
uv run python dev.py --demo     # то же плюс демо-данные
bash scripts/smoke.sh           # сквозная проверка: создать → скомпилировать → запустить
pnpm --dir frontend dev         # холст флоу, отдельный процесс
```

## Прод

```bash
scripts/install.sh    # Docker + compose, .env из .env.example, миграции, запуск
scripts/update.sh     # подтянуть, мигрировать, перезапустить
scripts/backup.sh     # дамп БД
scripts/restore.sh    # накатить дамп обратно
```

`LZT_FLOW_MASTER_KEY` обязателен для процесса API — пустое значение отклоняется на старте, а не всплывает при первом обращении к токену.

```bash
LZT_FLOW_MASTER_KEY=$(python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())")
```

## CLI

Пакет ставит две команды: `lzt-flow` для операторских задач и `lzt-flow-validate` для проверки модуля.

Глобальные флаги идут **до** подкоманды:

```bash
lzt-flow --api http://127.0.0.1:8000 --json list
```

| Флаг | Что делает |
|---|---|
| `--api URL` | адрес API, по умолчанию `http://127.0.0.1:8000` |
| `--api-key KEY` | ключ, если не задан в окружении — виден в `ps` и в истории |
| `--env-file PATH` | откуда читать `.env`, по умолчанию `.env` |
| `--json` | машинночитаемый вывод |

| Команда | Что делает |
|---|---|
| `status` | здоровье сервисов и режим маркета |
| `modules` | доступные модули из каталога |
| `list` | флоу: id, имя, скомпилирован ли |
| `install <MODULE> [--param K=V] [--account ID]` | создать флоу из модуля |
| `params <FLOW_ID>` | объявленные параметры флоу |
| `run <FLOW_ID> [--param K=V] [--watch] [--no-dry-run]` | запустить; `dry_run` включён по умолчанию |
| `runs [--flow ID]` | недавние запуски |
| `trace <RUN_ID\|FLOW_ID>` | трейс по узлам |
| `accounts [add --token --label]` | список аккаунтов или добавление |

`run` без `--no-dry-run` ничего не покупает. Выключайте холостой прогон, когда посмотрели трейс.

## REST API

| Префикс | О чём |
|---|---|
| `/flows` | создание, обновление, компиляция, запуск, триггеры, экспорт |
| `/runs` | статус запуска, трейс, поток событий |
| `/accounts` | токены маркета |
| `/modules` · `/plugins` | каталог модулей и плагинов, установка |
| `/catalog` | доступные типы узлов и динамические методы pylzt |
| `/tasks` | расписания |
| `/panel` · `/auth` | веб-панель |

Полный цикл целиком:

```bash
API=http://127.0.0.1:8000

FLOW=$(curl -sX POST $API/flows/create -H 'Content-Type: application/json' -d '{
  "name": "demo",
  "entry_node_id": "search",
  "nodes": [{"id":"search","type":"market.search","inputs":{"category":{"literal":"steam"}}}]
}' | jq -r .flow_id)

curl -sX POST $API/flows/$FLOW/compile
RUN=$(curl -sX POST $API/runs/create -H 'Content-Type: application/json' \
  -d "{\"flow_id\":\"$FLOW\",\"params\":{}}" | jq -r .run_id)

curl -s $API/runs/$RUN/get
curl -s $API/runs/$RUN/trace
```

Повесить расписание:

```bash
curl -sX POST $API/flows/$FLOW/triggers/create -H 'Content-Type: application/json' \
  -d '{"kind":"schedule","schedule_cron":"*/30 * * * *"}'
```

Ошибки приходят конвертом `{code, message, request_id}` — `request_id` ищите в логах.

## Готовые флоу и плагины

| Каталог | Что там | Кто публикует |
|---|---|---|
| [lzt-flows](https://github.com/open-lzt/lzt-flows) | модули-графы, это данные | любой автор через PR |
| [lzt-plugins](https://github.com/open-lzt/lzt-plugins) | исполняемый `.py`, работает в процессе движка с вашими токенами, без песочницы | только владелец стенда, из меню бота |

## Конфигурация

Префикс `LZT_FLOW_`. Главное:

| Переменная | По умолчанию | Что это |
|---|---|---|
| `LZT_FLOW_MASTER_KEY` | — | Fernet-ключ шифрования токенов, обязателен |
| `LZT_FLOW_DATABASE_URL` | `postgresql+asyncpg://lzt:lzt@localhost:5432/lztflow` | Postgres |
| `LZT_FLOW_REDIS_URL` | `redis://localhost:6379/0` | Redis для очереди |
| `LZT_FLOW_API_KEY` | пусто | общий секрет для `X-API-Key`; пусто — проверка выключена |
| `LZT_FLOW_EGRESS_ALLOWED_HOSTS` | пусто | куда узлам-запросам можно ходить; пусто означает никуда |
| `LZT_FLOW_DEFAULT_TENANT_ID` | `00000000-…-0001` | арендатор по умолчанию |
| `LZT_FLOW_BOT_ENABLED` | `0` | Telegram-бот управления |
| `LZT_FLOW_BOT_TOKEN` · `LZT_FLOW_BOT_ADMIN_IDS` | — | токен от @BotFather и id администраторов |
| `LZT_FLOW_PLUGIN_DIR` | `.system/plugins` | куда распаковываются плагины |
| `LZT_FLOW_PLUGIN_INDEX_URL` | каталог `lzt-plugins` | откуда берётся список плагинов |
| `LZT_FLOW_WORKER_ID` | `worker-1` | имя воркера |

Пустой `LZT_FLOW_EGRESS_ALLOWED_HOSTS` закрыт, а не открыт: узел-запрос без списка не сходит никуда.

Движок событий настраивается своими переменными с префиксом `LZT_` — они отдельные, `LZT_TOKEN_ENC_KEY` не то же самое, что `LZT_FLOW_MASTER_KEY`. Полный список — `.env.example`.

## Разработка

```bash
uv run ruff check .
uv run mypy app --strict
uv run pytest -q          # e2e и live исключены по умолчанию
uv run pytest -m e2e
```

Документация: [ARCHITECTURE.md](ARCHITECTURE.md) · [дизайн флоу](docs/flow-design-guide.md) · [модули](docs/modules.md) · [плагины](docs/plugins.md) · [ранбуки](docs/runbooks/README.md) · [для AI-агентов](docs/for_ai/)

## Экосистема

[pylzt](https://github.com/open-lzt/pylzt) — SDK маркета · [lzt-eventus](https://github.com/open-lzt/lzt-eventus) — события · [lzt-testnet](https://github.com/open-lzt/lzt-testnet) — мок-маркет · [lzt-mcp](https://github.com/open-lzt/lzt-mcp) — сервер для AI-агентов · [весь стенд](https://github.com/open-lzt/open-lzt)

## Лицензия

[MIT](LICENSE)
