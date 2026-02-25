-- ElRipley Database Schema - Agent Communication Domain
-- Purpose: Enable communication between suggest_response_agent and external (user/general_agent)
-- Includes: Conversation blocks and escalations
-- Database: PostgreSQL
-- ================================================================
-- AGENT COMMUNICATION TABLES
-- ================================================================

-- Blocks agent from being triggered on specific conversations
-- Used to prevent abuse/token burn from problematic conversations
CREATE TABLE conversation_agent_blocks (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    
    -- Which conversation is blocked
    conversation_type VARCHAR(20) NOT NULL CHECK (conversation_type IN ('messages', 'comments')),
    facebook_conversation_messages_id VARCHAR(255),
    facebook_conversation_comments_id UUID,
    fan_page_id VARCHAR(255) NOT NULL,
    
    -- Who blocked and why
    blocked_by VARCHAR(50) NOT NULL CHECK (blocked_by IN ('suggest_response_agent', 'general_agent', 'user')),
    reason TEXT,
    
    -- Active status (soft delete pattern)
    is_active BOOLEAN NOT NULL DEFAULT TRUE,
    
    -- Timestamps
    created_at BIGINT NOT NULL DEFAULT EXTRACT(EPOCH FROM NOW())::BIGINT * 1000,
    updated_at BIGINT NOT NULL DEFAULT EXTRACT(EPOCH FROM NOW())::BIGINT * 1000,
    
    -- Foreign keys
    FOREIGN KEY (fan_page_id) REFERENCES fan_pages(id) ON DELETE CASCADE,
    FOREIGN KEY (facebook_conversation_messages_id) REFERENCES facebook_conversation_messages(id) ON DELETE CASCADE,
    FOREIGN KEY (facebook_conversation_comments_id) REFERENCES facebook_conversation_comments(id) ON DELETE CASCADE,
    
    -- Ensure exactly one conversation reference based on type
    CONSTRAINT chk_agent_block_conversation_ref CHECK (
        (conversation_type = 'messages' AND facebook_conversation_messages_id IS NOT NULL AND facebook_conversation_comments_id IS NULL)
        OR
        (conversation_type = 'comments' AND facebook_conversation_comments_id IS NOT NULL AND facebook_conversation_messages_id IS NULL)
    )
);


-- ================================================================
-- Two-way communication channel between suggest_response_agent and user/general_agent
-- Design: Escalation = mini conversation thread with messages from both sides
-- 
-- Table split rationale (RLS field-level enforcement without triggers):
--   agent_escalations       → thread header (metadata, status)
--   agent_escalation_messages → messages back and forth (sender_type enforced by RLS)
--
-- Status model: open/closed (like a notebook on desk vs in cabinet)
--   open   = hot, actively needs attention, loaded into agent context
--   closed = cold, resolved or stale, NOT loaded into context
--   Both sides can close: agent closes after reading response, responder closes if not needed
-- ================================================================

CREATE TABLE agent_escalations (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    
    -- Context: Which FB conversation this escalation is about
    conversation_type VARCHAR(20) NOT NULL CHECK (conversation_type IN ('messages', 'comments')),
    facebook_conversation_messages_id VARCHAR(255),
    facebook_conversation_comments_id UUID,
    fan_page_id VARCHAR(255) NOT NULL,
    owner_user_id VARCHAR(36) NOT NULL,
    
    -- Thread metadata
    created_by VARCHAR(50) NOT NULL DEFAULT 'suggest_response_agent' CHECK (created_by IN ('suggest_response_agent', 'general_agent', 'user')),
    subject VARCHAR(500) NOT NULL,       -- brief summary for dashboard display
    priority VARCHAR(20) NOT NULL DEFAULT 'normal' CHECK (priority IN ('low', 'normal', 'high', 'urgent')),
    
    -- Status: open = hot (on desk), closed = cold (in cabinet)
    status VARCHAR(20) NOT NULL DEFAULT 'open' CHECK (status IN ('open', 'closed')),
    
    -- Timestamps
    created_at BIGINT NOT NULL DEFAULT EXTRACT(EPOCH FROM NOW())::BIGINT * 1000,
    updated_at BIGINT NOT NULL DEFAULT EXTRACT(EPOCH FROM NOW())::BIGINT * 1000,
    
    -- Optional: Link to suggest_response_history for audit trail
    suggest_response_history_id UUID,
    
    -- Foreign keys
    FOREIGN KEY (fan_page_id) REFERENCES fan_pages(id) ON DELETE CASCADE,
    FOREIGN KEY (owner_user_id) REFERENCES users(id) ON DELETE CASCADE,
    FOREIGN KEY (facebook_conversation_messages_id) REFERENCES facebook_conversation_messages(id) ON DELETE CASCADE,
    FOREIGN KEY (facebook_conversation_comments_id) REFERENCES facebook_conversation_comments(id) ON DELETE CASCADE,
    FOREIGN KEY (suggest_response_history_id) REFERENCES suggest_response_history(id) ON DELETE SET NULL,
    
    -- Ensure exactly one conversation reference based on type
    CONSTRAINT chk_escalation_conversation_ref CHECK (
        (conversation_type = 'messages' AND facebook_conversation_messages_id IS NOT NULL AND facebook_conversation_comments_id IS NULL)
        OR
        (conversation_type = 'comments' AND facebook_conversation_comments_id IS NOT NULL AND facebook_conversation_messages_id IS NULL)
    )
);


-- Messages within an escalation thread
-- Enables two-way communication: each side can only INSERT with its own sender_type (RLS enforced)
CREATE TABLE agent_escalation_messages (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    escalation_id UUID NOT NULL REFERENCES agent_escalations(id) ON DELETE CASCADE,
    
    -- Who sent this message (RLS enforces: each role can only INSERT with its own sender_type)
    sender_type VARCHAR(50) NOT NULL CHECK (sender_type IN ('suggest_response_agent', 'general_agent', 'user')),
    
    -- Content
    content TEXT NOT NULL,
    context_snapshot JSONB,              -- optional: snapshot of relevant data at time of message
    
    -- Timestamp
    created_at BIGINT NOT NULL DEFAULT EXTRACT(EPOCH FROM NOW())::BIGINT * 1000
);
