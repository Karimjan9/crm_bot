import logging
from typing import Any

import httpx

logger = logging.getLogger(__name__)


class CrmApiError(RuntimeError):
    def __init__(self, message: str, *, status_code: int | None = None, retryable: bool = True):
        super().__init__(message)
        self.status_code = status_code
        self.retryable = retryable


class CrmClient:
    """The bot never accesses the CRM database directly; all calls use this API client."""

    def __init__(self, base_url: str, api_key: str, timeout: float):
        self.client = httpx.AsyncClient(
            base_url=base_url.rstrip("/") + "/",
            headers={"Authorization": f"Bearer {api_key}", "Accept": "application/json"},
            timeout=timeout,
        )

    async def close(self) -> None:
        await self.client.aclose()

    async def _request(self, method: str, path: str, **kwargs: Any) -> dict[str, Any]:
        try:
            response = await self.client.request(method, path.lstrip("/"), **kwargs)
            response.raise_for_status()
        except httpx.HTTPStatusError as error:
            logger.warning("CRM request failed: %s %s", method, path)
            status_code = error.response.status_code
            raise CrmApiError("CRM request failed", status_code=status_code, retryable=status_code in {408, 425, 429} or status_code >= 500) from error
        except httpx.RequestError as error:
            logger.warning("CRM request failed: %s %s", method, path)
            raise CrmApiError("CRM is currently unavailable") from error
        if not response.content:
            return {}
        return response.json()

    async def create_lead(self, payload: dict[str, Any]) -> dict[str, Any]:
        return await self._request(
            "POST",
            "/bot/leads",
            json=payload,
            headers={"Idempotency-Key": str(payload["external_id"])},
        )

    async def upload_attachment(
        self, lead_id: int | str, attachment: dict[str, Any], content: bytes
    ) -> dict[str, Any]:
        return await self._request(
            "POST",
            f"/bot/leads/{lead_id}/attachments",
            data={
                "telegram_file_id": attachment["telegram_file_id"],
                "telegram_message_id": str(attachment["telegram_message_id"]),
                "kind": attachment["kind"],
            },
            files={
                "file": (
                    attachment["file_name"],
                    content,
                    attachment.get("mime_type") or "application/octet-stream",
                )
            },
        )

    async def message(self, payload: dict[str, Any]) -> dict[str, Any]:
        return await self._request("POST", "/bot/messages", json=payload)

    async def upload_message_attachment(
        self, payload: dict[str, Any], content: bytes
    ) -> dict[str, Any]:
        attachment = payload["attachment"]
        return await self._request(
            "POST",
            "/bot/messages/attachments",
            data={
                "telegram_chat_id": payload["telegram_chat_id"],
                "telegram_user_id": payload.get("telegram_user_id") or "",
                "telegram_message_id": str(attachment["telegram_message_id"]),
                "caption": payload.get("caption") or "",
                "kind": attachment["kind"],
            },
            files={
                "file": (
                    attachment["file_name"],
                    content,
                    attachment.get("mime_type") or "application/octet-stream",
                )
            },
        )

    async def operator_request(self, payload: dict[str, Any]) -> dict[str, Any]:
        return await self._request("POST", "/bot/operator-requests", json=payload)

    async def marketing_consent(self, payload: dict[str, Any]) -> dict[str, Any]:
        return await self._request("PUT", "/bot/marketing-consents", json=payload)

    async def orders(self, chat_id: int, phone: str) -> list[dict[str, Any]]:
        response = await self._request(
            "GET", "/bot/orders", params={"telegram_chat_id": str(chat_id), "phone": phone}
        )
        return list(response.get("data", []))

    async def delivery_report(self, payload: dict[str, Any]) -> dict[str, Any]:
        return await self._request("POST", "/bot/delivery-reports", json=payload)

    async def feedback(self, payload: dict[str, Any]) -> dict[str, Any]:
        return await self._request("POST", "/bot/feedback", json=payload)

    async def content(self, key: str) -> dict[str, Any]:
        return await self._request("GET", f"/bot/content/{key}")
