from dataclasses import dataclass

from aiogram import Bot

from app.config import Settings
from app.crm import CrmClient
from app.storage import BotStorage


@dataclass(frozen=True)
class Runtime:
    settings: Settings
    crm: CrmClient
    storage: BotStorage


_runtimes: dict[int, Runtime] = {}


def register_runtime(bot: Bot, runtime: Runtime) -> None:
    _runtimes[bot.id] = runtime


def unregister_runtime(bot: Bot) -> None:
    _runtimes.pop(bot.id, None)


def runtime_for(bot: Bot) -> Runtime:
    try:
        return _runtimes[bot.id]
    except KeyError as error:
        raise RuntimeError("Bot runtime has not been initialized") from error
