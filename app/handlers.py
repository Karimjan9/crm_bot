import logging
from datetime import datetime
from io import BytesIO
from typing import Any
from uuid import uuid4
from zoneinfo import ZoneInfo

from aiogram import Bot, F, Router
from aiogram.filters import CommandStart
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import CallbackQuery, Message, ReplyKeyboardRemove

from app.crm import CrmApiError, CrmClient
from app.formatters import after_hours_text, safe_order_text
from app.keyboards import (
    confirmation_keyboard,
    contact_keyboard,
    main_menu,
    marketing_keyboard,
    suggestions_keyboard,
)
from app.runtime import runtime_for
from app.storage import BotStorage

logger = logging.getLogger(__name__)
router = Router(name="customer")

ALLOWED_DOCUMENT_EXTENSIONS = {".pdf", ".jpg", ".jpeg", ".png"}
ALLOWED_DOCUMENT_MIMES = {"application/pdf", "image/jpeg", "image/png"}
OPERATOR_WORDS = ("operator", "narx", "tezroq", "bog'lan", "bog‘lan")


class Intake(StatesGroup):
    purpose = State()
    document_type = State()
    urgency = State()
    attachment = State()
    name = State()
    contact = State()
    notes = State()
    confirmation = State()


class OrderVerification(StatesGroup):
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
    file = await bot.get_file(attachment["telegram_file_id"])
    content = (await bot.download_file(file.file_path, destination=BytesIO())).getvalue()
    if len(content) > runtime_for(bot).settings.max_upload_bytes:
        raise ValueError("File is bigger than the configured limit")
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
        except (CrmApiError, ValueError):
            await storage.enqueue("attachment", {"lead_id": lead_id, "attachment": attachment})
    return True


async def _request_operator(message: Message, reason: str) -> None:
    crm, storage = _services(message)
    await storage.set_human_handoff(message.chat.id)
    await _dispatch(
        storage,
        crm,
        "operator_request",
        {**_identity(message), "reason": reason, "telegram_message_id": message.message_id},
    )
    settings = runtime_for(message.bot).settings
    note = after_hours_text(
        datetime.now(ZoneInfo(settings.default_timezone)), settings.workday_start, settings.workday_end
    )
    await message.answer(note or "Operatorimizga xabar yubordik. Tez orada siz bilan bog‘lanamiz.")


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


@router.message(F.text == "💬 Talab va taklif")
async def begin_suggestions(message: Message, state: FSMContext) -> None:
    await state.clear()
    await state.set_state(Suggestions.text)
    await message.answer(
        "Talab yoki taklifingizni yozing. Murojaatingiz mas’ullarga yuboriladi.",
        reply_markup=suggestions_keyboard(),
    )


@router.message(F.text == "🆕 Yangi xizmat")
async def begin_intake(message: Message, state: FSMContext) -> None:
    previous = await state.get_data()
    await state.clear()
    await state.update_data(
        intake_id=str(uuid4()),
        entry_payload=previous.get("entry_payload"),
        attachments=[],
        transcript=[],
    )
    await state.set_state(Intake.purpose)
    await message.answer("Hujjat qaysi maqsad uchun kerak: o‘qish, ish, chet el, notarial ish yoki boshqa?")


@router.message(F.text == "📎 Hujjat yuborish")
async def begin_document_upload(message: Message, state: FSMContext) -> None:
    await begin_intake(message, state)
    await state.update_data(purpose="Hujjat yuborildi", transcript=[{"field": "purpose", "value": "Hujjat yuborildi"}])
    await state.set_state(Intake.attachment)
    await message.answer("Hujjat rasmini yoki PDF faylni yuboring. Keyinroq yuborish ham mumkin.")


@router.message(Intake.purpose, F.text)
async def intake_purpose(message: Message, state: FSMContext) -> None:
    data = await state.get_data()
    await state.update_data(purpose=message.text[:500], transcript=_append(data, "purpose", message.text[:500]))
    await state.set_state(Intake.document_type)
    await message.answer("Qaysi hujjat: diplom, pasport, tug‘ilganlik/nikoh guvohnomasi yoki boshqa?")


@router.message(Intake.document_type, F.text)
async def intake_document_type(message: Message, state: FSMContext) -> None:
    data = await state.get_data()
    await state.update_data(
        document_type=message.text[:180], transcript=_append(data, "document_type", message.text[:180])
    )
    await state.set_state(Intake.urgency)
    await message.answer("Qachonga kerak? Masalan: odatiy, shoshilinch yoki aniq sana.")


@router.message(Intake.urgency, F.text)
async def intake_urgency(message: Message, state: FSMContext) -> None:
    data = await state.get_data()
    await state.update_data(urgency=message.text[:120], transcript=_append(data, "urgency", message.text[:120]))
    await state.set_state(Intake.attachment)
    await message.answer(
        "Hujjat rasmini yoki PDF faylni yuboring. Bu majburiy emas — yubormasangiz “Keyin yuboraman” deb yozing."
    )


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
    }


@router.message(Intake.attachment, F.photo | F.document)
async def intake_attachment(message: Message, state: FSMContext) -> None:
    attachment = _attachment_from_message(message)
    if not attachment:
        await message.answer("Faqat JPG, PNG yoki PDF yuboring. Fayl hajmi 20 MB dan oshmasin.")
        return
    data = await state.get_data()
    attachments = list(data.get("attachments", [])) + [attachment]
    await state.update_data(attachments=attachments, transcript=_append(data, "attachment", attachment["file_name"]))
    await message.answer("Qabul qilindi. Yana fayl yuborishingiz yoki “Davom etish” deb yozishingiz mumkin.")


@router.message(Intake.attachment, F.text)
async def intake_attachment_finished(message: Message, state: FSMContext) -> None:
    await state.set_state(Intake.name)
    await message.answer("Ismingizni yozing.")


@router.message(Intake.name, F.text)
async def intake_name(message: Message, state: FSMContext) -> None:
    data = await state.get_data()
    await state.update_data(name=message.text[:160], transcript=_append(data, "name", message.text[:160]))
    await state.set_state(Intake.contact)
    await message.answer("Telefon raqamingizni kontakt sifatida yuboring.", reply_markup=contact_keyboard())


async def _owned_contact(message: Message) -> str | None:
    contact = message.contact
    if not contact or not message.from_user or contact.user_id != message.from_user.id:
        return None
    return contact.phone_number[:40]


@router.message(Intake.contact, F.contact)
async def intake_contact(message: Message, state: FSMContext) -> None:
    phone = await _owned_contact(message)
    if not phone:
        await message.answer("Xavfsizlik uchun faqat o‘zingizning Telegram kontaktingizni yuboring.")
        return
    data = await state.get_data()
    await state.update_data(phone=phone, transcript=_append(data, "phone_confirmed", True))
    await state.set_state(Intake.notes)
    await message.answer("Qo‘shimcha izohingiz bo‘lsa yozing. Bo‘lmasa “Yo‘q” deb yuboring.", reply_markup=ReplyKeyboardRemove())


@router.message(Intake.contact)
async def intake_contact_required(message: Message) -> None:
    await message.answer("Iltimos, pastdagi “Kontaktni yuborish” tugmasi orqali o‘z kontaktingizni yuboring.")


@router.message(Intake.notes, F.text)
async def intake_notes(message: Message, state: FSMContext) -> None:
    data = await state.get_data()
    notes = "" if message.text.casefold() in {"yo‘q", "yo'q", "yoq"} else message.text[:2000]
    await state.update_data(notes=notes, transcript=_append(data, "notes", notes))
    await state.set_state(Intake.confirmation)
    await message.answer(
        "Ma’lumotlaringiz qabul qilishga tayyor. Tasdiqlasangiz, mutaxassis narx va muddatni ko‘rib sizga javob beradi.",
        reply_markup=confirmation_keyboard(),
    )


@router.message(Intake.confirmation, F.text == "✏️ Qayta boshlash")
async def restart_intake(message: Message, state: FSMContext) -> None:
    await begin_intake(message, state)


@router.message(Intake.confirmation, F.text == "✅ Tasdiqlayman")
async def submit_intake(message: Message, state: FSMContext) -> None:
    data = await state.get_data()
    if not all(data.get(key) for key in ("name", "phone", "purpose", "document_type", "urgency")):
        await message.answer("Ma’lumotlar to‘liq emas. Iltimos, qayta boshlang.", reply_markup=main_menu())
        await state.clear()
        return
    payload = {
        "external_id": data.get("intake_id") or str(uuid4()),
        "customer": {**_identity(message), "name": data["name"], "phone": data["phone"], "phone_verified": True},
        "source": {"channel": "telegram", "entry_payload": data.get("entry_payload")},
        "request": {
            "purpose": data["purpose"],
            "document_type": data["document_type"],
            "urgency": data["urgency"],
            "notes": data.get("notes", ""),
        },
        "attachments": data.get("attachments", []),
        "transcript": data.get("transcript", []),
    }
    delivered = await _create_lead_and_upload(message, payload)
    await state.clear()
    text = (
        "Rahmat, so‘rovingiz qabul qilindi. Mutaxassisimiz hujjatingizni ko‘rib, narx va muddat bo‘yicha sizga aloqaga chiqadi."
        if delivered
        else "So‘rovingiz qabul qilindi va aloqa tiklanganda mutaxassisimizga yuboriladi."
    )
    await message.answer(text, reply_markup=main_menu())
    await message.answer("Foydali ma’lumotlar va takliflarni olishga rozimisiz?", reply_markup=marketing_keyboard())


@router.message(F.text == "📦 Buyurtmam")
async def request_orders(message: Message, state: FSMContext) -> None:
    await state.set_state(OrderVerification.contact)
    await message.answer("Buyurtmalaringizni ko‘rish uchun o‘z kontaktingizni yuboring.", reply_markup=contact_keyboard())


@router.message(OrderVerification.contact, F.contact)
async def show_orders(message: Message, state: FSMContext) -> None:
    phone = await _owned_contact(message)
    if not phone:
        await message.answer("Faqat o‘zingizga tegishli Telegram kontaktini yuboring.")
        return
    crm, _ = _services(message)
    try:
        orders = await crm.orders(message.chat.id, phone)
    except CrmApiError:
        await message.answer("Buyurtma holatini hozir tekshirib bo‘lmadi. Birozdan keyin urinib ko‘ring.")
        return
    await state.clear()
    if not orders:
        await message.answer("Faol buyurtma topilmadi. Savolingiz bo‘lsa operatorga yozing.", reply_markup=main_menu())
        return
    await message.answer("\n\n".join(safe_order_text(order) for order in orders), reply_markup=main_menu())


@router.message(OrderVerification.contact)
async def order_contact_required(message: Message) -> None:
    await message.answer("Buyurtmalarni ko‘rish uchun o‘z kontaktingizni yuboring.")


@router.message(F.text == "👩‍💼 Operator")
async def operator_shortcut(message: Message) -> None:
    await _request_operator(message, "customer_requested_operator")


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
        await message.answer(str(content.get("text", "Manzil ma’lumoti yangilanmoqda.")), reply_markup=main_menu())
    except CrmApiError:
        await message.answer("Manzil va ish vaqti ma’lumotini operator aniqlashtirib beradi.", reply_markup=main_menu())


@router.message(F.text == "ℹ️ Foydali ma’lumotlar")
async def useful_info(message: Message) -> None:
    crm, _ = _services(message)
    try:
        content = await crm.content("useful-information")
        await message.answer(str(content.get("text", "Kerakli ma’lumotni operator aytib beradi.")), reply_markup=main_menu())
    except CrmApiError:
        await message.answer("Hujjatni yuboring — mutaxassis sizga aynan kerakli tartibni aytadi.", reply_markup=main_menu())


@router.message(Suggestions.text, F.text == "⬅️ Bosh menyu")
async def cancel_suggestions(message: Message, state: FSMContext) -> None:
    await state.clear()
    await message.answer("Kerakli bo‘limni tanlang.", reply_markup=main_menu())


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
        file = await message.bot.get_file(attachment["telegram_file_id"])
        content = (await message.bot.download_file(file.file_path, destination=BytesIO())).getvalue()
        await crm.upload_message_attachment(payload, content)
    except (CrmApiError, OSError, ValueError):
        await storage.enqueue("message_attachment", payload)
    await message.answer("Faylingiz qabul qilindi va mutaxassisga yuborildi.", reply_markup=main_menu())


@router.message()
async def forward_message(message: Message) -> None:
    if not message.text:
        return
    crm, storage = _services(message)
    text = message.text[:4000]
    is_operator_request = any(word in text.casefold() for word in OPERATOR_WORDS)
    if is_operator_request:
        await _request_operator(message, "keyword_request")
        return
    await _dispatch(
        storage,
        crm,
        "message",
        {**_identity(message), "telegram_message_id": message.message_id, "text": text},
    )
    if not await storage.has_human_handoff(message.chat.id):
        await message.answer("Xabaringiz qabul qilindi. Kerak bo‘lsa “Operator” tugmasini bosing.", reply_markup=main_menu())


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
