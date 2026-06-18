"""
models.py — Pydantic models for API requests, responses, and AI structured output.
"""

from datetime import datetime, timedelta, timezone
from typing import Literal, get_args

from pydantic import BaseModel, Field

TIER_FREE = "free"
TIER_PRO = "pro"
TIER_PREMIUM = "premium"

TIER_LIMITS: dict[str, int] = {
    TIER_FREE: 5,
    TIER_PRO: 75,
    TIER_PREMIUM: 300,
}

TIER_PRICES_GHS: dict[str, int] = {
    TIER_PRO: 20,
    TIER_PREMIUM: 49,
}

TRIAL_DAYS = 30
BRAND_NAME = "KountN"


def get_entry_limit(tier: str) -> int:
    return TIER_LIMITS.get(tier, TIER_LIMITS[TIER_FREE])


def nudge_threshold(tier: str) -> int:
    """Send a soft upgrade nudge when the user is this many entries from the limit."""
    limit = get_entry_limit(tier)
    return max(limit - 2, 1)


def can_export_csv(tier: str) -> bool:
    return tier == TIER_PREMIUM


def gets_monthly_summary(tier: str) -> bool:
    return tier in (TIER_PRO, TIER_PREMIUM)


def new_user_trial_ends_at() -> str:
    return (datetime.now(timezone.utc) + timedelta(days=TRIAL_DAYS)).isoformat()


def is_trial_user(record: dict | None) -> bool:
    """True if the user is on a complimentary Pro trial (not paid)."""
    if not record:
        return False
    return (
        record.get("tier") == TIER_PRO
        and not record.get("is_paid")
        and bool(record.get("trial_ends_at"))
    )


CategoryLiteral = Literal[
    "Food & Dining",
    "Transport",
    "Internet & Data",
    "Utilities",
    "Office Supplies",
    "Marketing",
    "Professional Services",
    "Entertainment",
    "Healthcare",
    "Shopping",
    "Rent & Housing",
    "Other",
]

CATEGORIES = list(get_args(CategoryLiteral))

# ── AI Parsing ────────────────────────────────────────────────────────────────

class ExpenseEntry(BaseModel):
    amount:      float = Field(description="Positive numeric amount")
    currency:    str   = Field(description="Currency code, default GHS")
    category:    CategoryLiteral = Field(description=f"One of: {', '.join(CATEGORIES)}")
    merchant:    str   = Field(description="Merchant or vendor name, empty string if unknown")
    description: str   = Field(description="Short description, max 60 chars")
    entry_type:  Literal["Income", "Expense"] = Field(description="'Income' or 'Expense'")
    timestamp:   str   = Field(description="ISO 8601 date-time string (UTC) of the transaction, resolved relative to the current time context. If no date/time is mentioned, default to the current time.")


class ExpenseDiaryPayload(BaseModel):
    entries: list[ExpenseEntry] = Field(description="List of structured expense/income entries extracted from the message. Can be empty if no transaction is found.")


# ── REST API ──────────────────────────────────────────────────────────────────

class ManualExpenseRequest(BaseModel):
    message:      str = Field(..., description="Expense in plain language")
    phone_number: str = Field(..., description="Phone with country code, no +")


class ManualExpenseResponse(BaseModel):
    success:    bool
    logged:     str
    amount:     float
    currency:   str
    category:   CategoryLiteral
    merchant:   str
    entry_type: Literal["Income", "Expense"]


class DbStatusResponse(BaseModel):
    status:            str
    tables_accessible: bool
    message:           str


class PaystackWebhookRequest(BaseModel):
    event: str
    data:  dict
