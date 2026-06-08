"""
security.py — Zero-knowledge encryption helpers.

Design:
  - Phone numbers are NEVER stored in plaintext.
  - get_phone_lookup_id()  → SHA-256(normalize(phone) + ":" + PHONE_LOOKUP_SALT)
  - _derive_fernet_key()   → HMAC-SHA256(ENCRYPTION_SECRET, normalize(phone))
                              → 32 raw bytes → base64url → Fernet key
  - Each user gets a unique key that is NEVER persisted anywhere.
  - Supabase stores only opaque Fernet ciphertext.
"""

import base64
import hashlib
import hmac
import json
import os
import re

from cryptography.fernet import Fernet, InvalidToken


def _normalize_phone(phone: str) -> str:
    """Strip all non-digit characters for consistent key derivation."""
    return re.sub(r"\D", "", phone)


def get_phone_lookup_id(phone: str) -> str:
    """
    One-way hash for database lookup.
    SHA-256(normalize(phone) + ":" + PHONE_LOOKUP_SALT)
    Cannot be reversed even with ENCRYPTION_SECRET.
    """
    salt = os.environ["PHONE_LOOKUP_SALT"]
    normalized = _normalize_phone(phone)
    return hashlib.sha256(f"{normalized}:{salt}".encode()).hexdigest()


def _derive_fernet_key(phone: str) -> bytes:
    """
    Derive a deterministic per-user AES-256 Fernet key.
    HMAC-SHA256(ENCRYPTION_SECRET, normalize(phone)) → 32 bytes → base64url
    """
    secret = os.environ["ENCRYPTION_SECRET"].encode()
    normalized = _normalize_phone(phone).encode()
    raw = hmac.new(secret, normalized, hashlib.sha256).digest()
    return base64.urlsafe_b64encode(raw)


def encrypt_for_user(data: dict, phone: str) -> str:
    """Encrypt a dict to a Fernet ciphertext string using the per-user key."""
    f = Fernet(_derive_fernet_key(phone))
    return f.encrypt(json.dumps(data, ensure_ascii=False).encode()).decode()


def decrypt_for_user(ciphertext: str, phone: str) -> dict:
    """Decrypt a Fernet ciphertext string back to a dict."""
    f = Fernet(_derive_fernet_key(phone))
    return json.loads(f.decrypt(ciphertext.encode()).decode())


def safe_log_phone(phone: str) -> str:
    """Return a non-identifying phone label safe to write to logs."""
    normalized = _normalize_phone(phone)
    return "****" + normalized[-2:] if len(normalized) >= 2 else "****"


def assert_secrets_strength() -> None:
    """
    Called once at startup. Raises ValueError if any secret is too weak.
    A short ENCRYPTION_SECRET reduces per-user key entropy.
    """
    enc_secret = os.environ.get("ENCRYPTION_SECRET", "")
    if len(enc_secret) < 32:
        raise ValueError(
            "ENCRYPTION_SECRET must be at least 32 characters. "
            "Generate one with: python -c \"import secrets; print(secrets.token_hex(32))\""
        )
    lookup_salt = os.environ.get("PHONE_LOOKUP_SALT", "")
    if len(lookup_salt) < 16:
        raise ValueError(
            "PHONE_LOOKUP_SALT must be at least 16 characters. "
            "Generate one with: python -c \"import secrets; print(secrets.token_hex(16))\""
        )
