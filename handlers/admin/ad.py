import asyncio

import asyncpg
import structlog
from aiogram import Bot, types
from aiogram.exceptions import TelegramAPIError, TelegramRetryAfter
from aiogram.fsm.context import FSMContext

from states.advertisement import Advertisement
from db.db_api.users import UsersRepo


async def start_ad(msg: types.Message, state: FSMContext):
    await msg.answer("Reklamangizni yuboring (bekor qilish: /cancel):")
    await state.set_state(Advertisement.ad)


async def cancel_ad(msg: types.Message, state: FSMContext):
    await state.clear()
    await msg.answer("Reklama bekor qilindi.")


async def send_ad(
    msg: types.Message,
    state: FSMContext,
    bot: Bot,
    db_pool: asyncpg.Pool,
    business_logger: structlog.typing.FilteringBoundLogger,
    db_logger_init: dict,
):
    await state.clear()  # before the loop, so a second message isn't broadcast too
    users = await UsersRepo(db_pool, business_logger, db_logger_init).all_users()
    await msg.answer(f"Reklama {len(users)} ta foydalanuvchiga yuborilmoqda...")

    sent = 0
    for user in users:
        if await _copy(bot, msg, user.telegram_id, business_logger):
            sent += 1
        await asyncio.sleep(0.05)  # ~20 msg/s, under Telegram's 30 msg/s limit

    await msg.answer(
        f"Reklama foydalanuvchilarga yuborildi!\n"
        f"✅ Yuborildi: {sent}\n❌ Yuborilmadi: {len(users) - sent}"
    )


async def _copy(
    bot: Bot,
    msg: types.Message,
    chat_id: int,
    logger: structlog.typing.FilteringBoundLogger,
) -> bool:
    # copy_message handles any content type and keeps formatting/buttons
    try:
        await bot.copy_message(
            chat_id, msg.chat.id, msg.message_id, reply_markup=msg.reply_markup
        )
    except TelegramRetryAfter as e:
        await asyncio.sleep(e.retry_after)
        return await _copy(bot, msg, chat_id, logger)
    except TelegramAPIError as e:  # blocked the bot, deleted account, etc.
        logger.debug("Ad not delivered", chat_id=chat_id, error=str(e))
        return False
    return True
