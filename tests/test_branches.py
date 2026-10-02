from copy import deepcopy
from datetime import UTC, datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock

import httpx
import pytest
from aiogram import Bot, Dispatcher
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.types import Chat, Message, Update, User

from app.config import Settings
from app.crm import CrmClient
from app.formatters import branch_messages
from app.handlers import router
from app.keyboards import main_menu
from app.runtime import Runtime, register_runtime, unregister_runtime


@pytest.fixture
async def branch_bot():
    settings = Settings(
        _env_file=None, bot_token="123456789:unit-test-only-token",
        crm_base_url="https://crm.invalid/api/v1", crm_bot_api_key="test-key", crm_webhook_secret="test-secret",
    )
    chat = Chat(id=123, type="private")
    user = User(id=123, is_bot=False, first_name="Ali")
    session = AsyncMock(return_value=Message(message_id=100, date=datetime.now(UTC), chat=chat))
    bot = Bot(settings.bot_token.get_secret_value(), session=session)
    responses = []
    requests = []

    async def receive(request):
        requests.append(request)
        return responses.pop(0)

    crm = CrmClient(settings.crm_base_url, "test-key", 5)
    await crm.close()
    crm.client = httpx.AsyncClient(
        base_url="https://crm.invalid/api/v1/", headers={"Authorization": "Bearer test-key"},
        transport=httpx.MockTransport(receive),
    )
    register_runtime(bot, Runtime(settings=settings, crm=crm, storage=SimpleNamespace()))
    dispatcher = Dispatcher(storage=MemoryStorage())
    dispatcher.include_router(deepcopy(router))
    sequence = 0

    async def press_button():
        nonlocal sequence
        sequence += 1
        message = Message(
            message_id=sequence, date=datetime.now(UTC), chat=chat, from_user=user,
            text="📍 Manzil va ish vaqti",
        )
        await dispatcher.feed_update(bot, Update(update_id=sequence, message=message))

    try:
        yield SimpleNamespace(press=press_button, session=session, responses=responses, requests=requests)
    finally:
        unregister_runtime(bot)
        await dispatcher.fsm.close()
        await crm.close()
        await bot.session.close()


async def test_branch_button_reads_the_current_crm_api_each_time(branch_bot):
    first = "📍 Buxoro filial\nManzil: Eski manzil\n🕘 Ish vaqti: 09:00–18:00"
    updated = "📍 Buxoro filial\nManzil: Yangi manzil\n🕘 Ish vaqti: 08:30–20:00"
    branch_bot.responses.extend([
        httpx.Response(200, json={"text": first}), httpx.Response(200, json={"text": updated}),
    ])
    await branch_bot.press()
    assert branch_bot.session.call_args.args[1].text == first
    await branch_bot.press()
    assert branch_bot.session.call_args.args[1].text == updated
    assert len(branch_bot.requests) == 2
    assert all(request.url.path == "/api/v1/bot/content/branches" for request in branch_bot.requests)
    assert all(request.headers["Authorization"] == "Bearer test-key" for request in branch_bot.requests)
    assert branch_bot.session.call_args.args[1].reply_markup == main_menu()


@pytest.mark.parametrize("response", [
    httpx.Response(503), httpx.Response(200, json={}), httpx.Response(200, json={"text": "  "}),
])
async def test_unavailable_or_empty_directory_shows_default_hours(branch_bot, response):
    branch_bot.responses.append(response)
    await branch_bot.press()
    text = branch_bot.session.call_args.args[1].text
    assert "Standart ish vaqti: 09:00–18:00" in text
    assert "operator" in text
    assert branch_bot.session.call_args.args[1].reply_markup == main_menu()


async def test_a_large_branch_directory_is_sent_as_multiple_valid_messages(branch_bot):
    directory = "\n\n".join(
        f"📍 Filial {number}\nManzil: " + "Uzun manzil " * 100 + "\n🕘 Ish vaqti: 09:00–18:00"
        for number in range(12)
    )
    branch_bot.responses.append(httpx.Response(200, json={"text": directory}))
    await branch_bot.press()
    messages = [call.args[1].text for call in branch_bot.session.call_args_list]
    assert len(messages) > 1
    assert "\n\n".join(messages) == directory
    assert all(len(text.encode("utf-16-le")) // 2 <= 4000 for text in messages)


def test_long_unicode_address_respects_telegram_limit_without_losing_text():
    directory = "📍 Filial\nManzil: " + "🏢" * 2500 + "\nIsh vaqti: 09:00–18:00"
    messages = branch_messages({"text": directory})
    assert len(messages) > 1
    assert "".join(messages).count("🏢") == 2500
    assert all(len(text.encode("utf-16-le")) // 2 <= 4000 for text in messages)
