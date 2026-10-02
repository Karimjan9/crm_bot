import asyncio
import hmac
import logging
from contextlib import asynccontextmanager
from datetime import timedelta

from aiogram import Bot, Dispatcher
from aiogram.exceptions import TelegramAPIError
from aiogram.fsm.storage.redis import RedisStorage
from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup, Update
from fastapi import FastAPI, HTTPException, Request, Response, status
from redis.asyncio import Redis

from app.config import get_settings
from app.crm import CrmApiError, CrmClient
from app.handlers import router
from app.runtime import Runtime, register_runtime, unregister_runtime
from app.schemas import CrmWebhook
from app.security import InvalidWebhookSignature, verify_crm_signature
from app.storage import BotStorage

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
logger = logging.getLogger(__name__)


def _feedback_keyboard(order_id: int | None) -> InlineKeyboardMarkup | None:
    if not order_id:
        return None
    return InlineKeyboardMarkup(
        inline_keyboard=[[InlineKeyboardButton(text=str(rating), callback_data=f"feedback:{order_id}:{rating}") for rating in range(1, 6)]]
    )


async def _send_telegram_event(bot: Bot, runtime: Runtime, event: CrmWebhook) -> None:
    payload = event.payload
    chat_id = payload.get("telegram_chat_id") or payload.get("chat_id")
    text = payload.get("message")
    if not chat_id or not text:
        logger.warning("CRM webhook %s has no customer message target", event.event_id)
        return
    if event.type == "operator.reply":
        await runtime.storage.set_human_handoff(int(chat_id), True)
    elif event.type == "operator.closed":
        await runtime.storage.set_human_handoff(int(chat_id), False)
    try:
        sent = await bot.send_message(chat_id=int(chat_id), text=str(text)[:4000], reply_markup=_feedback_keyboard(payload.get("order_id") if event.type == "feedback.request" else None))
    except (TelegramAPIError, OSError, ValueError) as error:
        logger.warning("Customer event delivery will be retried: %s", type(error).__name__)
        await runtime.storage.enqueue(
            "telegram_message",
            {"event_id": event.event_id, "telegram_chat_id": str(chat_id), "message": str(text)[:4000], "feedback_order_id": payload.get("order_id") if event.type == "feedback.request" else None},
        )
        return
    try:
        await runtime.crm.delivery_report(
            {
                "event_id": event.event_id,
                "telegram_chat_id": str(chat_id),
                "telegram_message_id": sent.message_id,
                "status": "sent",
            }
        )
    except CrmApiError:
        await runtime.storage.enqueue(
            "delivery_report",
            {"event_id": event.event_id, "telegram_chat_id": str(chat_id), "telegram_message_id": sent.message_id, "status": "sent"},
        )


@asynccontextmanager
async def lifespan(app: FastAPI):
    settings = get_settings()
    redis = Redis.from_url(settings.redis_url, decode_responses=True)
    await redis.ping()
    bot = Bot(token=settings.bot_token.get_secret_value())
    crm = CrmClient(
        settings.crm_base_url,
        settings.crm_bot_api_key.get_secret_value(),
        settings.crm_timeout_seconds,
    )
    runtime = Runtime(settings=settings, crm=crm, storage=BotStorage(redis))
    register_runtime(bot, runtime)
    fsm_storage = RedisStorage(
        redis,
        state_ttl=timedelta(hours=settings.redis_state_ttl_hours),
        data_ttl=timedelta(hours=settings.redis_state_ttl_hours),
    )
    dispatcher = Dispatcher(storage=fsm_storage, events_isolation=fsm_storage.create_isolation())
    dispatcher.include_router(router)
    app.state.bot = bot
    app.state.dispatcher = dispatcher
    app.state.runtime = runtime
    polling_task: asyncio.Task | None = None
    try:
        if settings.bot_mode == "webhook":
            secret = settings.telegram_webhook_secret
            if not settings.public_base_url or not secret:
                raise RuntimeError("PUBLIC_BASE_URL and TELEGRAM_WEBHOOK_SECRET are required in webhook mode")
            webhook_url = settings.public_base_url.rstrip("/") + "/telegram-bot/telegram/webhook"
            await bot.set_webhook(webhook_url, secret_token=secret.get_secret_value(), drop_pending_updates=False)
        else:
            await bot.delete_webhook(drop_pending_updates=False)
            polling_task = asyncio.create_task(
                dispatcher.start_polling(bot, allowed_updates=dispatcher.resolve_used_update_types())
            )
        yield
    finally:
        if polling_task:
            await dispatcher.stop_polling()
            polling_task.cancel()
        await dispatcher.storage.close()
        unregister_runtime(bot)
        await crm.close()
        await bot.session.close()
        await redis.aclose()


app = FastAPI(title="CRM Document Telegram Bot", lifespan=lifespan, root_path="/telegram-bot")


@app.get("/health")
async def health(request: Request) -> dict[str, str]:
    runtime: Runtime = request.app.state.runtime
    await runtime.storage.redis.ping()
    return {"status": "ok"}


@app.post("/telegram/webhook")
async def telegram_webhook(request: Request) -> Response:
    runtime: Runtime = request.app.state.runtime
    secret = runtime.settings.telegram_webhook_secret
    supplied = request.headers.get("X-Telegram-Bot-Api-Secret-Token", "")
    if not secret or not hmac.compare_digest(supplied, secret.get_secret_value()):
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Invalid Telegram webhook secret")
    try:
        update = Update.model_validate(await request.json(), context={"bot": request.app.state.bot})
    except ValueError as error:
        raise HTTPException(status_code=400, detail="Invalid Telegram update") from error
    await request.app.state.dispatcher.feed_update(request.app.state.bot, update)
    return Response(status_code=status.HTTP_200_OK)


@app.post("/crm/webhook")
async def crm_webhook(request: Request) -> dict[str, bool]:
    runtime: Runtime = request.app.state.runtime
    body = await request.body()
    try:
        verify_crm_signature(
            body,
            request.headers.get("X-CRM-Timestamp"),
            request.headers.get("X-CRM-Signature"),
            runtime.settings.crm_webhook_secret.get_secret_value(),
        )
        event = CrmWebhook.model_validate_json(body)
    except (InvalidWebhookSignature, ValueError) as error:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Invalid CRM webhook") from error
    if not await runtime.storage.mark_webhook_once(event.event_id):
        return {"accepted": True, "duplicate": True}
    await _send_telegram_event(request.app.state.bot, runtime, event)
    return {"accepted": True}
