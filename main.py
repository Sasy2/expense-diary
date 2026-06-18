"""
main.py — WhatsApp Expense Diary (FastAPI)

Endpoints:
  GET  /whatsapp_handler    Meta webhook verification
  POST /whatsapp_handler    Inbound WhatsApp messages from Meta
  GET  /payment/success     Browser return after Paystack payment
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
from fastapi.responses import HTMLResponse, PlainTextResponse
from structlog import get_logger

from app.database import (
    get_or_create_user,
    get_user_record,
    init_supabase,
    reset_all_entry_counts,
    save_expense,
)
from app.summaries import send_monthly_recaps
from app.trial import handle_trial_lifecycle, run_trial_expiry_cron
from app.handlers import detect_command, detect_greeting, handle_command, process_expense_message
from app.messaging import build_greeting_reply, build_welcome, send_wa_text
from app.models import (
    DbStatusResponse,
    ManualExpenseRequest,
    ManualExpenseResponse,
)
from app.parser import parse_expense
from app.payments import (
    process_successful_charge,
    verify_transaction,
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
    title="KountN",
    description=(
        "KountN — AI-powered expense tracker via WhatsApp. "
        "30-day Pro trial for new users. Pro: GHS 20 (75 txns/mo). Premium: GHS 49 (300 txns/mo + CSV)."
    ),
    version="5.0.0",
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
        record = await get_user_record(phone)
        trial_end = (record or {}).get("trial_ends_at", "")
        await send_wa_text(phone, build_welcome(trial_end))
        return

    await handle_trial_lifecycle(phone)

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


@app.get("/payment/success")
async def payment_success(
    reference: str | None = None,
    trxref: str | None = None,
):
    """
    Browser landing page after Paystack checkout.
    Verifies the transaction and upgrades the user's tier (fallback if webhook fails).
    """
    ref = (reference or trxref or "").strip()
    if not ref:
        return HTMLResponse(
            "<h1>Payment reference missing</h1>"
            "<p>Return to WhatsApp and send UPGRADE if your plan is not active yet.</p>",
            status_code=400,
        )

    try:
        charge = await verify_transaction(ref)
    except Exception as exc:
        logger.error("Paystack verify failed", error=str(exc), reference=ref[:12])
        return HTMLResponse(
            "<h1>Could not verify payment</h1>"
            "<p>Your payment may still have gone through. "
            "Return to WhatsApp — we will confirm shortly, or send UPGRADE to check.</p>",
            status_code=502,
        )

    if not charge:
        return HTMLResponse(
            "<h1>Payment not completed</h1>"
            "<p>If money was deducted, wait a minute and refresh, or contact support.</p>",
            status_code=400,
        )

    try:
        await process_successful_charge(charge)
    except Exception as exc:
        logger.error("Tier upgrade after payment failed", error=str(exc))
        return HTMLResponse(
            "<h1>Payment received</h1>"
            "<p>We could not activate your plan automatically. "
            "Please message us on WhatsApp with your payment reference.</p>",
            status_code=500,
        )

    return HTMLResponse(
        "<html><body style='font-family:sans-serif;text-align:center;padding:48px'>"
        "<h1>Payment successful!</h1>"
        "<p>Your KountN plan is now active. Return to <strong>WhatsApp</strong> and keep KountN!</p>"
        "</body></html>"
    )


@app.get("/payment_webhook")
async def payment_webhook_info():
    """Paystack webhooks use POST only — this explains the Method Not Allowed confusion."""
    return HTMLResponse(
        "<html><body style='font-family:sans-serif;padding:32px'>"
        "<h1>Paystack webhook endpoint</h1>"
        "<p>This URL is for Paystack server notifications (POST only), not for opening in a browser.</p>"
        "<p>After paying, you should see a <strong>Payment successful</strong> page, then return to WhatsApp.</p>"
        "</body></html>"
    )


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
        await process_successful_charge(payload.get("data") or {})

    return {"status": "ok"}


@app.post("/monthly_cron")
async def monthly_cron(request: Request):
    """
    Called by an external cron job on the 1st of each month.
    Sends last month's recap, expires trials, resets entry counters.
    """
    secret = request.headers.get("x-cron-secret", "")
    if secret != os.environ.get("CRON_SECRET", ""):
        raise HTTPException(status_code=401, detail="Unauthorised")

    logger.info("Monthly cron started")

    summaries_sent, eligible, recap_month = await send_monthly_recaps()
    logger.info(
        "Monthly recaps complete",
        sent=summaries_sent,
        eligible=eligible,
        recap_month=recap_month,
    )

    trials_expired = await run_trial_expiry_cron()
    logger.info("Expired trials downgraded", count=trials_expired)

    reset_count = await reset_all_entry_counts()
    logger.info("Entry counts reset", users=reset_count)

    logger.info("Monthly cron complete")
    return {
        "status":             "ok",
        "recap_month":        recap_month,
        "summaries_sent":     summaries_sent,
        "summaries_eligible": eligible,
        "trials_expired":     trials_expired,
        "entry_counts_reset": reset_count,
    }


@app.post("/", response_model=ManualExpenseResponse)
async def log_expense_manually(req: ManualExpenseRequest) -> ManualExpenseResponse:
    """Log an expense via REST — useful for testing without WhatsApp."""
    phone = req.phone_number.lstrip("+")
    if not re.fullmatch(r"\d{7,15}", phone):
        raise HTTPException(status_code=422, detail="Invalid phone_number format")

    user_id, _ = await get_or_create_user(phone)
    try:
        entries = await parse_expense(req.message)
    except ValueError as exc:
        if "injection" in str(exc):
            raise HTTPException(status_code=400, detail=str(exc))
        raise exc

    if not entries:
        raise HTTPException(status_code=422, detail="No transactions found in message")

    for entry in entries:
        # Zero amount validation check
        if entry.amount < 0 or (entry.amount == 0 and not re.search(r"\b0\b|\bzero\b|\bfree\b", req.message, re.IGNORECASE)):
            continue
        await save_expense(phone, user_id, entry, "manual")

    first = entries[0]
    return ManualExpenseResponse(
        success=True,
        logged=f"{first.entry_type}: {first.currency} {first.amount:,.2f} · {first.category}",
        amount=first.amount,
        currency=first.currency,
        category=first.category,
        merchant=first.merchant,
        entry_type=first.entry_type,
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
