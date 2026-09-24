import time
from typing import Any, Awaitable, Callable, cast

import structlog.typing
from aiogram import BaseMiddleware
from aiogram.dispatcher.event.bases import UNHANDLED
from aiogram.types import TelegramObject, Update

HANDLED_STR = ["Unhandled", "Handled"]


def _spent_ms(started: float) -> float:
    return round((time.time() - started) * 10000) / 10


class StructLoggingMiddleware(BaseMiddleware):
    def __init__(
        self, logger: structlog.typing.FilteringBoundLogger, logger_init_values: dict
    ):
        self.logger = logger
        self.logger_init_values = logger_init_values
        super(StructLoggingMiddleware, self).__init__()

    async def __call__(
        self,
        handler: Callable[[Update, dict[str, Any]], Awaitable[Any]],
        event: TelegramObject,
        data: dict[str, Any],
    ) -> Any:
        event = cast(Update, event)
        _started_processing_at = time.time()
        logger = self.logger.bind(update_id=event.update_id)
        if event.message:
            message = event.message
            logger = logger.bind(
                message_id=message.message_id,
                chat_type=message.chat.type,
                chat_id=message.chat.id,
            )
            if message.from_user is not None:
                logger = logger.bind(user_id=message.from_user.id)
            content: dict[str, Any] = {}  # user-typed, so DEBUG only
            if message.text:
                content.update(text=message.text, entities=message.entities)
            if message.caption:
                content.update(caption=message.caption, caption_entities=message.caption_entities)
            if message.video:
                logger = logger.bind(
                    video_id=message.video.file_id,
                    video_unique_id=message.video.file_unique_id,
                )
            if message.photo:
                logger = logger.bind(
                    photo_id=message.photo[-1].file_id,
                    photo_unique_id=message.photo[-1].file_unique_id,
                )
            logger.debug("Received message", **content)
        elif event.callback_query:
            c = event.callback_query
            logger = logger.bind(
                callback_query_id=c.id,
                callback_data=c.data,
                user_id=c.from_user.id,
                inline_message_id=c.inline_message_id,
                chat_instance=c.chat_instance,
            )
            if c.message is not None:
                logger = logger.bind(
                    message_id=c.message.message_id,
                    chat_type=c.message.chat.type,
                    chat_id=c.message.chat.id,
                )
            logger.debug("Received callback query")
        elif event.inline_query:
            query = event.inline_query
            logger = logger.bind(
                query_id=query.id,
                user_id=query.from_user.id,
                offset=query.offset,
                chat_type=query.chat_type,
                location=query.location,
            )
            logger.debug("Received inline query", query=query.query)
        elif event.my_chat_member:
            upd = event.my_chat_member
            logger = logger.bind(
                user_id=upd.from_user.id,
                chat_id=upd.chat.id,
                old_state=upd.old_chat_member,
                new_state=upd.new_chat_member,
            )
            logger.debug("Received my chat member update")
        elif event.chat_member:
            upd = event.chat_member
            logger = logger.bind(
                user_id=upd.from_user.id,
                chat_id=upd.chat.id,
                old_state=upd.old_chat_member,
                new_state=upd.new_chat_member,
            )
            logger.debug("Received chat member update")
        try:
            result = await handler(event, data)
        except Exception:
            # traceback is logged by handlers/errors; here only the update context and timing
            logger.warning("Failed to handle update", process_result=False, spent_time_ms=_spent_ms(_started_processing_at))
            raise
        handled = result is not UNHANDLED
        logger = logger.bind(
            process_result=handled,
            spent_time_ms=_spent_ms(_started_processing_at),
        )
        status = HANDLED_STR[handled]
        if event.message:
            logger.info(f"{status} message")
        elif event.callback_query:
            logger.info(f"{status} callback query")
        elif event.inline_query:
            logger.info(f"{status} inline query")
        elif event.my_chat_member:
            logger.info(f"{status} my chat member update")
        elif event.chat_member:
            logger.info(f"{status} chat member update")
        return result
