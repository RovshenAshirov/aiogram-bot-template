# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

Template for scalable Telegram bots on **aiogram 3.0.0b7** (beta — APIs like `aiogram.filters.Text` and `Bot.set_current` differ from aiogram 3 stable). Python 3.10, uv.

## Commands

```bash
uv sync --group dev         # dev group is opt-in (default-groups = [])
uv run python bot.py        # run (needs env vars / .env, see below)
uv run ruff .               # lint (E, F, B; E501 ignored)
uv run black .              # format
uv run mypy                 # type-check (configured to check bot.py only)
```

There is no test suite. Quick sanity check after dependency changes: `uv run python -c "import bot"` (needs a complete `.env`).

Dependencies are old (2023-era) and pinned only by range, so `uv lock --upgrade` can pull incompatible transitive versions. Known case: `environs` 9.x breaks on marshmallow 4, hence `constraint-dependencies = ["marshmallow<4"]` in `[tool.uv]`. Add similar constraints there rather than bumping direct deps casually.

## Configuration

`data/config.py` reads all settings from env / `.env` via `environs` at import time — every variable is required (no defaults). Required: `BOT_TOKEN`, `LOGGING_LEVEL`, `PG_*` (Postgres), `FSM_*` (Redis for FSM storage), `CACHE_*` (separate Redis for cache), `USE_WEBHOOK`, `USE_CUSTOM_API_SERVER`. Webhook-only vars (`MAIN_WEBHOOK_*`, `MAX_UPDATES_IN_QUEUE`) and custom-API-server vars (`CUSTOM_API_SERVER_*`) are only defined when their flag is true — referencing them otherwise raises `AttributeError`.

`PG_HOST` may be a unix-socket directory (e.g. `/var/run/postgresql`) for local peer auth with an empty `PG_PASSWORD`; empty Redis passwords are also accepted.

## Architecture

- **`bot.py`** is the composition root. It builds the `Bot`, a `Dispatcher` with `RedisStorage` for FSM, then `setup_aiogram` wires logging → DB connections → routers → middlewares. Runs either long polling or an aiohttp webhook server depending on `USE_WEBHOOK`.
- **Dependency injection via dispatcher workflow data**: shared resources are stored as `dp["key"]` and aiogram injects them into handlers by parameter name. Available keys: `db_pool` (asyncpg pool), `cache_pool` (redis `ConnectionPool`), `business_logger`/`aiogram_logger` (structlog) plus their `*_init` dicts, `db_logger_init`, `temp_bot_cloud_session`/`temp_bot_local_session`. Add new shared resources here, not as globals.
- **Handlers**: each package under `handlers/` exposes `prepare_router()` that builds a `Router`, applies router-level filters, and registers handler functions explicitly (`router.message.register(...)`) rather than via decorators. New routers must be included in `setup_handlers` in `bot.py`.
- **Webhook mode**: `web_handlers/tg_updates.py` is an aiohttp sub-app mounted at `/tg/webhooks/`, route `/bot/{token}`. It verifies the secret-token header and the path token, then spawns update processing on a shared `aiojobs.Scheduler` (bounded by `MAX_UPDATES_IN_QUEUE`, returns 429 when full). Shutdown drains the scheduler before closing the bot session. New HTTP endpoints go into the `subapps` list in `setup_aiohttp_app`.
- **Logging**: structlog everywhere. `StructLoggingMiddleware` (outer middleware on `dp.update`) binds update context and logs receive/handled with timing.
- **DB layer** (`db/db_api/storages/`): `RawConnection` defines `_fetch`/`_fetchrow`/`_execute`; `PostgresConnection` implements them over the asyncpg pool, mapping rows into model classes via `model(**record)`. Intended use: subclass `PostgresConnection` with domain-specific query methods. Errors are logged and swallowed (returns `[]`/`None`). `db/cache/` is an empty placeholder.
- **Models**: `models/base.py` — pydantic v1-style `BaseModel` (inner `Config`, orjson serialization).
- **Keyboards**: `DefaultConstructor` / `InlineConstructor` `_create_kb(actions, schema)` build markups where `schema` is a list of row lengths (sum must equal button count). `InlineConstructor` accepts `cb` as alias for `callback_data` and auto-packs `CallbackData` instances. Reusable reply keyboards live in `keyboards/default/basic.py` (`BasicButtons`).
- **States** in `states/`, **filters** in `filters/` (e.g. `ChatTypeFilter`), callback data factories in `keyboards/inline/callbacks.py`.

User-facing strings and error messages in the template are in Russian.
