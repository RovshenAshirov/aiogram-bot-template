import asyncio
import unittest
from unittest import mock

import structlog
from aiogram.dispatcher.event.bases import UNHANDLED
from aiogram.types import User
from redis.asyncio import ConnectionPool
from structlog.testing import capture_logs

from tests.helpers import PHOTO, USER, FakeBot, FakeRedis, callback, message, tg_user, update
from filters.chat_type import ChatTypeFilter
from middlewares.logging import StructLoggingMiddleware
from middlewares.throttling import THROTTLED_TEXT, ThrottlingMiddleware

FROM = User(id=USER, is_bot=False, first_name="U")


class ThrottlingTest(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.bot = FakeBot()
        self.mw = ThrottlingMiddleware(ConnectionPool(), rate_ms=50)
        self.mw.redis = FakeRedis()
        self.handler = mock.AsyncMock(return_value="ok")

    async def call(self, event, user=FROM):
        return await self.mw(self.handler, event, {"event_from_user": user})

    def msg(self, **extra):
        return update(self.bot, message=message(**extra)).message

    async def test_message_throttling_window(self):
        self.assertEqual(await self.call(self.msg()), "ok")
        self.assertIsNone(await self.call(self.msg()))  # too fast: dropped and warned
        self.assertIsNone(await self.call(self.msg()))  # dropped silently, already warned
        self.assertEqual(self.handler.await_count, 1)
        self.assertEqual([m.text for m in self.bot.sent("SendMessage")], [THROTTLED_TEXT])
        self.assertIsNotNone(self.bot.sent("SendMessage")[0].reply_parameters)
        await asyncio.sleep(0.06)
        self.assertEqual(await self.call(self.msg()), "ok")

    async def test_key_always_expires(self):
        await self.call(self.msg())
        await self.call(self.msg())
        self.assertLessEqual(self.mw.redis.ttl(f"throttle:{USER}"), 0.05)  # later hits don't extend the window

    async def test_callback_throttling(self):
        cb = lambda: update(self.bot, callback_query=callback("x")).callback_query  # noqa: E731
        await self.call(cb())
        await self.call(cb())
        await self.call(cb())
        self.assertEqual([m.text for m in self.bot.sent("AnswerCallbackQuery")], [THROTTLED_TEXT, None])  # always answered, so the button stops spinning
        self.assertEqual(self.handler.await_count, 1)

    async def test_no_user_and_different_users(self):
        self.assertEqual(await self.call(self.msg(), user=None), "ok")
        self.assertEqual(await self.call(self.msg(), user=None), "ok")
        self.assertEqual(await self.call(self.msg()), "ok")
        self.assertEqual(await self.call(self.msg(), user=User(id=7, is_bot=False, first_name="B")), "ok")


class LoggingTest(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.bot = FakeBot()
        self.mw = StructLoggingMiddleware(structlog.get_logger(), {})

    async def run_mw(self, upd, result="ok", error=None):
        handler = mock.AsyncMock(return_value=result, side_effect=error)
        with capture_logs() as logs:
            returned = await self.mw(handler, upd, {})
        return returned, logs

    async def test_message(self):
        upd = update(self.bot, message=message("secret", caption=None))
        result, logs = await self.run_mw(upd)
        self.assertEqual(result, "ok")
        received, handled = logs
        self.assertEqual((received["event"], received["log_level"], received["text"], received["user_id"]), ("Received message", "debug", "secret", USER))
        self.assertEqual((handled["event"], handled["log_level"], handled["process_result"]), ("Handled message", "info", True))
        self.assertNotIn("text", handled)  # user text only at DEBUG
        self.assertIsInstance(handled["spent_time_ms"], float)

    async def test_media_message_and_no_sender(self):
        msg = message(None, caption="cap", photo=PHOTO, video={"file_id": "v", "file_unique_id": "vu", "width": 1, "height": 1, "duration": 1})
        del msg["from"]
        _, logs = await self.run_mw(update(self.bot, message=msg))
        self.assertEqual((logs[0]["caption"], logs[0]["photo_id"], logs[0]["video_id"]), ("cap", "f", "v"))
        self.assertNotIn("user_id", logs[0])

    async def test_unhandled(self):
        result, logs = await self.run_mw(update(self.bot, message=message()), result=UNHANDLED)
        self.assertIs(result, UNHANDLED)
        self.assertEqual((logs[1]["event"], logs[1]["process_result"]), ("Unhandled message", False))

    async def test_failure_log(self):
        handler = mock.AsyncMock(side_effect=ValueError("x"))
        with capture_logs() as logs, self.assertRaises(ValueError):
            await self.mw(handler, update(self.bot, message=message()), {})
        self.assertEqual((logs[-1]["event"], logs[-1]["log_level"], logs[-1]["process_result"]), ("Failed to handle update", "warning", False))

    async def test_other_update_types(self):
        member = {"user": tg_user(USER), "status": "member"}
        left = {"user": tg_user(USER), "status": "left"}
        chat_member = {"chat": {"id": -5, "type": "group"}, "from": tg_user(USER), "date": 1, "old_chat_member": left, "new_chat_member": member}
        cases = [
            ({"callback_query": callback("menu")}, "callback query", {"callback_data": "menu"}),
            ({"callback_query": callback("x", msg={"chat": {"id": USER, "type": "private"}, "message_id": 1, "date": 0})}, "callback query", {"message_id": 1}),
            ({"inline_query": {"id": "q", "from": tg_user(USER), "query": "hello", "offset": ""}}, "inline query", {"query": "hello"}),
            ({"my_chat_member": chat_member}, "my chat member update", {"chat_id": -5}),
            ({"chat_member": chat_member}, "chat member update", {"chat_id": -5}),
        ]
        for payload, kind, fields in cases:
            with self.subTest(kind=kind):
                _, logs = await self.run_mw(update(self.bot, **payload))
                self.assertEqual(logs[0]["event"], f"Received {kind}")
                self.assertEqual(logs[0]["user_id"], USER)
                self.assertLessEqual(fields.items(), logs[0].items())
                self.assertEqual(logs[1]["event"], f"Handled {kind}")

    async def test_unknown_update_type_is_passed_through(self):
        upd = update(self.bot, edited_message=message())
        result, logs = await self.run_mw(upd)
        self.assertEqual((result, logs), ("ok", []))


class ChatTypeFilterTest(unittest.IsolatedAsyncioTestCase):
    async def test_filter(self):
        bot = FakeBot()
        private = update(bot, message=message()).message
        group = update(bot, message=message(chat_type="group")).message
        self.assertTrue(await ChatTypeFilter("private")(private))
        self.assertFalse(await ChatTypeFilter("private")(group))
        self.assertTrue(await ChatTypeFilter(["group", "supergroup"])(group))
        self.assertFalse(await ChatTypeFilter(["group", "supergroup"])(private))


if __name__ == "__main__":
    unittest.main()
