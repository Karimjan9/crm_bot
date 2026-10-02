import json
import time
from typing import Any

from redis.asyncio import Redis


class BotStorage:
    def __init__(self, redis: Redis):
        self.redis = redis

    async def mark_webhook_once(self, event_id: str) -> bool:
        return bool(await self.redis.set(f"crm-bot:event:{event_id}", "1", ex=7 * 86400, nx=True))

    async def set_human_handoff(self, chat_id: int, enabled: bool = True) -> None:
        key = f"crm-bot:handoff:{chat_id}"
        if enabled:
            await self.redis.set(key, "1", ex=30 * 86400)
        else:
            await self.redis.delete(key)

    async def has_human_handoff(self, chat_id: int) -> bool:
        return bool(await self.redis.exists(f"crm-bot:handoff:{chat_id}"))

    async def set_lead_id(self, chat_id: int, lead_id: int | str) -> None:
        await self.redis.set(f"crm-bot:lead:{chat_id}", str(lead_id), ex=90 * 86400)

    async def lead_id(self, chat_id: int) -> str | None:
        return await self.redis.get(f"crm-bot:lead:{chat_id}")

    async def enqueue(self, action: str, payload: dict[str, Any], attempts: int = 0) -> None:
        job = json.dumps({"action": action, "payload": payload, "attempts": attempts, "queued_at": int(time.time())}, ensure_ascii=False)
        await self.redis.lpush("crm-bot:outbox", job)

    async def dead_letter(self, job: dict[str, Any], reason: str, retention_days: int) -> None:
        safe_job = {**job, "failed_at": int(time.time()), "failure_reason": reason}
        await self.redis.lpush("crm-bot:outbox:dead", json.dumps(safe_job, ensure_ascii=False))
        await self.redis.ltrim("crm-bot:outbox:dead", 0, 999)
        await self.redis.expire("crm-bot:outbox:dead", retention_days * 86400)

    async def reserve_job(self, timeout: int = 5) -> str | None:
        return await self.redis.brpoplpush("crm-bot:outbox", "crm-bot:outbox:processing", timeout=timeout)

    async def acknowledge_job(self, job: str) -> None:
        await self.redis.lrem("crm-bot:outbox:processing", 1, job)

    async def recover_processing_jobs(self) -> None:
        while await self.redis.rpoplpush("crm-bot:outbox:processing", "crm-bot:outbox"):
            pass
