from aiogram import types, html


async def start(msg: types.Message):
    user = msg.from_user
    if user is None:  # channel posts / anonymous admins
        return
    m = [f'Hello, <a href="tg://user?id={user.id}">{html.quote(user.full_name)}</a>']
    await msg.answer("\n".join(m))
