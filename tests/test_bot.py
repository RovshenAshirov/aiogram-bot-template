import asyncio
import unittest
from unittest import mock

import aiojobs
from aiogram import Dispatcher
from aiohttp import web
from aiohttp.test_utils import TestClient, TestServer

import bot as bot_module
from tests.helpers import FakeBot, make_dp
from data import config
from db.db_api.users import CREATE_USERS_TABLE
from middlewares.logging import StructLoggingMiddleware
from middlewares.throttling import ThrottlingMiddleware
from web_handlers import tg_updates

SECRET = "s3cret"
WEBHOOK_CONFIG = {"MAIN_WEBHOOK_SECRET_TOKEN": SECRET, "MAX_UPDATES_IN_QUEUE": 10, "MAIN_WEBHOOK_ADDRESS": "https://example.com/tg/webhooks/bot/{token}"}


def patch_config(test: unittest.TestCase, **values):
    for name, value in values.items():
        patcher = mock.patch.object(config, name, value, create=True)
        patcher.start()
        test.addCleanup(patcher.stop)


class SetupTest(unittest.IsolatedAsyncioTestCase):
    def test_setup_logging(self):
        dp = Dispatcher()
        with mock.patch.object(bot_module, "setup_logger") as setup_logger:
            bot_module.setup_logging(dp)
        self.assertEqual((dp["business_logger_init"], dp["aiogram_logger_init"], dp["db_logger_init"]), ({"type": "business"}, {"type": "aiogram"}, {}))
        bind = setup_logger.return_value.bind
        self.assertEqual(bind.call_args_list, [mock.call(type="business"), mock.call(type="aiogram")])
        self.assertIs(dp["aiogram_logger"], bind.return_value)

    def test_router_order(self):
        dp, _ = make_dp()
        admin, user, group, channel, error = dp.sub_routers
        self.assertEqual(len(admin.message.handlers), 4)
        self.assertEqual(len(user.message.handlers), 2)
        self.assertEqual(len(error.observers["error"].handlers), 1)

    def test_middlewares(self):
        dp, _ = make_dp()
        self.assertIsInstance(dp.update.outer_middleware[-1], StructLoggingMiddleware)
        self.assertIsInstance(dp.message.outer_middleware[0], ThrottlingMiddleware)
        self.assertIs(dp.callback_query.outer_middleware[0], dp.message.outer_middleware[0])

    async def test_create_db_connections(self):
        dp = Dispatcher()
        pool = mock.AsyncMock()
        with mock.patch.object(bot_module.asyncpg, "create_pool", mock.AsyncMock(return_value=pool)) as create_pool:
            await bot_module.create_db_connections(dp)
        self.assertEqual(create_pool.call_args.kwargs["host"], config.PG_HOST)
        pool.execute.assert_awaited_once_with(CREATE_USERS_TABLE)
        self.assertIs(dp["db_pool"], pool)
        self.assertIsNotNone(dp["cache_pool"])
        self.assertIsNotNone(dp["temp_bot_cloud_session"])
        self.assertNotIn("temp_bot_local_session", dp.workflow_data)
        await dp["temp_bot_cloud_session"].close()

    async def test_create_db_connections_custom_api_server(self):
        patch_config(self, USE_CUSTOM_API_SERVER=True, CUSTOM_API_SERVER_BASE="http://local/bot{token}/{method}", CUSTOM_API_SERVER_FILE="http://local/file/bot{token}/{path}", CUSTOM_API_SERVER_IS_LOCAL=True)
        dp = Dispatcher()
        with mock.patch.object(bot_module.asyncpg, "create_pool", mock.AsyncMock()):
            await bot_module.create_db_connections(dp)
        local = dp["temp_bot_local_session"]
        self.assertTrue(local.api.is_local)
        self.assertEqual(local.api.base, "http://local/bot{token}/{method}")


class MainTest(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.bot = FakeBot()
        for target, kwargs in (("Bot", {"return_value": self.bot}), ("setup_aiogram", {"new": mock.AsyncMock(side_effect=self.fake_setup)}), ("set_bot_commands", {"new": mock.AsyncMock()}), ("notify_admins", {"new": mock.AsyncMock()})):
            patcher = mock.patch.object(bot_module, target, **kwargs)
            setattr(self, target, patcher.start())
            self.addCleanup(patcher.stop)

    async def fake_setup(self, dp):
        dp["aiogram_logger"] = mock.Mock()

    async def test_polling(self):
        with mock.patch.object(Dispatcher, "start_polling", mock.AsyncMock()) as polling:
            await bot_module.main()
        polling.assert_awaited_once_with(self.bot)
        self.assertTrue(self.bot.sent("DeleteWebhook")[0].drop_pending_updates)
        self.notify_admins.assert_awaited_once()
        self.assertEqual(self.notify_admins.call_args.args[1], "Bot ishga tushdi")
        self.set_bot_commands.assert_awaited_once()

    async def test_custom_api_server_session(self):
        patch_config(self, USE_CUSTOM_API_SERVER=True, CUSTOM_API_SERVER_BASE="http://local/bot{token}/{method}", CUSTOM_API_SERVER_FILE="http://local/file/bot{token}/{path}", CUSTOM_API_SERVER_IS_LOCAL=False)
        with mock.patch.object(Dispatcher, "start_polling", mock.AsyncMock()):
            await bot_module.main()
        self.assertEqual(self.Bot.call_args.kwargs["session"].api.base, "http://local/bot{token}/{method}")

    async def test_webhook(self):
        patch_config(self, USE_WEBHOOK=True, MAIN_WEBHOOK_LISTENING_HOST="127.0.0.1", MAIN_WEBHOOK_LISTENING_PORT=0, **WEBHOOK_CONFIG)
        started = asyncio.Event()
        with mock.patch.object(bot_module.web, "TCPSite") as site, mock.patch.object(bot_module.asyncio, "Event", return_value=mock.Mock(wait=mock.AsyncMock(side_effect=asyncio.CancelledError))), mock.patch.object(bot_module, "setup_aiohttp_app", side_effect=self.app_with_marker(started)):
            site.return_value.start = mock.AsyncMock()
            with self.assertRaises(asyncio.CancelledError):
                await bot_module.main()
        site.return_value.start.assert_awaited_once()
        self.assertEqual(site.call_args.kwargs, {"host": "127.0.0.1", "port": 0})
        [set_webhook] = self.bot.sent("SetWebhook")
        self.assertEqual((set_webhook.url, set_webhook.secret_token), ("https://example.com/tg/webhooks/bot/42:TEST", SECRET))
        self.assertTrue(started.is_set())  # the app's shutdown hook ran on cleanup

    def app_with_marker(self, event: asyncio.Event):
        def build(bot, dp):
            app = web.Application()
            app["bot"], app["dp"], app["scheduler"] = bot, dp, aiojobs.Scheduler()
            app.on_startup.append(bot_module.on_startup_webhook)
            app.on_shutdown.append(bot_module.on_shutdown_webhook)

            async def mark(_):
                event.set()

            app.on_shutdown.append(mark)
            return app

        return build


class WebhookTest(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        patch_config(self, **WEBHOOK_CONFIG)
        self.dp = mock.Mock(feed_webhook_update=mock.AsyncMock())
        self.scheduler = aiojobs.Scheduler()
        app = web.Application()
        app["bot"], app["dp"], app["scheduler"] = "bot", self.dp, self.scheduler
        app.router.add_post("/bot/{token}", tg_updates.execute)
        self.client = TestClient(TestServer(app))
        await self.client.start_server()
        self.addAsyncCleanup(self.client.close)
        self.addAsyncCleanup(self.scheduler.close)

    async def post(self, token=config.BOT_TOKEN, secret=SECRET):
        headers = {"X-Telegram-Bot-Api-Secret-Token": secret} if secret is not None else {}
        return await self.client.post(f"/bot/{token}", json={"update_id": 1}, headers=headers)

    async def test_valid_update_is_scheduled(self):
        self.assertEqual((await self.post()).status, 200)
        await self.scheduler.wait_and_close()
        self.dp.feed_webhook_update.assert_awaited_once_with("bot", {"update_id": 1})

    async def test_rejected_requests(self):
        for kwargs in ({"secret": None}, {"secret": "wrong"}, {"token": "1:WRONG"}):
            with self.subTest(**kwargs):
                self.assertEqual((await self.post(**kwargs)).status, 404)
        self.dp.feed_webhook_update.assert_not_awaited()

    async def test_queue_full(self):
        patch_config(self, MAX_UPDATES_IN_QUEUE=0)
        self.assertEqual((await self.post()).status, 429)

    async def test_closed_scheduler(self):
        await self.scheduler.close()
        self.assertEqual((await self.post()).status, 503)

    def test_route_is_registered(self):
        routes = [(r.method, r.resource.canonical) for r in tg_updates.tg_updates_app.router.routes()]
        self.assertTrue(any(method == "POST" and path.endswith("/bot/{token}") for method, path in routes))  # prefixed once mounted


class AiohttpAppTest(unittest.IsolatedAsyncioTestCase):
    async def test_lifecycle(self):
        patch_config(self, **WEBHOOK_CONFIG)
        bot, dp = FakeBot(), Dispatcher()
        dp["aiogram_logger"] = mock.Mock()
        dp.feed_webhook_update = mock.AsyncMock()
        app = bot_module.setup_aiohttp_app(bot, dp)
        async with TestClient(TestServer(app)) as client:
            [set_webhook] = bot.sent("SetWebhook")
            self.assertEqual(set_webhook.allowed_updates, dp.resolve_used_update_types())
            resp = await client.post(f"/tg/webhooks/bot/{config.BOT_TOKEN}", json={"update_id": 7}, headers={"X-Telegram-Bot-Api-Secret-Token": SECRET})
            self.assertEqual(resp.status, 200)
        self.assertTrue(app["scheduler"].closed)  # shutdown waited for the queue
        dp.feed_webhook_update.assert_awaited_once_with(bot, {"update_id": 7})
        self.assertIs(tg_updates.tg_updates_app["scheduler"], app["scheduler"])  # shared with the sub-app


class PollingHooksTest(unittest.IsolatedAsyncioTestCase):
    async def test_hooks_log(self):
        dp = Dispatcher()
        dp["aiogram_logger"] = mock.Mock()
        await bot_module.on_startup_polling(dp)
        await bot_module.on_shutdown_polling(dp)
        self.assertEqual([c.args[0] for c in dp["aiogram_logger"].info.call_args_list], ["Started polling", "Stopped polling"])


if __name__ == "__main__":
    unittest.main()
