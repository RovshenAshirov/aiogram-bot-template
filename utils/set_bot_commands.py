import structlog
from aiogram import Bot
from aiogram.exceptions import TelegramBadRequest
from aiogram.types import BotCommand, BotCommandScopeChat

from data import config

USER_COMMANDS = [
    BotCommand(command="start", description="Botni ishga tushirish"),
    BotCommand(command="help", description="Yordam"),
]
ADMIN_COMMANDS = USER_COMMANDS + [
    BotCommand(command="ad", description="Reklama yuborish"),
    BotCommand(command="stats", description="Foydalanuvchilar sonini ko'rish"),
]


async def set_bot_commands(bot: Bot, logger: structlog.typing.FilteringBoundLogger):
    await bot.set_my_commands(USER_COMMANDS)
    for admin in config.ADMINS:
        try:
            await bot.set_my_commands(ADMIN_COMMANDS, scope=BotCommandScopeChat(chat_id=admin))
        except TelegramBadRequest as e:  # admin hasn't started the bot yet: "chat not found"
            logger.warning("Can't set admin commands", admin=admin, error=str(e))
