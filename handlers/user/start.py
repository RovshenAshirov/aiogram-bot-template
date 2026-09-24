import asyncpg
import structlog
from aiogram import Bot, html, types
from aiogram.exceptions import TelegramAPIError

from data import config
from db.db_api.users import UsersRepo


async def start(
    msg: types.Message,
    bot: Bot,
    db_pool: asyncpg.Pool,
    business_logger: structlog.typing.FilteringBoundLogger,
    db_logger_init: dict,
):
    user = msg.from_user
    if user is None:  # channel posts / anonymous admins
        return
    repo = UsersRepo(db_pool, business_logger, db_logger_init)
    new_user = await repo.add_user(user.id, user.full_name, user.username)
    if new_user:
        count = await repo.count_users()
        try:
            await bot.send_message(
                config.ADMINS[0],
                f"{html.quote(new_user.full_name)} bazaga qo'shildi.\n"
                f"Bazada {count} ta foydalanuvchi bor.",
            )
        except TelegramAPIError as e:  # admin hasn't started the bot or blocked it
            business_logger.warning("Can't notify admin", admin=config.ADMINS[0], error=str(e))
    m = [f'Hello, <a href="tg://user?id={user.id}">{html.quote(user.full_name)}</a>']
    await msg.answer("\n".join(m))
