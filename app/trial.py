"""
trial.py — 30-day Pro trial lifecycle, expiry, and reminder prompts.
"""

from datetime import datetime, timezone

from structlog import get_logger

from app.database import (
    expire_trial_for_phone,
    expire_trials_bulk,
    get_user_record,
    update_trial_reminder_stage,
)
from app.messaging import (
    build_trial_ended_message,
    build_trial_reminder_message,
    send_wa_text,
)
from app.models import TIER_PRO

logger = get_logger()

# Days remaining thresholds (trial_reminders_sent stage after send)
_REMINDER_STAGES = (
    (15, 1),  # mid-month: 15 days or fewer left (first half done)
    (7, 2),   # last week
    (2, 3),   # last 2 days
)


def _parse_utc(value: str) -> datetime | None:
    if not value:
        return None
    try:
        text = value.replace("Z", "+00:00")
        dt = datetime.fromisoformat(text)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt.astimezone(timezone.utc)
    except (ValueError, TypeError):
        return None


def trial_days_remaining(record: dict) -> int | None:
    ends = _parse_utc(record.get("trial_ends_at"))
    if not ends:
        return None
    delta = ends - datetime.now(timezone.utc)
    return max(delta.days, 0)


async def handle_trial_lifecycle(phone: str) -> None:
    """
    On each inbound message: expire ended trials, send due reminders.
    """
    downgraded = await expire_trial_for_phone(phone)
    if downgraded:
        await send_wa_text(phone, build_trial_ended_message())

    record = await get_user_record(phone)
    if not record:
        return

    if record.get("is_paid") or record.get("tier") != TIER_PRO:
        return

    ends = _parse_utc(record.get("trial_ends_at"))
    if not ends or ends <= datetime.now(timezone.utc):
        return

    days_left = trial_days_remaining(record)
    if days_left is None:
        return

    sent = int(record.get("trial_reminders_sent") or 0)
    for threshold, stage in _REMINDER_STAGES:
        if days_left <= threshold and sent < stage:
            await send_wa_text(phone, build_trial_reminder_message(days_left, ends))
            await update_trial_reminder_stage(phone, stage)
            logger.info("Trial reminder sent", stage=stage, days_left=days_left)
            break


async def run_trial_expiry_cron() -> int:
    """Downgrade all expired trials (for users who have not messaged recently)."""
    count = await expire_trials_bulk()
    if count:
        logger.info("Expired trials downgraded via cron", count=count)
    return count
