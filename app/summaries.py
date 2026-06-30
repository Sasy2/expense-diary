"""
summaries.py — Monthly recap dispatch (1st of each month, previous month).
"""

from datetime import datetime, timedelta, timezone

from structlog import get_logger

from app.database import get_user_expenses, get_users_for_monthly_summary, get_pullback_candidates
from app.messaging import build_monthly_recap, send_wa_text, build_weekly_recap
from app.security import decrypt_stored_phone, safe_log_phone

logger = get_logger()


def previous_month_labels() -> tuple[str, str]:
    """Return (month_year 'YYYY-MM', display label 'Month YYYY') for last calendar month."""
    today = datetime.now(timezone.utc).date()
    last_day_prev = today.replace(day=1) - timedelta(days=1)
    return last_day_prev.strftime("%Y-%m"), last_day_prev.strftime("%B %Y")


async def send_monthly_recaps() -> tuple[int, int, str]:
    """
    Send last month's recap to Pro and Premium users via WhatsApp.
    Returns (sent_count, eligible_count, recap_month_year).
    """
    month_year, month_label = previous_month_labels()
    users = await get_users_for_monthly_summary()
    sent = 0

    for user in users:
        phone = decrypt_stored_phone(user.get("notify_phone_enc") or "")
        if not phone:
            logger.info(
                "Skipping recap — no notify phone on file",
                user_id=str(user.get("id", ""))[:8],
            )
            continue

        try:
            rows = await get_user_expenses(phone, month_year=month_year)
            if not rows:
                continue

            await send_wa_text(phone, build_monthly_recap(rows, month_label))
            sent += 1
            logger.info("Monthly recap sent", phone=safe_log_phone(phone), month=month_year)
        except Exception as exc:
            logger.error(
                "Monthly recap failed",
                phone=safe_log_phone(phone),
                error=str(exc),
            )

    return sent, len(users), month_year


async def send_weekly_recaps() -> tuple[int, int]:
    """
    Send last 7 days' recap to Pro and Premium users via WhatsApp.
    Returns (sent_count, eligible_count).
    """
    users = await get_users_for_monthly_summary()
    sent = 0
    seven_days_ago = datetime.now(timezone.utc) - timedelta(days=7)

    for user in users:
        phone = decrypt_stored_phone(user.get("notify_phone_enc") or "")
        if not phone:
            logger.info(
                "Skipping weekly recap — no notify phone on file",
                user_id=str(user.get("id", ""))[:8],
            )
            continue

        try:
            all_rows = await get_user_expenses(phone)
            # Filter for last 7 days
            rows = []
            for r in all_rows:
                try:
                    dt = datetime.fromisoformat(r["logged_at"].replace("Z", "+00:00"))
                    if dt.tzinfo is None:
                        dt = dt.replace(tzinfo=timezone.utc)
                    if dt >= seven_days_ago:
                        rows.append(r)
                except Exception:
                    pass

            if not rows:
                continue

            await send_wa_text(phone, build_weekly_recap(rows))
            sent += 1
            logger.info("Weekly recap sent", phone=safe_log_phone(phone))
        except Exception as exc:
            logger.error(
                "Weekly recap failed",
                phone=safe_log_phone(phone),
                error=str(exc),
            )

    return sent, len(users)


async def send_pullback_checkins() -> tuple[int, int]:
    """
    Send a single friendly check-in message to users who:
      - joined between 24 and 48 hours ago
      - have never logged a transaction (entry_count = 0)

    The 24-hour sliding window guarantees each user is contacted exactly once
    across hourly cron runs — no extra 'sent' flag required.

    Returns (sent_count, candidates_count).
    """
    candidates = await get_pullback_candidates()
    sent = 0

    MESSAGE = (
        "Hey! Just checking in — have you tried logging anything yet? 😊\n\n"
        "Just type something like:\n"
        "  • *20 GHS lunch*\n"
        "  • *Uber 35 GHS*\n"
        "  • *Client paid 500 GHS*\n\n"
        "Give it a go and see what happens!"
    )

    for user in candidates:
        phone = decrypt_stored_phone(user.get("notify_phone_enc") or "")
        if not phone:
            continue
        try:
            await send_wa_text(phone, MESSAGE)
            sent += 1
            logger.info("Pullback check-in sent", phone=safe_log_phone(phone))
        except Exception as exc:
            logger.error(
                "Pullback check-in failed",
                phone=safe_log_phone(phone),
                error=str(exc),
            )

    return sent, len(candidates)
