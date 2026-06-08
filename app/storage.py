"""
storage.py — Supabase Storage for CSV report downloads.
"""

import asyncio
import os
from datetime import datetime, timezone

from structlog import get_logger

from app.database import get_supabase

logger = get_logger()

BUCKET = os.environ.get("SUPABASE_STORAGE_BUCKET", "expense-reports")
SIGNED_URL_TTL_SECONDS = 86400  # 24 hours


async def upload_csv_report(user_id: str, csv_bytes: bytes) -> str:
    """Upload a CSV and return a signed download URL."""
    month = datetime.now(timezone.utc).strftime("%Y-%m")
    path = f"{user_id}/expenses_{month}.csv"
    sb = get_supabase()

    def _upload_and_sign() -> str:
        sb.storage.from_(BUCKET).upload(
            path,
            csv_bytes,
            file_options={"content-type": "text/csv", "upsert": "true"},
        )
        signed = sb.storage.from_(BUCKET).create_signed_url(path, SIGNED_URL_TTL_SECONDS)
        if isinstance(signed, dict):
            return (
                signed.get("signedURL")
                or signed.get("signedUrl")
                or signed.get("signed_url")
                or ""
            )
        return str(signed)

    url = await asyncio.to_thread(_upload_and_sign)
    if not url:
        raise RuntimeError("Supabase Storage did not return a signed URL")

    logger.info("CSV uploaded", bucket=BUCKET, path=path)
    return url
