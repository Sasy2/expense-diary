-- ============================================================
-- WhatsApp Expense Diary — Supabase Schema
-- Run this in the Supabase SQL editor before first deploy
-- ============================================================

-- Users table
-- phone_id is a one-way SHA-256 hash — never the real number
CREATE TABLE IF NOT EXISTS expense_users (
    id                    UUID        DEFAULT gen_random_uuid() PRIMARY KEY,
    phone_id              TEXT        UNIQUE NOT NULL,
    tier                  TEXT        DEFAULT 'pro',  -- new users start on Pro trial
    entry_count           INT         DEFAULT 0,
    trial_ends_at         TIMESTAMPTZ,                -- Pro trial end (null if paid)
    is_paid               BOOLEAN     DEFAULT FALSE,  -- true after Paystack payment
    trial_reminders_sent  INT         DEFAULT 0,      -- 0=none, 1=mid, 2=week, 3=final
    notify_phone_enc      TEXT,                       -- Fernet ciphertext for monthly recaps
    created_at            TIMESTAMPTZ DEFAULT NOW()
);

-- Expenses table
-- encrypted_payload is Fernet ciphertext — unreadable without ENCRYPTION_SECRET
-- month_year is the only plaintext financial field (needed for efficient filtering)
CREATE TABLE IF NOT EXISTS expenses (
    id                UUID        DEFAULT gen_random_uuid() PRIMARY KEY,
    user_id           UUID        REFERENCES expense_users(id) ON DELETE CASCADE,
    encrypted_payload TEXT        NOT NULL,
    month_year        TEXT        NOT NULL, -- e.g. "2025-06"
    logged_at         TIMESTAMPTZ DEFAULT NOW()
);

-- Indexes for common query patterns
CREATE INDEX IF NOT EXISTS idx_expenses_user_month
    ON expenses (user_id, month_year);

CREATE INDEX IF NOT EXISTS idx_expenses_user_logged
    ON expenses (user_id, logged_at DESC);

-- Row-Level Security
-- No policies = deny all anon/authenticated access
-- Service role key (used by server) bypasses RLS automatically
ALTER TABLE expense_users ENABLE ROW LEVEL SECURITY;
ALTER TABLE expenses      ENABLE ROW LEVEL SECURITY;
