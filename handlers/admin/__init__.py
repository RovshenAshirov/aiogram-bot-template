from aiogram import F, Router
from aiogram.filters import Command, StateFilter

from states.advertisement import Advertisement
from data import config
from filters.chat_type import ChatTypeFilter
from . import ad, stats


def prepare_router():
    admin_router = Router()
    admin_router.message.filter(ChatTypeFilter("private"), F.from_user.id.in_(config.ADMINS))

    admin_router.message.register(stats.show_stats, Command("stats"))

    admin_router.message.register(ad.start_ad, Command("ad"))
    admin_router.message.register(ad.cancel_ad, Command("cancel"), StateFilter(Advertisement.ad))
    admin_router.message.register(ad.send_ad, StateFilter(Advertisement.ad), ~F.text.startswith("/"))  # other commands fall through to the user router

    return admin_router
