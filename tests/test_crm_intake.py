import json
from io import BytesIO
from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import uuid4

import httpx
import pytest
from aiogram import Bot

from app.config import Settings
from app.crm import CrmApiError, CrmClient
from app.runtime import Runtime, register_runtime, unregister_runtime
from app.worker import process_job


async def test_intake_http_requests_keep_api_prefix_idempotency_and_file_caption():
    requests = []

    async def receive(request):
        requests.append(request)
        return httpx.Response(201, json={"data": {"id": 456}})

    crm = CrmClient("https://crm.invalid/api/v1", "test-key", 5)
    await crm.close()
    crm.client = httpx.AsyncClient(
        base_url="https://crm.invalid/api/v1/",
        headers={"Authorization": "Bearer test-key"},
        transport=httpx.MockTransport(receive),
    )
    payload = {"external_id": str(uuid4()), "request": {"mode": "compact", "notes": "Diplom tarjimasi"}}
    attachment = {
        "telegram_file_id": "test-file", "telegram_message_id": 10,
        "kind": "document", "file_name": "diplom.pdf", "mime_type": "application/pdf",
        "caption": "Diplom, ingliz tiliga.",
    }
    try:
        await crm.create_lead(payload)
        await crm.upload_attachment(456, attachment, b"%PDF-1.4 test")
    finally:
        await crm.close()

    assert requests[0].url.path == "/api/v1/bot/leads"
    assert requests[0].headers["Idempotency-Key"] == payload["external_id"]
    assert json.loads(requests[0].content) == payload
    assert requests[1].url.path == "/api/v1/bot/leads/456/attachments"
    assert requests[1].headers["Authorization"] == "Bearer test-key"
    body = requests[1].content
    assert b'name="caption"\r\n\r\nDiplom, ingliz tiliga.' in body
    assert b'filename="diplom.pdf"' in body
    assert b"%PDF-1.4 test" in body


async def test_queued_intake_retry_keeps_identity_all_files_and_lead_cache():
    settings = Settings(
        _env_file=None, bot_token="123456789:unit-test-only-token",
        crm_base_url="https://crm.invalid/api/v1", crm_bot_api_key="test-key",
        crm_webhook_secret="test-secret",
    )
    bot = Bot(settings.bot_token.get_secret_value())
    bot.get_file = AsyncMock(return_value=SimpleNamespace(file_path="test.pdf"))
    bot.download_file = AsyncMock(return_value=BytesIO(b"%PDF-1.4 test"))
    crm = SimpleNamespace(
        create_lead=AsyncMock(return_value={"data": {"id": 456}}),
        upload_attachment=AsyncMock(side_effect=[None, CrmApiError("offline test"), None, None]),
    )
    storage = SimpleNamespace(set_lead_id=AsyncMock())
    runtime = Runtime(settings=settings, crm=crm, storage=storage)
    register_runtime(bot, runtime)
    files = [
        {"telegram_file_id": "test-file-1", "telegram_message_id": 10, "caption": "Diplom"},
        {"telegram_file_id": "test-file-2", "telegram_message_id": 11, "caption": "Ilova"},
    ]
    payload = {"external_id": str(uuid4()), "customer": {"telegram_chat_id": "123"}, "attachments": files}
    try:
        with pytest.raises(CrmApiError):
            await process_job(bot, runtime, "create_lead", payload)
        await process_job(bot, runtime, "create_lead", payload)
        assert crm.create_lead.await_count == 2
        assert all(call.args[0] == payload for call in crm.create_lead.await_args_list)
        assert storage.set_lead_id.await_count == 2
        storage.set_lead_id.assert_awaited_with(123, 456)
        assert [call.args[1] for call in crm.upload_attachment.await_args_list] == files + files
    finally:
        unregister_runtime(bot)
        await bot.session.close()
