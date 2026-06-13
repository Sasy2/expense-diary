# Expense Diary — Codebase Reference

> Generated overview of the full project. See `README.md` for deploy instructions.

## What it is

**WhatsApp Expense Diary** is an AI-powered expense tracker for Ghanaian solopreneurs. Users log expenses by messaging a WhatsApp business number — no app or login. The backend is **FastAPI** on **Render**, messages via **Meta WhatsApp Cloud API**, data in **Supabase** (PostgreSQL + Storage), parsing via **OpenAI GPT-4.1-mini**, payments via **Paystack**.

**Version:** 4.0.0 (`main.py`)

---

## Architecture

```
User (WhatsApp)
      │
      ▼
Meta Cloud API ──POST──► /whatsapp_handler (main.py)
                              │
              ┌───────────────┼───────────────┐
              ▼               ▼               ▼
        handlers.py     parser.py       whatsapp.py
        (commands +     (OpenAI)        (send reply)
         expense flow)
              │
              ▼
        database.py ◄──► security.py (encrypt/decrypt)
              │
              ▼
        Supabase PostgreSQL + Storage
```

**Payment flow:**

```
User ──UPGRADE──► payments.py (Paystack link)
                        │
User pays ──► Paystack ──POST──► /payment_webhook
                                      │
                              set_user_tier() + WhatsApp confirmation
```

---

## Project structure

| Path | Purpose |
|------|---------|
| `main.py` | FastAPI app, all HTTP endpoints, WhatsApp message routing |
| `requirements.txt` | Python dependencies |
| `render.yaml` | Render.com deployment blueprint |
| `.env.example` | Environment variable template |
| `README.md` | Deploy guide and user docs |
| `app/__init__.py` | Package marker |
| `app/models.py` | Pydantic models, tier config, categories |
| `app/database.py` | Supabase data access layer |
| `app/handlers.py` | Command detection, expense pipeline |
| `app/parser.py` | GPT-4.1-mini structured expense extraction |
| `app/messaging.py` | WhatsApp message templates + CSV generation |
| `app/whatsapp.py` | Meta Graph API send + webhook verification |
| `app/storage.py` | Supabase Storage for CSV reports |
| `app/payments.py` | Paystack payment links + webhook verification |
| `app/security.py` | Phone hashing + per-user AES-256 Fernet encryption |
| `sql/schema.sql` | Tables + RLS |
| `sql/rpc_increment.sql` | Atomic `increment_entry_count` RPC |
| `sql/migrate_tiers.sql` | Migrate legacy `is_pro` → `tier` |

---

## HTTP endpoints (`main.py`)

| Method | Path | Auth | Description |
|--------|------|------|-------------|
| `GET` | `/health` | None | Render health check → `{"status": "ok"}` |
| `GET` | `/whatsapp_handler` | Verify token | Meta webhook handshake (returns challenge) |
| `POST` | `/whatsapp_handler` | `X-Hub-Signature-256` | Inbound WhatsApp messages |
| `POST` | `/payment_webhook` | `x-paystack-signature` | Paystack `charge.success` → tier upgrade |
| `POST` | `/monthly_cron` | `X-Cron-Secret` | Reset all users' `entry_count` on 1st of month |
| `POST` | `/` | None | Manual expense entry (testing) |
| `POST` | `/status` | None | Supabase connectivity check |

**Startup (`lifespan`):** validates encryption secrets, initializes Supabase client.

**WhatsApp message handling:**
- Ignores messages starting with bot emoji prefixes (prevents echo loops)
- **text** → route to welcome / command / expense
- **image** → uses caption as expense text, or prompts for caption
- **audio/voice** → not supported, prompts user to type instead

---

## Module reference

### `app/models.py`

**Tiers:**

| Tier | Entries/month | Price (GHS) | CSV export | Monthly summary |
|------|---------------|-------------|------------|-----------------|
| `free` | 5 | 0 | No | No |
| `pro` | 30 | 25 | No | Yes |
| `premium` | 100 | 99 | Yes | Yes |

**Key functions:**
- `get_entry_limit(tier)` — monthly cap
- `nudge_threshold(tier)` — soft upgrade nudge at `limit - 2`
- `can_export_csv(tier)` — Premium only
- `gets_monthly_summary(tier)` — Pro + Premium

**Categories:** Food & Dining, Transport, Internet & Data, Utilities, Office Supplies, Marketing, Professional Services, Entertainment, Healthcare, Shopping, Rent & Housing, Other

**Models:** `ExpenseEntry`, `ManualExpenseRequest`, `ManualExpenseResponse`, `DbStatusResponse`, `PaystackWebhookRequest`

---

### `app/security.py`

- **`get_phone_lookup_id(phone)`** — `SHA-256(normalize(phone) + ":" + PHONE_LOOKUP_SALT)` — irreversible, used as DB key
- **`_derive_fernet_key(phone)`** — `HMAC-SHA256(ENCRYPTION_SECRET, phone)` → Fernet key (never stored)
- **`encrypt_for_user(data, phone)`** / **`decrypt_for_user(ciphertext, phone)`** — JSON → Fernet ciphertext
- **`safe_log_phone(phone)`** — logs only last 2 digits
- **`assert_secrets_strength()`** — startup check: `ENCRYPTION_SECRET` ≥ 32 chars, `PHONE_LOOKUP_SALT` ≥ 16 chars

> **Critical:** Rotating `ENCRYPTION_SECRET` or `PHONE_LOOKUP_SALT` after users exist breaks all stored data.

---

### `app/database.py`

Singleton Supabase client via `init_supabase()` / `get_supabase()`. All DB calls use `asyncio.to_thread()`.

| Function | Description |
|----------|-------------|
| `get_or_create_user(phone)` | Returns `(user_id, is_new)` |
| `get_user_record(phone)` | `{id, tier, entry_count}` or None |
| `set_user_tier(phone, tier)` | After Paystack payment |
| `increment_entry_count(user_id)` | RPC `increment_entry_count` — atomic |
| `reset_all_entry_counts()` | Cron: set all `entry_count = 0` |
| `get_users_for_monthly_summary()` | Pro + Premium users (summary dispatch stub) |
| `save_expense(phone, user_id, entry, input_method)` | Encrypt payload, insert row |
| `get_user_expenses(phone, month_year?, limit?, order_desc?)` | Fetch + decrypt; skips bad rows |

**Encrypted payload fields:** amount, currency, category, merchant, description, entry_type, input_method, timestamp

**Plaintext DB fields:** `month_year` (for filtering), `logged_at`, `user_id`

---

### `app/handlers.py`

**Commands** (case/spacing insensitive): `HELP`, `TOTAL`, `REPORT`, `LAST5`, `UPGRADE`, `PRO`, `PREMIUM`

| Command | Behavior |
|---------|----------|
| `HELP` | Usage instructions |
| `TOTAL` | Current month category breakdown |
| `LAST5` | Last 5 entries (newest first) |
| `REPORT` | CSV export (Premium) or paywall |
| `UPGRADE` | Payment links for available upgrades |
| `PRO` / `PREMIUM` | Direct link to specific plan |

**`process_expense_message` pipeline:**
1. Check `entry_count` vs tier limit → paywall if exceeded
2. `parse_expense(text)` via OpenAI
3. `save_expense()` encrypted to Supabase
4. `increment_entry_count()`
5. Send confirmation + optional upgrade nudge

---

### `app/parser.py`

- Model: **`gpt-4.1-mini`**
- Uses OpenAI **structured output** (`response_format=ExpenseEntry`)
- 15s timeout
- System prompt: Ghana context, default GHS, positive amounts, Income vs Expense

---

### `app/messaging.py`

Message builders: `build_welcome`, `build_help`, `build_total_summary`, `build_last_n`, `build_confirmation`, `build_upgrade_nudge`, `build_upgrade_menu`, `build_premium_upgrade_message`, `build_limit_reached_message`, `build_report_paywall`, `build_tier_confirmation`

**`generate_and_upload_csv(rows, user_id)`** — writes CSV, uploads via `storage.py`, returns signed URL

Re-exports `send_wa_text` from `whatsapp.py`

---

### `app/whatsapp.py`

- **`send_wa_text(phone, text)`** — POST to `graph.facebook.com/{version}/{phone_number_id}/messages`
- **`verify_webhook_challenge(mode, token, challenge)`** — Meta GET verification
- **`verify_meta_signature(body, signature)`** — HMAC-SHA256; skipped if `WHATSAPP_APP_SECRET` empty

---

### `app/storage.py`

- Bucket: `SUPABASE_STORAGE_BUCKET` (default `expense-reports`)
- Path: `{user_id}/expenses_{YYYY-MM}.csv`
- Signed URL TTL: 24 hours

---

### `app/payments.py`

- **`create_payment_link(phone, tier)`** — Paystack `/transaction/initialize`
  - Pro: 2500 pesewas (GHS 25)
  - Premium: 9900 pesewas (GHS 99)
  - Synthetic email: `{phone}@expensediary.app`
  - Metadata: `phone`, `tier`, `product`
- **`verify_webhook_signature(body, signature)`** — HMAC-SHA512
- **`extract_phone_from_webhook`** / **`extract_tier_from_webhook`** — from metadata

---

## Database schema (`sql/`)

### `expense_users`

| Column | Type | Notes |
|--------|------|-------|
| `id` | UUID PK | |
| `phone_id` | TEXT UNIQUE | SHA-256 hash, not real phone |
| `tier` | TEXT | `free`, `pro`, `premium` |
| `entry_count` | INT | Resets monthly via cron |
| `created_at` | TIMESTAMPTZ | |

### `expenses`

| Column | Type | Notes |
|--------|------|-------|
| `id` | UUID PK | |
| `user_id` | UUID FK | → `expense_users` |
| `encrypted_payload` | TEXT | Fernet ciphertext |
| `month_year` | TEXT | `YYYY-MM` for queries |
| `logged_at` | TIMESTAMPTZ | |

**Indexes:** `(user_id, month_year)`, `(user_id, logged_at DESC)`

**RLS:** enabled on both tables, no policies — only service role can access.

### `increment_entry_count(user_uuid UUID) → INT`

Atomic `UPDATE ... SET entry_count = entry_count + 1 RETURNING entry_count`

---

## Dependencies (`requirements.txt`)

| Package | Use |
|---------|-----|
| `fastapi` | Web framework |
| `uvicorn[standard]` | ASGI server |
| `httpx` | WhatsApp + Paystack HTTP |
| `cryptography` | Fernet encryption |
| `openai` | Expense parsing |
| `structlog` | Structured logging |
| `supabase` | DB + Storage client |

---

## Environment variables

| Variable | Required | Description |
|----------|----------|-------------|
| `PORT` | No | Server port (default 8000) |
| `WHATSAPP_ACCESS_TOKEN` | Yes | Meta Graph API token |
| `WHATSAPP_PHONE_NUMBER_ID` | Yes | Business phone number ID |
| `WHATSAPP_VERIFY_TOKEN` | Yes | Webhook verify token (you choose) |
| `WHATSAPP_APP_SECRET` | Recommended | Webhook signature verification |
| `WHATSAPP_API_VERSION` | No | Default `v21.0` |
| `SUPABASE_URL` | Yes | Supabase project URL |
| `SUPABASE_SERVICE_KEY` | Yes | Service role key (not anon) |
| `SUPABASE_STORAGE_BUCKET` | No | Default `expense-reports` |
| `ENCRYPTION_SECRET` | Yes | ≥32 chars, never rotate after users |
| `PHONE_LOOKUP_SALT` | Yes | ≥16 chars, never rotate after users |
| `OPENAI_API_KEY` | Yes | For GPT-4.1-mini parsing |
| `PAYSTACK_SECRET_KEY` | Yes (prod) | Paystack API |
| `PAYSTACK_WEBHOOK_SECRET` | Yes (prod) | Webhook HMAC secret |
| `CRON_SECRET` | Yes (cron) | Protects `/monthly_cron` |

---

## User flow (end-to-end)

1. User messages WhatsApp business number
2. Meta POSTs webhook → `handle_whatsapp_event`
3. **First message:** create user in Supabase, send welcome
4. **Commands:** `handle_command` → formatted reply
5. **Expense text:** parse → encrypt → save → confirm (+ nudge near limit)
6. **At limit:** Paystack upgrade links
7. **Payment:** Paystack webhook → `set_user_tier` → confirmation message
8. **Monthly cron:** reset `entry_count` for all users (summaries not yet implemented)

---

## Known gaps / future work

- **Monthly summary dispatch** — `monthly_cron` fetches Pro/Premium users but `sent = 0` (cannot decrypt without phone; only `phone_id` hash stored)
- **Voice notes** — explicitly unsupported
- **Image receipts** — caption only, no OCR
- **`.env.example`** — should use placeholders only; do not commit real secrets

---

## Local development

```bash
cp .env.example .env   # fill with your values
pip install -r requirements.txt
uvicorn main:app --reload --port 8000
```

```bash
# Health
curl http://localhost:8000/health

# DB status
curl -X POST http://localhost:8000/status

# Manual expense
curl -X POST http://localhost:8000/ \
  -H "Content-Type: application/json" \
  -d '{"phone_number": "233XXXXXXXXX", "message": "45 GHS lunch at Papaye"}'
```
