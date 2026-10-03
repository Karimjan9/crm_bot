from copy import deepcopy
from datetime import UTC, datetime
from html import unescape
from html.parser import HTMLParser
from types import SimpleNamespace
from unittest.mock import AsyncMock

import httpx
import pytest
from aiogram import Bot, Dispatcher
from aiogram.exceptions import TelegramBadRequest
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.methods import EditMessageText, SendMessage
from aiogram.types import CallbackQuery, Chat, Message, Update, User

from app.config import Settings
from app.crm import CrmClient
from app.handlers import Intake, router
from app.information import (
    information_catalog,
    information_home,
    information_keyboard,
    information_topic,
)
from app.runtime import Runtime, register_runtime, unregister_runtime

CATALOG = {
    "title": "Foydali ma’lumotlar", "intro": "Kerakli mavzuni tanlang.",
    "topics": [{
        "id": "photo", "icon": "photo", "title": "Hujjatni sifatli yuborish", "summary": "Rasm yoki PDF yuboring.",
        "body": "Barcha sahifalarni yuboring.", "checklist": ["Matn aniq ko‘rinsin.", "Burchaklar kesilmasin."],
        "tip": "JPG, PNG yoki PDF yuboring.",
    }],
}


@pytest.fixture
async def information_bot():
    settings = Settings(
        _env_file=None, bot_token="123456789:unit-test-only-token",
        crm_base_url="https://crm.invalid/api/v1", crm_bot_api_key="test-key", crm_webhook_secret="test-secret",
    )
    chat = Chat(id=123, type="private")
    user = User(id=123, is_bot=False, first_name="Ali")
    bot_user = User(id=123456789, is_bot=True, first_name="Bot")
    sent = Message(message_id=100, date=datetime.now(UTC), chat=chat, from_user=bot_user, text="Ma’lumot")
    session = AsyncMock(return_value=sent)
    bot = Bot(settings.bot_token.get_secret_value(), session=session)
    responses, requests = [], []

    async def receive(request):
        requests.append(request)
        return responses.pop(0)

    crm = CrmClient(settings.crm_base_url, "test-key", 5)
    await crm.close()
    crm.client = httpx.AsyncClient(base_url="https://crm.invalid/api/v1/", headers={"Authorization": "Bearer test-key"}, transport=httpx.MockTransport(receive))
    storage = SimpleNamespace(verified_phone=AsyncMock(return_value="+998900000001"))
    register_runtime(bot, Runtime(settings=settings, crm=crm, storage=storage))
    dispatcher = Dispatcher(storage=MemoryStorage())
    dispatcher.include_router(deepcopy(router))
    sequence = 0

    async def press(data=None, actor=user, callback_message=sent):
        nonlocal sequence
        sequence += 1
        if data:
            update = Update(update_id=sequence, callback_query=CallbackQuery(id=str(sequence), from_user=actor, message=callback_message, chat_instance="test-chat", data=data))
        else:
            update = Update(update_id=sequence, message=Message(message_id=sequence, date=datetime.now(UTC), chat=chat, from_user=user, text="ℹ️ Foydali ma’lumotlar"))
        await dispatcher.feed_update(bot, update)

    try:
        yield SimpleNamespace(press=press, session=session, responses=responses, requests=requests, storage=storage,
                              state=dispatcher.fsm.get_context(bot=bot, chat_id=chat.id, user_id=user.id))
    finally:
        unregister_runtime(bot)
        await dispatcher.fsm.close()
        await crm.close()
        await bot.session.close()


def last_text_method(session):
    return next(call.args[1] for call in reversed(session.call_args_list) if isinstance(call.args[1], (SendMessage, EditMessageText)))


async def test_button_and_topic_read_fresh_authenticated_crm_content_and_format_cards(information_bot):
    information_bot.responses.append(httpx.Response(200, json=CATALOG))
    await information_bot.press()
    method = last_text_method(information_bot.session)
    assert method.parse_mode == "HTML"
    assert "<b>📚 Foydali ma’lumotlar</b>" in method.text
    assert method.reply_markup.inline_keyboard[0][0].callback_data == "info:topic:photo"
    updated = deepcopy(CATALOG)
    updated["topics"][0]["body"] = "Eng yangi ko‘rsatma."
    information_bot.responses.append(httpx.Response(200, json=updated))
    await information_bot.press("info:topic:photo")
    method = last_text_method(information_bot.session)
    assert isinstance(method, EditMessageText)
    assert "Eng yangi ko‘rsatma." in method.text
    assert "<i>Rasm yoki PDF yuboring.</i>" in method.text
    assert "▫️ Matn aniq ko‘rinsin." in method.text
    assert "<b>💡 Eslatma</b>" in method.text
    assert method.link_preview_options.is_disabled
    assert method.reply_markup.inline_keyboard[-1][0].callback_data == "info:home"
    assert len(information_bot.requests) == 2
    assert all(request.url.path == "/api/v1/bot/content/useful-information" for request in information_bot.requests)
    assert all(request.headers["Authorization"] == "Bearer test-key" for request in information_bot.requests)


async def test_back_refreshes_the_menu_and_removed_topics_return_to_current_menu(information_bot):
    changed = {**CATALOG, "title": "Yangi kutubxona", "topics": []}
    information_bot.responses.extend([httpx.Response(200, json=changed), httpx.Response(200, json=changed)])
    await information_bot.press("info:home")
    assert "Yangi kutubxona" in last_text_method(information_bot.session).text
    await information_bot.press("info:topic:photo")
    method = last_text_method(information_bot.session)
    assert "hozircha mavjud emas" in method.text
    assert len(method.reply_markup.inline_keyboard) == 1


@pytest.mark.parametrize("response", [httpx.Response(503), httpx.Response(200, json={}), httpx.Response(200, json={"text": ""})])
async def test_unavailable_content_shows_a_useful_fallback_and_request_button(information_bot, response):
    information_bot.responses.append(response)
    await information_bot.press()
    method = last_text_method(information_bot.session)
    assert "Ma’lumotlar hozircha yuklanmadi" in method.text
    assert method.reply_markup.inline_keyboard[0][0].callback_data == "info:intake"


async def test_old_text_only_api_is_formatted_without_losing_its_content(information_bot):
    information_bot.responses.append(httpx.Response(200, json={"text": "Mavjud ma’lumot\n• Hujjatni yuboring."}))
    await information_bot.press()
    assert "Mavjud ma’lumot\n• Hujjatni yuboring." in last_text_method(information_bot.session).text


async def test_reading_information_keeps_an_intake_draft_and_cta_uses_the_customer_contact(information_bot):
    await information_bot.state.set_state(Intake.contact)
    await information_bot.state.update_data(notes="Saqlangan murojaat", entry_payload="instagram")
    information_bot.responses.append(httpx.Response(200, json=CATALOG))
    await information_bot.press()
    assert await information_bot.state.get_state() == Intake.contact.state
    assert (await information_bot.state.get_data())["notes"] == "Saqlangan murojaat"
    await information_bot.press("info:intake")
    assert await information_bot.state.get_state() == Intake.request.state
    information_bot.storage.verified_phone.assert_awaited_once_with(123, 123)
    assert "Kontaktingiz saqlangan" in last_text_method(information_bot.session).text
    assert (await information_bot.state.get_data())["entry_payload"] == "instagram"


async def test_callback_cannot_start_an_intake_for_a_different_chat_user(information_bot):
    await information_bot.press("info:intake", actor=User(id=999, is_bot=False, first_name="Other"))
    assert not information_bot.requests
    information_bot.storage.verified_phone.assert_not_awaited()
    assert await information_bot.state.get_state() is None


def test_html_is_escaped_and_large_unicode_cards_split_into_balanced_valid_messages():
    data = deepcopy(CATALOG)
    data["topics"][0]["title"] = "<b>Yot sarlavha</b>"
    data["topics"][0]["body"] = "<script>test</script> " + "🏢<&>" * 1700
    topic = information_catalog(data)["topics"][0]
    messages = information_topic(topic)
    assert len(messages) > 1
    assert sum(unescape(text).count("🏢") for text in messages) == 1700
    assert all(len(text.encode("utf-16-le")) // 2 <= 4000 for text in messages)
    assert "&lt;script&gt;" in "".join(messages)
    assert all("<script>" not in text for text in messages)

    class Balanced(HTMLParser):
        def __init__(self):
            super().__init__()
            self.stack = []

        def handle_starttag(self, tag, attrs):
            self.stack.append(tag)

        def handle_endtag(self, tag):
            assert self.stack.pop() == tag

    for text in messages:
        parser = Balanced()
        parser.feed(text)
        assert not parser.stack
    legacy = information_home(information_catalog({"text": "💡" * 5000}))
    assert sum(text.count("💡") for text in legacy) == 5000
    assert all(len(text.encode("utf-16-le")) // 2 <= 4000 for text in legacy)


def test_callback_ids_are_bounded_unique_and_invalid_or_hidden_topics_are_filtered():
    valid = deepcopy(CATALOG["topics"][0])
    data = {"topics": [valid, valid, {**valid, "id": "bad:id"}, {**valid, "id": "hidden", "published": False}]}
    catalog = information_catalog(data)
    assert len(catalog["topics"]) == 1
    assert all(len(button.callback_data.encode()) <= 64 for row in information_keyboard(catalog).inline_keyboard for button in row)


def test_an_intentionally_empty_library_is_distinct_from_an_unavailable_api():
    catalog = information_catalog({"title": "Kutubxona", "intro": "", "topics": []})
    assert "e’lon qilingan mavzular yo‘q" in catalog["intro"]
    assert "yuklanmadi" not in catalog["intro"]


@pytest.mark.parametrize("error,send_new", [("message is not modified", False), ("message can't be edited", True)])
async def test_repeated_or_old_topic_messages_handle_telegram_edit_errors(information_bot, error, send_new):
    async def receive(bot, method, **kwargs):
        if isinstance(method, EditMessageText):
            raise TelegramBadRequest(method=method, message=error)
        return True

    information_bot.session.side_effect = receive
    information_bot.responses.append(httpx.Response(200, json=CATALOG))
    await information_bot.press("info:topic:photo")
    sends = [call.args[1] for call in information_bot.session.call_args_list if isinstance(call.args[1], SendMessage)]
    assert bool(sends) is send_new
    if sends:
        assert "Hujjatni sifatli yuborish" in sends[0].text
