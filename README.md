# WhatsApp Expense Diary

AI-powered expense tracker for Ghanaian solopreneurs. Runs entirely inside WhatsApp — no app, no login.

Hosted on **Render**, WhatsApp via **Meta Cloud API**, data in **Supabase**.

## Project structure

```
expense-diary/
├── main.py               # FastAPI app, all endpoints
├── requirements.txt      # Python dependencies
├── render.yaml           # Render blueprint (optional)
├── app/
│   ├── whatsapp.py       # Meta Cloud API send + webhook verify
│   ├── storage.py        # Supabase Storage for CSV reports
│   ├── security.py       # Phone hashing + AES-256 Fernet encryption
│   ├── database.py       # Supabase data access layer
│   ├── models.py         # Pydantic models + tier config
│   ├── parser.py         # GPT-4.1-mini structured expense extraction
│   ├── messaging.py      # Report formatters
│   ├── handlers.py       # Command router + expense pipeline
│   └── payments.py       # Paystack link generation + webhook verification
└── sql/
    ├── schema.sql         # Tables + RLS (run first)
    ├── rpc_increment.sql  # Atomic entry counter RPC (run second)
    └── migrate_tiers.sql  # Migrate is_pro → tier (existing deployments only)
```

---

## Step-by-step deploy

### Step 1 — Supabase (database)

1. Create a project at [supabase.com](https://supabase.com).
2. Open **SQL Editor** and run, in order:
   - `sql/schema.sql`
   - `sql/rpc_increment.sql`
3. Copy from **Project Settings → API**:
   - `SUPABASE_URL`
   - `SUPABASE_SERVICE_KEY` (service role, not anon)

#### CSV storage bucket

1. In Supabase → **Storage** → **New bucket**
2. Name: `expense-reports`
3. Set **Private** (not public)
4. No extra policies needed — the app uses the service role key

Generate encryption secrets (save these permanently):

```bash
python -c "import secrets; print(secrets.token_hex(32))"   # ENCRYPTION_SECRET
python -c "import secrets; print(secrets.token_hex(16))"   # PHONE_LOOKUP_SALT
python -c "import secrets; print(secrets.token_hex(16))"   # CRON_SECRET
```

---

### Step 2 — Meta WhatsApp Cloud API

1. Go to [developers.facebook.com](https://developers.facebook.com) → **Create App** → type **Business**.
2. Add the **WhatsApp** product to your app.
3. In **WhatsApp → API Setup**, note:
   - **Phone number ID** → `WHATSAPP_PHONE_NUMBER_ID`
   - Generate a **temporary access token** (or set up a System User for a permanent token) → `WHATSAPP_ACCESS_TOKEN`
4. In **App Settings → Basic**, copy **App Secret** → `WHATSAPP_APP_SECRET`
5. Choose a verify token (any random string you invent) → `WHATSAPP_VERIFY_TOKEN`

> **Test number:** Meta gives you a test WhatsApp number. Add your personal number under **API Setup → To** so you can send/receive during development.

---

### Step 3 — Deploy to Render

1. Push this repo to GitHub.
2. In [render.com](https://render.com) → **New → Web Service** → connect your repo.
3. Settings:
   - **Runtime:** Python 3
   - **Build command:** `pip install -r requirements.txt`
   - **Start command:** `uvicorn main:app --host 0.0.0.0 --port $PORT`
   - **Health check path:** `/health`
4. Add all environment variables from `.env.example` in the Render **Environment** tab.
5. Deploy and copy your live URL, e.g. `https://expense-diary-xxxx.onrender.com`

---

### Step 4 — Connect Meta webhook

1. In Meta Developer → **WhatsApp → Configuration → Webhook**:
   - **Callback URL:** `https://expense-diary-xxxx.onrender.com/whatsapp_handler`
   - **Verify token:** same value as `WHATSAPP_VERIFY_TOKEN`
2. Click **Verify and save** — Meta sends a GET; your app returns the challenge.
3. Subscribe to the **messages** field.
4. Send a test WhatsApp message to your business number — you should get the welcome reply.

---

### Step 5 — Paystack

1. In Paystack dashboard → **Settings → Webhooks**:
   - URL: `https://expense-diary-xxxx.onrender.com/payment_webhook`
2. Copy the webhook secret → `PAYSTACK_WEBHOOK_SECRET`
3. Add `PAYSTACK_SECRET_KEY` from Paystack **Settings → API Keys**

---

### Step 6 — Monthly cron (optional)

Use [cron-job.org](https://cron-job.org) or similar to call on the 1st of each month:

```
POST https://expense-diary-xxxx.onrender.com/monthly_cron
Header: X-Cron-Secret: <your CRON_SECRET>
```

This resets free-tier entry counts for all users.

---

### Step 7 — Local testing (without WhatsApp)

```bash
cp .env.example .env
# fill in all values
pip install -r requirements.txt
uvicorn main:app --reload --port 8000
```

Test Supabase connectivity:

```bash
curl -X POST http://localhost:8000/status
```

Test expense parsing:

```bash
curl -X POST http://localhost:8000/ \
  -H "Content-Type: application/json" \
  -d '{"phone_number": "233XXXXXXXXX", "message": "45 GHS lunch at Papaye"}'
```

---

## User flow

1. User messages your WhatsApp business number
2. Meta POSTs to `/whatsapp_handler`
3. First message → welcome sent, user created in Supabase
4. Subsequent messages → command or AI expense parsing
5. Near entry limit → upgrade nudge
6. At tier limit → paywall with Paystack links
7. Payment → Paystack webhook → tier activated → confirmation via WhatsApp

## Commands

| Command | Action |
|---------|--------|
| HELP    | Usage instructions |
| TOTAL   | This month's breakdown by category |
| LAST 5  | Last 5 entries |
| REPORT  | Download full CSV (Premium only) |
| UPGRADE | See Pro & Premium payment links |
| PRO     | Direct link to Pro plan |
| PREMIUM | Direct link to Premium plan |

## Tiers

| | Free | Pro | Premium |
|---|---|---|---|
| Entries/month | 5 | 30 | 100 |
| TOTAL | ✓ | ✓ | ✓ |
| LAST 5 | ✓ | ✓ | ✓ |
| CSV export (REPORT) | ✗ | ✗ | ✓ |
| Monthly summary | ✗ | ✓ | ✓ |
| Price | GHS 0 | GHS 25/month | GHS 99/month |

## Security model

- Phone numbers stored as `SHA-256(phone + PHONE_LOOKUP_SALT)` — irreversible
- Each user gets a unique AES-256 Fernet key derived via `HMAC-SHA256(ENCRYPTION_SECRET, phone)`
- All financial data encrypted before storage; only `month_year` stored in plaintext
- Supabase RLS blocks anon/authenticated access — service role only
- Meta webhook POSTs verified via `X-Hub-Signature-256` when `WHATSAPP_APP_SECRET` is set
- `ENCRYPTION_SECRET` and `PHONE_LOOKUP_SALT` must never be rotated after first user
