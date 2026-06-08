-- Migration: ensure tier column exists; migrate is_pro if present
-- Safe to run on fresh installs (new schema) and old installs (is_pro boolean)

ALTER TABLE expense_users
    ADD COLUMN IF NOT EXISTS tier TEXT DEFAULT 'free';

DO $$
BEGIN
    IF EXISTS (
        SELECT 1
          FROM information_schema.columns
         WHERE table_schema = 'public'
           AND table_name   = 'expense_users'
           AND column_name  = 'is_pro'
    ) THEN
        UPDATE expense_users
           SET tier = 'pro'
         WHERE is_pro = TRUE
           AND (tier IS NULL OR tier = 'free');

        ALTER TABLE expense_users DROP COLUMN is_pro;
    END IF;
END $$;

UPDATE expense_users
   SET tier = 'free'
 WHERE tier IS NULL;
