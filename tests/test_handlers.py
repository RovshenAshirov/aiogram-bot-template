import unittest
from unittest import mock

import asyncpg
import structlog
from aiogram.dispatcher.event.bases import UNHANDLED
from aiogram.exceptions import TelegramBadRequest, TelegramForbiddenError, TelegramRetryAfter
from aiogram.types import ErrorEvent
from structlog.testing import capture_logs

from tests.helpers import ADMIN, PHOTO, USER, FakeBot, FakeDB, callback, make_dp, message, update, user_row
from handlers.error.error_handler import ERROR_TEXT, error_handler
from handlers.user import start
from middlewares.throttling import THROTTLED_TEXT
from states.advertisement import Advertisement


class BotTestCase(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.bot = FakeBot()
        self.db = FakeDB()
        self.dp, self.redis = make_dp(self.db)

    async def feed(self, **payload):
        return await self.dp.feed_update(self.bot, update(self.bot, **payload))

    async def send(self, text: str | None = "hi", uid: int = USER, **extra):
        return await self.feed(message=message(text, uid, **extra))

    def fsm(self, uid: int = USER):
        return self.dp.fsm.get_context(self.bot, chat_id=uid, user_id=uid)

    def texts(self) -> list[str]:
        return [m.text for m in self.bot.sent("SendMessage")]


class StartTest(BotTestCase):
    async def test_new_user(self):
        await self.send("/start")
        notification, greeting = self.bot.sent("SendMessage")
        self.assertEqual((notification.chat_id, notification.text), (ADMIN, "User100 bazaga qo'shildi.\nBazada 1 ta foydalanuvchi bor."))
        self.assertEqual((greeting.chat_id, greeting.text), (USER, f'Hello, <a href="tg://user?id={USER}">User100</a>'))
        self.assertEqual(self.db.users[0]["telegram_id"], USER)

    async def test_existing_user_is_not_announced(self):
        self.db.users.append(user_row(USER))
        await self.send("/start")
        self.assertEqual(len(self.bot.sent("SendMessage")), 1)

    async def test_name_is_escaped(self):
        msg = message("/start")
        msg["from"]["first_name"] = "<b>Ali&"
        await self.feed(message=msg)
        notification, greeting = self.texts()
        self.assertTrue(notification.startswith("&lt;b&gt;Ali&amp; bazaga"))
        self.assertIn(">&lt;b&gt;Ali&amp;</a>", greeting)

    async def test_admin_notification_failure_is_logged(self):
        self.bot.responses["SendMessage"] = lambda m: TelegramForbiddenError(m, "blocked") if m.chat_id == ADMIN else True
        with capture_logs() as logs:
            await self.send("/start")
        self.assertEqual(len(self.bot.sent("SendMessage")), 2)  # the user is still greeted
        self.assertTrue(any(log["event"] == "Can't notify admin" for log in logs))

    async def test_ignored_outside_private_chats(self):
        self.assertIs(await self.send("/start", chat_type="group"), UNHANDLED)
        self.assertEqual((self.bot.calls, self.db.queries), ([], []))

    async def test_message_without_sender(self):
        msg = message("/start")
        del msg["from"]
        await start.start(update(self.bot, message=msg).message, self.bot, self.db, structlog.get_logger(), {})
        self.assertEqual((self.bot.calls, self.db.queries), ([], []))


class HelpTest(BotTestCase):
    async def test_user_help(self):
        await self.send("/help")
        self.assertEqual(self.texts(), ["Buyruqlar: \n/start - Botni ishga tushirish\n/help - Yordam"])

    async def test_admin_help(self):
        await self.send("/help", uid=ADMIN)
        self.assertIn("/ad - Reklama yuborish", self.texts()[0])
        self.assertIn("/stats", self.texts()[0])

    async def test_unknown_text_is_unhandled(self):
        self.assertIs(await self.send("salom"), UNHANDLED)
        self.assertEqual(self.bot.calls, [])

    async def test_throttling_is_wired(self):
        self.dp, _ = make_dp(self.db, rate_ms=1000)
        await self.send("/help")
        await self.send("/help")
        self.assertEqual(len(self.texts()), 2)
        self.assertEqual(self.texts()[1], THROTTLED_TEXT)


class AdminTest(BotTestCase):
    def setUp(self):
        super().setUp()
        self.db.users.extend([user_row(100), user_row(101), user_row(102)])

    async def test_stats(self):
        await self.send("/stats", uid=ADMIN)
        self.assertEqual(self.texts(), ["Bot foydalanuvchilari soni:  3"])

    async def test_admin_commands_are_admin_only_and_private_only(self):
        for text, uid, chat_type in (("/stats", USER, "private"), ("/ad", USER, "private"), ("/stats", ADMIN, "group"), ("/ad", ADMIN, "supergroup")):
            with self.subTest(text=text, uid=uid, chat_type=chat_type):
                self.assertIs(await self.send(text, uid=uid, chat_type=chat_type), UNHANDLED)
        self.assertEqual(self.bot.calls, [])

    async def test_cancel(self):
        await self.send("/ad", uid=ADMIN)
        self.assertEqual(await self.fsm(ADMIN).get_state(), Advertisement.ad.state)
        self.assertEqual(self.texts(), ["Reklamangizni yuboring (bekor qilish: /cancel):"])
        await self.send("/cancel", uid=ADMIN)
        self.assertIsNone(await self.fsm(ADMIN).get_state())
        self.assertEqual(self.texts()[-1], "Reklama bekor qilindi.")
        self.assertEqual(self.bot.sent("CopyMessage"), [])

    async def test_cancel_without_ad_is_unhandled(self):
        self.assertIs(await self.send("/cancel", uid=ADMIN), UNHANDLED)

    async def test_broadcast_text(self):
        self.bot.responses["CopyMessage"] = lambda m: TelegramForbiddenError(m, "bot was blocked") if m.chat_id == 101 else True
        await self.send("/ad", uid=ADMIN)
        kb = {"inline_keyboard": [[{"text": "Link", "url": "https://example.com"}]]}
        await self.send("Reklama!", uid=ADMIN, reply_markup=kb)
        copies = self.bot.sent("CopyMessage")
        self.assertEqual([c.chat_id for c in copies], [100, 101, 102])
        self.assertTrue(all(c.from_chat_id == ADMIN and c.reply_markup.inline_keyboard[0][0].url == "https://example.com" for c in copies))
        self.assertEqual(self.texts()[1:], ["Reklama 3 ta foydalanuvchiga yuborilmoqda...", "Reklama foydalanuvchilarga yuborildi!\n✅ Yuborildi: 2\n❌ Yuborilmadi: 1"])
        self.assertIsNone(await self.fsm(ADMIN).get_state())

    async def test_broadcast_media_with_caption(self):
        await self.send("/ad", uid=ADMIN)
        await self.send(None, uid=ADMIN, photo=PHOTO, caption="/not a command")
        self.assertEqual(len(self.bot.sent("CopyMessage")), 3)

    async def test_retry_after_is_retried(self):
        attempts = []

        def copy(m):
            attempts.append(m.chat_id)
            return TelegramRetryAfter(m, "flood", retry_after=0) if attempts.count(m.chat_id) == 1 and m.chat_id == 100 else True

        self.bot.responses["CopyMessage"] = copy
        await self.send("/ad", uid=ADMIN)
        await self.send("Reklama", uid=ADMIN)
        self.assertEqual(attempts, [100, 100, 101, 102])
        self.assertIn("✅ Yuborildi: 3", self.texts()[-1])

    async def test_commands_while_composing_are_not_broadcast(self):
        await self.send("/ad", uid=ADMIN)
        await self.send("/help", uid=ADMIN)
        self.assertIn("Admin uchun buyruqlar", self.texts()[-1])
        self.assertEqual(await self.fsm(ADMIN).get_state(), Advertisement.ad.state)
        await self.send("/stats", uid=ADMIN)
        self.assertEqual(self.texts()[-1], "Bot foydalanuvchilari soni:  3")
        self.assertEqual(self.bot.sent("CopyMessage"), [])

    async def test_second_message_after_broadcast_is_not_sent(self):
        await self.send("/ad", uid=ADMIN)
        await self.send("one", uid=ADMIN)
        self.assertIs(await self.send("two", uid=ADMIN), UNHANDLED)
        self.assertEqual(len(self.bot.sent("CopyMessage")), 3)

    async def test_no_users(self):
        self.db.users.clear()
        await self.send("/ad", uid=ADMIN)
        await self.send("x", uid=ADMIN)
        self.assertIn("✅ Yuborildi: 0\n❌ Yuborilmadi: 0", self.texts()[-1])


class ErrorsTest(BotTestCase):
    async def test_unhandled_error_is_reported_to_user(self):
        self.db.error = asyncpg.PostgresError("db down")
        with capture_logs() as logs:
            await self.send("/stats", uid=ADMIN)
        self.assertEqual(self.texts(), [ERROR_TEXT])
        self.assertTrue(any(log["event"] == "Unhandled error in handler" for log in logs))

    def event(self, exc, **payload) -> ErrorEvent:
        return ErrorEvent(update=update(self.bot, **payload), exception=exc)

    async def test_callback_error_alerts(self):
        with capture_logs():
            self.assertTrue(await error_handler(self.event(ValueError("x"), callback_query=callback("x")), structlog.get_logger()))
        [answer] = self.bot.sent("AnswerCallbackQuery")
        self.assertEqual((answer.text, answer.show_alert), (ERROR_TEXT, True))

    async def test_telegram_api_error_is_only_logged(self):
        with capture_logs() as logs:
            await error_handler(self.event(TelegramBadRequest(mock.Mock(), "message is not modified"), message=message()), structlog.get_logger())
        self.assertEqual(self.bot.calls, [])
        self.assertEqual((logs[0]["event"], logs[0]["log_level"]), ("Telegram API error", "warning"))

    async def test_notify_failure_is_swallowed(self):
        self.bot.responses["SendMessage"] = lambda m: TelegramForbiddenError(m, "blocked")
        with capture_logs():
            self.assertTrue(await error_handler(self.event(ValueError("x"), message=message()), structlog.get_logger()))

    async def test_update_without_user_target(self):
        with capture_logs() as logs:
            await error_handler(self.event(ValueError("x"), edited_message=message()), structlog.get_logger())
        self.assertEqual(self.bot.calls, [])
        self.assertEqual(logs[0]["log_level"], "error")


if __name__ == "__main__":
    unittest.main()
