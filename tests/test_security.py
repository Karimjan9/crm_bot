import hashlib
import hmac
import time

import pytest

from app.security import InvalidWebhookSignature, verify_crm_signature


def test_accepts_valid_signature() -> None:
    body = b'{"event_id":"12345678"}'
    timestamp = str(int(time.time()))
    signature = hmac.new(b"secret", timestamp.encode() + b"." + body, hashlib.sha256).hexdigest()
    verify_crm_signature(body, timestamp, signature, "secret")


def test_rejects_invalid_signature() -> None:
    with pytest.raises(InvalidWebhookSignature):
        verify_crm_signature(b"{}", str(int(time.time())), "wrong", "secret")
