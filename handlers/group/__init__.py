from aiogram import Router

from filters.chat_type import ChatTypeFilter


def prepare_router():
    group_router = Router()
    group_router.message.filter(ChatTypeFilter(["group", "supergroup"]))

    return group_router
