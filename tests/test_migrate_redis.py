from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from app.migrate_redis import export_state, import_state, write_snapshot


async def test_export_preserves_only_bot_values_and_expiration():
    async def scan(*, match, count):
        keys = ["crm-bot:lead:123", "crm-bot:outbox"] if match == "crm-bot:*" else ["fsm:123:123:data", "fsm:123:123:lock"]
        for key in keys:
            yield key

    redis = SimpleNamespace(
        scan_iter=scan, type=AsyncMock(side_effect=["string", "list", "string"]),
        get=AsyncMock(side_effect=["456", '{"notes":"Diplom tarjimasi"}']),
        lrange=AsyncMock(return_value=["new-job", "old-job"]),
        time=AsyncMock(return_value=(100, 0)), pttl=AsyncMock(side_effect=[5000, -1, 24000]),
    )
    snapshot = await export_state(redis, 123456789)
    assert snapshot["bot_id"] == 123456789
    assert [record["key"] for record in snapshot["records"]] == ["crm-bot:lead:123", "crm-bot:outbox", "fsm:123:123:data"]
    assert [record["expires_at_ms"] for record in snapshot["records"]] == [105000, None, 124000]
    assert snapshot["records"][1]["value"] == ["new-job", "old-job"]


def destination():
    pipeline = MagicMock()
    pipeline.execute = AsyncMock()
    pipeline.__aenter__ = AsyncMock(return_value=pipeline)
    pipeline.__aexit__ = AsyncMock(return_value=False)
    redis = SimpleNamespace(dbsize=AsyncMock(return_value=0), time=AsyncMock(return_value=(101, 0)), pipeline=MagicMock(return_value=pipeline))
    return redis, pipeline


def snapshot():
    return {"version": 1, "bot_id": 123456789, "records": [
        {"key": "crm-bot:outbox", "type": "list", "value": ["new-job", "old-job"], "expires_at_ms": None},
        {"key": "fsm:123:123:data", "type": "string", "value": '{"notes":"Tarjima"}', "expires_at_ms": 124000},
        {"key": "crm-bot:event:expired", "type": "string", "value": "1", "expires_at_ms": 100000},
    ]}


async def test_import_keeps_job_order_and_original_expiration_and_skips_expired_keys():
    redis, pipeline = destination()
    assert await import_state(redis, 123456789, snapshot()) == 2
    pipeline.rpush.assert_called_once_with("crm-bot:outbox", "new-job", "old-job")
    pipeline.set.assert_called_once_with("fsm:123:123:data", '{"notes":"Tarjima"}')
    pipeline.pexpireat.assert_called_once_with("fsm:123:123:data", 124000)
    pipeline.execute.assert_awaited_once()


async def test_import_refuses_wrong_bot_foreign_keys_or_existing_destination_data():
    redis, _ = destination()
    with pytest.raises(ValueError, match="identity"):
        await import_state(redis, 987654321, snapshot())
    invalid = snapshot()
    invalid["records"][0]["key"] = "another-project:customer"
    with pytest.raises(ValueError, match="Invalid state key"):
        await import_state(redis, 123456789, invalid)
    redis.dbsize.return_value = 1
    with pytest.raises(ValueError, match="must be empty"):
        await import_state(redis, 123456789, snapshot())
    redis.pipeline.assert_not_called()


def test_export_cannot_replace_an_existing_backup(tmp_path):
    path = tmp_path / "state.json"
    path.write_text("old state", encoding="utf-8")
    with pytest.raises(FileExistsError):
        write_snapshot(path, snapshot())
    assert path.read_text(encoding="utf-8") == "old state"
