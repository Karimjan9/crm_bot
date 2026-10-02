from copy import deepcopy
from datetime import UTC, datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from aiogram import Bot, Dispatcher
from aiogram.fsm.storage.memory import MemoryStorage, SimpleEventIsolation
from aiogram.types import Chat, Contact, Message, Update, User

from app.config import Settings
from app.crm import CrmApiError
from app.handlers import OperatorRequest, router
from app.runtime import Runtime, register_runtime, unregister_runtime
from app.storage import BotStorage


@pytest.fixture
async def flow():
    values = {}

    async def save(key, value, **kwargs):
        values[key] = value

    redis = SimpleNamespace(
        set=AsyncMock(side_effect=save),
        get=AsyncMock(side_effect=lambda key: values.get(key)),
        exists=AsyncMock(side_effect=lambda key: key in values),
    )
    storage = BotStorage(redis)
    storage.enqueue = AsyncMock()
    settings = Settings(
        _env_file=None, bot_token="123456789:unit-test-only-token",
        crm_base_url="https://crm.invalid/api/v1", crm_bot_api_key="test-key",
        crm_webhook_secret="test-secret",
    )
    chat = Chat(id=123, type="private")
    user = User(id=123, is_bot=False, first_name="Ali", last_name="Valiyev", username="ali_test")
    session = AsyncMock(return_value=Message(message_id=100, date=datetime.now(UTC), chat=chat))
    bot = Bot(settings.bot_token.get_secret_value(), session=session)
    crm = SimpleNamespace(
        operator_request=AsyncMock(return_value={"ok": True, "data": {"id": 12}}),
        orders=AsyncMock(return_value=[]), create_lead=AsyncMock(return_value={"data": {"id": 456}}),
        message=AsyncMock(),
        verified_contact=AsyncMock(return_value=None),
    )
    register_runtime(bot, Runtime(settings=settings, crm=crm, storage=storage))
    dispatcher = Dispatcher(storage=MemoryStorage(), events_isolation=SimpleEventIsolation())
    dispatcher.include_router(deepcopy(router))
    state = dispatcher.fsm.get_context(bot=bot, chat_id=chat.id, user_id=user.id)
    sequence = 0

    async def send(**content):
        nonlocal sequence
        sequence += 1
        message = Message(message_id=sequence, date=datetime.now(UTC), chat=chat, from_user=user, **content)
        await dispatcher.feed_update(bot, Update(update_id=sequence, message=message))

    try:
        yield SimpleNamespace(
            send=send, state=state, crm=crm, storage=storage, redis=redis,
            session=session, values=values,
            contact=Contact(phone_number="+998900000001", first_name="Ali", user_id=user.id),
        )
    finally:
        unregister_runtime(bot)
        await dispatcher.fsm.close()
        await bot.session.close()


async def test_operator_collects_text_and_own_contact_then_saves_complete_request(flow):
    await flow.send(text="👩‍💼 Operator")
    flow.crm.operator_request.assert_not_awaited()
    assert await flow.state.get_state() == OperatorRequest.text.state
    await flow.send(text=" ")
    await flow.send(text="x" * 2001)
    assert await flow.state.get_state() == OperatorRequest.text.state
    await flow.send(text="Diplom tarjimasi narxini bilmoqchiman.")
    assert await flow.state.get_state() == OperatorRequest.contact.state
    await flow.send(contact=Contact(phone_number="+998900000002", first_name="Other", user_id=999))
    flow.crm.operator_request.assert_not_awaited()
    assert await flow.storage.verified_phone(123, 123) is None
    await flow.send(text="Juma kuniga kerak.")
    await flow.send(contact=flow.contact)
    payload = flow.crm.operator_request.await_args.args[0]
    assert payload["text"] == "Diplom tarjimasi narxini bilmoqchiman.\nJuma kuniga kerak."
    assert payload["customer"]["name"] == "Ali Valiyev"
    assert payload["customer"]["phone"] == "+998900000001"
    assert payload["customer"]["phone_verified"] is True
    assert payload["telegram_username"] == "ali_test"
    assert await flow.state.get_state() is None
    assert "Operator sizga bog‘lanadi." in flow.session.call_args.args[1].text
    assert await flow.storage.has_human_handoff(123)
    assert await flow.storage.verified_phone(123, 123) == "+998900000001"
    # The contact survives FSM resets and has no expiry; another user cannot reuse it.
    assert await BotStorage(flow.redis).verified_phone(123, 123) == "+998900000001"
    assert await flow.storage.verified_phone(123, 999) is None
    call = next(call for call in flow.redis.set.await_args_list if call.args[0] == "crm-bot:contact:123")
    assert call.kwargs == {}


async def test_one_contact_is_reused_by_operator_orders_and_intake_after_start(flow):
    # A contact shared through order verification is remembered across all flows.
    await flow.send(text="📦 Buyurtmam")
    await flow.send(contact=flow.contact)
    await flow.send(text="/start")
    await flow.send(text="👩‍💼 Operator")
    await flow.send(text="Buyurtmam bo‘yicha yordam kerak.")
    flow.crm.operator_request.assert_awaited_once()
    assert await flow.state.get_state() is None
    flow.crm.orders.reset_mock()
    await flow.send(text="📦 Buyurtmam")
    flow.crm.orders.assert_awaited_once_with(123, "+998900000001")
    await flow.send(text="📝 Yangi murojaat")
    await flow.send(text="Pasport tarjimasi kerak.")
    keyboard = flow.session.call_args.args[1].reply_markup
    assert keyboard.keyboard[0][0].text == "📨 Murojaatni jo‘natish"
    assert not any(button.request_contact for row in keyboard.keyboard for button in row)
    flow.crm.create_lead.assert_not_awaited()
    await flow.send(text="📨 Murojaatni jo‘natish")
    payload = flow.crm.create_lead.await_args.args[0]
    assert payload["customer"]["phone"] == "+998900000001"
    assert payload["request"]["notes"] == "Pasport tarjimasi kerak."
    assert await flow.state.get_state() is None


async def test_early_contact_cancel_and_keyword_request_do_not_lose_identity_or_text(flow):
    await flow.send(text="👩‍💼 Operator")
    await flow.send(contact=flow.contact)
    flow.crm.operator_request.assert_not_awaited()
    await flow.send(text="⬅️ Bosh menyu")
    assert await flow.state.get_state() is None
    await flow.send(text="Narx haqida operator bilan gaplashmoqchiman.")
    payload = flow.crm.operator_request.await_args.args[0]
    assert payload["text"] == "Narx haqida operator bilan gaplashmoqchiman."
    assert payload["reason"] == "keyword_request"
    assert payload["customer"]["phone"] == "+998900000001"
    flow.crm.message.assert_not_awaited()


async def test_operator_request_is_queued_with_stable_id_if_crm_is_unavailable(flow):
    await flow.send(text="👩‍💼 Operator")
    await flow.send(text="Menga operator yordami kerak.")
    request_id = (await flow.state.get_data())["operator_request_id"]
    flow.crm.operator_request.side_effect = CrmApiError("offline test")
    await flow.send(contact=flow.contact)
    action, payload = flow.storage.enqueue.await_args.args
    assert action == "operator_request"
    assert payload["external_id"] == request_id
    assert payload["text"] == "Menga operator yordami kerak."
    assert payload["customer"]["phone_verified"] is True
    assert await flow.state.get_state() is None
    assert "Operator sizga bog‘lanadi." in flow.session.call_args.args[1].text


async def test_previously_verified_crm_contact_is_restored_without_asking_again(flow):
    flow.crm.verified_contact.return_value = "+998900000001"
    await flow.send(text="👩‍💼 Operator")
    await flow.send(text="Avvalgi buyurtmam bo‘yicha yordam kerak.")
    flow.crm.operator_request.assert_awaited_once()
    flow.crm.verified_contact.assert_awaited_once_with(123, 123)
    assert await flow.storage.verified_phone(123, 123) == "+998900000001"
    assert await flow.state.get_state() is None


async def test_contact_shared_in_suggestions_is_also_remembered_by_other_sections(flow):
    await flow.send(text="💬 Talab va taklif")
    await flow.send(contact=flow.contact)
    assert await flow.storage.verified_phone(123, 123) == "+998900000001"
    flow.crm.message.assert_not_awaited()
    await flow.send(text="Ish vaqtini uzaytirish taklifim bor.")
    assert flow.crm.message.await_args.args[0]["text"] == "Talab va taklif:\nIsh vaqtini uzaytirish taklifim bor."
    await flow.send(text="📦 Buyurtmam")
    flow.crm.orders.assert_awaited_once_with(123, "+998900000001")
