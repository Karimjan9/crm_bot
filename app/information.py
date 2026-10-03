import html
import re
from typing import Any

from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup

ICONS = {
    "document": "📄", "photo": "📷", "checklist": "✅", "clock": "🕒",
    "question": "❓", "order": "📦", "info": "ℹ️", "support": "💬",
}
DEFAULT_INTRO = "Ma’lumotlar hozircha yuklanmadi. Savolingizni “📝 Yangi murojaat” yoki “👩‍💼 Operator” orqali yuborishingiz mumkin."


def information_catalog(content: Any) -> dict[str, Any]:
    if not isinstance(content, dict):
        content = {}
    topics = []
    seen = set()
    for item in content.get("topics", []) if isinstance(content.get("topics"), list) else []:
        if not isinstance(item, dict) or item.get("published") is False:
            continue
        topic_id = str(item.get("id") or "")
        if not re.fullmatch(r"[a-z0-9_-]{1,40}", topic_id) or topic_id in seen or not item.get("title"):
            continue
        seen.add(topic_id)
        checklist = item.get("checklist")
        topics.append({
            "id": topic_id, "icon": str(item.get("icon") or "info"),
            "title": str(item["title"])[:70], "summary": str(item.get("summary") or ""),
            "body": str(item.get("body") or ""), "tip": str(item.get("tip") or ""),
            "checklist": [str(line) for line in checklist if str(line).strip()] if isinstance(checklist, list) else [],
        })
        if len(topics) == 12:
            break
    # The original API had only `text`; keep that content readable during rollout.
    intro = content.get("intro") if "intro" in content else content.get("text")
    fallback = "Kerakli mavzuni tanlang." if topics else DEFAULT_INTRO
    if not topics and "intro" in content and isinstance(content.get("topics"), list):
        fallback = "Hozircha e’lon qilingan mavzular yo‘q. Savolingizni “Yangi murojaat” orqali yuboring."
    return {
        "title": str(content.get("title") or "Foydali ma’lumotlar")[:80],
        "intro": str(intro or "").strip() or fallback,
        "topics": topics,
    }


def _units(text: str) -> int:
    return len(text.encode("utf-16-le")) // 2


def _html_messages(blocks: list[tuple[str, str]]) -> list[str]:
    messages = []
    current = ""
    for text, tag in blocks:
        if not text:
            continue
        opening, closing = (f"<{tag}>", f"</{tag}>") if tag else ("", "")
        pieces = []
        escaped = ""
        units = _units(opening + closing)
        for char in text:
            token = html.escape(char)
            size = _units(token)
            if units + size > 3400:
                pieces.append(opening + escaped + closing)
                escaped, units = "", _units(opening + closing)
            escaped += token
            units += size
        if escaped:
            pieces.append(opening + escaped + closing)
        for piece in pieces:
            combined = current + ("\n\n" if current else "") + piece
            if _units(combined) > 3900:
                messages.append(current)
                current = piece
            else:
                current = combined
    if current:
        messages.append(current)
    return messages


def information_home(catalog: dict[str, Any]) -> list[str]:
    blocks = [("📚 " + catalog["title"], "b"), (catalog["intro"], "")]
    if catalog["topics"]:
        blocks.append(("👇 Mavzuni tanlang — tavsiya va eslatmalarni ko‘ring.", ""))
    return _html_messages(blocks)


def information_topic(topic: dict[str, Any]) -> list[str]:
    blocks = [
        (ICONS.get(topic["icon"], "ℹ️") + " " + topic["title"], "b"),
        (topic["summary"], "i"), (topic["body"], ""),
    ]
    if topic["checklist"]:
        blocks.extend([("✅ Amaliy ro‘yxat", "b"), ("\n".join("▫️ " + line for line in topic["checklist"]), "")])
    if topic["tip"]:
        blocks.extend([("💡 Eslatma", "b"), (topic["tip"], "")])
    return _html_messages(blocks)


def information_keyboard(catalog: dict[str, Any], *, back: bool = False) -> InlineKeyboardMarkup:
    rows = []
    if not back:
        for topic in catalog["topics"]:
            rows.append([InlineKeyboardButton(
                text=ICONS.get(topic["icon"], "ℹ️") + " " + topic["title"],
                callback_data="info:topic:" + topic["id"],
            )])
    rows.append([InlineKeyboardButton(text="📝 Yangi murojaat", callback_data="info:intake")])
    if back:
        rows.append([InlineKeyboardButton(text="◀️ Mavzularga qaytish", callback_data="info:home")])
    return InlineKeyboardMarkup(inline_keyboard=rows)
