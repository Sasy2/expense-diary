"""
database.py — Supabase data access layer.

All reads/writes go through the service role key.
Phone numbers are never stored — only SHA-256 hashes.
All financial data is encrypted before insert and decrypted after fetch.
"""

import asyncio
import os
from datetime import datetime, timezone
from typing import Optional

from cryptography.fernet import InvalidToken
from supabase import Client, create_client
from structlog import get_logger

from app.security import (
    decrypt_for_user,
    encrypt_for_user,
    encrypt_stored_phone,
    get_phone_lookup_id,
    safe_log_phone,
)
from app.models import ExpenseEntry, TIER_FREE, TIER_PRO, new_user_trial_ends_at

logger = get_logger()

_supabase: Optional[Client] = None


def init_supabase() -> None:
    """
    Initialise the Supabase client once at startup (called from lifespan).
    Using a module-level singleton avoids per-request connection overhead.
    """
    global _supabase
    _supabase = create_client(
        os.environ["SUPABASE_URL"],
        os.environ["SUPABASE_SERVICE_KEY"],
    )
    logger.info("Supabase client initialised")


def get_supabase() -> Client:
    if _supabase is None:
        raise RuntimeError("Supabase not initialised — call init_supabase() first")
    return _supabase


# ── Users ────────────────────────────────────────────────────────────────────

async def get_or_create_user(phone: str) -> tuple[str, bool]:
    """
    Return (user_id, is_new).
    Creates the user record if this is their first message.
    The phone is stored only as a one-way hash.
    """
    phone_id = get_phone_lookup_id(phone)
    sb = get_supabase()

    result = await asyncio.to_thread(
        lambda: sb.table("expense_users")
            .select("id")
            .eq("phone_id", phone_id)
            .execute()
    )
    if result.data:
        user_id = result.data[0]["id"]
        await ensure_notify_phone(phone, user_id)
        return user_id, False

    insert_result = await asyncio.to_thread(
        lambda: sb.table("expense_users")
            .insert({
                "phone_id":             phone_id,
                "tier":                 TIER_PRO,
                "trial_ends_at":        new_user_trial_ends_at(),
                "is_paid":              False,
                "trial_reminders_sent": 0,
                "notify_phone_enc":     encrypt_stored_phone(phone),
            })
            .execute()
    )
    user_id = insert_result.data[0]["id"]
    logger.info("New user created", phone=safe_log_phone(phone))
    return user_id, True


async def ensure_notify_phone(phone: str, user_id: str) -> None:
    """Backfill encrypted phone for monthly recaps (existing users on next message)."""
    enc = encrypt_stored_phone(phone)
    sb = get_supabase()
    await asyncio.to_thread(
        lambda: sb.table("expense_users")
            .update({"notify_phone_enc": enc})
            .eq("id", user_id)
            .is_("notify_phone_enc", "null")
            .execute()
    )


async def get_user_record(phone: str) -> Optional[dict]:
    """Return the full user row or None if not found."""
    phone_id = get_phone_lookup_id(phone)
    sb = get_supabase()
    result = await asyncio.to_thread(
        lambda: sb.table("expense_users")
            .select(
                "id, tier, entry_count, trial_ends_at, is_paid, trial_reminders_sent"
            )
            .eq("phone_id", phone_id)
            .execute()
    )
    return result.data[0] if result.data else None


async def set_user_tier(phone: str, tier: str) -> bool:
    """
    Set a user's subscription tier after successful Paystack payment.
    Resets entry_count when the tier actually changes so paid users can log again.
    Returns True if the tier changed.
    """
    record = await get_user_record(phone)
    old_tier = (record.get("tier") if record else None) or "free"
    phone_id = get_phone_lookup_id(phone)
    sb = get_supabase()

    updates: dict = {
        "tier":                 tier,
        "is_paid":              True,
        "trial_ends_at":        None,
        "trial_reminders_sent": 0,
    }
    if old_tier != tier:
        updates["entry_count"] = 0

    await asyncio.to_thread(
        lambda: sb.table("expense_users")
            .update(updates)
            .eq("phone_id", phone_id)
            .execute()
    )
    logger.info(
        "User tier updated",
        phone=safe_log_phone(phone),
        tier=tier,
        reset_entries=old_tier != tier,
    )
    return old_tier != tier


async def expire_trial_for_phone(phone: str) -> bool:
    """Downgrade expired trial to free. Returns True if downgraded."""
    record = await get_user_record(phone)
    if not record or record.get("is_paid") or record.get("tier") != TIER_PRO:
        return False

    trial_ends = record.get("trial_ends_at")
    if not trial_ends:
        return False

    ends = datetime.fromisoformat(trial_ends.replace("Z", "+00:00"))
    if ends.tzinfo is None:
        ends = ends.replace(tzinfo=timezone.utc)
    if ends > datetime.now(timezone.utc):
        return False

    phone_id = get_phone_lookup_id(phone)
    sb = get_supabase()
    await asyncio.to_thread(
        lambda: sb.table("expense_users")
            .update({"tier": TIER_FREE, "trial_ends_at": None})
            .eq("phone_id", phone_id)
            .execute()
    )
    logger.info("Trial expired — downgraded to free", phone=safe_log_phone(phone))
    return True


async def expire_trials_bulk() -> int:
    """Downgrade all users whose Pro trial has ended (cron safety net)."""
    sb = get_supabase()
    now = datetime.now(timezone.utc).isoformat()

    def _fetch_and_update():
        rows = (
            sb.table("expense_users")
              .select("id")
              .eq("tier", TIER_PRO)
              .eq("is_paid", False)
              .lt("trial_ends_at", now)
              .execute()
              .data
            or []
        )
        if not rows:
            return 0
        ids = [r["id"] for r in rows]
        sb.table("expense_users").update({
            "tier": TIER_FREE,
            "trial_ends_at": None,
        }).in_("id", ids).execute()
        return len(ids)

    return await asyncio.to_thread(_fetch_and_update)


async def update_trial_reminder_stage(phone: str, stage: int) -> None:
    phone_id = get_phone_lookup_id(phone)
    sb = get_supabase()
    await asyncio.to_thread(
        lambda: sb.table("expense_users")
            .update({"trial_reminders_sent": stage})
            .eq("phone_id", phone_id)
            .execute()
    )


async def decrement_entry_count(user_id: str) -> int:
    """Decrement entry_count (floor at 0). Used when a transaction is undone."""
    sb = get_supabase()
    result = await asyncio.to_thread(
        lambda: sb.rpc("decrement_entry_count", {"user_uuid": user_id}).execute()
    )
    return result.data if isinstance(result.data, int) else 0


async def delete_last_expense(phone: str, user_id: str) -> Optional[list[dict]]:
    """Delete the most recent transaction (or all transactions in the last batch) and return the decrypted data list."""
    rows = await get_user_expenses(phone, limit=1, order_desc=True)
    if not rows:
        return None

    last_entry = rows[0]
    batch_id = last_entry.get("batch_id")

    if batch_id:
        # Fetch recent entries to find all that share this batch_id
        # Limit 50 should be more than enough for any single batch
        recent_rows = await get_user_expenses(phone, limit=50, order_desc=True)
        to_delete = [r for r in recent_rows if r.get("batch_id") == batch_id]
    else:
        to_delete = [last_entry]

    expense_ids = [r["id"] for r in to_delete]
    sb = get_supabase()
    await asyncio.to_thread(
        lambda: sb.table("expenses")
            .delete()
            .in_("id", expense_ids)
            .eq("user_id", user_id)
            .execute()
    )
    logger.info("Transactions deleted (undo)", phone=safe_log_phone(phone), count=len(expense_ids))
    return to_delete


async def increment_entry_count(user_id: str) -> int:
    """
    Increment entry_count and return the new value.
    Uses a raw RPC call to avoid race conditions on concurrent increments.
    """
    sb = get_supabase()
    result = await asyncio.to_thread(
        lambda: sb.rpc(
            "increment_entry_count",
            {"user_uuid": user_id}
        ).execute()
    )
    # RPC returns the new count
    return result.data if isinstance(result.data, int) else 0


async def reset_all_entry_counts() -> int:
    """
    Reset entry_count=0 for every user.
    Called by the monthly cron job on the 1st of each month.
    Returns number of rows updated.
    """
    sb = get_supabase()
    result = await asyncio.to_thread(
        lambda: sb.table("expense_users")
            .update({"entry_count": 0})
            .neq("id", "00000000-0000-0000-0000-000000000000")  # update all rows
            .execute()
    )
    count = len(result.data) if result.data else 0
    logger.info("Entry counts reset", user_count=count)
    return count


async def get_users_for_monthly_summary() -> list[dict]:
    """Return Pro and Premium users eligible for monthly recap dispatch."""
    sb = get_supabase()
    result = await asyncio.to_thread(
        lambda: sb.table("expense_users")
            .select("id, phone_id, tier, notify_phone_enc")
            .in_("tier", ["pro", "premium"])
            .execute()
    )
    return result.data or []


# ── Expenses ─────────────────────────────────────────────────────────────────

async def save_expense(
    phone: str,
    user_id: str,
    entry: ExpenseEntry,
    input_method: str,
    batch_id: Optional[str] = None,
    offset_seconds: int = 0,
) -> None:
    """
    Encrypt all financial fields and insert into Supabase.
    Only month_year (YYYY-MM) is stored plaintext for filtering.
    """
    # Parse the resolved timestamp from the entry, fallback to current time on error
    try:
        iso_str = entry.timestamp.replace("Z", "+00:00")
        entry_dt = datetime.fromisoformat(iso_str)
        if entry_dt.tzinfo is None:
            entry_dt = entry_dt.replace(tzinfo=timezone.utc)
        entry_dt = entry_dt.astimezone(timezone.utc)
    except Exception:
        entry_dt = datetime.now(timezone.utc)

    if offset_seconds:
        from datetime import timedelta
        entry_dt += timedelta(seconds=offset_seconds)

    month_year = entry_dt.strftime("%Y-%m")
    payload = {
        "amount":       round(entry.amount, 2),
        "currency":     entry.currency.upper(),
        "category":     entry.category,
        "merchant":     entry.merchant,
        "description":  entry.description,
        "entry_type":   entry.entry_type,
        "input_method": input_method,
        "timestamp":    entry_dt.strftime("%Y-%m-%d %H:%M UTC"),
        "client_tag":   getattr(entry, "client_tag", None),
        "classification": getattr(entry, "classification", "personal"),
    }
    if batch_id:
        payload["batch_id"] = batch_id
    encrypted = encrypt_for_user(payload, phone)
    sb = get_supabase()
    await asyncio.to_thread(
        lambda: sb.table("expenses").insert({
            "user_id":           user_id,
            "encrypted_payload": encrypted,
            "month_year":        month_year,
            "logged_at":         entry_dt.isoformat(),
        }).execute()
    )
    logger.info(
        "Expense saved",
        phone=safe_log_phone(phone),
        category=entry.category,
        entry_type=entry.entry_type,
    )


async def get_user_expenses(
    phone: str,
    month_year: Optional[str] = None,
    limit: Optional[int] = None,
    order_desc: bool = False,
) -> list[dict]:
    """
    Fetch and decrypt expenses for a user.
    Silently skips any rows that fail decryption (e.g. key mismatch).
    """
    phone_id = get_phone_lookup_id(phone)
    sb = get_supabase()

    # First resolve the user_id from phone_id
    user_result = await asyncio.to_thread(
        lambda: sb.table("expense_users")
            .select("id")
            .eq("phone_id", phone_id)
            .execute()
    )
    if not user_result.data:
        return []
    user_id = user_result.data[0]["id"]

    def _query():
        q = (
            sb.table("expenses")
              .select("id, encrypted_payload, logged_at, month_year")
              .eq("user_id", user_id)
              .order("logged_at", desc=order_desc)
        )
        if month_year:
            q = q.eq("month_year", month_year)
        if limit:
            q = q.limit(limit)
        return q.execute().data or []

    rows = await asyncio.to_thread(_query)
    results: list[dict] = []
    for row in rows:
        try:
            data = decrypt_for_user(row["encrypted_payload"], phone)
            data["id"] = row["id"]
            data["logged_at"] = row["logged_at"]
            results.append(data)
        except (InvalidToken, Exception) as exc:
            logger.warning("Decrypt failed", row_id=row["id"], error=str(exc))
    return results


async def get_category_budget(user_id: str, category: str, month_year: str) -> Optional[dict]:
    """Retrieve budget limit for a category and month."""
    sb = get_supabase()
    result = await asyncio.to_thread(
        lambda: sb.table("expense_budgets")
            .select("id, limit_amount")
            .eq("user_id", user_id)
            .eq("category", category)
            .eq("month_year", month_year)
            .execute()
    )
    return result.data[0] if result.data else None


async def get_savings_goals(user_id: str) -> list[dict]:
    """Retrieve active savings goals for a user."""
    sb = get_supabase()
    result = await asyncio.to_thread(
        lambda: sb.table("savings_goals")
            .select("id, name, target_amount, current_amount, target_date")
            .eq("user_id", user_id)
            .execute()
    )
    return result.data or []


async def register_message_id(message_id: str) -> bool:
    """
    Attempt to insert the message_id into processed_messages.
    Returns True if it was successfully inserted (new message),
    False if it already existed (duplicate).
    """
    if not message_id:
        return True
    sb = get_supabase()
    try:
        await asyncio.to_thread(
            lambda: sb.table("processed_messages")
                .insert({"message_id": message_id})
                .execute()
        )
        return True
    except Exception as exc:
        logger.info("Duplicate WhatsApp message detected and ignored", message_id=message_id)
        return False


