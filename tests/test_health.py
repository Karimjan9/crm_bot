import json
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from app.main import health


@pytest.mark.asyncio
@pytest.mark.parametrize("failed_dependency", [None, "redis", "crm"])
async def test_health_checks_redis_and_authenticated_crm(failed_dependency):
    redis = SimpleNamespace(ping=AsyncMock(return_value=True))
    crm = SimpleNamespace(content=AsyncMock(return_value={"data": []}))
    if failed_dependency == "redis":
        redis.ping.side_effect = ConnectionError("private Redis details")
    elif failed_dependency == "crm":
        crm.content.side_effect = RuntimeError("private CRM credentials")
    runtime = SimpleNamespace(storage=SimpleNamespace(redis=redis), crm=crm)
    request = SimpleNamespace(app=SimpleNamespace(state=SimpleNamespace(runtime=runtime)))

    response = await health(request)
    payload = json.loads(response.body)

    assert response.status_code == (503 if failed_dependency else 200)
    assert payload["status"] == ("error" if failed_dependency else "ok")
    assert payload["checks"] == {
        "redis": "failed" if failed_dependency == "redis" else "ok",
        "crm": "failed" if failed_dependency == "crm" else "ok",
    }
    assert b"private" not in response.body
    crm.content.assert_awaited_once_with("branches")
