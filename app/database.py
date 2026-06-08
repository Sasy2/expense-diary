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
    get_phone_lookup_id,
    safe_log_phone,
)
from app.models import ExpenseEntry

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
        return result.data[0]["id"], False

    insert_result = await asyncio.to_thread(
        lambda: sb.table("expense_users")
            .insert({"phone_id": phone_id})
            .execute()
    )
    user_id = insert_result.data[0]["id"]
    logger.info("New user created", phone=safe_log_phone(phone))
    return user_id, True


async def get_user_record(phone: str) -> Optional[dict]:
    """Return the full user row or None if not found."""
    phone_id = get_phone_lookup_id(phone)
    sb = get_supabase()
    result = await asyncio.to_thread(
        lambda: sb.table("expense_users")
            .select("id, tier, entry_count")
            .eq("phone_id", phone_id)
            .execute()
    )
    return result.data[0] if result.data else None


async def set_user_tier(phone: str, tier: str) -> None:
    """Set a user's subscription tier after successful Paystack payment."""
    phone_id = get_phone_lookup_id(phone)
    sb = get_supabase()
    await asyncio.to_thread(
        lambda: sb.table("expense_users")
            .update({"tier": tier})
            .eq("phone_id", phone_id)
            .execute()
    )
    logger.info("User tier updated", phone=safe_log_phone(phone), tier=tier)


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
    """Return Pro and Premium users for monthly summary dispatch."""
    sb = get_supabase()
    result = await asyncio.to_thread(
        lambda: sb.table("expense_users")
            .select("id, phone_id, tier")
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
) -> None:
    """
    Encrypt all financial fields and insert into Supabase.
    Only month_year (YYYY-MM) is stored plaintext for filtering.
    """
    month_year = datetime.now(timezone.utc).strftime("%Y-%m")
    payload = {
        "amount":       round(entry.amount, 2),
        "currency":     entry.currency.upper(),
        "category":     entry.category,
        "merchant":     entry.merchant,
        "description":  entry.description,
        "entry_type":   entry.entry_type,
        "input_method": input_method,
        "timestamp":    datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC"),
    }
    encrypted = encrypt_for_user(payload, phone)
    sb = get_supabase()
    await asyncio.to_thread(
        lambda: sb.table("expenses").insert({
            "user_id":           user_id,
            "encrypted_payload": encrypted,
            "month_year":        month_year,
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
