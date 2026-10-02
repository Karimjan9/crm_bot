# CRM API contract for the Telegram bot

`Crm_document` remains the system of record. The Python application never connects to its database. The sibling Laravel project implements these endpoints behind its dedicated machine-to-machine key; every write is idempotent.

Base URL: `https://crm.example.uz/api/v1`

Authorization: `Authorization: Bearer <CRM_BOT_API_KEY>`

## Bot → CRM

| Endpoint | Purpose |
| --- | --- |
| `POST /bot/leads` | Find/create the client by normalized phone, set `telegram_chat_id`, create a lead and save source, request fields and transcript. The bot sends `Idempotency-Key` equal to `external_id`; CRM deduplicates by `external_id`. Return `{ "data": { "id": 123, "client_id": 456 } }`. |
| `POST /bot/leads/{lead}/attachments` | Multipart `file`, `telegram_file_id`, `telegram_message_id`, `kind` and optional `caption` (up to 2000 characters). Store privately and deduplicate by chat/message ID before storing another file. |
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
  "request": {
    "mode": "compact",
    "purpose": "Diplomni ingliz tiliga tarjima qilish kerak, 3 kun ichida",
    "document_type": "Mutaxassis aniqlashtiradi",
    "urgency": "Mutaxassis aniqlashtiradi",
    "notes": "Diplomni ingliz tiliga tarjima qilish kerak, 3 kun ichida"
  },
  "attachments": [],
  "transcript": []
}
```

The two-step intake uses the Telegram profile name and submits after the customer's own contact is verified. The free-form description and attachment captions are stored in `request.notes` (up to 2000 characters). Each attachment additionally carries its own `caption`. The bot retains the legacy `purpose`, `document_type` and `urgency` fields for compatibility with older servers. In compact mode, the updated CRM turns the generic file-only purpose and “Mutaxassis aniqlashtiradi” placeholders into nulls, stores the full text once in lead notes, and creates an employee response task.

The updated API also accepts a minimal `request` such as `{"mode":"compact","notes":"Pasport tarjimasi kerak"}`. For file-only requests, notes may be null, but `attachments` must contain at least one valid item. Empty requests and unverified contacts return 422. Legacy detailed requests remain supported (`mode=guided`, inferred if detailed fields are provided).

Attachment metadata fields are `telegram_file_id`, `telegram_message_id` (distinct within the request), `file_name`, optional `mime_type`, `size` (bytes; 0 if Telegram omitted the size), `kind` (`photo` or `document`), and optional `caption`. The limit is 10 files, 20 MiB each. Metadata is submitted first, followed by one multipart upload per file. Both immediate uploads and outbox retries enforce the download size limit. Replaying the same intake UUID or uploaded Telegram message returns the existing record and does not store duplicate files.

CRM employees see the full original text, contact confirmation, and file links/captions together on the leads page. If uploads have not arrived, the received/expected file count is shown. Existing filial/assignee visibility and private file download permissions still apply.

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
