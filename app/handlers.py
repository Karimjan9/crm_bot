import logging
from datetime import datetime
from typing import Any
from uuid import uuid4
from zoneinfo import ZoneInfo

from aiogram import Bot, F, Router
from aiogram.exceptions import TelegramAPIError, TelegramBadRequest
from aiogram.filters import CommandStart, StateFilter
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import CallbackQuery, LinkPreviewOptions, Message

from app.attachments import download_attachment
from app.crm import CrmApiError, CrmClient
from app.formatters import after_hours_text, branch_messages, safe_order_text
from app.information import (
    information_catalog,
    information_home,
    information_keyboard,
    information_topic,
)
from app.keyboards import (
    back_keyboard,
    contact_keyboard,
    intake_keyboard,
    main_menu,
    marketing_keyboard,
)
from app.runtime import runtime_for
from app.storage import BotStorage

logger = logging.getLogger(__name__)
router = Router(name="customer")

ALLOWED_DOCUMENT_EXTENSIONS = {".pdf", ".jpg", ".jpeg", ".png"}
ALLOWED_DOCUMENT_MIMES = {"application/pdf", "image/jpeg", "image/png"}
OPERATOR_WORDS = ("operator", "narx", "tezroq", "bog'lan", "bog‘lan")


class Intake(StatesGroup):
    request = State()
    contact = State()


class OrderVerification(StatesGroup):
    contact = State()


class OperatorRequest(StatesGroup):
    text = State()
    contact = State()


class Suggestions(StatesGroup):
    text = State()


def _services(message: Message) -> tuple[CrmClient, BotStorage]:
    runtime = runtime_for(message.bot)
    return runtime.crm, runtime.storage


def _identity(message: Message) -> dict[str, Any]:
    user = message.from_user
    return {
        "telegram_chat_id": str(message.chat.id),
        "telegram_user_id": str(user.id) if user else None,
        "telegram_username": user.username if user else None,
    }


def _append(data: dict[str, Any], field: str, value: Any) -> list[dict[str, Any]]:
    transcript = list(data.get("transcript", []))
    transcript.append({"field": field, "value": value})
    return transcript


async def _dispatch(storage: BotStorage, crm: CrmClient, action: str, payload: dict[str, Any]) -> Any:
    try:
        return await getattr(crm, action)(payload)
    except CrmApiError:
        await storage.enqueue(action, payload)
        return None


async def _upload_attachment(bot: Bot, crm: CrmClient, lead_id: int | str, attachment: dict[str, Any]) -> None:
    content = await download_attachment(bot, attachment)
    await crm.upload_attachment(lead_id, attachment, content)


async def _create_lead_and_upload(message: Message, payload: dict[str, Any]) -> bool:
    crm, storage = _services(message)
    result = await _dispatch(storage, crm, "create_lead", payload)
    if result is None:
        return False
    lead = result.get("data", result)
    lead_id = lead.get("id")
    if not lead_id:
        logger.error("CRM lead response did not include an ID")
        await storage.enqueue("create_lead", payload)
        return False
    await storage.set_lead_id(message.chat.id, lead_id)
    for attachment in payload.get("attachments", []):
        try:
            await _upload_attachment(message.bot, crm, lead_id, attachment)
        except (CrmApiError, TelegramAPIError, OSError, ValueError):
            await storage.enqueue("attachment", {"lead_id": lead_id, "attachment": attachment})
    return True


async def _saved_phone(message: Message) -> str | None:
    if not message.from_user:
        return None
    crm, storage = _services(message)
    phone = await storage.verified_phone(message.chat.id, message.from_user.id)
    if phone:
        return phone
    try:
        phone = await crm.verified_contact(message.chat.id, message.from_user.id)
    except CrmApiError:
        return None
    if phone:
        await storage.save_verified_contact(message.chat.id, message.from_user.id, phone)
    return phone


async def _remember_contact(message: Message) -> str | None:
    phone = await _owned_contact(message)
    if phone:
        _, storage = _services(message)
        await storage.save_verified_contact(message.chat.id, message.from_user.id, phone)
    return phone


async def _submit_operator_request(message: Message, state: FSMContext, phone: str) -> None:
    data = await state.get_data()
    crm, storage = _services(message)
    await _dispatch(
        storage,
        crm,
        "operator_request",
        {
            **_identity(message),
            "external_id": data["operator_request_id"],
            "reason": data.get("operator_reason", "customer_requested_operator"),
            "text": data["operator_text"],
            "telegram_message_id": message.message_id,
            "customer": {
                **_identity(message),
                "name": message.from_user.full_name[:160],
                "phone": phone,
                "phone_verified": True,
            },
        },
    )
    await storage.set_human_handoff(message.chat.id)
    await state.clear()
    await state.update_data(entry_payload=data.get("entry_payload"))
    settings = runtime_for(message.bot).settings
    note = after_hours_text(
        datetime.now(ZoneInfo(settings.default_timezone)), settings.workday_start, settings.workday_end
    )
    text = "Murojaatingiz qabul qilindi. Operator sizga bog‘lanadi."
    if note:
        text += "\n" + note
    await message.answer(text, reply_markup=main_menu())


async def _begin_operator_request(
    message: Message, state: FSMContext, text: str | None = None,
    reason: str = "customer_requested_operator",
) -> None:
    previous = await state.get_data()
    await state.clear()
    await state.update_data(
        entry_payload=previous.get("entry_payload"),
        operator_request_id=str(uuid4()), operator_reason=reason,
    )
    await state.set_state(OperatorRequest.text)
    if text:
        await _accept_operator_text(message, state, text)
        return
    await message.answer(
        "Operatorga murojaatingizni yozing.\n"
        "Masalan: «Diplom tarjimasi narxi va tayyor bo‘lish muddatini bilmoqchiman». "
        "Yoki: «Buyurtmam bo‘yicha yordam kerak».",
        reply_markup=back_keyboard(),
    )


@router.message(F.text == "👩‍💼 Operator")
async def operator_shortcut(message: Message, state: FSMContext) -> None:
    await _begin_operator_request(message, state)


@router.message(CommandStart())
async def start(message: Message, state: FSMContext) -> None:
    parts = (message.text or "").split(maxsplit=1)
    source = parts[1].strip()[:120] if len(parts) > 1 else None
    await state.clear()
    await state.update_data(entry_payload=source, attachments=[], transcript=[])
    await message.answer(
        "Assalomu alaykum. Hujjatingiz bo‘yicha yordam beramiz. Kerakli bo‘limni tanlang "
        "yoki savolingizni shu yerga yozing.",
        reply_markup=main_menu(),
    )


@router.message(F.text == "ℹ️ Foydali ma’lumotlar")
async def useful_info(message: Message) -> None:
    crm, _ = _services(message)
    try:
        content = await crm.content("useful-information")
    except CrmApiError:
        content = {}
    catalog = information_catalog(content)
    texts = information_home(catalog)
    for index, text in enumerate(texts):
        await message.answer(
            text, parse_mode="HTML", link_preview_options=LinkPreviewOptions(is_disabled=True),
            reply_markup=information_keyboard(catalog) if index == len(texts) - 1 else None,
        )


@router.message(F.text == "💬 Talab va taklif")
async def begin_suggestions(message: Message, state: FSMContext) -> None:
    await state.clear()
    await state.set_state(Suggestions.text)
    await message.answer(
        "Talab yoki taklifingizni yozing. Murojaatingiz mas’ullarga yuboriladi.",
        reply_markup=back_keyboard(),
    )


@router.message(F.text.in_({"📝 Yangi murojaat", "🆕 Yangi xizmat", "📎 Hujjat yuborish"}))
async def begin_intake(message: Message, state: FSMContext) -> None:
    previous = await state.get_data()
    await state.clear()
    await state.update_data(
        intake_id=str(uuid4()),
        entry_payload=previous.get("entry_payload"),
        attachments=[],
        transcript=[],
        notes="",
    )
    await state.set_state(Intake.request)
    contact_note = (
        "Kontaktingiz saqlangan. Yakunda «📨 Murojaatni jo‘natish» tugmasini bosing."
        if await _saved_phone(message)
        else "Keyin o‘z kontaktingizni yuborsangiz, murojaatingiz mutaxassisga jo‘natiladi."
    )
    await message.answer(
        "Kerakli xizmatni qisqacha yozing yoki hujjat rasmini/PDF faylni yuboring.\n"
        "Masalan: “Diplomni ingliz tiliga tarjima qilish kerak, 3 kun ichida”.\n"
        + contact_note,
        reply_markup=back_keyboard(),
    )


@router.message(F.text == "⬅️ Bosh menyu")
async def return_to_menu(message: Message, state: FSMContext) -> None:
    data = await state.get_data()
    await state.clear()
    await state.update_data(entry_payload=data.get("entry_payload"))
    await message.answer("Kerakli bo‘limni tanlang.", reply_markup=main_menu())


async def _intake_ready(message: Message, prefix: str) -> None:
    saved = bool(await _saved_phone(message))
    instruction = (
        "Kontaktingiz saqlangan. Jo‘natish uchun «📨 Murojaatni jo‘natish» tugmasini bosing."
        if saved
        else "Murojaatni jo‘natish uchun pastdagi tugma orqali o‘z kontaktingizni yuboring."
    )
    await message.answer(
        prefix + " Yana fayl yoki izoh qo‘shishingiz mumkin. " + instruction,
        reply_markup=intake_keyboard(contact_saved=saved),
    )


@router.message(Intake.contact, F.text == "📨 Murojaatni jo‘natish")
async def submit_intake_saved(message: Message, state: FSMContext) -> None:
    phone = await _saved_phone(message)
    if not phone:
        await message.answer("O‘z kontaktingizni yuboring.", reply_markup=intake_keyboard())
        return
    await _submit_intake(message, state, phone)


@router.message(StateFilter(Intake.request, Intake.contact), F.text)
async def intake_text(message: Message, state: FSMContext) -> None:
    data = await state.get_data()
    text = (message.text or "").strip()
    if not text:
        await message.answer("Murojaatingizni yozing yoki hujjat rasmini/PDF faylni yuboring.")
        return
    notes = "\n".join(part for part in (data.get("notes", ""), text) if part)
    if len(notes) > 2000:
        await message.answer("Murojaat va izohlar jami 2000 belgidan oshmasin. Qisqaroq yozing.")
        return
    await state.update_data(notes=notes, transcript=_append(data, "request", text))
    await state.set_state(Intake.contact)
    await _intake_ready(message, "Qabul qilindi.")


def _attachment_from_message(message: Message) -> dict[str, Any] | None:
    settings = runtime_for(message.bot).settings
    if message.photo:
        photo = message.photo[-1]
        if photo.file_size and photo.file_size > settings.max_upload_bytes:
            return None
        return {
            "telegram_file_id": photo.file_id,
            "telegram_message_id": message.message_id,
            "file_name": f"photo-{message.message_id}.jpg",
            "mime_type": "image/jpeg",
            "size": photo.file_size or 0,
            "kind": "photo",
            "caption": (message.caption or "").strip()[:2000],
        }
    document = message.document
    if not document or (document.file_size and document.file_size > settings.max_upload_bytes):
        return None
    filename = document.file_name or f"document-{message.message_id}"
    extension = "." + filename.rsplit(".", 1)[-1].lower() if "." in filename else ""
    if extension not in ALLOWED_DOCUMENT_EXTENSIONS or document.mime_type not in ALLOWED_DOCUMENT_MIMES:
        return None
    return {
        "telegram_file_id": document.file_id,
        "telegram_message_id": message.message_id,
        "file_name": filename[:180],
        "mime_type": document.mime_type,
        "size": document.file_size or 0,
        "kind": "document",
        "caption": (message.caption or "").strip()[:2000],
    }


@router.message(StateFilter(Intake.request, Intake.contact), F.photo | F.document)
async def intake_attachment(message: Message, state: FSMContext) -> None:
    attachment = _attachment_from_message(message)
    if not attachment:
        await message.answer("Faqat JPG, PNG yoki PDF yuboring. Fayl hajmi 20 MB dan oshmasin.")
        return
    data = await state.get_data()
    attachments = list(data.get("attachments", []))
    if len(attachments) >= 10:
        await message.answer("Bitta murojaatga ko‘pi bilan 10 ta fayl qo‘shish mumkin.")
        return
    notes = "\n".join(
        part for part in (data.get("notes", ""), (message.caption or "").strip()) if part
    )
    if len(notes) > 2000:
        await message.answer("Murojaat va izohlar jami 2000 belgidan oshmasin. Izohni qisqartiring.")
        return
    attachments.append(attachment)
    await state.update_data(
        attachments=attachments,
        notes=notes,
        transcript=_append(data, "attachment", attachment["file_name"]),
    )
    await state.set_state(Intake.contact)
    await _intake_ready(message, "Hujjat qabul qilindi.")


async def _owned_contact(message: Message) -> str | None:
    contact = message.contact
    if not contact or not message.from_user or contact.user_id != message.from_user.id:
        return None
    return contact.phone_number[:40]


@router.message(StateFilter(Intake.request, Intake.contact), F.contact)
async def submit_intake(message: Message, state: FSMContext) -> None:
    phone = await _remember_contact(message)
    if not phone:
        await message.answer("Xavfsizlik uchun faqat o‘zingizning Telegram kontaktingizni yuboring.")
        return
    await _submit_intake(message, state, phone)


async def _submit_intake(message: Message, state: FSMContext, phone: str) -> None:
    data = await state.get_data()
    if not data.get("notes") and not data.get("attachments"):
        await message.answer("Avval murojaatingizni yozing yoki hujjat rasmini/PDF faylni yuboring.")
        return
    notes = data.get("notes", "")
    payload = {
        "external_id": data.get("intake_id") or str(uuid4()),
        "customer": {
            **_identity(message),
            "name": message.from_user.full_name[:160],
            "phone": phone,
            "phone_verified": True,
        },
        "source": {"channel": "telegram", "entry_payload": data.get("entry_payload")},
        "request": {
            "mode": "compact",
            # Keep these fields until the deployed CRM also accepts compact requests.
            "purpose": notes[:500] or "Hujjat bo‘yicha murojaat",
            "document_type": "Mutaxassis aniqlashtiradi",
            "urgency": "Mutaxassis aniqlashtiradi",
            "notes": notes,
        },
        "attachments": data.get("attachments", []),
        "transcript": _append(data, "phone_confirmed", True)[:50],
    }
    delivered = await _create_lead_and_upload(message, payload)
    await state.clear()
    await state.update_data(entry_payload=data.get("entry_payload"))
    text = (
        "Rahmat, so‘rovingiz qabul qilindi. Mutaxassisimiz hujjatingizni ko‘rib, narx va muddat bo‘yicha sizga aloqaga chiqadi."
        if delivered
        else "So‘rovingiz qabul qilindi va aloqa tiklanganda mutaxassisimizga yuboriladi."
    )
    await message.answer(text, reply_markup=main_menu())
    await message.answer("Foydali ma’lumotlar va takliflarni olishga rozimisiz?", reply_markup=marketing_keyboard())


@router.message(StateFilter(Intake.request, Intake.contact))
async def intake_message_required(message: Message) -> None:
    await message.answer("Murojaat matni, JPG/PNG/PDF fayl yoki o‘z kontaktingizni yuboring.")


@router.message(F.text == "📦 Buyurtmam")
async def request_orders(message: Message, state: FSMContext) -> None:
    await state.clear()
    phone = await _saved_phone(message)
    if phone:
        await _show_orders(message, state, phone)
        return
    await state.set_state(OrderVerification.contact)
    await message.answer("Buyurtmalaringizni ko‘rish uchun o‘z kontaktingizni yuboring.", reply_markup=contact_keyboard())


@router.message(OrderVerification.contact, F.contact)
async def show_orders(message: Message, state: FSMContext) -> None:
    phone = await _remember_contact(message)
    if not phone:
        await message.answer("Faqat o‘zingizga tegishli Telegram kontaktini yuboring.")
        return
    await _show_orders(message, state, phone)


async def _show_orders(message: Message, state: FSMContext, phone: str) -> None:
    crm, _ = _services(message)
    try:
        orders = await crm.orders(message.chat.id, phone)
    except CrmApiError:
        await state.clear()
        await message.answer("Buyurtma holatini hozir tekshirib bo‘lmadi. Birozdan keyin urinib ko‘ring.", reply_markup=main_menu())
        return
    await state.clear()
    if not orders:
        await message.answer("Faol buyurtma topilmadi. Savolingiz bo‘lsa operatorga yozing.", reply_markup=main_menu())
        return
    await message.answer("\n\n".join(safe_order_text(order) for order in orders), reply_markup=main_menu())


@router.message(OrderVerification.contact)
async def order_contact_required(message: Message) -> None:
    await message.answer("Buyurtmalarni ko‘rish uchun o‘z kontaktingizni yuboring.")


async def _accept_operator_text(message: Message, state: FSMContext, text: str) -> None:
    text = text.strip()
    if not text or len(text) > 2000:
        await message.answer("Murojaatingizni 1–2000 belgidan iborat matn ko‘rinishida yozing.")
        return
    data = await state.get_data()
    previous = data.get("operator_text", "")
    text = "\n".join(part for part in (previous, text) if part)
    if len(text) > 2000:
        await message.answer("Murojaat va izohlar jami 2000 belgidan oshmasin.")
        return
    await state.update_data(operator_text=text)
    phone = await _saved_phone(message)
    if phone:
        await _submit_operator_request(message, state, phone)
        return
    await state.set_state(OperatorRequest.contact)
    await message.answer(
        "Operator sizga bog‘lanishi uchun o‘z kontaktingizni yuboring. "
        "Kontaktingiz saqlanadi va keyingi safar qayta so‘ralmaydi.",
        reply_markup=contact_keyboard(),
    )


@router.message(StateFilter(OperatorRequest.text, OperatorRequest.contact), F.text)
async def operator_text(message: Message, state: FSMContext) -> None:
    await _accept_operator_text(message, state, message.text or "")


@router.message(StateFilter(OperatorRequest.text, OperatorRequest.contact), F.contact)
async def operator_contact(message: Message, state: FSMContext) -> None:
    phone = await _remember_contact(message)
    if not phone:
        await message.answer("Faqat o‘zingizning Telegram kontaktingizni yuboring.")
        return
    if not (await state.get_data()).get("operator_text"):
        await message.answer("Kontaktingiz saqlandi. Endi operatorga murojaatingizni yozing.", reply_markup=back_keyboard())
        return
    await _submit_operator_request(message, state, phone)


@router.message(StateFilter(OperatorRequest.text, OperatorRequest.contact))
async def operator_message_required(message: Message) -> None:
    await message.answer("Operatorga murojaat matnini yoki o‘z kontaktingizni yuboring.")


@router.message(F.text.in_({"✅ Roziman", "❌ Kerak emas"}))
async def marketing_consent(message: Message) -> None:
    crm, storage = _services(message)
    consent = message.text == "✅ Roziman"
    await _dispatch(storage, crm, "marketing_consent", {**_identity(message), "consent": consent})
    await message.answer(
        "Tanlovingiz saqlandi. Servis xabarlari buyurtmangizga qarab davom etadi.", reply_markup=main_menu()
    )


@router.message(F.text == "Xabarlarni to‘xtatish")
async def stop_marketing(message: Message) -> None:
    crm, storage = _services(message)
    await _dispatch(storage, crm, "marketing_consent", {**_identity(message), "consent": False})
    await message.answer("Marketing xabarlari to‘xtatildi. Buyurtmangiz bo‘yicha servis xabarlari davom etadi.", reply_markup=main_menu())


@router.message(F.text == "📍 Manzil va ish vaqti")
async def branches(message: Message) -> None:
    crm, _ = _services(message)
    try:
        content = await crm.content("branches")
    except CrmApiError:
        content = {}
    for text in branch_messages(content):
        await message.answer(text, reply_markup=main_menu())


@router.message(Suggestions.text, F.text)
async def submit_suggestion(message: Message, state: FSMContext) -> None:
    text = (message.text or "").strip()
    if not text:
        await message.answer("Talab yoki taklifingizni matn ko‘rinishida yozing.")
        return
    crm, storage = _services(message)
    await _dispatch(
        storage,
        crm,
        "message",
        {
            **_identity(message),
            "telegram_message_id": message.message_id,
            "text": ("Talab va taklif:\n" + text)[:4000],
        },
    )
    await state.clear()
    await message.answer("Murojaatingiz qabul qilindi. Rahmat!", reply_markup=main_menu())


@router.message(Suggestions.text, F.contact)
async def suggestion_contact(message: Message) -> None:
    if not await _remember_contact(message):
        await message.answer("Faqat o‘zingizning Telegram kontaktingizni yuboring.")
        return
    await message.answer("Kontaktingiz saqlandi. Endi talab yoki taklifingizni yozing.", reply_markup=back_keyboard())


@router.message(Suggestions.text)
async def suggestion_text_required(message: Message) -> None:
    await message.answer("Talab yoki taklifingizni matn ko‘rinishida yuboring.")


@router.message(F.photo | F.document)
async def forward_attachment(message: Message) -> None:
    attachment = _attachment_from_message(message)
    if not attachment:
        await message.answer("Faqat JPG, PNG yoki PDF yuboring. Fayl hajmi 20 MB dan oshmasin.")
        return
    crm, storage = _services(message)
    payload = {**_identity(message), "attachment": attachment, "caption": (message.caption or "")[:2000]}
    try:
        content = await download_attachment(message.bot, attachment)
        await crm.upload_message_attachment(payload, content)
    except (CrmApiError, TelegramAPIError, OSError, ValueError):
        await storage.enqueue("message_attachment", payload)
    await message.answer("Faylingiz qabul qilindi va mutaxassisga yuborildi.", reply_markup=main_menu())


@router.message(F.contact)
async def remember_shared_contact(message: Message) -> None:
    if not await _remember_contact(message):
        await message.answer("Faqat o‘zingizning Telegram kontaktingizni yuboring.")
        return
    await message.answer("Kontaktingiz saqlandi. Keyingi safar qayta so‘ralmaydi.", reply_markup=main_menu())


@router.message()
async def forward_message(message: Message, state: FSMContext) -> None:
    if not message.text:
        return
    crm, storage = _services(message)
    text = message.text[:4000]
    is_operator_request = any(word in text.casefold() for word in OPERATOR_WORDS)
    if is_operator_request:
        await _begin_operator_request(message, state, text[:2000], "keyword_request")
        return
    await _dispatch(
        storage,
        crm,
        "message",
        {**_identity(message), "telegram_message_id": message.message_id, "text": text},
    )
    if not await storage.has_human_handoff(message.chat.id):
        await message.answer("Xabaringiz qabul qilindi. Kerak bo‘lsa “Operator” tugmasini bosing.", reply_markup=main_menu())


@router.callback_query(F.data.startswith("info:"))
async def information_callback(callback: CallbackQuery, state: FSMContext) -> None:
    if not isinstance(callback.message, Message) or callback.message.chat.type != "private" or callback.message.chat.id != callback.from_user.id:
        await callback.answer("Ma’lumotni botning shaxsiy chatida oching.", show_alert=True)
        return
    data = callback.data or ""
    if data not in {"info:home", "info:intake"} and not data.startswith("info:topic:"):
        await callback.answer("Tugmani qayta oching.", show_alert=True)
        return
    await callback.answer()
    if data == "info:intake":
        # A callback message belongs to the bot; use the person who pressed the
        # button so contact lookup and subsequent intake use the correct user.
        user_message = callback.message.model_copy(update={"from_user": callback.from_user}).as_(callback.bot)
        await begin_intake(user_message, state)
        return
    runtime = runtime_for(callback.bot)
    try:
        content = await runtime.crm.content("useful-information")
    except CrmApiError:
        content = {}
    catalog = information_catalog(content)
    topic = next((item for item in catalog["topics"] if "info:topic:" + item["id"] == data), None)
    texts = information_topic(topic) if topic else information_home(catalog)
    if data.startswith("info:topic:") and topic is None:
        texts[0] = "<i>Mavzu yangilangan yoki hozircha mavjud emas.</i>\n\n" + texts[0]
    keyboard = information_keyboard(catalog, back=topic is not None)
    for index, text in enumerate(texts):
        options = {
            "parse_mode": "HTML", "link_preview_options": LinkPreviewOptions(is_disabled=True),
            "reply_markup": keyboard if index == len(texts) - 1 else None,
        }
        if index == 0:
            try:
                await callback.message.edit_text(text, **options)
            except TelegramBadRequest as error:
                if "message is not modified" not in error.message.casefold():
                    await callback.message.answer(text, **options)
        else:
            await callback.message.answer(text, **options)


@router.callback_query(F.data.startswith("feedback:"))
async def feedback(callback: CallbackQuery) -> None:
    try:
        _, order_id, rating = (callback.data or "").split(":")
        order_id, rating = int(order_id), int(rating)
        if rating not in range(1, 6):
            raise ValueError
    except ValueError:
        await callback.answer("Bahoni o‘qib bo‘lmadi.", show_alert=True)
        return
    runtime = runtime_for(callback.bot)
    payload = {"telegram_chat_id": str(callback.message.chat.id), "order_id": order_id, "rating": rating}
    await _dispatch(runtime.storage, runtime.crm, "feedback", payload)
    await callback.answer("Rahmat!")
    await callback.message.answer("Fikringiz uchun rahmat. Sizning bahoyingiz biz uchun muhim.")
