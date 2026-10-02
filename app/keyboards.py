from aiogram.types import KeyboardButton, ReplyKeyboardMarkup


def main_menu() -> ReplyKeyboardMarkup:
    return ReplyKeyboardMarkup(
        keyboard=[
            [KeyboardButton(text="🆕 Yangi xizmat"), KeyboardButton(text="📎 Hujjat yuborish")],
            [KeyboardButton(text="📦 Buyurtmam"), KeyboardButton(text="💬 Talab va taklif")],
            [KeyboardButton(text="👩‍💼 Operator"), KeyboardButton(text="📍 Manzil va ish vaqti")],
            [KeyboardButton(text="ℹ️ Foydali ma’lumotlar")],
        ],
        resize_keyboard=True,
    )


def suggestions_keyboard() -> ReplyKeyboardMarkup:
    return ReplyKeyboardMarkup(
        keyboard=[[KeyboardButton(text="⬅️ Bosh menyu")]],
        resize_keyboard=True,
    )


def contact_keyboard() -> ReplyKeyboardMarkup:
    return ReplyKeyboardMarkup(
        keyboard=[[KeyboardButton(text="📱 Kontaktni yuborish", request_contact=True)]],
        resize_keyboard=True,
        one_time_keyboard=True,
    )


def confirmation_keyboard() -> ReplyKeyboardMarkup:
    return ReplyKeyboardMarkup(
        keyboard=[[KeyboardButton(text="✅ Tasdiqlayman"), KeyboardButton(text="✏️ Qayta boshlash")]],
        resize_keyboard=True,
        one_time_keyboard=True,
    )


def marketing_keyboard() -> ReplyKeyboardMarkup:
    return ReplyKeyboardMarkup(
        keyboard=[[KeyboardButton(text="✅ Roziman"), KeyboardButton(text="❌ Kerak emas")]],
        resize_keyboard=True,
        one_time_keyboard=True,
    )
