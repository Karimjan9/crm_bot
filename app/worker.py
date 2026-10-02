import asyncio
import json
import logging
from io import BytesIO

from aiogram import Bot
from aiogram.exceptions import TelegramAPIError
from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup
from redis.asyncio import Redis

from app.config import get_settings
from app.crm import CrmApiError, CrmClient
from app.runtime import Runtime, register_runtime, unregister_runtime
from app.storage import BotStorage

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
logger = logging.getLogger(__name__)


async def upload_attachment(bot: Bot, crm: CrmClient, lead_id: int | str, attachment: dict) -> None:
    remote = await bot.get_file(attachment["telegram_file_id"])
    content = (await bot.download_file(remote.file_path, destination=BytesIO())).getvalue()
    await crm.upload_attachment(lead_id, attachment, content)


async def upload_message_attachment(bot: Bot, crm: CrmClient, payload: dict) -> None:
    attachment = payload["attachment"]
    remote = await bot.get_file(attachment["telegram_file_id"])
    content = (await bot.download_file(remote.file_path, destination=BytesIO())).getvalue()
    await crm.upload_message_attachment(payload, content)


async def process_job(bot: Bot, runtime: Runtime, action: str, payload: dict) -> None:
    crm = runtime.crm
    if action == "create_lead":
        result = await crm.create_lead(payload)
        lead = result.get("data", result)
        lead_id = lead["id"]
        for attachment in payload.get("attachments", []):
            await upload_attachment(bot, crm, lead_id, attachment)
    elif action == "attachment":
        await upload_attachment(bot, crm, payload["lead_id"], payload["attachment"])
    elif action == "message_attachment":
        await upload_message_attachment(bot, crm, payload)
    elif action == "telegram_message":
        keyboard = None
        if payload.get("feedback_order_id"):
            order_id = payload["feedback_order_id"]
            keyboard = InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text=str(rating), callback_data=f"feedback:{order_id}:{rating}") for rating in range(1, 6)]])
        sent = await bot.send_message(int(payload["telegram_chat_id"]), payload["message"], reply_markup=keyboard)
        try:
            await crm.delivery_report({"event_id": payload["event_id"], "telegram_chat_id": payload["telegram_chat_id"], "telegram_message_id": sent.message_id, "status": "sent"})
        except CrmApiError:
            await runtime.storage.enqueue("delivery_report", {"event_id": payload["event_id"], "telegram_chat_id": payload["telegram_chat_id"], "telegram_message_id": sent.message_id, "status": "sent"})
    elif action in {"message", "operator_request", "marketing_consent", "delivery_report", "feedback"}:
        await getattr(crm, action)(payload)
    else:
        raise ValueError(f"Unsupported outbox action: {action}")


async def run() -> None:
    settings = get_settings()
    redis = Redis.from_url(settings.redis_url, decode_responses=True)
    await redis.ping()
    storage = BotStorage(redis)
    await storage.recover_processing_jobs()
    bot = Bot(token=settings.bot_token.get_secret_value())
    crm = CrmClient(settings.crm_base_url, settings.crm_bot_api_key.get_secret_value(), settings.crm_timeout_seconds)
    runtime = Runtime(settings=settings, crm=crm, storage=storage)
    register_runtime(bot, runtime)
    try:
        while True:
            raw_job = await storage.reserve_job(timeout=5)
            if not raw_job:
                continue
            job: dict = {}
            try:
                job = json.loads(raw_job)
                await process_job(bot, runtime, job["action"], job["payload"])
            except (CrmApiError, TelegramAPIError, OSError, ValueError, KeyError) as error:
                await storage.acknowledge_job(raw_job)
                attempts = int(job.get("attempts", 0)) + 1
                retryable = not isinstance(error, CrmApiError) or error.retryable
                if not retryable or attempts >= settings.outbox_max_attempts:
                    logger.warning("Outbox job moved to dead letter: %s", type(error).__name__)
                    await storage.dead_letter(job, type(error).__name__, settings.dead_letter_retention_days)
                else:
                    logger.warning("Outbox job will retry: %s", type(error).__name__)
                    await storage.enqueue(job.get("action", "unknown"), job.get("payload", {}), attempts)
                    await asyncio.sleep(min(60, 2 ** attempts))
            else:
                await storage.acknowledge_job(raw_job)
    finally:
        unregister_runtime(bot)
        await crm.close()
        await bot.session.close()
        await redis.aclose()


if __name__ == "__main__":
    asyncio.run(run())
