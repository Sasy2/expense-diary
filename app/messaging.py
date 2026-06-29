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
        "Hey, welcome back! 👋\n\n"
        "Ready to log something? Just tell me naturally — no special format needed:\n"
        "  • '25 GHS trotro to work'\n"
        "  • 'Bought data bundle 50 cedis'\n"
        "  • 'Client paid me 1500 GHS'\n\n"
        "Or type *TOTAL* to see how your month is going 📊"
    )


def build_small_talk_reply() -> str:
    import random
    responses = [
        (
            "Doing great — especially when people like you take control of their money! 💪\n\n"
            "What did you spend or earn today? Just tell me and I'll log it."
        ),
        (
            "I'm here and ready to help you stay on top of your finances! 📊\n\n"
            "Every cedi you track is a step toward understanding your money. "
            "What would you like to log?"
        ),
        (
            "All good on my end! The real question is — how are *your* finances doing? 😄\n\n"
            "Log something today and let's find out. Just type it naturally, like:\n"
            "  • '45 GHS lunch'\n"
            "  • 'Received 2000 GHS'"
        ),
    ]
    return random.choice(responses)


def build_farewell_reply() -> str:
    import random
    responses = [
        "Take care! 👋 Come back and log your next expense — every cedi counts. 💚",
        "See you! The people who track their money are the ones who grow it. 📈",
        "Bye for now! 👋 Your money diary is here whenever you need it.",
    ]
    return random.choice(responses)


def build_welcome(trial_ends_at: str) -> str:
    end_date = _format_trial_end(trial_ends_at)
    return (
        f"Hey! 👋 Welcome to *{BRAND_NAME}* — your personal money diary, right here in WhatsApp.\n\n"
        "Most people work hard but never really know where their money goes. "
        f"*{BRAND_NAME}* changes that — just text your expenses and income like you're "
        "telling a friend, and we'll handle the tracking.\n\n"
        f"🎊 You're on a *free Pro trial* for {TRIAL_DAYS} days (until {end_date}):\n"
        f"  ✔ {get_entry_limit(TIER_PRO)} transactions/month\n"
        "  ✔ Monthly summaries & weekly recaps\n"
        "  ✔ AI financial insights (EXPLAIN)\n"
        "  ✔ UNDO mistakes instantly\n\n"
        "*To log anything, just type it naturally:*\n"
        "  • '45 GHS lunch at Papaye'\n"
        "  • 'Uber 35 cedis'\n"
        "  • 'Client Kwame paid me 2000 GHS'\n"
        "  • '50 cedis data bundle'\n\n"
        "*Commands you can use anytime:*\n"
        "  *TOTAL* — this month's breakdown\n"
        "  *TODAY* — what you've spent today\n"
        "  *WEEK* — this week's breakdown\n"
        "  *LAST WEEK* / *LAST MONTH* — previous period\n"
        "  *TOTAL JUNE* — any specific month\n"
        "  *LAST 5* — your last 5 transactions\n"
        "  *EXPLAIN* — AI insights on your spending\n"
        "  *UNDO* — remove your last entry\n"
        "  *HELP* — full guide\n\n"
        "📸 You can also send a *photo of a receipt* with a caption describing the amount.\n\n"
        "_Your data is private and encrypted — only you can read it._\n\n"
        "What did you spend or earn today? 💚"
    )


def build_not_an_expense_hint() -> str:
    return (
        "Hmm, I couldn't quite catch that 🤔\n\n"
        "To log an expense or income, just include an amount. For example:\n"
        "  • '45 GHS lunch'\n"
        "  • 'Client paid me 500 cedis'\n\n"
        "Type *HELP* if you need the full guide."
    )


def build_help() -> str:
    return (
        f"\U0001f4a1 *{BRAND_NAME} \u2014 Commands*\n\n"
        "*Log a transaction:* Just type it naturally\n"
        "  \u2022 '450 cedis internet data'\n"
        "  \u2022 'Grocery 120 GHS at Shoprite'\n"
        "  \u2022 'Client paid 5000 GHS'\n\n"
        "*Commands:*\n"
        "  TOTAL \u2014 This month's expenses & income\n"
        "  TODAY \u2014 What you've logged today\n"
        "  WEEK \u2014 This week's breakdown\n"
        "  LAST WEEK \u2014 Previous week's breakdown\n"
        "  LAST MONTH \u2014 Previous month's breakdown\n"
        "  TOTAL JUNE \u2014 Any specific month's totals\n"
        "  TOTAL June 1 to June 15 \u2014 Custom date range\n"
        "  LAST 5 / LAST 10 \u2014 Recent transactions (up to 50)\n"
        "  UNDO \u2014 Remove your most recent transaction\n"
        "  EXPLAIN \u2014 AI financial insights and tips\n"
        "  HELP \u2014 This message\n"
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
            f"Send {BRAND_NAME} your first expense or income to get started!\n\n"
            "\U0001f4a1 Just type naturally \u2014 '45 GHS lunch' or 'Client paid 2000 GHS'"
        )

    # Detect currency from rows
    currency = "GHS"
    for r in rows:
        c = str(r.get("currency", "")).strip()
        if c:
            currency = c
            break

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

    lines = [f"\U0001f3e6 *{BRAND_NAME} \u2014 {month_name}*", ""]

    # Expenses section
    if by_category:
        lines.append(f"*Expenses* ({currency} {total_expense:,.2f})")
        for cat, amt in sorted(by_category.items(), key=lambda x: -x[1]):
            pct = (amt / total_expense * 100) if total_expense else 0
            lines.append(f"  \u2022 {cat}: {currency} {amt:,.2f} ({pct:.0f}%)")
    else:
        lines.append(f"*Expenses* ({currency} 0.00)")
        lines.append("  (none this month)")

    lines.append("")

    # Income section
    if by_income_source:
        lines.append(f"*Income* ({currency} {total_income:,.2f})")
        for source, amt in sorted(by_income_source.items(), key=lambda x: -x[1]):
            pct = (amt / total_income * 100) if total_income else 0
            lines.append(f"  \u2022 {source}: {currency} {amt:,.2f} ({pct:.0f}%)")
    else:
        lines.append(f"*Income* ({currency} 0.00)")
        lines.append("  (none this month)")

    lines.append("")

    # Net
    if net >= 0:
        lines.append(f"*Net:* {currency} {net:,.2f} \u2705")
    else:
        lines.append(f"*Net:* -{currency} {abs(net):,.2f} \u26a0\ufe0f")

    return "\n".join(lines)



def build_monthly_recap(rows: list[dict], month_label: str) -> str:
    """First-of-month WhatsApp recap for the previous calendar month."""
    summary = build_total_summary(rows, month_label=month_label)
    return (
        f"\U0001f4ec *Your {month_label} recap from {BRAND_NAME}*\n\n"
        f"{summary}\n\n"
        "Keep KountN this month! Type *TOTAL* anytime for a live view."
    )


def build_weekly_recap(rows: list[dict]) -> str:
    """Format a weekly recap showing summary metrics for the last 7 days."""
    total_expense = 0.0
    total_income = 0.0
    by_category: dict[str, float] = {}

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

    net = total_income - total_expense
    if net >= 0:
        net_str = f"+GHS {net:,.2f} \u2705"
    else:
        net_str = f"-GHS {abs(net):,.2f} \u26a0"

    if by_category:
        top_cat, top_amt = max(by_category.items(), key=lambda x: x[1])
        top_pct = (top_amt / total_expense * 100) if total_expense else 0
        top_expense_str = f"{top_cat} ({top_pct:.0f}%)"
    else:
        top_expense_str = "None (0%)"

    return (
        f"\U0001f4ec *Your Weekly Recap from {BRAND_NAME}*\n"
        f"Last 7 days performance:\n\n"
        f"Net: {net_str}\n"
        f"\U0001f4b8 Spent: GHS {total_expense:,.2f}\n"
        f"\U0001f4b0 Earned: GHS {total_income:,.2f}\n\n"
        f"Top expense: {top_expense_str}\n\n"
        f"Keep KountN! Type *TOTAL* anytime for your monthly summary."
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


def build_undo_confirmation(deleted: list[dict] | dict) -> str:
    if isinstance(deleted, dict):
        deleted_list = [deleted]
    else:
        deleted_list = deleted

    if len(deleted_list) == 1:
        single = deleted_list[0]
        try:
            amt = float(single.get("amount", 0))
        except (ValueError, TypeError):
            amt = 0.0
        currency = str(single.get("currency", "GHS"))
        etype = str(single.get("entry_type", "Expense"))
        desc = str(single.get("description", ""))
        return (
            f"\u2705 *Undone* \u2014 last transaction removed.\n\n"
            f"Removed: {etype} {currency} {amt:,.2f}\n"
            f"{desc}\n\n"
            "Your monthly transaction count was restored.\n"
            "Re-send the correct entry if needed."
        )
    else:
        lines = []
        for item in deleted_list:
            try:
                amt = float(item.get("amount", 0))
            except (ValueError, TypeError):
                amt = 0.0
            currency = str(item.get("currency", "GHS"))
            etype = str(item.get("entry_type", "Expense"))
            desc = str(item.get("description", ""))
            lines.append(f"  \u2022 {etype}: {currency} {amt:,.2f} \u00b7 {desc}")
        
        details = "\n".join(lines)
        return (
            f"\u2705 *Undone* \u2014 last batch of transactions removed.\n\n"
            f"Removed:\n{details}\n\n"
            "Your monthly transaction count was restored.\n"
            "Re-send the correct entry if needed."
        )


def build_confirmation(
    entry,
    spent_this_month: float = 0.0,
    show_hint: bool = True,
    budget_alert: str = "",
    savings_progress: str = ""
) -> str:
    emoji = "\U0001f4b0" if entry.entry_type == "Income" else "\U0001f4b8"
    merchant_line = f"\n\U0001f3ea {entry.merchant}" if entry.merchant else ""
    client_tag_line = f" \u00b7 Tag: {entry.client_tag}" if getattr(entry, "client_tag", None) else ""
    classification_line = f" ({entry.classification})" if getattr(entry, "classification", None) else ""

    msg = (
        f"\u2705 *Logged \u00b7 GHS {spent_this_month:,.2f} spent this month*\n"
        f"{emoji} {entry.entry_type}: {entry.currency} {entry.amount:,.2f}{classification_line}\n"
        f"\U0001f4c1 {entry.category}{client_tag_line}"
        f"{merchant_line}\n"
        f"\U0001f4dd {entry.description}"
    )

    if budget_alert:
        msg += budget_alert
    if savings_progress:
        msg += savings_progress

    if show_hint:
        msg += "\n\nType *TOTAL* to see your month so far."

    return msg



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
