from typing import Any

from pydantic import BaseModel, Field


class CrmWebhook(BaseModel):
    event_id: str = Field(min_length=8, max_length=160)
    type: str = Field(min_length=3, max_length=80)
    payload: dict[str, Any]


class Attachment(BaseModel):
    telegram_file_id: str
    telegram_message_id: int
    file_name: str
    mime_type: str | None = None
    size: int
    kind: str
