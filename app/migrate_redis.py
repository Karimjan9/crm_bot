"""Transfer this bot's Redis state while both installations are stopped."""

import argparse
import asyncio
import json
import os
from pathlib import Path
from typing import Any

from redis.asyncio import Redis

from app.config import get_settings


def is_bot_key(key: str) -> bool:
    return key.startswith("crm-bot:") or (
        key.startswith("fsm:") and key.rsplit(":", 1)[-1] in {"data", "state"}
    )


async def redis_time_ms(redis: Redis) -> int:
    seconds, microseconds = await redis.time()
    return seconds * 1000 + microseconds // 1000


async def export_state(redis: Redis, bot_id: int) -> dict[str, Any]:
    records = []
    for pattern in ["crm-bot:*", "fsm:*"]:
        async for key in redis.scan_iter(match=pattern, count=100):
            if not is_bot_key(key):
                continue
            kind = await redis.type(key)
            if kind == "none":
                continue
            if kind not in {"string", "list"}:
                raise ValueError("Unsupported bot state type; no state file was written")
            value = await redis.get(key) if kind == "string" else await redis.lrange(key, 0, -1)
            now = await redis_time_ms(redis)
            ttl = await redis.pttl(key)
            if ttl == -2 or value is None or value == []:
                continue
            records.append({"key": key, "type": kind, "value": value, "expires_at_ms": now + ttl if ttl >= 0 else None})
    return {"version": 1, "bot_id": bot_id, "records": records}


async def import_state(redis: Redis, bot_id: int, snapshot: dict[str, Any]) -> int:
    if snapshot.get("version") != 1 or snapshot.get("bot_id") != bot_id:
        raise ValueError("State file version or Telegram bot identity does not match")
    records = snapshot.get("records")
    if not isinstance(records, list):
        raise TypeError("Invalid state file")
    seen = set()
    for record in records:
        key = record.get("key")
        kind = record.get("type")
        value = record.get("value")
        expires = record.get("expires_at_ms")
        valid_value = (kind == "string" and isinstance(value, str)) or (
            kind == "list" and isinstance(value, list) and bool(value) and all(isinstance(item, str) for item in value)
        )
        if not isinstance(key, str) or not is_bot_key(key) or key in seen or not valid_value:
            raise ValueError("Invalid state key, type or value")
        if expires is not None and (type(expires) is not int or expires < 0):
            raise ValueError("Invalid state expiration")
        seen.add(key)
    if await redis.dbsize():
        raise ValueError("Destination Redis database must be empty; existing data was preserved")
    now = await redis_time_ms(redis)
    imported = 0
    async with redis.pipeline(transaction=True) as pipeline:
        for record in records:
            expires = record["expires_at_ms"]
            if expires is not None and expires <= now:
                continue
            key = record["key"]
            if record["type"] == "string":
                pipeline.set(key, record["value"])
            else:
                pipeline.rpush(key, *record["value"])
            if expires is not None:
                pipeline.pexpireat(key, expires)
            imported += 1
        await pipeline.execute()
    return imported


def write_snapshot(path: Path, snapshot: dict[str, Any]) -> None:
    # Exclusive creation prevents replacing an existing state backup.
    descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(descriptor, "w", encoding="utf-8") as output:
        json.dump(snapshot, output, ensure_ascii=False)


async def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=["export", "import"])
    parser.add_argument("path", type=Path)
    arguments = parser.parse_args()
    settings = get_settings()
    bot_id = int(settings.bot_token.get_secret_value().split(":", 1)[0])
    redis = Redis.from_url(settings.redis_url, decode_responses=True)
    try:
        if arguments.action == "export":
            snapshot = await export_state(redis, bot_id)
            write_snapshot(arguments.path, snapshot)
            print(f"Exported {len(snapshot['records'])} bot state keys")
        else:
            snapshot = json.loads(arguments.path.read_text(encoding="utf-8"))
            count = await import_state(redis, bot_id, snapshot)
            print(f"Imported {count} bot state keys")
    finally:
        await redis.aclose()


if __name__ == "__main__":
    asyncio.run(main())
