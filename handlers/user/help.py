from aiogram import types

from data import config


async def bot_help(msg: types.Message):
    if msg.from_user and msg.from_user.id in config.ADMINS:
        text: tuple[str, ...] = (
            "Admin uchun buyruqlar: ",
            "/start - Botni ishga tushirish",
            "/help - Yordam",
            "/ad - Reklama yuborish",
            "/stats - Foydalanuvchilar sonini ko'rish",
        )
    else:
        text = ("Buyruqlar: ", "/start - Botni ishga tushirish", "/help - Yordam")
    await msg.answer("\n".join(text))
