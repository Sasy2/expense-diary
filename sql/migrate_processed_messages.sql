-- Migration: Add processed_messages table for webhook deduplication
CREATE TABLE IF NOT EXISTS processed_messages (
    message_id TEXT PRIMARY KEY,
    created_at TIMESTAMPTZ DEFAULT NOW()
);
