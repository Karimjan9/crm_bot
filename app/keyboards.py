from aiogram.types import KeyboardButton, ReplyKeyboardMarkup


def main_menu() -> ReplyKeyboardMarkup:
    return ReplyKeyboardMarkup(
        keyboard=[
            [KeyboardButton(text="📝 Yangi murojaat")],
            [KeyboardButton(text="📦 Buyurtmam"), KeyboardButton(text="💬 Talab va taklif")],
            [KeyboardButton(text="👩‍💼 Operator"), KeyboardButton(text="📍 Manzil va ish vaqti")],
            [KeyboardButton(text="ℹ️ Foydali ma’lumotlar")],
        ],
        resize_keyboard=True,
    )


def back_keyboard() -> ReplyKeyboardMarkup:
    return ReplyKeyboardMarkup(
        keyboard=[[KeyboardButton(text="⬅️ Bosh menyu")]],
        resize_keyboard=True,
    )


def contact_keyboard() -> ReplyKeyboardMarkup:
    return ReplyKeyboardMarkup(
        keyboard=[
            [KeyboardButton(text="📱 Kontaktni yuborish", request_contact=True)],
            [KeyboardButton(text="⬅️ Bosh menyu")],
        ],
        resize_keyboard=True,
        one_time_keyboard=True,
    )


def intake_keyboard(contact_saved: bool = False) -> ReplyKeyboardMarkup:
    return ReplyKeyboardMarkup(
        keyboard=[
            [KeyboardButton(text="📨 Murojaatni jo‘natish")]
            if contact_saved
            else [KeyboardButton(text="📱 Kontaktni yuborish va jo‘natish", request_contact=True)],
            [KeyboardButton(text="⬅️ Bosh menyu")],
        ],
        resize_keyboard=True,
    )


def marketing_keyboard() -> ReplyKeyboardMarkup:
    return ReplyKeyboardMarkup(
        keyboard=[[KeyboardButton(text="✅ Roziman"), KeyboardButton(text="❌ Kerak emas")]],
        resize_keyboard=True,
        one_time_keyboard=True,
    )
