# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

Template for scalable Telegram bots on **aiogram 3.x** (pydantic v2). Python ≥3.14 (`requires-python`), uv. Out of the box: `/start` (saves the user), `/help`, admin `/ad` (broadcast any message type) and `/stats` (user count), throttling, per-admin command menus and a startup notice to admins.

## Commands

```bash
uv sync --group dev         # dev group is opt-in (default-groups = [])
uv run python bot.py        # run (needs env vars / .env, see below)
uv run ruff check .         # lint (E, F, B; E501 ignored)
uv run black .              # format (don't run over existing code, see Rules)
uv run mypy                 # type-check; entry is bot.py, follows its imports
uv run python -m unittest discover -s tests -t .   # test suite (stdlib unittest)
```

Tests need no Postgres, Redis or network: `tests/__init__.py` sets fake env vars (limit discovery to `tests/`, otherwise packages importing `data.config` load the real `.env` first), and `tests/helpers.py` has `FakeBot` (records API calls, `responses[MethodName]` sets results), `FakeRedis` (only the throttling pipeline), `FakeDB` and `make_dp()`, which wires the real routers and middlewares like `bot.py`. Handler tests feed updates through that dispatcher. Quick sanity check after dependency changes: `uv run python -c "import bot"` (needs a complete `.env`). Local Postgres and Redis are needed to run the bot.

## Configuration

`data/config.py` reads all settings from env / `.env` (template: `example.env`) via `environs` at import time — every variable is required (no defaults). Required: `BOT_TOKEN`, `LOGGING_LEVEL`, `ADMINS` (comma-separated ids; `ADMINS[0]` gets new-user notifications from `/start`), `PG_*` (Postgres), `FSM_*` (Redis for FSM storage), `CACHE_*` (separate Redis for cache), `USE_WEBHOOK`, `USE_CUSTOM_API_SERVER`. `CHANNELS` is commented out in `config.py` until a feature needs it. Webhook-only vars (`MAIN_WEBHOOK_*`, `MAX_UPDATES_IN_QUEUE`) and custom-API-server vars (`CUSTOM_API_SERVER_*`) are only defined when their flag is true — referencing them otherwise raises `AttributeError`.

`PG_HOST` may be a unix-socket directory (e.g. `/var/run/postgresql`) for local peer auth with an empty `PG_PASSWORD`; empty Redis passwords are also accepted.

## Architecture

- **`bot.py`** is the composition root. `main()` builds the `Bot` (HTML parse mode) and a `Dispatcher` with `RedisStorage` for FSM, runs `setup_aiogram` (logging → DB connections → routers → middlewares), then `set_bot_commands` (per-admin command menus) and `notify_admins`, then starts long polling or an aiohttp webhook server depending on `USE_WEBHOOK`. The `users` table is created at startup (`CREATE TABLE IF NOT EXISTS` in `db/db_api/users.py`); there are no migrations.
- **Dependency injection via dispatcher workflow data**: shared resources are stored as `dp["key"]` and aiogram injects them into handlers by parameter name. Available keys: `db_pool` (asyncpg pool), `cache_pool` (redis `ConnectionPool`), `business_logger`/`aiogram_logger` (structlog) plus their `*_init` dicts, `db_logger_init`, `temp_bot_cloud_session`/`temp_bot_local_session`. Add new shared resources here, not as globals.
- **Handlers**: each package under `handlers/` exposes `prepare_router()` in its `__init__.py` that builds a `Router`, applies router-level filters, and registers handler functions explicitly (`router.message.register(...)`) rather than via decorators. New routers must be included in `setup_handlers` in `bot.py`; order is `admin` (private chats + `config.ADMINS`) → `user` (private chats) → `group` (groups/supergroups) → `channel` (empty; channel posts arrive as `channel_post`) → `error`.
- **Middlewares** (wired in `setup_middlewares`):
  - `StructLoggingMiddleware` — outer on `dp.update`; logs received (DEBUG, includes user-typed text) and handled/unhandled/failed (INFO, no user text) with timing, and returns the handler result.
  - `ThrottlingMiddleware` — outer on `dp.message` and `dp.callback_query`; drops a user's updates closer than 100 ms apart (Redis `INCR` + `PEXPIRE NX`, needs Redis ≥ 7.0), warns once per window.
- **Errors**: `handlers/error` router logs every unhandled exception; for non-Telegram-API errors it also tells the user "Xatolik yuz berdi" (message reply or callback alert). Telegram API errors are logged as warnings only.
- **Admin**: `/ad` sets `Advertisement.ad` state; the next message that isn't a command is `copy_message`d to every user (other commands like `/help` still work meanwhile) (~20 msg/s, retries on `RetryAfter`), `/cancel` aborts. `/stats` shows the user count.
- **DB layer** (`db/db_api/`): `PostgresConnection` (`postgresql.py`) provides `_fetch`/`_fetchrow`/`_execute` over the asyncpg pool, mapping rows into model classes via `model(**record)`. Subclass it with domain-specific query methods (`UsersRepo`). Errors are logged and re-raised, never swallowed, so `None`/`[]` always mean "no rows". `db/cache/` is an empty placeholder.
- **Webhook mode**: `web_handlers/tg_updates.py` is an aiohttp sub-app mounted at `/tg/webhooks/`, route `/bot/{token}`. It verifies the secret-token header and the path token, then spawns update processing on a shared `aiojobs.Scheduler` (bounded by `MAX_UPDATES_IN_QUEUE`, returns 429 when full). Shutdown calls `scheduler.wait_and_close()` before closing the bot session. New HTTP endpoints go into the `subapps` list in `setup_aiohttp_app`.
- **Models**: `models/base.py` — project-wide pydantic v2 `BaseModel` base (empty hook for shared `model_config`); also holds `orjson_dumps` used by the JSON log renderer (`utils/log.py` — deliberately not `logging.py`, which shadowed the stdlib module when running files under `utils/` directly). `models/user.py` — `User` row model.
- **Keyboards**: `keyboards/` holds empty packages (`default/`, `inline/`, `inline/callbacks/`, `keyboard_utils/`). Use plain aiogram `InlineKeyboardMarkup` / `InlineKeyboardButton`, one module-level `<name>_kb` per file (a function when the keyboard depends on arguments). Callback data is client-controlled, so handlers validate it before use.

User-facing bot text is in Uzbek.

## Rules

- Write code comments (including in config files like `example.env`) in English.
- Comment only what isn't obvious from the code; no redundant or explanatory-filler comments.
- Commit directly to the current branch (including `master`); never create a separate branch for a commit.
- Code is English: identifiers, callback data, state names, file names. Only text shown to bot users (messages, captions, button labels) is Uzbek.
- One keyboard per file (`keyboards/inline/<name>.py` → `<name>_kb`), one `StatesGroup` per file (`states/<name>.py`, imported as `from states.<name> import <Name>`) and one `CallbackData` factory per file (`keyboards/inline/callbacks/<name>.py`).
- No re-exports in `__init__.py`. Import from the defining module and only the names you use (`from filters.chat_type import ChatTypeFilter`, `from handlers.user import prepare_router as prepare_user_router`), not a package or whole module. Exception: each `handlers/<name>/__init__.py` holds that router's `prepare_router()` and imports its sibling handler modules whole (`from . import start, help`) to register `start.start`, `help.bot_help`, etc. — it shows where each handler lives and avoids name clashes between modules.
- Don't reformat existing code. Leave lines you aren't changing exactly as they are; never split a working one-line statement into several lines (or vice versa) for style. Long single lines are fine (E501 is ignored), and new code follows the same one-line style; don't run `black` over files you touch.
