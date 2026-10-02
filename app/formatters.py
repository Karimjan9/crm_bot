from datetime import datetime

STATUS_LABELS = {
    "received": "So‘rov qabul qilindi",
    "waiting_documents": "Hujjatlar tekshirilmoqda",
    "awaiting_payment": "To‘lov kutilmoqda",
    "partially_paid": "Qisman to‘langan",
    "paid": "To‘lov qabul qilindi",
    "in_processing": "Ish jarayonda",
    "waiting_review": "Hujjatlar tekshirilmoqda",
    "ready_for_delivery": "Tayyor",
    "courier_sent": "Kuryerga topshirildi",
    "delivered": "Mijozga topshirildi",
    "completed": "Mijozga topshirildi",
}

DEFAULT_BRANCH_TEXT = (
    "Standart ish vaqti: 09:00–18:00.\n"
    "Manzil va aniq ish vaqtini operator orqali aniqlashtiring."
)


def branch_messages(content: dict) -> list[str]:
    text = str(content.get("text") or "").strip() or DEFAULT_BRANCH_TEXT
    messages = []
    while text:
        units = 0
        end = 0
        for char in text:
            size = 2 if ord(char) > 0xFFFF else 1
            if units + size > 4000:
                break
            units += size
            end += 1
        if end < len(text):
            # Prefer separating whole branches, then lines, while respecting Telegram's limit.
            boundary = text.rfind("\n\n", 0, end)
            if boundary < end // 2:
                boundary = text.rfind("\n", 0, end)
            if boundary > 0:
                end = boundary
        messages.append(text[:end].strip())
        text = text[end:].lstrip()
    return messages


def safe_order_text(order: dict) -> str:
    """Render only fields explicitly safe for a customer to see."""
    status = STATUS_LABELS.get(str(order.get("status", "")), "Holat yangilanmoqda")
    lines = [f"Buyurtma: {order.get('code', '—')}", f"Holati: {status}"]
    if order.get("promised_at"):
        lines.append(f"Tayyor bo‘lish sanasi: {order['promised_at']}")
    if order.get("paid_amount") is not None:
        lines.append(f"To‘langan: {order['paid_amount']} {order.get('currency', 'UZS')}")
    if order.get("balance_amount") is not None:
        lines.append(f"Qoldiq: {order['balance_amount']} {order.get('currency', 'UZS')}")
    if order.get("delivery_type"):
        lines.append(f"Olish usuli: {order['delivery_type']}")
    return "\n".join(lines)


def after_hours_text(now: datetime, start: str, end: str) -> str | None:
    current = now.strftime("%H:%M")
    if now.weekday() >= 5 or current < start or current >= end:
        return "Xabaringiz qabul qilindi. Operatorimiz ish vaqtida siz bilan aloqaga chiqadi."
    return None
