"""
models.py — Pydantic models for API requests, responses, and AI structured output.
"""

from pydantic import BaseModel, Field

TIER_FREE = "free"
TIER_PRO = "pro"
TIER_PREMIUM = "premium"

TIER_LIMITS: dict[str, int] = {
    TIER_FREE: 5,
    TIER_PRO: 30,
    TIER_PREMIUM: 100,
}

TIER_PRICES_GHS: dict[str, int] = {
    TIER_PRO: 25,
    TIER_PREMIUM: 99,
}


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


CATEGORIES = [
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
    "Income",
    "Other",
]

# ── AI Parsing ────────────────────────────────────────────────────────────────

class ExpenseEntry(BaseModel):
    amount:      float = Field(description="Positive numeric amount")
    currency:    str   = Field(description="Currency code, default GHS")
    category:    str   = Field(description=f"One of: {', '.join(CATEGORIES)}")
    merchant:    str   = Field(description="Merchant or vendor name, empty string if unknown")
    description: str   = Field(description="Short description, max 60 chars")
    entry_type:  str   = Field(description="'Income' or 'Expense'")


# ── REST API ──────────────────────────────────────────────────────────────────

class ManualExpenseRequest(BaseModel):
    message:      str = Field(..., description="Expense in plain language")
    phone_number: str = Field(..., description="Phone with country code, no +")


class ManualExpenseResponse(BaseModel):
    success:    bool
    logged:     str
    amount:     float
    currency:   str
    category:   str
    merchant:   str
    entry_type: str


class DbStatusResponse(BaseModel):
    status:            str
    tables_accessible: bool
    message:           str


class PaystackWebhookRequest(BaseModel):
    event: str
    data:  dict
