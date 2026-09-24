import structlog
from aiogram import Bot
from aiogram.exceptions import TelegramAPIError

from data import config


async def notify_admins(bot: Bot, text: str, logger: structlog.typing.FilteringBoundLogger):
    for admin in config.ADMINS:
        try:
            await bot.send_message(admin, text)
        except TelegramAPIError as e:  # admin hasn't started the bot or blocked it
            logger.warning("Can't notify admin", admin=admin, error=str(e))
