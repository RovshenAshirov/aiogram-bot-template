import structlog
from aiogram.exceptions import TelegramAPIError
from aiogram.types import ErrorEvent

ERROR_TEXT = "⚠️ Xatolik yuz berdi. Iltimos, birozdan keyin qayta urinib ko'ring."


async def error_handler(event: ErrorEvent, aiogram_logger: structlog.typing.FilteringBoundLogger):
    logger = aiogram_logger.bind(update_id=event.update.update_id, update=event.update.model_dump(exclude_none=True))
    if isinstance(event.exception, TelegramAPIError):  # "message is not modified", blocked by user, etc. — no traceback needed
        logger.warning("Telegram API error", error=str(event.exception))
    else:
        logger.exception("Unhandled error in handler", exc_info=event.exception)
        await _notify_user(event)
    return True


async def _notify_user(event: ErrorEvent):
    try:
        if event.update.message:
            await event.update.message.answer(ERROR_TEXT)
        elif event.update.callback_query:
            await event.update.callback_query.answer(ERROR_TEXT, show_alert=True)
    except TelegramAPIError:  # user blocked the bot, callback expired, etc.
        pass
