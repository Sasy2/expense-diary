"""
messaging.py — WhatsApp send helpers and report formatters.

All outbound messages go through send_wa_text().
Report builders operate on already-decrypted expense dicts.
"""

import csv
import io
from datetime import datetime, timezone

from structlog import get_logger

from app.storage import upload_csv_report
from app.whatsapp import send_wa_text  # noqa: F401 — re-exported for callers

from app.models import (
    BRAND_NAME,
    TIER_FREE,
    TIER_PREMIUM,
    TIER_PRO,
    TIER_PRICES_GHS,
    TRIAL_DAYS,
    get_entry_limit,
)
logger = get_logger()

# Emoji prefixes this bot sends — skip inbound messages starting with these
# to prevent the bot replying to its own confirmations (echo loop).
BOT_PREFIXES = (
    "\u2705",  # ✅
    "\u274c",  # ❌
    "\U0001f4ca",  # 📊
    "\U0001f4b0",  # 💰
    "\U0001f4b8",  # 💸
    "\U0001f4c1",  # 📁
    "\u26a0",   # ⚠
    "\U0001f9fe",  # 🧾
    "\U0001f4c8",  # 📈
    "\U0001f399",  # 🎙
    "\U0001f4f8",  # 📸
    "\U0001f4a1",  # 💡
    "\U0001f38a",  # 🎊
)


def _format_trial_end(trial_ends_at: str) -> str:
    try:
        dt = datetime.fromisoformat(trial_ends_at.replace("Z", "+00:00"))
        return dt.strftime("%d %B %Y")
    except (ValueError, TypeError):
        return "soon"


def _income_source_label(row: dict) -> str:
    merchant = str(row.get("merchant", "")).strip()
    if merchant:
        return merchant
    desc = str(row.get("description", "")).strip()
    if desc:
        return desc
    return str(row.get("category", "Other"))


def _expense_lines(by_category: dict[str, float], total: float) -> list[str]:
    if not by_category:
        return ["  (none)"]
    lines = []
    for cat, amt in sorted(by_category.items(), key=lambda x: -x[1]):
        pct = (amt / total * 100) if total else 0
        lines.append(f"  \u2022 {cat}: GHS {amt:,.2f} ({pct:.0f}%)")
    return lines


def _income_lines(by_source: dict[str, float], total: float) -> list[str]:
    if not by_source:
        return ["  (none)"]
    lines = []
    for source, amt in sorted(by_source.items(), key=lambda x: -x[1]):
        pct = (amt / total * 100) if total else 0
        lines.append(f"  \u2022 {source}: GHS {amt:,.2f} ({pct:.0f}%)")
    return lines


# ── Report formatters ─────────────────────────────────────────────────────────

def build_greeting_reply() -> str:
    return (
        f"Hey! \U0001f44b *{BRAND_NAME}* — Keep KountN your expenses.\n\n"
        "Send any transaction in plain language, e.g.:\n"
        "  \u2022 '45 GHS lunch at Papaye'\n"
        "  \u2022 'Uber 35 GHS'\n\n"
        "Type *HELP* for all commands."
    )


def build_not_an_expense_hint() -> str:
    return (
        "I didn't spot an amount in that message.\n\n"
        "To KountN a transaction, include a number, e.g.:\n"
        "  \u2022 '45 GHS lunch'\n"
        "  \u2022 'Uber ride 35 cedis'\n\n"
        "Type *HELP* for commands."
    )


def build_welcome(trial_ends_at: str) -> str:
    end_date = _format_trial_end(trial_ends_at)
    return (
        f"Welcome to *{BRAND_NAME}*! \U0001f9fe\n\n"
        f"\U0001f38a You're on a *free Pro trial* for {TRIAL_DAYS} days "
        f"(until {end_date}).\n"
        f"  \u2714 {get_entry_limit(TIER_PRO)} transactions/month\n"
        "  \u2714 Monthly summaries (TOTAL)\n"
        "  \u2714 LAST 5, HELP & more\n\n"
        "Keep KountN your expenses — just type naturally:\n"
        "  \u2022 '45 GHS lunch at Papaye'\n"
        "  \u2022 'Uber 35 GHS'\n"
        "  \u2022 'Client paid 2000 GHS'\n\n"
        "\U0001f4f8 *Photos:* Send with a caption describing the amount.\n\n"
        "*Commands:*\n"
        "  TOTAL \u2014 this month's breakdown\n"
        "  LAST 5 / LAST 10 \u2014 recent entries\n"
        "  UNDO \u2014 remove last transaction\n"
        "  HELP \u2014 full instructions\n"
        "  UPGRADE \u2014 see paid plans after your trial\n\n"
        "After your trial, you'll move to Free unless you upgrade."
    )


def build_help() -> str:
    return (
        f"\U0001f4a1 *{BRAND_NAME} \u2014 Commands*\n\n"
        "*Log a transaction:* Just type it naturally\n"
        "  \u2022 '450 cedis internet data'\n"
        "  \u2022 'Grocery 120 GHS at Shoprite'\n"
        "  \u2022 'Client paid 5000 GHS'\n\n"
        "*Commands:*\n"
        "  TOTAL \u2014 Expenses & Income breakdown\n"
        "  LAST 5 / LAST 10 \u2014 recent transactions (any number up to 50)\n"
        "  UNDO \u2014 remove your most recent transaction\n"
        "  HELP \u2014 this message\n"
        "  UPGRADE \u2014 Pro & Premium plans\n\n"
        "*Made a mistake?* Reply *UNDO* to delete your last entry, then re-send the correct one.\n\n"
        "*Plans:*\n"
        f"  Free \u2014 {get_entry_limit(TIER_FREE)} transactions/month\n"
        f"  Pro (GHS {TIER_PRICES_GHS[TIER_PRO]}/mo) \u2014 "
        f"{get_entry_limit(TIER_PRO)} transactions + monthly recap\n"
        f"  Premium (GHS {TIER_PRICES_GHS[TIER_PREMIUM]}/mo) \u2014 "
        f"{get_entry_limit(TIER_PREMIUM)} transactions + CSV (REPORT)\n\n"
        f"Pro & Premium get an auto recap on the 1st (last month's totals).\n"
        f"New users get {TRIAL_DAYS} days of Pro free."
    )


def build_total_summary(rows: list[dict], month_label: str | None = None) -> str:
    month_name = month_label or datetime.now(timezone.utc).strftime("%B %Y")
    if not rows:
        return (
            f"No transactions logged yet for {month_name}.\n\n"
            f"Send {BRAND_NAME} your first expense or income to get started!"
        )

    by_category: dict[str, float] = {}
    by_income_source: dict[str, float] = {}
    total_expense = 0.0
    total_income = 0.0

    for r in rows:
        try:
            amt = float(r.get("amount", 0))
        except (ValueError, TypeError):
            continue
        if "income" in str(r.get("entry_type", "")).lower():
            source = _income_source_label(r)
            by_income_source[source] = by_income_source.get(source, 0.0) + amt
            total_income += amt
        else:
            cat = str(r.get("category", "Other"))
            by_category[cat] = by_category.get(cat, 0.0) + amt
            total_expense += amt

    net = total_income - total_expense
    lines = [
        f"\U0001f4ca *{BRAND_NAME} \u2014 {month_name}*",
        "",
        f"*Expenses* (GHS {total_expense:,.2f})",
        *_expense_lines(by_category, total_expense),
        "",
        f"*Income* (GHS {total_income:,.2f})",
        *_income_lines(by_income_source, total_income),
        "",
        f"*Net:* GHS {net:,.2f}",
    ]
    return "\n".join(lines)


def build_monthly_recap(rows: list[dict], month_label: str) -> str:
    """First-of-month WhatsApp recap for the previous calendar month."""
    summary = build_total_summary(rows, month_label=month_label)
    return (
        f"\U0001f4ec *Your {month_label} recap from {BRAND_NAME}*\n\n"
        f"{summary}\n\n"
        "Keep KountN this month! Type *TOTAL* anytime for a live view."
    )


def build_last_n(rows: list[dict], n: int = 5) -> str:
    if not rows:
        return f"No transactions yet. Send {BRAND_NAME} your first entry!"
    lines = [f"\U0001f9fe *Last {min(n, len(rows))} Transactions*", ""]
    for r in rows[:n]:
        try:
            amt = float(r.get("amount", 0))
        except (ValueError, TypeError):
            amt = 0.0
        currency  = str(r.get("currency", "GHS"))
        cat       = str(r.get("category", "?"))
        desc      = str(r.get("description", ""))
        etype     = str(r.get("entry_type", "Expense"))
        date_part = str(r.get("timestamp", r.get("logged_at", "")))[:10]
        lines.append(
            f"  \u2022 {etype}: {currency} {amt:,.2f} \u00b7 {cat} \u00b7 "
            f"{desc} ({date_part})"
        )
    return "\n".join(lines)


def build_undo_confirmation(deleted: dict) -> str:
    try:
        amt = float(deleted.get("amount", 0))
    except (ValueError, TypeError):
        amt = 0.0
    currency = str(deleted.get("currency", "GHS"))
    etype = str(deleted.get("entry_type", "Expense"))
    desc = str(deleted.get("description", ""))
    return (
        f"\u2705 *Undone* \u2014 last transaction removed.\n\n"
        f"Removed: {etype} {currency} {amt:,.2f}\n"
        f"{desc}\n\n"
        "Your monthly transaction count was restored.\n"
        "Re-send the correct entry if needed."
    )


def build_confirmation(entry) -> str:
    emoji = "\U0001f4b0" if entry.entry_type == "Income" else "\U0001f4b8"
    merchant_line = f"\n\U0001f3ea {entry.merchant}" if entry.merchant else ""
    return (
        f"\u2705 *{BRAND_NAME}!*\n"
        f"{emoji} {entry.entry_type}: {entry.currency} {entry.amount:,.2f}\n"
        f"\U0001f4c1 {entry.category}"
        f"{merchant_line}\n"
        f"\U0001f4dd {entry.description}\n\n"
        "Type *TOTAL* to see your month so far."
    )


def build_trial_reminder_message(days_left: int, trial_ends_at) -> str:
    raw = (
        trial_ends_at.isoformat()
        if hasattr(trial_ends_at, "isoformat")
        else str(trial_ends_at)
    )
    end_date = _format_trial_end(raw)
    if days_left <= 2:
        urgency = "Your free Pro trial ends in *2 days or less*"
    elif days_left <= 7:
        urgency = "Your free Pro trial ends in *about a week*"
    else:
        urgency = "You're halfway through your free Pro trial"

    return (
        f"\u26a0 {urgency} ({end_date}).\n\n"
        f"After that you'll move to Free ({get_entry_limit(TIER_FREE)} transactions/mo).\n"
        f"Reply *UPGRADE* to keep Pro (GHS {TIER_PRICES_GHS[TIER_PRO]}/mo) "
        f"or Premium (GHS {TIER_PRICES_GHS[TIER_PREMIUM]}/mo)."
    )


def build_trial_ended_message() -> str:
    return (
        f"\U0001f4c5 Your *{BRAND_NAME}* Pro trial has ended.\n\n"
        f"You're now on *Free* ({get_entry_limit(TIER_FREE)} transactions/month).\n\n"
        f"Reply *UPGRADE* for Pro (GHS {TIER_PRICES_GHS[TIER_PRO]}/mo, "
        f"{get_entry_limit(TIER_PRO)} transactions) or Premium "
        f"(GHS {TIER_PRICES_GHS[TIER_PREMIUM]}/mo, {get_entry_limit(TIER_PREMIUM)} transactions + CSV)."
    )


def build_upgrade_nudge(count: int, tier: str) -> str:
    limit = get_entry_limit(tier)
    remaining = limit - count
    if remaining <= 0:
        return ""
    return (
        f"\n\n\U0001f4a1 *{remaining} {'transaction' if remaining == 1 else 'transactions'} "
        f"left this month.* Reply *UPGRADE* to see plans."
    )


def build_upgrade_menu(pro_url: str, premium_url: str) -> str:
    return (
        f"\U0001f4b3 *{BRAND_NAME} Plans*\n\n"
        f"*Pro \u2014 GHS {TIER_PRICES_GHS[TIER_PRO]}/month*\n"
        f"  \u2714 {get_entry_limit(TIER_PRO)} transactions/month\n"
        "  \u2714 Monthly summaries (TOTAL)\n"
        f"  \U0001f449 {pro_url}\n\n"
        f"*Premium \u2014 GHS {TIER_PRICES_GHS[TIER_PREMIUM]}/month*\n"
        f"  \u2714 {get_entry_limit(TIER_PREMIUM)} transactions/month\n"
        "  \u2714 Monthly summaries\n"
        "  \u2714 CSV export (REPORT command)\n"
        f"  \U0001f449 {premium_url}\n\n"
        "(Links expire in 24 hours)"
    )


def build_premium_upgrade_message(premium_url: str) -> str:
    return (
        f"\U0001f680 *Upgrade to {BRAND_NAME} Premium*\n\n"
        f"GHS {TIER_PRICES_GHS[TIER_PREMIUM]}/month:\n"
        f"  \u2714 {get_entry_limit(TIER_PREMIUM)} transactions/month\n"
        "  \u2714 Monthly summaries\n"
        "  \u2714 CSV export (REPORT command)\n\n"
        f"\U0001f449 Pay here: {premium_url}\n\n"
        "(Link expires in 24 hours)"
    )


def build_limit_reached_message(tier: str, pro_url: str | None = None, premium_url: str | None = None) -> str:
    limit = get_entry_limit(tier)

    if tier == TIER_PREMIUM:
        return (
            f"\U0001f512 You've used all {limit} Premium transactions for this month.\n\n"
            "Your limit resets on the 1st. Type TOTAL to review this month."
        )

    if tier == TIER_PRO and premium_url:
        return (
            f"\U0001f512 You've used all {limit} Pro transactions for this month.\n\n"
            f"Upgrade to *Premium* (GHS {TIER_PRICES_GHS[TIER_PREMIUM]}/month) for "
            f"{get_entry_limit(TIER_PREMIUM)} transactions + CSV export:\n"
            f"\U0001f449 {premium_url}\n\n"
            "(Link expires in 24 hours)"
        )

    if pro_url and premium_url:
        return (
            f"\U0001f512 You've used all {limit} free transactions for this month.\n\n"
            + build_upgrade_menu(pro_url, premium_url)
        )

    return (
        f"\U0001f512 You've used all {limit} transactions for this month.\n\n"
        "Reply *UPGRADE* to see paid plans."
    )


def build_report_paywall(premium_url: str) -> str:
    return (
        f"\U0001f4c1 CSV export is a *{BRAND_NAME} Premium* feature.\n\n"
        f"GHS {TIER_PRICES_GHS[TIER_PREMIUM]}/month:\n"
        f"  \u2714 {get_entry_limit(TIER_PREMIUM)} transactions/month\n"
        "  \u2714 Monthly summaries\n"
        "  \u2714 CSV export (REPORT command)\n\n"
        f"\U0001f449 Pay here: {premium_url}\n\n"
        "(Link expires in 24 hours)"
    )


def build_tier_confirmation(tier: str) -> str:
    if tier == TIER_PREMIUM:
        return (
            f"\U0001f38a *You're on {BRAND_NAME} Premium!*\n\n"
            f"{get_entry_limit(TIER_PREMIUM)} transactions/month, summaries, "
            "and CSV exports (REPORT) are now active.\n\n"
            "Keep KountN \u2014 your data is encrypted and only you can read it."
        )
    return (
        f"\U0001f38a *You're on {BRAND_NAME} Pro!*\n\n"
        f"{get_entry_limit(TIER_PRO)} transactions/month and summaries are now active.\n\n"
        "Reply *UPGRADE* anytime for Premium (CSV exports).\n\n"
        "Keep KountN \u2014 your data is encrypted and only you can read it."
    )


async def generate_and_upload_csv(rows: list[dict], user_id: str) -> str:
    """Write decrypted rows to CSV, upload to Supabase Storage, return signed URL."""
    output = io.StringIO()
    writer = csv.writer(output)
    writer.writerow([
        "Timestamp", "Amount", "Currency", "Category",
        "Merchant", "Description", "Type", "InputMethod",
    ])
    for r in rows:
        writer.writerow([
            r.get("timestamp", r.get("logged_at", "")),
            r.get("amount", ""),
            r.get("currency", ""),
            r.get("category", ""),
            r.get("merchant", ""),
            r.get("description", ""),
            r.get("entry_type", ""),
            r.get("input_method", ""),
        ])
    csv_bytes = output.getvalue().encode("utf-8")
    return await upload_csv_report(user_id, csv_bytes)
