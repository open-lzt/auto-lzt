"""/start and /help — the bot's only self-description.

Reached only by admins: AdminGuard sits on the dispatcher, so even /start refuses a stranger.
"""

from __future__ import annotations

from aiogram import Router
from aiogram.filters import Command, CommandStart
from aiogram.methods import SendMessage
from aiogram.types import Message

router = Router(name="common")


class HelpScreen:
    @staticmethod
    def text() -> str:
        return (
            "<b>lzt-flow</b>\n\n"
            "/flows — ваши флоу: запуск и логи\n"
            "/nodes — доступные узлы\n"
            "/modules — официальные модули\n"
            "/plugins — плагины: установка, обновление, настройки"
        )


@router.message(CommandStart())
async def start(m: Message) -> SendMessage:
    return SendMessage(chat_id=m.chat.id, text=HelpScreen.text())


@router.message(Command("help"))
async def help_command(m: Message) -> SendMessage:
    return SendMessage(chat_id=m.chat.id, text=HelpScreen.text())
