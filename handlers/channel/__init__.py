from aiogram import Router


def prepare_router():
    # channel posts arrive as channel_post / edited_channel_post, not message
    channel_router = Router()

    return channel_router
