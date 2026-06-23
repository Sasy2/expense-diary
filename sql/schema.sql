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

-- Expense budgets table
CREATE TABLE IF NOT EXISTS expense_budgets (
    id            UUID        DEFAULT gen_random_uuid() PRIMARY KEY,
    user_id       UUID        REFERENCES expense_users(id) ON DELETE CASCADE,
    category      TEXT        NOT NULL,
    limit_amount  NUMERIC     NOT NULL,
    month_year    TEXT        NOT NULL, -- e.g. "2026-06"
    created_at    TIMESTAMPTZ DEFAULT NOW(),
    UNIQUE (user_id, category, month_year)
);

-- Savings goals table
CREATE TABLE IF NOT EXISTS savings_goals (
    id             UUID        DEFAULT gen_random_uuid() PRIMARY KEY,
    user_id        UUID        REFERENCES expense_users(id) ON DELETE CASCADE,
    name           TEXT        NOT NULL,
    target_amount  NUMERIC     NOT NULL,
    current_amount NUMERIC     DEFAULT 0.0 NOT NULL,
    target_date    TIMESTAMPTZ,
    created_at     TIMESTAMPTZ DEFAULT NOW()
);

-- Processed messages for webhook deduplication
CREATE TABLE IF NOT EXISTS processed_messages (
    message_id TEXT PRIMARY KEY,
    created_at TIMESTAMPTZ DEFAULT NOW()
);

-- Enable RLS for the new tables
ALTER TABLE expense_budgets    ENABLE ROW LEVEL SECURITY;
ALTER TABLE savings_goals      ENABLE ROW LEVEL SECURITY;
ALTER TABLE processed_messages ENABLE ROW LEVEL SECURITY;


