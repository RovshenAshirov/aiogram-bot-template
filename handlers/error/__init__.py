from aiogram import Router

from . import error_handler


def prepare_router():
    error_router = Router()

    error_router.errors.register(error_handler.error_handler)

    return error_router
