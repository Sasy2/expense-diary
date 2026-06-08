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
    TIER_FREE,
    TIER_PREMIUM,
    TIER_PRO,
    TIER_PRICES_GHS,
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


# ── Report formatters ─────────────────────────────────────────────────────────

def build_greeting_reply() -> str:
    return (
        "Hey! \U0001f44b I'm your expense diary.\n\n"
        "Send any expense in plain language, e.g.:\n"
        "  \u2022 '45 GHS lunch at Papaye'\n"
        "  \u2022 'Uber 35 GHS'\n\n"
        "Type *HELP* for all commands."
    )


def build_not_an_expense_hint() -> str:
    return (
        "I didn't spot an amount in that message.\n\n"
        "To log an expense, include a number, e.g.:\n"
        "  \u2022 '45 GHS lunch'\n"
        "  \u2022 'Uber ride 35 cedis'\n\n"
        "Type *HELP* for commands."
    )


def build_welcome() -> str:
    return (
        "Welcome to Expense Diary! \U0001f9fe\n\n"
        "Just tell me any expense in plain language:\n"
        "  \u2022 '45 GHS lunch at Papaye'\n"
        "  \u2022 'Uber 35 GHS'\n"
        "  \u2022 'Client paid 2000 GHS'\n\n"
        "\U0001f4f8 *Photos:* Send with a caption describing the amount.\n\n"
        "*Commands:*\n"
        "  TOTAL \u2014 this month's breakdown\n"
        "  LAST 5 \u2014 recent entries\n"
        "  HELP \u2014 full instructions\n"
        "  UPGRADE \u2014 see paid plans\n\n"
        f"You get {get_entry_limit(TIER_FREE)} free entries/month. Type HELP anytime."
    )


def build_help() -> str:
    return (
        "\U0001f4a1 *Expense Diary \u2014 Commands*\n\n"
        "*Log an expense:* Just type it naturally\n"
        "  \u2022 '450 cedis internet data'\n"
        "  \u2022 'Grocery 120 GHS at Shoprite'\n"
        "  \u2022 'Client paid 5000 GHS'\n\n"
        "*Commands (all tiers):*\n"
        "  TOTAL \u2014 this month's summary\n"
        "  LAST 5 \u2014 your last 5 entries\n"
        "  HELP \u2014 this message\n"
        "  UPGRADE \u2014 see Pro & Premium plans\n\n"
        "*Plans:*\n"
        f"  Free \u2014 {get_entry_limit(TIER_FREE)} entries/month\n"
        f"  Pro (GHS {TIER_PRICES_GHS[TIER_PRO]}/mo) \u2014 {get_entry_limit(TIER_PRO)} entries + monthly summary\n"
        f"  Premium (GHS {TIER_PRICES_GHS[TIER_PREMIUM]}/mo) \u2014 {get_entry_limit(TIER_PREMIUM)} entries + CSV export (REPORT)"
    )


def build_total_summary(rows: list[dict]) -> str:
    month_name = datetime.now(timezone.utc).strftime("%B %Y")
    if not rows:
        return (
            f"No expenses logged yet for {month_name}.\n\n"
            "Send me any expense to get started!"
        )
    by_category: dict[str, float] = {}
    total_expense = 0.0
    total_income = 0.0
    for r in rows:
        try:
            amt = float(r.get("amount", 0))
        except (ValueError, TypeError):
            continue
        if "income" in str(r.get("entry_type", "")).lower():
            total_income += amt
        else:
            cat = str(r.get("category", "Other"))
            by_category[cat] = by_category.get(cat, 0.0) + amt
            total_expense += amt

    lines = [
        f"\U0001f4ca *{month_name} Summary*",
        f"Total Expenses: GHS {total_expense:,.2f}",
        f"Total Income:   GHS {total_income:,.2f}",
        "",
        "*By Category:*",
    ]
    for cat, amt in sorted(by_category.items(), key=lambda x: -x[1]):
        pct = (amt / total_expense * 100) if total_expense else 0
        lines.append(f"  \u2022 {cat}: GHS {amt:,.2f} ({pct:.0f}%)")
    return "\n".join(lines)


def build_last_n(rows: list[dict], n: int = 5) -> str:
    if not rows:
        return "No expenses logged yet. Send me your first expense!"
    lines = [f"\U0001f9fe *Last {min(n, len(rows))} Entries*", ""]
    for r in rows[:n]:
        try:
            amt = float(r.get("amount", 0))
        except (ValueError, TypeError):
            amt = 0.0
        currency  = str(r.get("currency", "GHS"))
        cat       = str(r.get("category", "?"))
        desc      = str(r.get("description", ""))
        date_part = str(r.get("timestamp", r.get("logged_at", "")))[:10]
        lines.append(f"  \u2022 {currency} {amt:,.2f} \u00b7 {cat} \u00b7 {desc} ({date_part})")
    return "\n".join(lines)


def build_confirmation(entry) -> str:
    emoji = "\U0001f4b0" if entry.entry_type == "Income" else "\U0001f4b8"
    merchant_line = f"\n\U0001f3ea {entry.merchant}" if entry.merchant else ""
    return (
        f"\u2705 Logged!\n"
        f"{emoji} {entry.entry_type}: {entry.currency} {entry.amount:,.2f}\n"
        f"\U0001f4c1 {entry.category}"
        f"{merchant_line}\n"
        f"\U0001f4dd {entry.description}"
    )


def build_upgrade_nudge(count: int, tier: str) -> str:
    limit = get_entry_limit(tier)
    remaining = limit - count
    if remaining <= 0:
        return ""
    return (
        f"\n\n\U0001f4a1 *{remaining} {'entry' if remaining == 1 else 'entries'} left "
        f"this month.* Reply *UPGRADE* to see Pro & Premium plans."
    )


def build_upgrade_menu(pro_url: str, premium_url: str) -> str:
    return (
        "\U0001f4b3 *Expense Diary Plans*\n\n"
        f"*Pro \u2014 GHS {TIER_PRICES_GHS[TIER_PRO]}/month*\n"
        f"  \u2714 {get_entry_limit(TIER_PRO)} entries/month\n"
        "  \u2714 Monthly auto-summary\n"
        f"  \U0001f449 {pro_url}\n\n"
        f"*Premium \u2014 GHS {TIER_PRICES_GHS[TIER_PREMIUM]}/month*\n"
        f"  \u2714 {get_entry_limit(TIER_PREMIUM)} entries/month\n"
        "  \u2714 Monthly auto-summary\n"
        "  \u2714 CSV export (REPORT command)\n"
        f"  \U0001f449 {premium_url}\n\n"
        "(Links expire in 24 hours)"
    )


def build_premium_upgrade_message(premium_url: str) -> str:
    return (
        "\U0001f680 *Upgrade to Premium*\n\n"
        f"GHS {TIER_PRICES_GHS[TIER_PREMIUM]}/month:\n"
        f"  \u2714 {get_entry_limit(TIER_PREMIUM)} entries/month\n"
        "  \u2714 Monthly auto-summary\n"
        "  \u2714 CSV export (REPORT command)\n\n"
        f"\U0001f449 Pay here: {premium_url}\n\n"
        "(Link expires in 24 hours)"
    )


def build_limit_reached_message(tier: str, pro_url: str | None = None, premium_url: str | None = None) -> str:
    limit = get_entry_limit(tier)

    if tier == TIER_PREMIUM:
        return (
            f"\U0001f512 You've used all {limit} Premium entries for this month.\n\n"
            "Your limit resets on the 1st. Type TOTAL to review this month."
        )

    if tier == TIER_PRO and premium_url:
        return (
            f"\U0001f512 You've used all {limit} Pro entries for this month.\n\n"
            f"Upgrade to *Premium* (GHS {TIER_PRICES_GHS[TIER_PREMIUM]}/month) for "
            f"{get_entry_limit(TIER_PREMIUM)} entries + CSV export:\n"
            f"\U0001f449 {premium_url}\n\n"
            "(Link expires in 24 hours)"
        )

    if pro_url and premium_url:
        return (
            f"\U0001f512 You've used all {limit} free entries for this month.\n\n"
            + build_upgrade_menu(pro_url, premium_url)
        )

    return (
        f"\U0001f512 You've used all {limit} entries for this month.\n\n"
        "Reply *UPGRADE* to see paid plans."
    )


def build_report_paywall(premium_url: str) -> str:
    return (
        "\U0001f4c1 CSV export is a *Premium* feature.\n\n"
        f"GHS {TIER_PRICES_GHS[TIER_PREMIUM]}/month:\n"
        f"  \u2714 {get_entry_limit(TIER_PREMIUM)} entries/month\n"
        "  \u2714 Monthly auto-summary\n"
        "  \u2714 CSV export (REPORT command)\n\n"
        f"\U0001f449 Pay here: {premium_url}\n\n"
        "(Link expires in 24 hours)"
    )


def build_tier_confirmation(tier: str) -> str:
    if tier == TIER_PREMIUM:
        return (
            "\U0001f38a *You're on Premium!*\n\n"
            f"{get_entry_limit(TIER_PREMIUM)} entries/month, monthly summaries, "
            "and CSV exports (REPORT) are now active.\n\n"
            "Keep logging \u2014 your data is encrypted and only you can read it."
        )
    return (
        "\U0001f38a *You're on Pro!*\n\n"
        f"{get_entry_limit(TIER_PRO)} entries/month and monthly summaries are now active.\n\n"
        "Reply *UPGRADE* anytime to move to Premium for CSV exports.\n\n"
        "Keep logging \u2014 your data is encrypted and only you can read it."
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
