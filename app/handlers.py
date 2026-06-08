"""
handlers.py — Command routing and core expense processing logic.

detect_command()         → Normalise text and return command name or None
handle_command()         → Execute HELP / TOTAL / REPORT / LAST5 / UPGRADE
process_expense_message() → Parse → check limits → encrypt → save → confirm
"""

import re
from datetime import datetime, timezone
from typing import Optional

from structlog import get_logger

from app.database import (
    get_user_expenses,
    get_user_record,
    increment_entry_count,
    save_expense,
)
from app.messaging import (
    build_confirmation,
    build_help,
    build_last_n,
    build_limit_reached_message,
    build_premium_upgrade_message,
    build_report_paywall,
    build_total_summary,
    build_upgrade_menu,
    build_upgrade_nudge,
    generate_and_upload_csv,
    send_wa_text,
)
from app.models import (
    TIER_FREE,
    TIER_PREMIUM,
    TIER_PRO,
    can_export_csv,
    get_entry_limit,
    nudge_threshold,
)
from app.parser import parse_expense
from app.payments import create_payment_link

logger = get_logger()


def _user_tier(record: dict | None) -> str:
    if not record:
        return TIER_FREE
    return record.get("tier") or TIER_FREE


def detect_command(text: str) -> Optional[str]:
    """
    Normalise text and return a command name if recognised, else None.
    Handles spacing and case variants: "last 5", "LAST5", "Last  5" all work.
    """
    normalised = re.sub(r"\s+", "", text.strip().upper())
    return {
        "HELP":    "HELP",
        "TOTAL":   "TOTAL",
        "REPORT":  "REPORT",
        "LAST5":   "LAST5",
        "UPGRADE": "UPGRADE",
        "PRO":     "PRO",
        "PREMIUM": "PREMIUM",
    }.get(normalised)


async def _send_upgrade_options(phone: str, tier: str) -> None:
    """Send tier-appropriate upgrade links."""
    if tier == TIER_PREMIUM:
        await send_wa_text(phone, "\U0001f38a You're on Premium! Keep logging.")
        return

    try:
        if tier == TIER_FREE:
            pro_url = await create_payment_link(phone, TIER_PRO)
            premium_url = await create_payment_link(phone, TIER_PREMIUM)
            await send_wa_text(phone, build_upgrade_menu(pro_url, premium_url))
        else:
            premium_url = await create_payment_link(phone, TIER_PREMIUM)
            await send_wa_text(phone, build_premium_upgrade_message(premium_url))
    except Exception as exc:
        logger.error("Payment link error", error=str(exc))
        await send_wa_text(
            phone,
            "\u274c Couldn't generate a payment link right now. Please try again shortly."
        )


async def _send_limit_reached(phone: str, tier: str) -> None:
    """Notify the user they've hit their monthly entry cap."""
    try:
        if tier == TIER_FREE:
            pro_url = await create_payment_link(phone, TIER_PRO)
            premium_url = await create_payment_link(phone, TIER_PREMIUM)
            await send_wa_text(phone, build_limit_reached_message(tier, pro_url, premium_url))
        elif tier == TIER_PRO:
            premium_url = await create_payment_link(phone, TIER_PREMIUM)
            await send_wa_text(phone, build_limit_reached_message(tier, premium_url=premium_url))
        else:
            await send_wa_text(phone, build_limit_reached_message(tier))
    except Exception as exc:
        logger.error("Payment link error", error=str(exc))
        limit = get_entry_limit(tier)
        await send_wa_text(
            phone,
            f"\U0001f512 You've used all {limit} entries for this month.\n\n"
            "Reply *UPGRADE* to see paid plans."
        )


async def handle_command(phone: str, command: str) -> None:
    """Dispatch a recognised command and send the reply via WhatsApp."""
    record = await get_user_record(phone)
    tier = _user_tier(record)

    if command == "HELP":
        await send_wa_text(phone, build_help())
        return

    if command in ("UPGRADE", "PRO", "PREMIUM"):
        if command == "PRO":
            if tier == TIER_PREMIUM:
                await send_wa_text(phone, "\U0001f38a You're on Premium \u2014 our top plan!")
                return
            if tier == TIER_PRO:
                await send_wa_text(phone, "\U0001f38a You're already on Pro! Reply *PREMIUM* to upgrade.")
                return
            try:
                url = await create_payment_link(phone, TIER_PRO)
                await send_wa_text(
                    phone,
                    f"\U0001f4b3 *Pro \u2014 GHS 25/month*\n"
                    f"  \u2714 {get_entry_limit(TIER_PRO)} entries/month\n"
                    "  \u2714 Monthly auto-summary\n\n"
                    f"\U0001f449 {url}\n\n"
                    "(Link expires in 24 hours)"
                )
            except Exception as exc:
                logger.error("Payment link error", error=str(exc))
                await send_wa_text(
                    phone,
                    "\u274c Couldn't generate a payment link right now. Please try again shortly."
                )
            return

        if command == "PREMIUM":
            if tier == TIER_PREMIUM:
                await send_wa_text(phone, "\U0001f38a You're on Premium! Keep logging.")
                return
            try:
                url = await create_payment_link(phone, TIER_PREMIUM)
                await send_wa_text(phone, build_premium_upgrade_message(url))
            except Exception as exc:
                logger.error("Payment link error", error=str(exc))
                await send_wa_text(
                    phone,
                    "\u274c Couldn't generate a payment link right now. Please try again shortly."
                )
            return

        await _send_upgrade_options(phone, tier)
        return

    if command == "TOTAL":
        month_year = datetime.now(timezone.utc).strftime("%Y-%m")
        rows = await get_user_expenses(phone, month_year=month_year)
        await send_wa_text(phone, build_total_summary(rows))
        return

    if command == "LAST5":
        rows = await get_user_expenses(phone, limit=5, order_desc=True)
        await send_wa_text(phone, build_last_n(rows, 5))
        return

    if command == "REPORT":
        if not can_export_csv(tier):
            try:
                url = await create_payment_link(phone, TIER_PREMIUM)
                await send_wa_text(phone, build_report_paywall(url))
            except Exception as exc:
                logger.error("Payment link error", error=str(exc))
                await send_wa_text(
                    phone,
                    "\U0001f4c1 CSV export is a Premium feature (GHS 99/month). Reply UPGRADE to unlock."
                )
            return

        rows = await get_user_expenses(phone)
        if not rows:
            await send_wa_text(
                phone,
                "\U0001f4c1 No expenses yet. Start logging to generate a report!"
            )
            return
        if not record:
            await send_wa_text(phone, "\u274c Could not find your account. Try again.")
            return
        url = await generate_and_upload_csv(rows, record["id"])
        month_name = datetime.now(timezone.utc).strftime("%B %Y")
        await send_wa_text(
            phone,
            f"\U0001f4c1 *{month_name} CSV Report*\n\n"
            f"Your expenses are ready:\n{url}\n\n"
            "(Link valid for 24 hours)"
        )


async def process_expense_message(
    phone: str,
    user_id: str,
    text: str,
    input_method: str,
) -> None:
    """
    Full expense pipeline:
      1. Check entry limit for the user's tier
      2. Parse with GPT-4.1-mini
      3. Encrypt and save to Supabase
      4. Increment entry counter
      5. Send confirmation (+ upgrade nudge if approaching limit)
    """
    record = await get_user_record(phone)
    tier = _user_tier(record)
    entry_count = record.get("entry_count", 0) if record else 0
    limit = get_entry_limit(tier)

    if entry_count >= limit:
        logger.info("Entry limit reached", user_id=user_id[:8], tier=tier)
        await _send_limit_reached(phone, tier)
        return

    try:
        entry = await parse_expense(text)
    except Exception as exc:
        logger.error("Parse failed", error=str(exc))
        await send_wa_text(
            phone,
            "\u274c Couldn't understand that expense. Try something like:\n"
            "  \u2022 '45 GHS lunch'\n"
            "  \u2022 'Uber ride 35 cedis'\n"
            "  \u2022 'Client paid 2000 GHS'"
        )
        return

    await save_expense(phone, user_id, entry, input_method)
    new_count = await increment_entry_count(user_id)

    confirmation = build_confirmation(entry)
    if new_count >= nudge_threshold(tier) and new_count < limit:
        confirmation += build_upgrade_nudge(new_count, tier)

    await send_wa_text(phone, confirmation)
