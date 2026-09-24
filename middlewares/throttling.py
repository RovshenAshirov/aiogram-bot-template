from typing import Any, Awaitable, Callable

from aiogram import BaseMiddleware
from aiogram.types import CallbackQuery, Message, TelegramObject
from redis.asyncio import ConnectionPool, Redis

THROTTLED_TEXT = "⏳ Juda tez! Iltimos, biroz kuting."


class ThrottlingMiddleware(BaseMiddleware):
    """Drops a user's updates that come faster than `rate_ms` apart."""

    def __init__(self, cache_pool: ConnectionPool, rate_ms: int = 100):
        self.redis = Redis(connection_pool=cache_pool)
        self.rate_ms = rate_ms

    async def __call__(self, handler: Callable[[TelegramObject, dict[str, Any]], Awaitable[Any]], event: TelegramObject, data: dict[str, Any]) -> Any:
        user = data.get("event_from_user")
        if user is None:
            return await handler(event, data)
        key = f"throttle:{user.id}"
        # INCR + PEXPIRE NX in one transaction: a TTL is always set, even if the key expired just before (Redis >= 7.0)
        async with self.redis.pipeline(transaction=True) as pipe:
            pipe.incr(key)  # buffered, not awaited: redis-py types pipeline commands as Awaitable
            pipe.pexpire(key, self.rate_ms, nx=True)
            count, _ = await pipe.execute()
        if count == 1:
            return await handler(event, data)
        warn = count == 2  # warn once per window
        if isinstance(event, CallbackQuery):
            await event.answer(THROTTLED_TEXT if warn else None)
        elif isinstance(event, Message) and warn:
            await event.reply(THROTTLED_TEXT)
        return None
