import itertools
import time
from contextlib import asynccontextmanager

import structlog
from aiogram import Bot, Dispatcher
from aiogram.client.default import DefaultBotProperties
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.types import Update, User
from redis.asyncio import ConnectionPool

from bot import setup_handlers, setup_middlewares

ADMIN = 1
USER = 100
DATE = 1700000000  # date=0 would make aiogram treat the message as inaccessible
_ids = itertools.count(1)


class FakeBot(Bot):
    """Records every API call; `responses[MethodName]` is a value, an exception or a callable(method)."""

    def __init__(self, *args, **kwargs):
        super().__init__("42:TEST", default=DefaultBotProperties(parse_mode="HTML"))
        self.calls: list = []
        self.responses: dict = {"GetMe": User(id=42, is_bot=True, first_name="Bot", username="testbot")}

    async def __call__(self, method, request_timeout=None):
        self.calls.append(method)
        result = self.responses.get(type(method).__name__, True)
        if callable(result) and not isinstance(result, type):
            result = result(method)
        if isinstance(result, Exception):
            raise result
        return result

    def sent(self, name: str) -> list:
        return [c for c in self.calls if type(c).__name__ == name]


class FakeRedis:
    """Only the INCR + PEXPIRE NX pipeline ThrottlingMiddleware uses; commands run eagerly, execute() returns their results."""

    def __init__(self):
        self.data: dict = {}  # key -> [value, expires_at | None]
        self.results: list = []

    def _alive(self, key) -> bool:
        item = self.data.get(key)
        if item and item[1] is not None and time.monotonic() >= item[1]:
            del self.data[key]
        return key in self.data

    def pipeline(self, transaction=True):
        self.results = []
        return self

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return None

    def incr(self, key):
        if not self._alive(key):
            self.data[key] = [0, None]
        self.data[key][0] += 1
        self.results.append(self.data[key][0])

    def pexpire(self, key, ms, nx=False):
        item = self.data[key]
        if nx and item[1] is not None:
            self.results.append(0)
            return
        item[1] = time.monotonic() + ms / 1000
        self.results.append(1)

    async def execute(self) -> list:
        return self.results

    def ttl(self, key) -> float:
        return self.data[key][1] - time.monotonic()


class FakeDB:
    """asyncpg pool stand-in for the users table."""

    def __init__(self, users: list[dict] | None = None, error: Exception | None = None):
        self.users = users or []
        self.error = error
        self.queries: list = []

    @asynccontextmanager
    async def acquire(self):
        yield self

    async def fetchrow(self, sql, *args):
        self.queries.append((sql, args))
        if self.error:
            raise self.error
        if sql.startswith("INSERT INTO users"):
            full_name, username, telegram_id = args
            if any(u["telegram_id"] == telegram_id for u in self.users):
                return None
            row = {"id": len(self.users) + 1, "full_name": full_name, "username": username, "telegram_id": telegram_id}
            self.users.append(row)
            return row
        if "COUNT(*)" in sql:
            return {"count": len(self.users)}
        raise AssertionError(sql)

    async def fetch(self, sql, *args):
        self.queries.append((sql, args))
        if self.error:
            raise self.error
        return list(self.users)


def user_row(telegram_id: int) -> dict:
    return {"id": telegram_id, "full_name": f"User {telegram_id}", "username": None, "telegram_id": telegram_id}


def make_dp(db: FakeDB | None = None, rate_ms: int = 0) -> tuple[Dispatcher, FakeRedis]:
    """Dispatcher wired like bot.py: real routers and middlewares, fake Redis/Postgres."""
    dp = Dispatcher(storage=MemoryStorage())
    logger = structlog.get_logger()
    dp["business_logger"] = dp["aiogram_logger"] = logger
    dp["aiogram_logger_init"], dp["db_logger_init"] = {}, {}
    dp["db_pool"] = db or FakeDB()
    dp["cache_pool"] = ConnectionPool()
    setup_handlers(dp)
    setup_middlewares(dp)
    redis = FakeRedis()
    for mw in dp.message.outer_middleware:  # the same instance also throttles callback queries
        if hasattr(mw, "redis"):
            mw.redis = redis
            mw.rate_ms = rate_ms
    return dp, redis


def tg_user(uid: int) -> dict:
    return {"id": uid, "is_bot": False, "first_name": f"User{uid}", "username": f"u{uid}"}


def message(text: str | None = "hi", uid: int = USER, chat_type: str = "private", **extra) -> dict:
    msg = {"message_id": next(_ids), "date": DATE, "chat": {"id": uid if chat_type == "private" else -5, "type": chat_type}, "from": tg_user(uid)}
    if text is not None:
        msg["text"] = text
    msg.update(extra)
    return msg


def bot_message(uid: int = USER, **extra) -> dict:
    return {"message_id": next(_ids), "date": DATE, "chat": {"id": uid, "type": "private"}, "from": tg_user(42), **extra}


def callback(data: str, uid: int = USER, msg: dict | None = None) -> dict:
    return {"id": str(next(_ids)), "from": tg_user(uid), "chat_instance": "ci", "data": data, "message": msg if msg is not None else bot_message(uid, text="menu")}


def update(bot: Bot, **payload) -> Update:
    return Update.model_validate({"update_id": next(_ids), **payload}, context={"bot": bot})


PHOTO = [{"file_id": "f", "file_unique_id": "u", "width": 1, "height": 1}]
