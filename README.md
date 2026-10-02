# CRM Document Telegram bot

Python 3.11+ customer bot for the sibling `Crm_document` Laravel project. It uses **aiogram**, **FastAPI** and **Redis**. Customer, order and payment data stay in Laravel; Redis only holds Telegram FSM state, webhook deduplication and retry jobs.

## What is implemented

- Telegram deep-link source capture (`/start instagram_korea`), short intake flow, verified own-contact request and optional JPG/PNG/PDF upload;
- one customer lead per normalized phone is delegated to CRM, with the entire intake transcript and source retained;
- contact-verified order lookup that renders only customer-safe fields;
- a “Talab va taklif” menu that collects customer requests and suggestions and records them in CRM;
- operator handoff stops automatic replies and forwards subsequent messages;
- explicit marketing opt-in/out, editable CRM content, and honest out-of-hours wording;
- signed CRM-to-bot webhook endpoint, seven-day event idempotency and persistent Redis retry queue;
- file format/size checks, no direct CRM database access and no secret/phone logging.

## Local run

```powershell
Copy-Item .env.example .env
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -e ".[dev]"
docker compose up -d redis
uvicorn app.main:app --reload --port 8000
```

Use `BOT_MODE=polling` for local development. For production set `BOT_MODE=webhook`, a real `PUBLIC_BASE_URL`, `TELEGRAM_WEBHOOK_SECRET`, `CRM_BOT_API_KEY`, and a long random `CRM_WEBHOOK_SECRET`.

Run the worker separately:

```powershell
python -m app.worker
```

Or start all required processes with `docker compose up -d --build`.

## One CRM domain

No separate domain or external webhook provider is needed. Keep the Laravel public URL such as `https://crm.example.uz` and add [`deploy/nginx-crm-bot.conf`](deploy/nginx-crm-bot.conf) to its existing HTTPS server block. Nginx forwards `/telegram-bot/` to FastAPI on `127.0.0.1:8000`; Laravel continues serving `/` and `/api/v1`.

The two public callbacks are:

```text
Telegram → https://crm.example.uz/telegram-bot/telegram/webhook
CRM      → https://crm.example.uz/telegram-bot/crm/webhook
```

For production, keep Redis on the private Docker/server network only, protect it with a password and TLS where it crosses a network boundary, and do not enable Redis command access from the Internet. Telegram state is automatically expired after 24 hours; failed retry jobs move to a 30-day dead-letter list.

The required Laravel endpoints are implemented in the sibling `Crm_document` project under `/api/v1/bot/*`. Before deployment, set the matching `CRM_BOT_API_KEY` in both projects and configure the CRM webhook variables listed in that project's `.env.example`.

## Checks

```powershell
python -m compileall app
pytest
ruff check .
```
