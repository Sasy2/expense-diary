"""
summaries.py — Monthly recap dispatch (1st of each month, previous month).
"""

from datetime import datetime, timedelta, timezone

from structlog import get_logger

from app.database import get_user_expenses, get_users_for_monthly_summary
from app.messaging import build_monthly_recap, send_wa_text
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
