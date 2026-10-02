from io import BytesIO
from typing import Any

from aiogram import Bot

from app.runtime import runtime_for


async def download_attachment(bot: Bot, attachment: dict[str, Any]) -> bytes:
    limit = runtime_for(bot).settings.max_upload_bytes
    remote = await bot.get_file(attachment["telegram_file_id"])
    if (getattr(remote, "file_size", None) or 0) > limit:
        raise ValueError("File is bigger than the configured limit")
    if not remote.file_path:
        raise ValueError("Telegram did not provide a file path")
    content = (await bot.download_file(remote.file_path, destination=BytesIO())).getvalue()
    if len(content) > limit:
        raise ValueError("File is bigger than the configured limit")
    return content
