from io import BytesIO
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from aiogram import Bot

from app.config import Settings
from app.handlers import _upload_attachment
from app.runtime import Runtime, register_runtime, unregister_runtime
from app.worker import upload_attachment as upload_queued_attachment


@pytest.fixture
def attachment_runtime():
    settings = Settings(
        _env_file=None,
        bot_token="123456789:unit-test-only-token",
        crm_base_url="https://crm.invalid/api/v1",
        crm_bot_api_key="test-key",
        crm_webhook_secret="test-secret",
        max_upload_bytes=1_000_000,
    )
    bot = Bot(settings.bot_token.get_secret_value())
    bot.get_file = AsyncMock(return_value=SimpleNamespace(file_path="test.pdf"))
    bot.download_file = AsyncMock(return_value=BytesIO(b"%PDF-1.4 test"))
    crm = SimpleNamespace(upload_attachment=AsyncMock())
    runtime = Runtime(settings=settings, crm=crm, storage=None)
    register_runtime(bot, runtime)
    try:
        yield bot, crm, settings
    finally:
        unregister_runtime(bot)


@pytest.mark.parametrize('upload', [_upload_attachment, upload_queued_attachment])
async def test_uploads_attachment_with_registered_runtime(attachment_runtime, upload):
    bot, crm, _ = attachment_runtime
    attachment = {"telegram_file_id": "test-file"}

    await upload(bot, crm, 123, attachment)

    crm.upload_attachment.assert_awaited_once_with(123, attachment, b"%PDF-1.4 test")


@pytest.mark.parametrize('upload', [_upload_attachment, upload_queued_attachment])
async def test_rejects_download_larger_than_upload_limit(attachment_runtime, upload):
    bot, crm, settings = attachment_runtime
    bot.download_file.return_value = BytesIO(b"x" * (settings.max_upload_bytes + 1))

    with pytest.raises(ValueError, match="configured limit"):
        await upload(bot, crm, 123, {"telegram_file_id": "test-file"})

    crm.upload_attachment.assert_not_awaited()


async def test_oversized_telegram_file_is_rejected_before_downloading(attachment_runtime):
    bot, crm, settings = attachment_runtime
    bot.get_file.return_value = SimpleNamespace(file_path="test.pdf", file_size=settings.max_upload_bytes + 1)

    with pytest.raises(ValueError, match="configured limit"):
        await upload_queued_attachment(bot, crm, 123, {"telegram_file_id": "test-file"})

    bot.download_file.assert_not_awaited()
    crm.upload_attachment.assert_not_awaited()
