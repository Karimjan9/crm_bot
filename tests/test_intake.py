from copy import deepcopy
from datetime import UTC, datetime
from io import BytesIO
from types import SimpleNamespace
from unittest.mock import AsyncMock

from aiogram import Bot, Dispatcher
from aiogram.fsm.storage.memory import MemoryStorage, SimpleEventIsolation
from aiogram.types import Chat, Contact, Document, Message, PhotoSize, Update, User

from app.config import Settings
from app.crm import CrmApiError
from app.handlers import Intake, router
from app.keyboards import main_menu
from app.runtime import Runtime, register_runtime, unregister_runtime


async def test_two_step_intake_submits_text_and_files_with_verified_contact():
    settings = Settings(
        _env_file=None,
        bot_token="123456789:unit-test-only-token",
        crm_base_url="https://crm.invalid/api/v1",
        crm_bot_api_key="test-key",
        crm_webhook_secret="test-secret",
    )
    chat = Chat(id=123, type="private")
    user = User(id=123, is_bot=False, first_name="Ali", last_name="Valiyev")
    session = AsyncMock(
        return_value=Message(message_id=100, date=datetime.now(UTC), chat=chat)
    )
    bot = Bot(settings.bot_token.get_secret_value(), session=session)
    bot.get_file = AsyncMock(return_value=SimpleNamespace(file_path="test.pdf"))
    bot.download_file = AsyncMock(return_value=BytesIO(b"%PDF-1.4 test"))
    crm = SimpleNamespace(
        create_lead=AsyncMock(return_value={"data": {"id": 456}}),
        upload_attachment=AsyncMock(),
        operator_request=AsyncMock(),
        verified_contact=AsyncMock(return_value=None),
    )
    storage = SimpleNamespace(
        set_lead_id=AsyncMock(), enqueue=AsyncMock(),
        verified_phone=AsyncMock(return_value=None), save_verified_contact=AsyncMock(),
    )
    register_runtime(bot, Runtime(settings=settings, crm=crm, storage=storage))
    dispatcher = Dispatcher(storage=MemoryStorage(), events_isolation=SimpleEventIsolation())
    dispatcher.include_router(deepcopy(router))
    state = dispatcher.fsm.get_context(bot=bot, chat_id=chat.id, user_id=user.id)
    own_contact = Contact(phone_number="+998900000001", first_name="Ali", user_id=user.id)
    document = Document(
        file_id="test-pdf", file_unique_id="unique-pdf", file_name="diplom.pdf",
        mime_type="application/pdf", file_size=20,
    )
    sequence = 0

    async def send(**content):
        nonlocal sequence
        sequence += 1
        message = Message(
            message_id=sequence, date=datetime.now(UTC),
            chat=chat, from_user=user, **content,
        )
        await dispatcher.feed_update(bot, Update(update_id=sequence, message=message))

    try:
        await send(text="/start instagram_korea")
        await send(text="📝 Yangi murojaat")
        await send(contact=own_contact)
        await send(text="   ")
        await send(text="x" * 2001)
        assert await state.get_state() == Intake.request.state
        crm.create_lead.assert_not_awaited()

        description = "Diplomni ingliz tiliga tarjima qilish kerak, 3 kun ichida."
        await send(text=description)
        assert await state.get_state() == Intake.contact.state
        await send(contact=Contact(phone_number="+998900000002", first_name="Other", user_id=999))
        crm.create_lead.assert_not_awaited()
        await send(contact=own_contact)
        crm.create_lead.assert_awaited_once()
        payload = crm.create_lead.await_args.args[0]
        assert payload["customer"]["name"] == "Ali Valiyev"
        assert payload["customer"]["phone_verified"] is True
        assert payload["source"]["entry_payload"] == "instagram_korea"
        assert payload["request"]["notes"] == description
        assert payload["request"]["mode"] == "compact"
        assert payload["attachments"] == []
        assert await state.get_state() is None
        crm.operator_request.assert_not_awaited()
        await send(contact=own_contact)
        assert crm.create_lead.await_count == 1

        crm.create_lead.reset_mock()
        await send(text="📎 Hujjat yuborish")
        await send(document=document)
        await send(contact=own_contact)
        payload = crm.create_lead.await_args.args[0]
        assert payload["request"]["purpose"] == "Hujjat bo‘yicha murojaat"
        assert payload["request"]["notes"] == ""
        assert payload["request"]["document_type"]
        assert payload["request"]["urgency"]
        assert len(payload["attachments"]) == 1
        crm.upload_attachment.assert_awaited_once()

        crm.upload_attachment.reset_mock()
        await send(text="📝 Yangi murojaat")
        await send(document=document, caption="Diplom, ingliz tiliga.")
        await send(photo=[PhotoSize(
            file_id="test-photo", file_unique_id="unique-photo",
            width=10, height=10, file_size=20,
        )])
        await send(text="Juma kuniga kerak.")
        await send(contact=own_contact)
        payload = crm.create_lead.await_args.args[0]
        assert len(payload["attachments"]) == 2
        assert payload["request"]["notes"] == "Diplom, ingliz tiliga.\nJuma kuniga kerak."
        assert crm.upload_attachment.await_count == 2
        assert payload["attachments"][0]["caption"] == "Diplom, ingliz tiliga."
        assert crm.upload_attachment.await_args_list[0].args[1]["caption"] == "Diplom, ingliz tiliga."

        previous_count = crm.create_lead.await_count
        await send(text="🆕 Yangi xizmat")
        for _ in range(11):
            await send(document=document)
        assert len((await state.get_data())["attachments"]) == 10
        await send(text="⬅️ Bosh menyu")
        assert await state.get_state() is None
        assert crm.create_lead.await_count == previous_count
        assert session.call_args.args[1].reply_markup == main_menu()

        crm.create_lead.side_effect = CrmApiError("offline test")
        await send(text="📝 Yangi murojaat")
        await send(text="Pasport tarjimasi kerak.")
        intake_id = (await state.get_data())["intake_id"]
        await send(contact=own_contact)
        storage.enqueue.assert_awaited_once()
        action, queued = storage.enqueue.await_args.args
        assert action == "create_lead" and queued["external_id"] == intake_id
        assert queued["request"]["notes"] == "Pasport tarjimasi kerak."
        assert await state.get_state() is None
    finally:
        unregister_runtime(bot)
        await dispatcher.fsm.close()
        await bot.session.close()
