-- Migration: encrypted phone for monthly WhatsApp recaps (cron on 1st of month)
-- Populated automatically when users message the bot.

ALTER TABLE expense_users
    ADD COLUMN IF NOT EXISTS notify_phone_enc TEXT;
