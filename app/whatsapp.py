"""
whatsapp.py — Meta WhatsApp Cloud API integration.

  send_wa_text()              → Send a text message via Graph API
  verify_webhook_challenge()  → Meta webhook verification (GET)
  verify_meta_signature()     → Optional X-Hub-Signature-256 check (POST)
"""

import hashlib
import hmac
import os

import httpx
from structlog import get_logger

from app.security import safe_log_phone

logger = get_logger()

GRAPH_API_VERSION = os.environ.get("WHATSAPP_API_VERSION", "v21.0")


def normalize_wa_phone(phone: str) -> str:
    return phone.lstrip("+").replace(" ", "")


def verify_webhook_challenge(mode: str | None, token: str | None, challenge: str | None) -> str | None:
    """
    Meta sends GET with hub.mode=subscribe, hub.verify_token, hub.challenge.
    Return the challenge string if the verify token matches.
    """
    expected = os.environ.get("WHATSAPP_VERIFY_TOKEN", "")
    if mode == "subscribe" and token == expected and challenge:
        return challenge
    return None


def verify_meta_signature(body: bytes, signature: str) -> bool:
    """Verify X-Hub-Signature-256 from Meta webhook POSTs."""
    secret = os.environ.get("WHATSAPP_APP_SECRET", "")
    if not secret:
        return True

    if not signature or not signature.startswith("sha256="):
        return False

    expected = hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()
    return hmac.compare_digest(expected, signature[7:])


async def send_wa_text(phone: str, text: str) -> None:
    """Send a free-form WhatsApp text message via the Meta Cloud API."""
    phone_number_id = os.environ["WHATSAPP_PHONE_NUMBER_ID"]
    token = os.environ["WHATSAPP_ACCESS_TOKEN"]
    url = f"https://graph.facebook.com/{GRAPH_API_VERSION}/{phone_number_id}/messages"

    payload = {
        "messaging_product": "whatsapp",
        "to":                normalize_wa_phone(phone),
        "type":              "text",
        "text":              {"body": text},
    }

    async with httpx.AsyncClient(timeout=15.0) as client:
        resp = await client.post(
            url,
            headers={
                "Authorization": f"Bearer {token}",
                "Content-Type":  "application/json",
            },
            json=payload,
        )
        if not resp.is_success:
            logger.error(
                "WhatsApp send failed",
                status=resp.status_code,
                body=resp.text,
                phone=safe_log_phone(phone),
            )
        resp.raise_for_status()

    logger.info("WhatsApp message sent", phone=safe_log_phone(phone))
