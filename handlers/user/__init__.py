from aiogram import Router
from aiogram.filters import Command, CommandStart

from filters.chat_type import ChatTypeFilter
from . import start, help


def prepare_router():
    user_router = Router()
    user_router.message.filter(ChatTypeFilter("private"))

    user_router.message.register(start.start, CommandStart())
    user_router.message.register(help.bot_help, Command("help"))

    return user_router
