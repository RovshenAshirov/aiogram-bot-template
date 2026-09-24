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
        if await self.redis.set(key, 1, px=self.rate_ms, nx=True):  # SET NX PX is atomic, so the key can't outlive its TTL
            return await handler(event, data)
        warn = await self.redis.incr(key) == 2  # incr keeps the TTL; warn once per window
        if isinstance(event, CallbackQuery):
            await event.answer(THROTTLED_TEXT if warn else None)
        elif isinstance(event, Message) and warn:
            await event.reply(THROTTLED_TEXT)
        return None
