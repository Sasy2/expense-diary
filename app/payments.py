"""
payments.py — Paystack integration.

  create_payment_link()      → Initialise a Paystack transaction for Pro or Premium.
  verify_webhook_signature() → HMAC-SHA512 check on inbound Paystack webhooks.

Paystack uses pesewas (1/100 of a Cedi) for GHS amounts.
GHS 25.00 = 2500 pesewas, GHS 99.00 = 9900 pesewas.
"""

import hashlib
import hmac
import os

import httpx
from structlog import get_logger

from app.models import TIER_PREMIUM, TIER_PRO, TIER_PRICES_GHS

logger = get_logger()

PAYSTACK_API = "https://api.paystack.co"
TIER_AMOUNT_PESEWAS = {
    TIER_PRO: 2500,      # GHS 25.00
    TIER_PREMIUM: 9900,  # GHS 99.00
}


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
    payload = {
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


def verify_webhook_signature(body: bytes, signature: str) -> bool:
    """
    Verify a Paystack webhook using HMAC-SHA512.
    Paystack sends the signature in the X-Paystack-Signature header.
    Returns True if the signature matches, False otherwise.
    """
    secret = os.environ["PAYSTACK_WEBHOOK_SECRET"].encode()
    expected = hmac.new(secret, body, hashlib.sha512).hexdigest()
    return hmac.compare_digest(expected, signature)


def extract_phone_from_webhook(payload: dict) -> str | None:
    """
    Pull the customer phone number out of a Paystack charge.success event.
    We stored it in metadata.phone when creating the transaction.
    """
    try:
        return payload["data"]["metadata"]["phone"]
    except (KeyError, TypeError):
        return None


def extract_tier_from_webhook(payload: dict) -> str | None:
    """
    Pull the purchased tier out of a Paystack charge.success event.
    Falls back to 'pro' for legacy transactions without metadata.tier.
    """
    try:
        tier = payload["data"]["metadata"]["tier"]
        if tier in (TIER_PRO, TIER_PREMIUM):
            return tier
    except (KeyError, TypeError):
        pass

    try:
        product = payload["data"]["metadata"]["product"]
        if product == "expense_diary_premium":
            return TIER_PREMIUM
        if product == "expense_diary_pro":
            return TIER_PRO
    except (KeyError, TypeError):
        pass

    return TIER_PRO
