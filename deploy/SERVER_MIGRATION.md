# Move the bot to the CRM server

The server needs SSH access, Docker Engine with the Compose plugin, and access to the existing HTTPS Nginx configuration for `globalvoice.uz`. Use the server's actual bot and Laravel paths. Keep the local bot running while preparing the server; stop it for the final handover so only one instance receives Telegram updates.

## Prepare without receiving updates

1. Clone `https://github.com/Karimjan9/crm_bot.git` into a separate server directory, outside Laravel's `public/` directory.
2. Transfer the local bot `.env` over SSH, preserving the bot token, CRM API key and CRM webhook secret. Set its file permissions to `600`. The `.env` is excluded from Git and from the Docker build context.
3. In the server bot `.env`, set `BOT_MODE=webhook`, `PUBLIC_BASE_URL=https://globalvoice.uz` and `CRM_BASE_URL=https://globalvoice.uz/api/v1`. Set a nonempty `TELEGRAM_WEBHOOK_SECRET` (Telegram permits letters, digits, `_` and `-`). Keep all secrets out of command output and shell history.
4. Check that port `127.0.0.1:8000` is available. If it is occupied, choose another host port in Compose and change the Nginx upstream to match.
5. From the bot directory, run `docker compose build` and `docker compose up -d redis`. Do not start `bot` or `worker` during this preparation stage.
6. Include `deploy/nginx-crm-bot.conf` in the existing HTTPS server block for `globalvoice.uz`. Validate with `nginx -t` before reloading Nginx. The existing Laravel routes continue using their current configuration.
7. Deploy the compact-intake CRM update. In Laravel's server `.env`, ensure `CRM_BOT_API_KEY` matches the bot, set `CRM_BOT_WEBHOOK_URL=https://globalvoice.uz/telegram-bot/crm/webhook` and match `CRM_BOT_WEBHOOK_SECRET` to the bot's `CRM_WEBHOOK_SECRET`. Clear/rebuild the Laravel configuration cache and restart its queue worker using the project's deployment procedure.

## Handover

1. Verify the image is built, Redis is healthy and Nginx passes its configuration check.
2. Stop the local bot and local outbox worker. Use their recorded process IDs only after checking that they identify this project's Python commands.
3. Export bot state locally with `python -m app.migrate_redis export .pytest_cache/migration.state.json`. It copies only `crm-bot:*` and FSM data/state keys, excluding locks, and preserves expiry times and job order. Transfer the file over SSH to a private location on the server. In the server bot directory, import into its empty Redis database with `docker compose run --rm --no-deps -v /root/migration.state.json:/tmp/migration.state.json:ro bot python -m app.migrate_redis import /tmp/migration.state.json`. The importer verifies the bot identity and refuses to overwrite an existing database. The state file may contain customer data; keep it outside Git and the public web directory. Expired records are skipped. The worker recovers transferred in-progress retry jobs on startup.
4. Run `docker compose up -d bot worker` on the server. The bot registers its HTTPS Telegram webhook during startup, and the worker processes any transferred retry jobs.
5. Check `docker compose ps`, `curl --fail http://127.0.0.1:8000/health`, and `curl --fail https://globalvoice.uz/telegram-bot/health`.
6. Confirm Telegram's webhook URL and any webhook delivery errors with `getWebhookInfo` using a client that reads the token from the environment. Confirm the CRM content API is reachable with the configured key. Do not put the token in a browser URL or log it.
7. From the customer's Telegram account, send `/start` and submit a test request. Check its text/contact/files and employee response task in CRM.
8. Confirm the local bot/worker remain stopped. Docker's restart policy starts the server services after a server restart, provided Docker itself is enabled at boot.

Do not disable or remove the local Redis service if another project uses it. Keep local source files and `.env`; stopping the processes is sufficient.

## Update operator requests and remembered contacts

Deploy the CRM update first. It adds the `operator_requests` table, the super-admin-only `/operators` page, and the authenticated contact lookup used to restore a previously verified Telegram contact. Follow the Laravel project's deployment procedure so the database is backed up before migrations:

```bash
cd /var/www/crm_document
bash deploy.sh
```

Once the CRM deployment succeeds, update the bot without recreating its Redis volume:

```bash
cd /var/www/crm_bot
git pull --ff-only
docker compose up -d --build --wait --wait-timeout 120 bot worker
```

Send `/start` and choose `Operator`. On a first visit the bot collects inquiry text and the customer's own Telegram contact, then confirms `Operator sizga bog‘lanadi.` A remembered contact skips the contact step. The same contact is reused by `Buyurtmam` and `Yangi murojaat`; the latter shows a send button instead of requesting the phone again. Verified contacts from earlier CRM intakes are restored automatically. Contact keys have no expiry and are included in Redis state exports; Docker Redis persistence preserves them through normal restarts.

As super-admin, open `Operatorlar` in the CRM sidebar. Confirm the name, phone, full inquiry text and receipt time, try search/status filters, and change a request to `Bog‘lanildi` or `Yakunlandi`. Changes record the administrator and time and update the corresponding response task. Retrying the same request does not create duplicates or reset a handled request.

## Update the branch directory

Deploy CRM first using its `deploy.sh`, then pull and rebuild the bot as above. This directory update uses the existing filial columns and introduces no new migration. The authenticated `/api/v1/bot/content/branches` response reads the current filial address, phone, opening/closing times, working days and upcoming closure dates; it retains `text` for older bot versions and also supplies public structured `data`. It is not cached and does not use the old static `branches` bot text.

Edit a filial in CRM's `Filiallar` section, then press `Manzil va ish vaqti` in Telegram to confirm the updated address and schedule. Missing opening or closing times use `09:00` or `18:00`. If there are no filials, no address is invented; the response shows standard hours and asks the customer to contact the operator. Other customer-facing bot texts remain editable under bot content.

## If the server fails during handover

Stop the server `bot` and `worker` before restarting the local instance. The local polling bot deletes the Telegram webhook on startup. If the server received new updates or queue jobs, preserve and transfer that bot state before rolling back; use the latest state and avoid running two workers against separate copies of the same jobs.
