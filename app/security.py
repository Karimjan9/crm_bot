import hashlib
import hmac
import time


class InvalidWebhookSignature(ValueError):
    pass


def verify_crm_signature(body: bytes, timestamp: str | None, signature: str | None, secret: str) -> None:
    """Validate a timestamped HMAC SHA-256 CRM webhook without logging its body."""
    if not timestamp or not signature:
        raise InvalidWebhookSignature("Signature headers are missing")
    try:
        sent_at = int(timestamp)
    except ValueError as error:
        raise InvalidWebhookSignature("Timestamp is invalid") from error
    if abs(time.time() - sent_at) > 300:
        raise InvalidWebhookSignature("Webhook timestamp has expired")

    expected = hmac.new(secret.encode(), timestamp.encode() + b"." + body, hashlib.sha256).hexdigest()
    if not hmac.compare_digest(expected, signature):
        raise InvalidWebhookSignature("Signature does not match")
