"""
handlers.py — Command routing and core expense processing logic.

detect_command()         → Normalise text and return command name or None
handle_command()         → Execute HELP / TOTAL / REPORT / LAST5 / UPGRADE
process_expense_message() → Parse → check limits → encrypt → save → confirm
"""

import re
import uuid
from datetime import datetime, timezone, timedelta, date
from typing import Optional

from structlog import get_logger

from app.database import (
    decrement_entry_count,
    delete_last_expense,
    get_user_expenses,
    get_user_record,
    increment_entry_count,
    save_expense,
)
from app.messaging import (
    build_confirmation,
    build_farewell_reply,
    build_greeting_reply,
    build_help,
    build_last_n,
    build_limit_reached_message,
    build_undo_confirmation,
    build_not_an_expense_hint,
    build_premium_upgrade_message,
    build_report_paywall,
    build_small_talk_reply,
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
    TIER_PRICES_GHS,
    can_export_csv,
    get_entry_limit,
    is_trial_user,
    nudge_threshold,
)
from app.parser import parse_expense
from app.payments import create_payment_link

logger = get_logger()

_MAX_LAST_N = 50

# Compile regexes for each command to handle natural language variations
_LAST_CMD = re.compile(
    r"^\s*(?:show\s+|get\s+)?last\s*(\d+)(?:\s*(?:entries|transactions|items))?\s*$",
    re.IGNORECASE
)

_CMD_PATTERNS = {
    "UNDO": re.compile(
        r"^\s*(?:please\s+|can\s+you\s+)?(?:undo|delete|remove|cancel)(?:\s+(?:my|the|that|last|transaction|entry|latest))*\s*(?:please|thanks|thank\s+you)?\s*$",
        re.IGNORECASE
    ),
    "HELP": re.compile(
        r"^\s*(?:please\s+|get\s+|show\s+)?(?:help|info|instructions|commands|guide|menu)(?:\s+(?:help|info|instructions|commands|guide|menu|me|please|thanks|thank\s+you|us))*\s*$"
        r"|^\s*how\s+(?:to\s+use|does\s+this\s+work|do\s+i\s+use|it\s+works|to|do)(?:\s+\w+)*\s*$",
        re.IGNORECASE
    ),
    "TOTAL_TODAY": re.compile(
        r"^\s*(?:can\s+i\s+see\s+|can\s+you\s+|what\s+is\s+|what's\s+|whats\s+|what\s+about\s+|show\s+|get\s+|view\s+)*(?:my\s+|the\s+)*(?:today(?:\s+only)?(?:\s+total|\s+totals|\s+summary|\s+recap|\s+breakdown)?|total(?:\s+for)?\s+today|spent\s+today|earned\s+today|today\s+spend|today\s+spending|today\s+earnings|today(?:\s+this\s+week\s+only)?)\s*(?:please|thanks)?\s*$",
        re.IGNORECASE
    ),
    "TOTAL_WEEK": re.compile(
        r"^\s*(?:can\s+i\s+see\s+|can\s+you\s+|what\s+is\s+|what's\s+|whats\s+|what\s+about\s+|show\s+|get\s+|view\s+)*(?:my\s+|the\s+)*(?:this\s+week(?:\s+only)?(?:\s+total|\s+totals|\s+summary|\s+recap|\s+breakdown)?|weekly(?:\s+total|\s+totals|\s+summary|\s+recap|\s+breakdown)?|total(?:\s+for)?\s+this\s+week|spent\s+this\s+week|earned\s+this\s+week|week\s+total|weekly\s+summary|total(?:\s+this\s+week\s+only)?)\s*(?:please|thanks)?\s*$",
        re.IGNORECASE
    ),
    "TOTAL_LAST_WEEK": re.compile(
        r"^\s*(?:can\s+i\s+see\s+|can\s+you\s+|what\s+is\s+|what's\s+|whats\s+|show\s+|get\s+|view\s+)*(?:my\s+|the\s+)*(?:last\s+week(?:'s)?(?:\s+total|\s+totals|\s+summary|\s+recap|\s+breakdown)?|total(?:\s+for)?\s+last\s+week|last\s+week(?:\s+only)?)\s*(?:please|thanks)?\s*$",
        re.IGNORECASE
    ),
    "TOTAL_LAST_MONTH": re.compile(
        r"^\s*(?:can\s+i\s+see\s+|can\s+you\s+|what\s+is\s+|what's\s+|whats\s+|show\s+|get\s+|view\s+)*(?:my\s+|the\s+)*(?:last\s+month(?:'s)?(?:\s+total|\s+totals|\s+summary|\s+recap|\s+breakdown)?|total(?:\s+for)?\s+last\s+month|previous\s+month(?:'s)?(?:\s+total|\s+summary)?)\s*(?:please|thanks)?\s*$",
        re.IGNORECASE
    ),
    "TOTAL": re.compile(
        r"^\s*(?:can\s+i\s+see\s+|can\s+you\s+|what\s+is\s+|what's\s+|whats\s+|what\s+about\s+|show\s+|get\s+|view\s+)*(?:my\s+|the\s+|this\s+month's\s+|monthly\s+)*(?:total|totals|summary|summaries|recap|breakdown)(?:\s+(?:total|totals|summary|summaries|recap|breakdown|for\s+this\s+month|this\s+month|for|month))*\s*(?:please|thanks)?\s*$",
        re.IGNORECASE
    ),
    "REPORT": re.compile(
        r"^\s*(?:can\s+i\s+)?(?:see|get|download|export|send|view|have|generate|want|need|show)?(?:\s+(?:my|the|a|some|an))*\s*(?:report|csv|excel|sheet|data|expenses|transactions)(?:\s+(?:report|csv|excel|sheet|file|export|download|for\s+this\s+month|this\s+month|for|month))*\s*(?:please|thanks|thank\s+you)?\s*$"
        r"|^\s*(?:report|csv|excel|sheet)(?:\s+(?:please|thanks|thank\s+you))?\s*$",
        re.IGNORECASE
    ),
    "UPGRADE": re.compile(
        r"^\s*(?:show\s+|get\s+|view\s+|how\s+to\s+)?(?:my\s+|the\s+)*(?:upgrade|plans|prices|pricing|subscription|subscribe)(?:\s+plan|\s+please|\s+thanks)?\s*$",
        re.IGNORECASE
    ),
    "PRO": re.compile(
        r"^\s*(?:upgrade\s+to\s+|get\s+)?pro(?:\s+plan)?(?:\s+please|\s+thanks)?\s*$",
        re.IGNORECASE
    ),
    "PREMIUM": re.compile(
        r"^\s*(?:upgrade\s+to\s+|get\s+)?premium(?:\s+plan)?(?:\s+please|\s+thanks)?\s*$",
        re.IGNORECASE
    ),
    "EXPLAIN": re.compile(
        r"^\s*(?:can\s+i\s+see\s+|can\s+you\s+|what\s+is\s+|what\s+is\s+my\s+|what's\s+|whats\s+|show\s+|get\s+|explain\s+|view\s+)*(?:my\s+|the\s+)*(?:explain|explanation|insights|insight|recap|summary|recap\s+insights)(?:\s+(?:report|recap|insights|insight|for\s+this\s+month|this\s+month|for|month))*\s*(?:please|thanks)?\s*$",
        re.IGNORECASE
    ),
}


def _user_tier(record: dict | None) -> str:
    if not record:
        return TIER_FREE
    return record.get("tier") or TIER_FREE


def detect_command(text: str) -> Optional[str]:
    """
    Normalise text and return a command name if recognised, else None.
    Supports natural language variations for LAST, UNDO, HELP, TOTAL, REPORT, and UPGRADE.
    """
    cleaned = re.sub(r"[^\w\s]", "", text)
    stripped = cleaned.strip()
    
    # 1. Match LAST command (e.g. "last 5", "show last 10 entries", "last5")
    last_match = _LAST_CMD.match(stripped)
    if last_match:
        n = min(max(int(last_match.group(1)), 1), _MAX_LAST_N)
        return f"LAST:{n}"

    # 2. Match regex patterns for other commands
    for cmd, pattern in _CMD_PATTERNS.items():
        if pattern.match(stripped):
            # Resolve aliases
            if cmd == "UNDO":
                return "UNDO"
            return cmd

    # 3. Try to detect a date-range command: "total from X to Y" / "total Jun 1-15"
    range_cmd = _detect_total_range(stripped)
    if range_cmd:
        return range_cmd

    # 4. Fallback: space-stripped exact match
    normalised = re.sub(r"\s+", "", stripped.upper())
    fallback_map = {
        "HELP":          "HELP",
        "TOTAL":         "TOTAL",
        "TOTALTODAY":    "TOTAL_TODAY",
        "TODAY":         "TOTAL_TODAY",
        "TOTALWEEK":     "TOTAL_WEEK",
        "WEEK":          "TOTAL_WEEK",
        "WEEKLY":        "TOTAL_WEEK",
        "TOTALLASTWEEK": "TOTAL_LAST_WEEK",
        "LASTWEEK":      "TOTAL_LAST_WEEK",
        "TOTALLASTMONTH":"TOTAL_LAST_MONTH",
        "LASTMONTH":     "TOTAL_LAST_MONTH",
        "REPORT":        "REPORT",
        "LAST5":         "LAST:5",
        "UPGRADE":       "UPGRADE",
        "PRO":           "PRO",
        "PREMIUM":       "PREMIUM",
        "UNDO":          "UNDO",
        "DELETE":        "UNDO",
        "EXPLAIN":       "EXPLAIN",
        "INSIGHTS":      "EXPLAIN",
    }
    return fallback_map.get(normalised)


# ── Date-range detection ──────────────────────────────────────────────────────

_MONTH_NAMES = {
    "jan": 1, "january": 1,
    "feb": 2, "february": 2,
    "mar": 3, "march": 3,
    "apr": 4, "april": 4,
    "may": 5,
    "jun": 6, "june": 6,
    "jul": 7, "july": 7,
    "aug": 8, "august": 8,
    "sep": 9, "sept": 9, "september": 9,
    "oct": 10, "october": 10,
    "nov": 11, "november": 11,
    "dec": 12, "december": 12,
}

# Patterns like: "total from June 1 to June 15", "total Jun 1-15", "total 1/6 to 15/6"
_RANGE_FROM_TO = re.compile(
    r"""(?:total|summary|recap)\s+(?:from\s+)?
        (?P<m1>[a-z]+)?\s*(?P<d1>\d{1,2})(?:\s*/\s*(?P<m1b>\d{1,2}))?
        \s*(?:to|-|through|till|until)\s+
        (?P<m2>[a-z]+)?\s*(?P<d2>\d{1,2})(?:\s*/\s*(?P<m2b>\d{1,2}))?
        (?:\s+(?P<yr>\d{4}))?\s*$""",
    re.IGNORECASE | re.VERBOSE,
)

# Patterns like: "total June", "total June 2025", "total last June"
_RANGE_MONTH_ONLY = re.compile(
    r"""(?:total|summary|recap)\s+(?:for\s+)?(?:last\s+)?(?P<month>[a-z]+)(?:\s+(?P<year>\d{4}))?\s*$""",
    re.IGNORECASE | re.VERBOSE,
)


def _detect_total_range(text: str) -> str | None:
    """Try to parse a date-range from the stripped text; return 'TOTAL_RANGE:start:end' or None."""
    now = datetime.now(timezone.utc)
    cur_year = now.year

    # Try "from X to Y" style
    m = _RANGE_FROM_TO.search(text.lower())
    if m:
        try:
            year = int(m.group("yr")) if m.group("yr") else cur_year
            # Start date
            if m.group("m1") and m.group("m1") in _MONTH_NAMES:
                sm = _MONTH_NAMES[m.group("m1")]
                sd = int(m.group("d1"))
            elif m.group("m1b"):  # numeric month like d/m
                sm = int(m.group("m1b"))
                sd = int(m.group("d1"))
            else:
                sm = now.month
                sd = int(m.group("d1"))
            # End date
            if m.group("m2") and m.group("m2") in _MONTH_NAMES:
                em = _MONTH_NAMES[m.group("m2")]
                ed = int(m.group("d2"))
            elif m.group("m2b"):  # numeric month
                em = int(m.group("m2b"))
                ed = int(m.group("d2"))
            else:
                em = sm
                ed = int(m.group("d2"))

            start = date(year, sm, sd).isoformat()
            end = date(year, em, ed).isoformat()
            return f"TOTAL_RANGE:{start}:{end}"
        except (ValueError, TypeError):
            pass

    # Try "total <MonthName>" or "total <MonthName> <year>"
    m2 = _RANGE_MONTH_ONLY.search(text.lower())
    if m2:
        month_str = m2.group("month").lower()
        if month_str in _MONTH_NAMES:
            month_num = _MONTH_NAMES[month_str]
            year = int(m2.group("year")) if m2.group("year") else cur_year
            # Build first and last day of that month
            start = date(year, month_num, 1).isoformat()
            if month_num == 12:
                last_day = date(year + 1, 1, 1) - timedelta(days=1)
            else:
                last_day = date(year, month_num + 1, 1) - timedelta(days=1)
            end = last_day.isoformat()
            return f"TOTAL_RANGE:{start}:{end}"

    return None


_GREETINGS = frozenset({
    "hi", "hey", "hello", "hola", "howdy", "greetings",
    "good morning", "good afternoon", "good evening", "good day",
    "morning", "afternoon", "evening",
    "whats up", "what's up", "wassup", "sup", "yo", "hiya",
    "hi there", "hey there", "hello there",
    "thanks", "thank you", "thankyou", "ok", "okay", "cheers",
    "akwaaba", "medaase", "ete sen", "ɛte sɛn",
})

_FAREWELLS = frozenset({
    "bye", "goodbye", "good bye", "bye bye", "later", "see you", "see ya",
    "take care", "cya", "ttyl", "have a good day", "have a great day",
    "gotta go", "gotta run", "talk later", "until next time", "night",
    "good night", "goodnight", "nite",
})

_SMALL_TALK = re.compile(
    r"^\s*(?:"
    r"how are you"
    r"|how are you doing"
    r"|how's it going"
    r"|how are things"
    r"|how do you do"
    r"|you good"
    r"|are you there"
    r"|you okay"
    r"|what are you"
    r"|who are you"
    r"|what can you do"
    r"|what is this"
    r"|whats this"
    r"|what's this"
    r"|nice to meet you"
    r"|pleased to meet you"
    r")\s*[?!.]*\s*$",
    re.IGNORECASE,
)


def detect_greeting(text: str) -> bool:
    """
    Return True for greetings that are not expenses.
    Messages containing digits are never treated as greetings.
    """
    if re.search(r"\d", text):
        return False
    normalised = re.sub(r"[^\w\s']", "", text.strip().lower())
    normalised = re.sub(r"\s+", " ", normalised).strip()
    if not normalised:
        return False
    if normalised in _GREETINGS:
        return True
    for stem in ("hello", "hey", "hi"):
        if normalised.startswith(stem) and len(normalised) <= len(stem) + 3:
            return True
    return False


def detect_farewell(text: str) -> bool:
    """Return True if the message is a farewell/goodbye."""
    if re.search(r"\d", text):
        return False
    normalised = re.sub(r"[^\w\s']", "", text.strip().lower())
    normalised = re.sub(r"\s+", " ", normalised).strip()
    return normalised in _FAREWELLS


def detect_small_talk(text: str) -> bool:
    """Return True if the message is conversational small talk."""
    if re.search(r"\d", text):
        return False
    return bool(_SMALL_TALK.match(text.strip()))


def _row_in_range(row: dict, start: datetime, end: datetime) -> bool:
    """Return True if row['logged_at'] falls within [start, end)."""
    try:
        logged_at = datetime.fromisoformat(row["logged_at"].replace("Z", "+00:00"))
        if logged_at.tzinfo is None:
            logged_at = logged_at.replace(tzinfo=timezone.utc)
        return start <= logged_at < end
    except Exception:
        return False


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
                if is_trial_user(record):
                    await send_wa_text(
                        phone,
                        "\U0001f38a You're on a *free Pro trial* right now!\n\n"
                        "Enjoy it while it lasts, or reply *PREMIUM* to upgrade early."
                    )
                else:
                    await send_wa_text(
                        phone,
                        "\U0001f38a You're already on Pro! Reply *PREMIUM* to upgrade."
                    )
                return
            try:
                url = await create_payment_link(phone, TIER_PRO)
                await send_wa_text(
                    phone,
                    f"\U0001f4b3 *Pro \u2014 GHS {TIER_PRICES_GHS[TIER_PRO]}/month*\n"
                    f"  \u2714 {get_entry_limit(TIER_PRO)} transactions/month\n"
                    "  \u2714 Monthly summaries (TOTAL)\n\n"
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

    if command == "TOTAL_TODAY":
        rows = await get_user_expenses(phone)
        now = datetime.now(timezone.utc)
        today_start = now.replace(hour=0, minute=0, second=0, microsecond=0)
        today_rows = [
            r for r in rows
            if _row_in_range(r, today_start, now)
        ]
        await send_wa_text(phone, build_total_summary(today_rows, month_label="Today"))
        return

    if command == "TOTAL_WEEK":
        rows = await get_user_expenses(phone)
        now = datetime.now(timezone.utc)
        # Mon of current week
        week_start = (now - timedelta(days=now.weekday())).replace(hour=0, minute=0, second=0, microsecond=0)
        week_rows = [
            r for r in rows
            if _row_in_range(r, week_start, now)
        ]
        await send_wa_text(phone, build_total_summary(week_rows, month_label="This Week"))
        return

    if command == "TOTAL_LAST_WEEK":
        rows = await get_user_expenses(phone)
        now = datetime.now(timezone.utc)
        this_week_start = (now - timedelta(days=now.weekday())).replace(hour=0, minute=0, second=0, microsecond=0)
        last_week_start = this_week_start - timedelta(weeks=1)
        last_week_end = this_week_start
        lw_rows = [
            r for r in rows
            if _row_in_range(r, last_week_start, last_week_end)
        ]
        label = f"Last Week ({last_week_start.strftime('%d %b')}–{(last_week_end - timedelta(days=1)).strftime('%d %b')})"
        await send_wa_text(phone, build_total_summary(lw_rows, month_label=label))
        return

    if command == "TOTAL_LAST_MONTH":
        now = datetime.now(timezone.utc)
        first_of_this_month = now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
        last_month_end = first_of_this_month
        if now.month == 1:
            first_of_last_month = now.replace(year=now.year - 1, month=12, day=1, hour=0, minute=0, second=0, microsecond=0)
        else:
            first_of_last_month = now.replace(month=now.month - 1, day=1, hour=0, minute=0, second=0, microsecond=0)
        prev_month_year = first_of_last_month.strftime("%Y-%m")
        prev_month_label = first_of_last_month.strftime("%B %Y")
        rows = await get_user_expenses(phone, month_year=prev_month_year)
        await send_wa_text(phone, build_total_summary(rows, month_label=prev_month_label))
        return

    if command.startswith("TOTAL_RANGE:"):
        _, start_str, end_str = command.split(":", 2)
        try:
            start_dt = datetime.fromisoformat(start_str).replace(tzinfo=timezone.utc)
            # end is inclusive to end of day
            end_dt = datetime.fromisoformat(end_str).replace(
                hour=23, minute=59, second=59, tzinfo=timezone.utc
            )
            rows = await get_user_expenses(phone)
            range_rows = [
                r for r in rows
                if _row_in_range(r, start_dt, end_dt)
            ]
            label = f"{start_dt.strftime('%d %b')}–{end_dt.strftime('%d %b %Y')}"
            await send_wa_text(phone, build_total_summary(range_rows, month_label=label))
        except (ValueError, TypeError) as exc:
            logger.warning("TOTAL_RANGE parse error", error=str(exc))
            await send_wa_text(phone, "❌ Couldn't understand that date range. Try: *total June 1 to June 15*")
        return

    if command == "TOTAL":
        month_year = datetime.now(timezone.utc).strftime("%Y-%m")
        month_label = datetime.now(timezone.utc).strftime("%B %Y")
        rows = await get_user_expenses(phone, month_year=month_year)
        await send_wa_text(phone, build_total_summary(rows, month_label=month_label))
        return

    if command == "EXPLAIN":
        month_year = datetime.now(timezone.utc).strftime("%Y-%m")
        month_name = datetime.now(timezone.utc).strftime("%B %Y")
        rows = await get_user_expenses(phone, month_year=month_year)
        if not rows:
            await send_wa_text(
                phone,
                f"No transactions logged yet for {month_name}.\n\n"
                "Keep logging your expenses and income to get insights!"
            )
            return
        
        from app.parser import generate_monthly_insights
        try:
            insights = await generate_monthly_insights(rows, month_name)
            await send_wa_text(phone, insights)
        except Exception as exc:
            logger.error("Failed to generate monthly insights", error=str(exc))
            await send_wa_text(
                phone,
                "\u274c Sorry, I couldn't generate insights right now. Please try again later."
            )
        return


    if command.startswith("LAST:"):
        n = int(command.split(":", 1)[1])
        rows = await get_user_expenses(phone, limit=n, order_desc=True)
        await send_wa_text(phone, build_last_n(rows, n))
        return

    if command == "UNDO":
        if not record:
            await send_wa_text(phone, "\u274c Could not find your account. Try again.")
            return
        deleted = await delete_last_expense(phone, record["id"])
        if not deleted:
            await send_wa_text(
                phone,
                "Nothing to undo — you have no transactions logged yet."
            )
            return
        for _ in deleted:
            await decrement_entry_count(record["id"])
        await send_wa_text(phone, build_undo_confirmation(deleted))
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
                    f"\U0001f4c1 CSV export is Premium (GHS {TIER_PRICES_GHS[TIER_PREMIUM]}/month). "
                    "Reply UPGRADE to unlock."
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
      2. Parse with GPT-4.1-mini (supports multiple entries)
      3. Encrypt and save each entry to Supabase
      4. Increment entry counter for each entry
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
        last_expenses = await get_user_expenses(phone, limit=1, order_desc=True)
        last_expense = last_expenses[0] if last_expenses else None
        entries = await parse_expense(text, context=last_expense)
    except ValueError as exc:
        if "injection" in str(exc):
            logger.warning("Prompt injection blocked", text=text)
            await send_wa_text(
                phone,
                "\u274c Sorry, I cannot process messages containing system overrides or instructions."
            )
            return
        raise exc
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

    if not entries:
        logger.info("No entries parsed", phone=phone[-4:] if len(phone) >= 4 else "****")
        await send_wa_text(phone, build_not_an_expense_hint())
        return

    batch_id = str(uuid.uuid4())
    for i, entry in enumerate(entries):
        # Re-check limit for each item in the batch
        record = await get_user_record(phone)
        entry_count = record.get("entry_count", 0) if record else 0
        if entry_count >= limit:
            logger.info("Entry limit reached mid-batch", user_id=user_id[:8], tier=tier)
            await _send_limit_reached(phone, tier)
            break

        # Zero amount validation check
        if entry.amount == 0:
            logger.info("Rejected zero-amount entry", phone=phone[-4:] if len(phone) >= 4 else "****")
            await send_wa_text(phone, "Looks like that was free — nothing to log! 😊")
            continue

        await save_expense(phone, user_id, entry, input_method, batch_id=batch_id, offset_seconds=i)
        new_count = await increment_entry_count(user_id)

        try:
            iso_str = entry.timestamp.replace("Z", "+00:00")
            entry_dt = datetime.fromisoformat(iso_str)
            if entry_dt.tzinfo is None:
                entry_dt = entry_dt.replace(tzinfo=timezone.utc)
            entry_dt = entry_dt.astimezone(timezone.utc)
        except Exception:
            entry_dt = datetime.now(timezone.utc)

        month_year = entry_dt.strftime("%Y-%m")
        monthly_expenses = await get_user_expenses(phone, month_year=month_year)

        spent_this_month = sum(
            float(e.get("amount", 0)) for e in monthly_expenses if e.get("entry_type") == "Expense"
        )
        show_hint = len(monthly_expenses) <= 3

        # Query budget checks
        from app.database import get_category_budget, get_savings_goals
        budget_alert = ""
        try:
            category_budget = await get_category_budget(user_id, entry.category, month_year)
            if category_budget:
                limit_amount = float(category_budget.get("limit_amount", 0))
                spent_in_category = sum(
                    float(e.get("amount", 0)) for e in monthly_expenses
                    if e.get("entry_type") == "Expense" and e.get("category") == entry.category
                )
                if spent_in_category > limit_amount:
                    budget_alert = f"\n🔴 *Over Budget:* You've spent GHS {spent_in_category:,.2f} / GHS {limit_amount:,.2f} on {entry.category} this month!"
                elif spent_in_category >= limit_amount * 0.8:
                    budget_alert = f"\n⚠️ *Budget Alert:* You've spent GHS {spent_in_category:,.2f} / GHS {limit_amount:,.2f} on {entry.category} this month."
        except Exception as e_bud:
            logger.warning("Failed to query budget", error=str(e_bud))

        # Query savings progress
        savings_progress = ""
        try:
            goals = await get_savings_goals(user_id)
            if goals:
                lines = []
                for goal in goals:
                    target = float(goal.get("target_amount", 0))
                    current = float(goal.get("current_amount", 0))
                    pct = (current / target * 100) if target else 0
                    lines.append(f"🎯 *Savings Goal '{goal.get('name')}'*: GHS {current:,.2f} / GHS {target:,.2f} saved ({pct:.0f}%)")
                savings_progress = "\n" + "\n".join(lines)
        except Exception as e_sav:
            logger.warning("Failed to query savings goals", error=str(e_sav))

        confirmation = build_confirmation(
            entry,
            spent_this_month=spent_this_month,
            show_hint=show_hint,
            budget_alert=budget_alert,
            savings_progress=savings_progress,
        )

        if new_count >= nudge_threshold(tier) and new_count < limit:
            confirmation += build_upgrade_nudge(new_count, tier)

        await send_wa_text(phone, confirmation)

