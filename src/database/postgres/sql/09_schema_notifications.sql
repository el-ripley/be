-- ElRipley Database Schema - Notifications Domain
-- Purpose: In-app notifications (e.g. escalation events) with persistence and read tracking
-- Database: PostgreSQL
-- No RLS: only application layer (el-ripley-user) accesses this table
-- ================================================================

CREATE TABLE notifications (
    id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    owner_user_id   VARCHAR(36) NOT NULL REFERENCES users(id) ON DELETE CASCADE,

    -- Notification content
    type            VARCHAR(100) NOT NULL,
    title           VARCHAR(500) NOT NULL,
    body            TEXT,

    -- Reference to source entity (e.g. agent_escalation)
    reference_type  VARCHAR(100),
    reference_id    UUID,

    -- Extra data for frontend (routing, display)
    metadata        JSONB DEFAULT '{}',

    -- Read tracking
    is_read         BOOLEAN NOT NULL DEFAULT FALSE,
    read_at         BIGINT,

    -- Timestamp
    created_at      BIGINT NOT NULL DEFAULT (EXTRACT(EPOCH FROM NOW())::BIGINT * 1000)
);
