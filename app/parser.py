"""
parser.py — GPT-4.1-mini structured expense extraction.

Uses OpenAI's beta structured output (response_format=Pydantic model)
so the response is always a valid ExpenseEntry — no regex, no error-prone JSON parsing.
"""

import asyncio
from datetime import datetime, timezone
from textwrap import dedent

from openai import AsyncOpenAI
from structlog import get_logger

from app.models import CATEGORIES, ExpenseEntry

logger = get_logger()

_client: AsyncOpenAI | None = None


def get_openai_client() -> AsyncOpenAI:
    global _client
    if _client is None:
        _client = AsyncOpenAI()
    return _client


async def parse_expense(text: str) -> ExpenseEntry:
    """
    Extract a structured expense or income entry from a natural language message.
    Raises OpenAI exceptions on network/API failure — caller must handle.
    """
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    client = get_openai_client()

    response = await asyncio.wait_for(
        client.beta.chat.completions.parse(
            model="gpt-4.1-mini",
            max_tokens=300,
            messages=[
                {
                    "role": "system",
                    "content": dedent(f"""\
                        You are a financial diary assistant for Ghanaian solopreneurs.
                        Extract a structured expense or income entry from the user message.

                        Rules:
                        - Default currency is GHS (Ghana Cedis) unless clearly stated otherwise.
                        - Amount must always be a positive number.
                        - entry_type is 'Income' if they received money, otherwise 'Expense'.
                        - Choose the best category from: {', '.join(CATEGORIES)}
                        - If no merchant is mentioned, use an empty string.
                        - Keep description concise (max 60 chars).
                        - Today is {today}.\
                    """),
                },
                {"role": "user", "content": text},
            ],
            response_format=ExpenseEntry,
        ),
        timeout=15.0,
    )

    entry = response.choices[0].message.parsed
    logger.info(
        "Expense parsed",
        amount=entry.amount,
        currency=entry.currency,
        category=entry.category,
        entry_type=entry.entry_type,
    )
    return entry
