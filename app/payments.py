"""
payments.py — Paystack integration.

  create_payment_link()      → Initialise a Paystack transaction for Pro or Premium.
  verify_transaction()       → Confirm payment by reference (browser callback fallback).
  process_successful_charge() → Upgrade tier + WhatsApp confirmation.
  verify_webhook_signature() → HMAC-SHA512 check on inbound Paystack webhooks.

Paystack uses pesewas (1/100 of a Cedi) for GHS amounts.
GHS 25.00 = 2500 pesewas, GHS 99.00 = 9900 pesewas.
"""

import hashlib
import hmac
import json
import os

import httpx
from structlog import get_logger

from app.database import set_user_tier
from app.messaging import build_tier_confirmation, send_wa_text
from app.models import TIER_PREMIUM, TIER_PRO, TIER_PRICES_GHS
from app.security import safe_log_phone

logger = get_logger()

PAYSTACK_API = "https://api.paystack.co"
TIER_AMOUNT_PESEWAS = {
    TIER_PRO: 2500,      # GHS 25.00
    TIER_PREMIUM: 9900,  # GHS 99.00
}


def _app_url() -> str | None:
    url = os.environ.get("APP_URL", "").strip().rstrip("/")
    return url or None


async def create_payment_link(phone: str, tier: str) -> str:
    """
    Initialise a Paystack transaction for a tier upgrade.
    Returns the authorization_url (mobile-friendly payment page).

    Paystack requires an email — we synthesise one from the phone number
    since we don't collect real emails. The metadata fields are what
    matter for the webhook handler.
    """
    if tier not in TIER_AMOUNT_PESEWAS:
        raise ValueError(f"Unknown tier: {tier}")

    synthetic_email = f"{phone.lstrip('+').replace(' ', '')}@expensediary.app"
    headers = {
        "Authorization": f"Bearer {os.environ['PAYSTACK_SECRET_KEY']}",
        "Content-Type":  "application/json",
    }
    payload: dict = {
        "amount":   TIER_AMOUNT_PESEWAS[tier],
        "email":    synthetic_email,
        "currency": "GHS",
        "metadata": {
            "phone":         phone,
            "tier":          tier,
            "product":       f"expense_diary_{tier}",
            "cancel_action": "https://expensediary.app",
        },
    }
    app_url = _app_url()
    if app_url:
        payload["callback_url"] = f"{app_url}/payment/success"
    else:
        logger.warning("APP_URL not set — Paystack will not redirect after payment")

    async with httpx.AsyncClient(timeout=10.0) as client:
        resp = await client.post(
            f"{PAYSTACK_API}/transaction/initialize",
            headers=headers,
            json=payload,
        )
        resp.raise_for_status()
        data = resp.json()

    if not data.get("status"):
        raise RuntimeError(f"Paystack init failed: {data.get('message')}")

    url = data["data"]["authorization_url"]
    logger.info("Paystack link created", phone=phone[-4:], tier=tier)
    return url


async def verify_transaction(reference: str) -> dict | None:
    """Verify a Paystack transaction by reference. Returns charge data if successful."""
    reference = reference.strip()
    if not reference:
        return None

    headers = {"Authorization": f"Bearer {os.environ['PAYSTACK_SECRET_KEY']}"}
    async with httpx.AsyncClient(timeout=15.0) as client:
        resp = await client.get(
            f"{PAYSTACK_API}/transaction/verify/{reference}",
            headers=headers,
        )
        resp.raise_for_status()
        body = resp.json()

    if not body.get("status"):
        logger.warning("Paystack verify rejected", message=body.get("message"))
        return None

    data = body.get("data") or {}
    if data.get("status") != "success":
        return None
    return data


def _normalise_metadata(raw) -> dict:
    if isinstance(raw, dict):
        return raw
    if isinstance(raw, str):
        try:
            parsed = json.loads(raw)
            return parsed if isinstance(parsed, dict) else {}
        except json.JSONDecodeError:
            return {}
    return {}


def extract_phone_from_charge(charge_data: dict) -> str | None:
    """Pull the customer phone from Paystack charge/transaction data."""
    meta = _normalise_metadata(charge_data.get("metadata"))

    phone = meta.get("phone")
    if phone:
        return str(phone)

    for field in meta.get("custom_fields") or []:
        if isinstance(field, dict) and field.get("variable_name") == "phone":
            value = field.get("value")
            if value:
                return str(value)

    customer = charge_data.get("customer") or {}
    if customer.get("phone"):
        return str(customer["phone"])

    return None


def extract_tier_from_charge(charge_data: dict) -> str:
    """Pull the purchased tier from Paystack charge/transaction data."""
    meta = _normalise_metadata(charge_data.get("metadata"))

    tier = meta.get("tier")
    if tier in (TIER_PRO, TIER_PREMIUM):
        return tier

    product = meta.get("product")
    if product == "expense_diary_premium":
        return TIER_PREMIUM
    if product == "expense_diary_pro":
        return TIER_PRO

    return TIER_PRO


def extract_phone_from_webhook(payload: dict) -> str | None:
    """Pull phone from a Paystack webhook envelope."""
    try:
        return extract_phone_from_charge(payload["data"])
    except (KeyError, TypeError):
        return None


def extract_tier_from_webhook(payload: dict) -> str:
    try:
        return extract_tier_from_charge(payload["data"])
    except (KeyError, TypeError):
        return TIER_PRO


async def process_successful_charge(charge_data: dict) -> bool:
    """
    Apply tier upgrade after confirmed payment.
    Returns True if the user's tier changed.
    """
    phone = extract_phone_from_charge(charge_data)
    if not phone:
        logger.warning("Paystack charge missing phone in metadata")
        return False

    tier = extract_tier_from_charge(charge_data)
    changed = await set_user_tier(phone, tier)
    if changed:
        await send_wa_text(phone, build_tier_confirmation(tier))
        logger.info("Tier upgrade complete", phone=safe_log_phone(phone), tier=tier)
    else:
        logger.info("Tier already active", phone=safe_log_phone(phone), tier=tier)
    return changed


def verify_webhook_signature(body: bytes, signature: str) -> bool:
    """
    Verify a Paystack webhook using HMAC-SHA512.
    Paystack sends the signature in the X-Paystack-Signature header.
    Returns True if the signature matches, False otherwise.
    """
    secret = os.environ.get("PAYSTACK_WEBHOOK_SECRET", "")
    if not secret:
        logger.warning("PAYSTACK_WEBHOOK_SECRET not set — rejecting webhook")
        return False

    if not signature:
        return False

    expected = hmac.new(secret.encode(), body, hashlib.sha512).hexdigest()
    return hmac.compare_digest(expected, signature)
