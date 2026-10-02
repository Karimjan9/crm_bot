# CRM API contract for the Telegram bot

`Crm_document` remains the system of record. The Python application never connects to its database. The sibling Laravel project implements these endpoints behind its dedicated machine-to-machine key; every write is idempotent.

Base URL: `https://crm.example.uz/api/v1`

Authorization: `Authorization: Bearer <CRM_BOT_API_KEY>`

## Bot → CRM

| Endpoint | Purpose |
| --- | --- |
| `POST /bot/leads` | Find/create the client by normalized phone, set `telegram_chat_id`, create a lead and save source, request fields and transcript. Require `Idempotency-Key`, equal to `external_id`. Return `{ "data": { "id": 123 } }`. |
| `POST /bot/leads/{lead}/attachments` | Multipart field `file`; also accepts Telegram IDs and file metadata. Store using the CRM private-file policy. |
| `POST /bot/messages` | Append an incoming Telegram message to the lead/order history. Deduplicate with `(telegram_chat_id, telegram_message_id)`. |
| `POST /bot/messages/attachments` | Multipart field `file` for an attachment sent outside the intake flow; retain its message/chat identifiers and caption. |
| `POST /bot/operator-requests` | Create/raise a responsible employee task and mark the chat in handoff. |
| `GET /bot/orders?telegram_chat_id=&phone=` | Return **only** orders where both values match the same client. Return customer-safe fields: `code`, `status`, `promised_at`, `paid_amount`, `balance_amount`, `currency`, `delivery_type`. |
| `PUT /bot/marketing-consents` | Persist explicit consent/revocation and its timestamp. |
| `POST /bot/delivery-reports` | Record Telegram delivery attempt/result by CRM event ID. |
| `GET /bot/content/{key}` | Return editable customer text for `branches` and `useful-information`. |

`POST /bot/leads` should map the payload as follows:

```json
{
  "external_id": "uuid",
  "customer": {
    "telegram_chat_id": "123",
    "telegram_user_id": "123",
    "name": "Ali", "phone": "+998...", "phone_verified": true
  },
  "source": {"channel": "telegram", "entry_payload": "instagram_korea"},
  "request": {"purpose": "O‘qish", "document_type": "Diplom", "urgency": "Shoshilinch", "notes": ""},
  "attachments": [],
  "transcript": []
}
```

## CRM → bot

Send `POST https://crm.example.uz/telegram-bot/crm/webhook` for `operator.reply`, `operator.closed`, `order.status_changed`, `payment.updated`, and other customer service events.

The body has this shape:

```json
{
  "event_id": "unique-event-uuid",
  "type": "order.status_changed",
  "payload": {"telegram_chat_id": "123", "message": "Hujjatingiz tayyor bo‘ldi."}
}
```

Sign the raw body with `HMAC-SHA256(CRM_WEBHOOK_SECRET, "<unix-timestamp>.<raw-body>")`; send the result in `X-CRM-Signature`, and send the Unix timestamp in `X-CRM-Timestamp`. The bot accepts a five-minute clock skew and ignores an already processed `event_id` for seven days.

Never include internal prices, employee notes, cost, discount reasons, another customer’s details, or raw card data in this payload.
