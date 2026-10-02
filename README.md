# CRM Document Telegram bot

Python 3.11+ customer bot for the sibling `Crm_document` Laravel project. It uses **aiogram**, **FastAPI** and **Redis**. Customer, order and payment data stay in Laravel; Redis only holds Telegram FSM state, webhook deduplication and retry jobs.

## What is implemented

- Telegram deep-link source capture (`/start instagram_korea`), two-step intake, verified own-contact request and optional JPG/PNG/PDF upload;
- customer matching by normalized phone and idempotent lead creation in CRM, with the intake transcript and source retained;
- contact-verified order lookup that renders only customer-safe fields;
- a “Talab va taklif” menu that collects customer requests and suggestions and records them in CRM;
- operator handoff stops automatic replies and forwards subsequent messages;
- explicit marketing opt-in/out, editable CRM content, and honest out-of-hours wording;
- signed CRM-to-bot webhook endpoint, seven-day event idempotency and persistent Redis retry queue;
- file format/size checks, no direct CRM database access and no secret/phone logging.

The “Yangi murojaat” button combines service requests and document uploads. Customers send a description or up to 10 files, then share their own contact to submit the lead. Their name comes from their Telegram profile. Text and file captions are retained in CRM notes, and each file also retains its own caption. Additional files and comments can be added before sharing the contact. Marketing consent is optional and is requested after submission.

Requests use `request.mode=compact`. The updated CRM allows document type and urgency to be empty for employee clarification and shows the text, verified contact and private files together under “Telegram murojaati” on the leads page. It creates a response task for the assigned employee. The bot keeps the older required fields with “Mutaxassis aniqlashtiradi” until the server is updated; the updated CRM converts those placeholders to empty values.

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

`Operator` collects the inquiry text and the customer's own Telegram contact. A verified contact is remembered outside the conversation state and reused across operator inquiries, order lookup and new intakes, including after `/start` or a bot restart. Previous verified CRM intake contacts can also be restored through the authenticated contact API. CRM stores operator requests independently of leads and lists them under the super-admin-only `Operatorlar` sidebar entry, with search, status filters and tracked status changes. Deploy the CRM schema/API update before updating the bot; see `deploy/SERVER_MIGRATION.md`.

Run the worker separately:

```powershell
python -m app.worker
```

Or start all required processes with `docker compose up -d --build`.

For moving this local installation to a Linux server, follow [the server migration guide](deploy/SERVER_MIGRATION.md). Docker Compose uses its own Redis service and binds the bot's HTTP port to `127.0.0.1:8000`. Local Redis settings do not need to be edited for Docker.

## One CRM domain

No separate domain or external webhook provider is needed. Keep the Laravel public URL such as `https://crm.example.uz` and add [`deploy/nginx-crm-bot.conf`](deploy/nginx-crm-bot.conf) to its existing HTTPS server block. Nginx forwards `/telegram-bot/` to FastAPI on `127.0.0.1:8000`; Laravel continues serving `/` and `/api/v1`.

The two public callbacks are:

```text
Telegram → https://crm.example.uz/telegram-bot/telegram/webhook
CRM      → https://crm.example.uz/telegram-bot/crm/webhook
```

For production, keep Redis on the private Docker/server network only, protect it with a password and TLS where it crosses a network boundary, and do not enable Redis command access from the Internet. Telegram state is automatically expired after 24 hours; failed retry jobs move to a 30-day dead-letter list.

The required Laravel endpoints are implemented in the sibling `Crm_document` project under `/api/v1/bot/*`. Before deployment, set the matching `CRM_BOT_API_KEY` in both projects and configure the CRM webhook variables listed in that project's `.env.example`.

For the compact intake update, deploy both projects, clear Laravel's cached views/configuration (`php artisan optimize:clear`), and restart the bot and its outbox worker. This update needs no additional database migration beyond the existing bot integration tables. See the sibling project's `docs/deployment-checklist.md`. Restarting only the bot does not update the CRM website on the server.

## Checks

```powershell
python -m compileall app
pytest
ruff check .
```
