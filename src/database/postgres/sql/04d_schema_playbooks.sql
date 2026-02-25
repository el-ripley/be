-- ElRipley Database Schema - Playbooks Domain
-- Purpose: Situational coaching/guidance for suggest_response_agent
-- Unlike page_memory (static knowledge), playbooks are dynamic behavioral guidance
-- that gets injected only when the situation matches.
--
-- Architecture:
--   page_playbooks (content, owner-scoped, not tied to any specific page)
--   page_playbook_assignments (links playbooks to specific page_admin + conversation_type)
--
-- One playbook can be assigned to multiple (page_admin, conversation_type) pairs.
-- Assignments are scoped to page_admin (not page) so two admins of the same page
-- have independent playbook sets.
--
-- Database: PostgreSQL
-- ================================================================

-- ================================================================
-- PLAYBOOK CONTENT TABLE
-- ================================================================
-- Stores the actual playbook content: title, situation, guidance.
-- Owned by a user (owner_user_id) and reusable across multiple pages.
-- Not tied to any specific page — assignment is handled by page_playbook_assignments.

CREATE TABLE page_playbooks (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),

    -- Owner: the user who owns this playbook (created via general_agent on their behalf)
    owner_user_id VARCHAR(36) NOT NULL,

    -- Identity & matching
    title VARCHAR(500) NOT NULL,          -- Human-readable label, e.g. "Xử lý khách hỏi giá"
    situation TEXT NOT NULL,              -- WHEN to use this playbook (embedded for semantic search)
    content TEXT NOT NULL,                -- HOW to handle the situation (guidance + examples, injected into context)

    -- Categorization
    tags TEXT[],                          -- Optional tags for SQL filtering, e.g. {'pricing', 'sales'}

    -- Embedding: which model generated vectors (for re-embed on model upgrade)
    embedding_model VARCHAR(100),         -- e.g. 'text-embedding-3-large', NULL = not yet embedded

    -- Metadata
    created_by_type VARCHAR(20) NOT NULL DEFAULT 'agent'
        CHECK (created_by_type IN ('user', 'agent')),

    created_at BIGINT NOT NULL DEFAULT EXTRACT(EPOCH FROM NOW())::BIGINT * 1000,
    updated_at BIGINT NOT NULL DEFAULT EXTRACT(EPOCH FROM NOW())::BIGINT * 1000,
    deleted_at BIGINT,                   -- NULL = active, non-null = soft deleted (ms timestamp)

    FOREIGN KEY (owner_user_id) REFERENCES users(id) ON DELETE CASCADE
);

-- ================================================================
-- PLAYBOOK ASSIGNMENT TABLE
-- ================================================================
-- Links a playbook to a specific page admin + conversation type.
-- Scoped to page_admin (not page) to prevent cross-admin contamination:
-- two admins of the same page have independent playbook assignments.

CREATE TABLE page_playbook_assignments (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),

    playbook_id UUID NOT NULL,
    page_admin_id VARCHAR(36) NOT NULL,   -- FK to facebook_page_admins.id (encodes user+page pair)
    conversation_type VARCHAR(20) NOT NULL
        CHECK (conversation_type IN ('messages', 'comments')),

    created_at BIGINT NOT NULL DEFAULT EXTRACT(EPOCH FROM NOW())::BIGINT * 1000,
    deleted_at BIGINT,                   -- NULL = active, non-null = soft deleted (ms timestamp)

    FOREIGN KEY (playbook_id) REFERENCES page_playbooks(id) ON DELETE CASCADE,
    FOREIGN KEY (page_admin_id) REFERENCES facebook_page_admins(id) ON DELETE CASCADE,

    -- One playbook can only be assigned once per (page_admin, conversation_type)
    UNIQUE (playbook_id, page_admin_id, conversation_type)
);
