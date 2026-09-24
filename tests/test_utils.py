import sys
import unittest
from unittest import mock

import structlog
from aiogram.exceptions import TelegramBadRequest, TelegramForbiddenError
from aiogram.types import BotCommandScopeChat
from structlog.testing import capture_logs

from tests.helpers import FakeBot
from data import config
from models.base import orjson_dumps
from models.user import User as DbUser
from utils.log import setup_logger
from utils.notify_admins import notify_admins
from utils.set_bot_commands import ADMIN_COMMANDS, USER_COMMANDS, set_bot_commands


class StartupUtilsTest(unittest.IsolatedAsyncioTestCase):
    async def test_notify_admins(self):
        bot = FakeBot()
        await notify_admins(bot, "hi", structlog.get_logger())
        self.assertEqual([(m.chat_id, m.text) for m in bot.sent("SendMessage")], [(1, "hi"), (2, "hi")])

    async def test_notify_admins_continues_after_error(self):
        bot = FakeBot()
        bot.responses["SendMessage"] = lambda m: TelegramForbiddenError(m, "blocked") if m.chat_id == 1 else True
        with capture_logs() as logs:
            await notify_admins(bot, "hi", structlog.get_logger())
        self.assertEqual(len(bot.sent("SendMessage")), 2)
        self.assertEqual((logs[0]["event"], logs[0]["admin"]), ("Can't notify admin", 1))

    async def test_set_bot_commands(self):
        bot = FakeBot()
        await set_bot_commands(bot, structlog.get_logger())
        calls = bot.sent("SetMyCommands")
        self.assertEqual((calls[0].commands, calls[0].scope), (USER_COMMANDS, None))
        self.assertEqual([(c.commands, c.scope) for c in calls[1:]], [(ADMIN_COMMANDS, BotCommandScopeChat(chat_id=a)) for a in config.ADMINS])

    async def test_set_bot_commands_admin_chat_not_found(self):
        bot = FakeBot()
        bot.responses["SetMyCommands"] = lambda m: TelegramBadRequest(m, "chat not found") if m.scope and m.scope.chat_id == 1 else True
        with capture_logs() as logs:
            await set_bot_commands(bot, structlog.get_logger())
        self.assertEqual(len(bot.sent("SetMyCommands")), 3)
        self.assertEqual((logs[0]["event"], logs[0]["admin"]), ("Can't set admin commands", 1))

    def test_admin_commands_extend_user_commands(self):
        self.assertEqual(ADMIN_COMMANDS[:len(USER_COMMANDS)], USER_COMMANDS)
        self.assertEqual([c.command for c in ADMIN_COMMANDS], ["start", "help", "ad", "stats"])


class ModelsAndLogTest(unittest.TestCase):
    def test_orjson_dumps(self):
        self.assertEqual(orjson_dumps({"a": "ʻ"}, default=str), '{"a":"ʻ"}')
        self.assertEqual(orjson_dumps({"a": {1}}, default=list), '{"a":[1]}')

    def test_user_model(self):
        self.assertIsNone(DbUser(id=1, full_name="A", username=None, telegram_id=5).username)

    def test_config(self):
        self.assertEqual(config.ADMINS, [1, 2])
        self.assertFalse(config.USE_WEBHOOK)
        self.assertFalse(hasattr(config, "MAIN_WEBHOOK_ADDRESS"))  # only defined with USE_WEBHOOK

    def test_setup_logger(self):
        self.addCleanup(structlog.configure, logger_factory=structlog.ReturnLoggerFactory())
        self.addCleanup(structlog.reset_defaults)
        for tty, renderer in ((False, structlog.processors.JSONRenderer), (True, structlog.dev.ConsoleRenderer)):
            with self.subTest(tty=tty), mock.patch.object(sys.stderr, "isatty", return_value=tty), mock.patch("logging.basicConfig") as basic_config:
                self.assertIsNotNone(setup_logger())
                self.assertEqual(basic_config.call_args.kwargs["level"], config.LOGGING_LEVEL)
                self.assertIsInstance(structlog.get_config()["processors"][-1], renderer)


if __name__ == "__main__":
    unittest.main()
