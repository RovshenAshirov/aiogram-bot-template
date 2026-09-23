import asyncpg
import structlog
from aiogram import html, types

from db.db_api.users import UsersRepo


async def start(
    msg: types.Message,
    db_pool: asyncpg.Pool,
    business_logger: structlog.typing.FilteringBoundLogger,
    db_logger_init: dict,
):
    user = msg.from_user
    if user is None:  # channel posts / anonymous admins
        return
    await UsersRepo(db_pool, business_logger, db_logger_init).add_user(
        user.id, user.full_name, user.username
    )
    m = [f'Hello, <a href="tg://user?id={user.id}">{html.quote(user.full_name)}</a>']
    await msg.answer("\n".join(m))
