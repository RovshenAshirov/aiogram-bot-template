import asyncio

import aiojobs
import asyncpg
import orjson
from aiogram import Bot, Dispatcher
from aiogram.client.default import DefaultBotProperties
from aiogram.client.session.aiohttp import AiohttpSession
from aiogram.client.telegram import TelegramAPIServer
from aiogram.fsm.storage.redis import RedisStorage
from aiohttp import web
from redis.asyncio import ConnectionPool, Redis

from data import config
from db.db_api.users import CREATE_USERS_TABLE
from handlers.admin import prepare_router as prepare_admin_router
from handlers.channel import prepare_router as prepare_channel_router
from handlers.error import prepare_router as prepare_error_router
from handlers.group import prepare_router as prepare_group_router
from handlers.user import prepare_router as prepare_user_router
from middlewares.logging import StructLoggingMiddleware
from middlewares.throttling import ThrottlingMiddleware
from utils.log import setup_logger
from utils.notify_admins import notify_admins
from utils.set_bot_commands import set_bot_commands


async def create_db_connections(dp: Dispatcher):
    db_pool = await asyncpg.create_pool(
        host=config.PG_HOST,
        port=config.PG_PORT,
        user=config.PG_USER,
        password=config.PG_PASSWORD,
        database=config.PG_DATABASE,
        min_size=1,
        max_size=3,
    )
    await db_pool.execute(CREATE_USERS_TABLE)
    dp["db_pool"] = db_pool
    redis_pool = ConnectionPool(
        host=config.CACHE_HOST,
        password=config.CACHE_PASSWORD,
        port=config.CACHE_PORT,
        db=0,
    )
    dp["cache_pool"] = redis_pool

    dp["temp_bot_cloud_session"] = AiohttpSession(json_loads=orjson.loads)
    if config.USE_CUSTOM_API_SERVER:
        dp["temp_bot_local_session"] = AiohttpSession(
            api=TelegramAPIServer(
                base=config.CUSTOM_API_SERVER_BASE,
                file=config.CUSTOM_API_SERVER_FILE,
                is_local=config.CUSTOM_API_SERVER_IS_LOCAL,
            ),
            json_loads=orjson.loads,
        )


def setup_handlers(dp: Dispatcher):
    dp.include_router(prepare_admin_router())
    dp.include_router(prepare_user_router())
    dp.include_router(prepare_group_router())
    dp.include_router(prepare_channel_router())
    dp.include_router(prepare_error_router())


def setup_middlewares(dp: Dispatcher):
    dp.update.outer_middleware(
        StructLoggingMiddleware(
            logger=dp["aiogram_logger"], logger_init_values=dp["aiogram_logger_init"]
        )
    )
    throttling = ThrottlingMiddleware(dp["cache_pool"])
    dp.message.outer_middleware(throttling)
    dp.callback_query.outer_middleware(throttling)


def setup_logging(dp: Dispatcher):
    dp["business_logger_init"] = {"type": "business"}
    dp["business_logger"] = setup_logger().bind(
        **dp["business_logger_init"]
    )
    dp["aiogram_logger_init"] = {"type": "aiogram"}
    dp["aiogram_logger"] = setup_logger().bind(
        **dp["aiogram_logger_init"]
    )
    dp["db_logger_init"] = {}


async def setup_aiogram(dp: Dispatcher):
    setup_logging(dp)
    await create_db_connections(dp)
    setup_handlers(dp)
    setup_middlewares(dp)


async def on_startup_webhook(app: web.Application):
    dp: Dispatcher = app["dp"]
    bot: Bot = app["bot"]

    webhook_logger = dp["aiogram_logger"].bind(webhook_url=config.MAIN_WEBHOOK_ADDRESS)
    webhook_logger.info("Configured webhook")
    await bot.set_webhook(
        url=config.MAIN_WEBHOOK_ADDRESS.format(token=config.BOT_TOKEN),
        allowed_updates=dp.resolve_used_update_types(),
        secret_token=config.MAIN_WEBHOOK_SECRET_TOKEN,
    )


async def on_shutdown_webhook(app: web.Application):
    dp: Dispatcher = app["dp"]
    scheduler: aiojobs.Scheduler = app["scheduler"]  # shared with subapps
    dp["aiogram_logger"].info(f"Waiting for {scheduler.active_count} tasks to complete")
    await scheduler.wait_and_close()
    bot: Bot = app["bot"]
    await bot.session.close()
    dp["aiogram_logger"].info("Stopped webhook")


async def on_startup_polling(dispatcher: Dispatcher):
    dispatcher["aiogram_logger"].info("Started polling")


async def on_shutdown_polling(dispatcher: Dispatcher):
    dispatcher["aiogram_logger"].info("Stopped polling")


def setup_aiohttp_app(bot: Bot, dp: Dispatcher) -> web.Application:
    from web_handlers.tg_updates import tg_updates_app

    scheduler = aiojobs.Scheduler()
    app = web.Application()
    subapps: list[tuple[str, web.Application]] = [
        ("/tg/webhooks/", tg_updates_app),
    ]
    for prefix, subapp in subapps:
        subapp["bot"] = bot
        subapp["dp"] = dp
        subapp["scheduler"] = scheduler
        app.add_subapp(prefix, subapp)
    app["bot"] = bot
    app["dp"] = dp
    app["scheduler"] = scheduler
    app.on_startup.append(on_startup_webhook)
    app.on_shutdown.append(on_shutdown_webhook)
    return app


async def main():
    if config.USE_CUSTOM_API_SERVER:
        session = AiohttpSession(
            api=TelegramAPIServer(
                base=config.CUSTOM_API_SERVER_BASE,
                file=config.CUSTOM_API_SERVER_FILE,
                is_local=config.CUSTOM_API_SERVER_IS_LOCAL,
            ),
            json_loads=orjson.loads,
        )
    else:
        session = AiohttpSession(json_loads=orjson.loads)
    bot = Bot(config.BOT_TOKEN, default=DefaultBotProperties(parse_mode="HTML"), session=session)

    dp = Dispatcher(
        storage=RedisStorage(
            redis=Redis(
                host=config.FSM_HOST,
                password=config.FSM_PASSWORD,
                port=config.FSM_PORT,
                db=0,
            )
        )
    )

    await setup_aiogram(dp)
    await set_bot_commands(bot, dp["aiogram_logger"])
    await notify_admins(bot, "Bot ishga tushdi", dp["aiogram_logger"])

    if config.USE_WEBHOOK:
        runner = web.AppRunner(setup_aiohttp_app(bot, dp))
        await runner.setup()
        site = web.TCPSite(
            runner,
            host=config.MAIN_WEBHOOK_LISTENING_HOST,
            port=config.MAIN_WEBHOOK_LISTENING_PORT,
        )
        await site.start()
        try:
            await asyncio.Event().wait()
        finally:  # runs on_shutdown_webhook
            await runner.cleanup()
    else:
        await bot.delete_webhook(drop_pending_updates=True)
        dp.startup.register(on_startup_polling)
        dp.shutdown.register(on_shutdown_polling)
        await dp.start_polling(bot)


if __name__ == "__main__":
    asyncio.run(main())
