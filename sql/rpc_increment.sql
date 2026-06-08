-- ============================================================
-- Supabase RPC: increment_entry_count
-- Run this in the Supabase SQL editor after schema.sql
-- ============================================================

-- Atomically increment entry_count and return the new value.
-- Using a database function prevents the read-modify-write race condition
-- that would occur if we did SELECT then UPDATE in application code.

CREATE OR REPLACE FUNCTION increment_entry_count(user_uuid UUID)
RETURNS INT
LANGUAGE plpgsql
SECURITY DEFINER  -- runs as the function owner, bypasses RLS
AS $$
DECLARE
    new_count INT;
BEGIN
    UPDATE expense_users
       SET entry_count = entry_count + 1
     WHERE id = user_uuid
    RETURNING entry_count INTO new_count;

    RETURN COALESCE(new_count, 0);
END;
$$;
