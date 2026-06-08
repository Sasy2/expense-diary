"""
main.py — WhatsApp Expense Diary (FastAPI)

Endpoints:
  GET  /whatsapp_handler    Meta webhook verification
  POST /whatsapp_handler    Inbound WhatsApp messages from Meta
  POST /payment_webhook     Paystack charge.success webhook
  POST /monthly_cron        Monthly entry reset (external cron)
  GET  /health              Render health check
  POST /status              Supabase connectivity check
  POST /                    Manual expense entry (testing)
"""

import json
import os
import re
from contextlib import asynccontextmanager
from datetime import datetime, timezone

import structlog
import uvicorn
from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import PlainTextResponse
from structlog import get_logger

from app.database import (
    get_or_create_user,
    init_supabase,
    reset_all_entry_counts,
    get_users_for_monthly_summary,
    save_expense,
    set_user_tier,
)
from app.handlers import detect_command, detect_greeting, handle_command, process_expense_message
from app.messaging import build_greeting_reply, build_tier_confirmation, build_welcome, send_wa_text
from app.models import (
    DbStatusResponse,
    ManualExpenseRequest,
    ManualExpenseResponse,
)
from app.parser import parse_expense
from app.payments import (
    extract_phone_from_webhook,
    extract_tier_from_webhook,
    verify_webhook_signature,
)
from app.security import assert_secrets_strength, safe_log_phone
from app.whatsapp import verify_meta_signature, verify_webhook_challenge

structlog.configure(
    processors=[
        structlog.processors.TimeStamper(fmt="iso"),
        structlog.processors.add_log_level,
        structlog.dev.ConsoleRenderer(),
    ]
)
logger = get_logger()

BOT_PREFIXES = (
    "\u2705", "\u274c", "\U0001f4ca", "\U0001f4b0", "\U0001f4b8",
    "\U0001f4c1", "\u26a0", "\U0001f9fe", "\U0001f4c8",
    "\U0001f399", "\U0001f4f8", "\U0001f4a1", "\U0001f38a",
)


@asynccontextmanager
async def lifespan(app: FastAPI):
    assert_secrets_strength()
    init_supabase()
    logger.info("Expense Diary started", whatsapp_api=os.environ.get("WHATSAPP_API_VERSION", "v21.0"))
    yield
    logger.info("Shutting down")


app = FastAPI(
    title="WhatsApp Expense Diary",
    description=(
        "AI-powered multi-tenant expense tracker via WhatsApp. "
        "Supabase PostgreSQL with per-user AES-256-Fernet encryption. "
        "Free: 5 entries/month. Pro: GHS 25 (30 entries). Premium: GHS 99 (100 entries + CSV)."
    ),
    version="4.0.0",
    lifespan=lifespan,
)


@app.get("/health")
async def health():
    """Lightweight health check for Render."""
    return {"status": "ok"}


@app.get("/whatsapp_handler")
async def verify_whatsapp_webhook(request: Request):
    """
    Meta webhook verification handshake.
    Meta sends: hub.mode, hub.verify_token, hub.challenge
    """
    challenge = verify_webhook_challenge(
        request.query_params.get("hub.mode"),
        request.query_params.get("hub.verify_token"),
        request.query_params.get("hub.challenge"),
    )
    if challenge is None:
        raise HTTPException(status_code=403, detail="Verification failed")
    logger.info("WhatsApp webhook verified")
    return PlainTextResponse(challenge)


@app.post("/whatsapp_handler")
async def handle_whatsapp_event(request: Request):
    """
    Receives inbound WhatsApp messages from Meta Cloud API.
    Always returns HTTP 200 — webhook must never retry on app errors.
    """
    body = await request.body()

    if not verify_meta_signature(body, request.headers.get("X-Hub-Signature-256", "")):
        logger.warning("Invalid Meta webhook signature")
        raise HTTPException(status_code=403, detail="Invalid signature")

    try:
        payload = json.loads(body)

        if payload.get("object") != "whatsapp_business_account":
            return {"status": "skip", "reason": "not_whatsapp_event"}

        for entry in payload.get("entry", []):
            for change in entry.get("changes", []):
                if change.get("field") != "messages":
                    continue
                value = change.get("value", {})
                for msg in value.get("messages", []):
                    await _process_single_message(msg)

    except Exception as exc:
        logger.error("WhatsApp handler error", error=str(exc))

    return {"status": "ok"}


async def _process_single_message(msg: dict) -> None:
    """Handle one inbound WhatsApp message object."""
    from_phone = msg.get("from", "")
    msg_type   = msg.get("type", "text")

    if msg_type == "text":
        body = msg.get("text", {}).get("body", "").strip()
        if not body:
            return
        if any(body.startswith(p) for p in BOT_PREFIXES):
            return
        await _route_text(from_phone, body)

    elif msg_type == "image":
        caption = msg.get("image", {}).get("caption", "").strip()
        if caption:
            await _route_text(from_phone, caption, input_method="image")
        else:
            await send_wa_text(
                from_phone,
                "\U0001f4f8 Got your receipt!\n\n"
                "Please resend it *with a caption* describing the expense.\n"
                "Example: 'Grocery receipt GHS 120'"
            )

    elif msg_type in ("audio", "voice"):
        await send_wa_text(
            from_phone,
            "\U0001f399 Voice notes aren't supported yet.\n\n"
            "Please *type* your expense instead:\n"
            "  \u2022 '450 GHS internet data'\n"
            "  \u2022 'Uber 35 GHS'\n"
            "  \u2022 'Client paid 2000 GHS'"
        )


async def _route_text(phone: str, text: str, input_method: str = "text") -> None:
    """Route a text message: new user welcome / command / expense."""
    user_id, is_new = await get_or_create_user(phone)
    if is_new:
        logger.info("First message from new user", phone=safe_log_phone(phone))
        await send_wa_text(phone, build_welcome())
        return

    command = detect_command(text)
    if command:
        logger.info("Command received", command=command, phone=safe_log_phone(phone))
        await handle_command(phone, command)
        return

    if detect_greeting(text):
        logger.info("Greeting received", phone=safe_log_phone(phone))
        await send_wa_text(phone, build_greeting_reply())
        return

    await process_expense_message(phone, user_id, text, input_method)


@app.post("/payment_webhook")
async def payment_webhook(request: Request):
    """Receives Paystack charge.success events and upgrades user tier."""
    body = await request.body()
    signature = request.headers.get("x-paystack-signature", "")

    if not verify_webhook_signature(body, signature):
        logger.warning("Invalid Paystack webhook signature")
        raise HTTPException(status_code=400, detail="Invalid signature")

    try:
        payload = json.loads(body)
    except Exception:
        raise HTTPException(status_code=400, detail="Invalid JSON")

    event = payload.get("event")
    logger.info("Paystack webhook", event=event)

    if event == "charge.success":
        phone = extract_phone_from_webhook(payload)
        if phone:
            tier = extract_tier_from_webhook(payload)
            await set_user_tier(phone, tier)
            await send_wa_text(phone, build_tier_confirmation(tier))
            logger.info("Tier upgrade complete", phone=safe_log_phone(phone), tier=tier)
        else:
            logger.warning("Paystack webhook missing phone in metadata")

    return {"status": "ok"}


@app.post("/monthly_cron")
async def monthly_cron(request: Request):
    """
    Called by an external cron job on the 1st of each month.
    Resets entry counters. Proactive summaries need encrypted_phone (future).
    """
    secret = request.headers.get("x-cron-secret", "")
    if secret != os.environ.get("CRON_SECRET", ""):
        raise HTTPException(status_code=401, detail="Unauthorised")

    logger.info("Monthly cron started")
    month_year = datetime.now(timezone.utc).strftime("%Y-%m")

    reset_count = await reset_all_entry_counts()
    logger.info("Entry counts reset", users=reset_count)

    summary_users = await get_users_for_monthly_summary()
    sent = 0

    logger.info("Monthly cron complete", summaries_sent=sent, eligible_users=len(summary_users))
    return {
        "status":             "ok",
        "entry_counts_reset": reset_count,
        "summaries_sent":     sent,
        "month":              month_year,
    }


@app.post("/", response_model=ManualExpenseResponse)
async def log_expense_manually(req: ManualExpenseRequest) -> ManualExpenseResponse:
    """Log an expense via REST — useful for testing without WhatsApp."""
    phone = req.phone_number.lstrip("+")
    if not re.fullmatch(r"\d{7,15}", phone):
        raise HTTPException(status_code=422, detail="Invalid phone_number format")

    user_id, _ = await get_or_create_user(phone)
    entry = await parse_expense(req.message)
    await save_expense(phone, user_id, entry, "manual")

    return ManualExpenseResponse(
        success=True,
        logged=f"{entry.entry_type}: {entry.currency} {entry.amount:,.2f} · {entry.category}",
        amount=entry.amount,
        currency=entry.currency,
        category=entry.category,
        merchant=entry.merchant,
        entry_type=entry.entry_type,
    )


@app.post("/status", response_model=DbStatusResponse)
async def db_status() -> DbStatusResponse:
    """Test Supabase connectivity."""
    try:
        import asyncio
        from app.database import get_supabase
        sb = get_supabase()
        await asyncio.to_thread(
            lambda: sb.table("expense_users").select("id").limit(1).execute()
        )
        return DbStatusResponse(
            status="ok",
            tables_accessible=True,
            message="Supabase connected. expense_users and expenses tables accessible.",
        )
    except Exception as exc:
        raise HTTPException(status_code=503, detail=f"Supabase error: {exc}")


if __name__ == "__main__":
    port = int(os.environ.get("PORT", "8000"))
    uvicorn.run("main:app", host="0.0.0.0", port=port)
