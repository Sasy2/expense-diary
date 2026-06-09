-- ============================================================
-- Supabase RPC: decrement_entry_count
-- Run after rpc_increment.sql (for UNDO command)
-- ============================================================

CREATE OR REPLACE FUNCTION decrement_entry_count(user_uuid UUID)
RETURNS INT
LANGUAGE plpgsql
SECURITY DEFINER
AS $$
DECLARE
    new_count INT;
BEGIN
    UPDATE expense_users
       SET entry_count = GREATEST(entry_count - 1, 0)
     WHERE id = user_uuid
    RETURNING entry_count INTO new_count;

    RETURN COALESCE(new_count, 0);
END;
$$;
