-- Migration: 30-day Pro trial for new users + reminder tracking
-- Safe to run on existing deployments.

ALTER TABLE expense_users
    ADD COLUMN IF NOT EXISTS trial_ends_at TIMESTAMPTZ;

ALTER TABLE expense_users
    ADD COLUMN IF NOT EXISTS is_paid BOOLEAN DEFAULT FALSE;

ALTER TABLE expense_users
    ADD COLUMN IF NOT EXISTS trial_reminders_sent INT DEFAULT 0;

UPDATE expense_users SET is_paid = FALSE WHERE is_paid IS NULL;
UPDATE expense_users SET trial_reminders_sent = 0 WHERE trial_reminders_sent IS NULL;
