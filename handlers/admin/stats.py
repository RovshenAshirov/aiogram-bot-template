import asyncpg
import structlog
from aiogram import types

from db.db_api.users import UsersRepo


async def show_stats(
    msg: types.Message,
    db_pool: asyncpg.Pool,
    business_logger: structlog.typing.FilteringBoundLogger,
    db_logger_init: dict,
):
    count = await UsersRepo(db_pool, business_logger, db_logger_init).count_users()
    await msg.answer(f"Bot foydalanuvchilari soni:  {count}")
