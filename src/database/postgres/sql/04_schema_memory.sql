-- ElRipley Database Schema - Memory Domain
-- Purpose: Store memory/prompts for agents (page-level, user-level, global)
-- Philosophy: APPEND-ONLY (no updates, no deletes) for full reproducibility
-- Database: PostgreSQL
-- ================================================================
-- MEMORY TABLES
-- ================================================================

-- Page-level memory for agents (suggest_response, auto_reply, etc.)
-- Can be used for either messages or comments conversations
-- Append-only: When prompt changes, create new record and deactivate old one
CREATE TABLE page_memory (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    fan_page_id VARCHAR(255) NOT NULL,
    
    -- Owner: User who owns this page in the app (user sử dụng app)
    -- If created_by_type = 'user', this is the user who created it
    -- If created_by_type = 'agent', this is the owner whose agent created it
    owner_user_id VARCHAR(36) NOT NULL,
    
    -- Prompt applies to which conversation type
    -- 'messages' = facebook_conversation_messages only
    -- 'comments' = facebook_conversation_comments only
    prompt_type VARCHAR(20) NOT NULL CHECK (prompt_type IN ('messages', 'comments')),
    
    -- Note: content field removed - memory is now stored in memory_blocks
    
    -- Creator tracking: Who created/modified this prompt
    -- 'user' = Created by owner (owner_user_id) manually
    -- 'agent' = Created by AI agent of owner (owner_user_id) automatically
    created_by_type VARCHAR(20) NOT NULL CHECK (created_by_type IN ('user', 'agent')),
    
    -- Only one active prompt per (fan_page_id, prompt_type) combination
    -- When updating, create new record with is_active=true and set old one to false
    is_active BOOLEAN NOT NULL DEFAULT TRUE,
    
    -- Immutable timestamp (no updated_at since we don't update)
    created_at BIGINT NOT NULL DEFAULT EXTRACT(EPOCH FROM NOW())::BIGINT * 1000,
    
    FOREIGN KEY (fan_page_id) REFERENCES fan_pages(id) ON DELETE CASCADE,
    FOREIGN KEY (owner_user_id) REFERENCES users(id) ON DELETE CASCADE
);


-- Page-scope user-level memory for agents (suggest_response, auto_reply, etc.)
-- Only applicable to messages (not comments) as per requirement
-- Append-only: When prompt changes, create new record and deactivate old one
CREATE TABLE page_scope_user_memory (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    fan_page_id VARCHAR(255) NOT NULL,
    facebook_page_scope_user_id VARCHAR(255) NOT NULL, -- PSID of the user
    
    -- Owner: User who owns this page in the app (user sử dụng app)
    -- If created_by_type = 'user', this is the user who created it
    -- If created_by_type = 'agent', this is the owner whose agent created it
    owner_user_id VARCHAR(36) NOT NULL,
    
    -- Note: content field removed - memory is now stored in memory_blocks
    
    -- Creator tracking: Who created/modified this prompt
    -- 'user' = Created by owner (owner_user_id) manually
    -- 'agent' = Created by AI agent of owner (owner_user_id) automatically
    created_by_type VARCHAR(20) NOT NULL CHECK (created_by_type IN ('user', 'agent')),
    
    -- Only one active prompt per (fan_page_id, page_scope_user) combination
    is_active BOOLEAN NOT NULL DEFAULT TRUE,
    
    -- Immutable timestamp
    created_at BIGINT NOT NULL DEFAULT EXTRACT(EPOCH FROM NOW())::BIGINT * 1000,
    
    FOREIGN KEY (fan_page_id) REFERENCES fan_pages(id) ON DELETE CASCADE,
    FOREIGN KEY (facebook_page_scope_user_id) REFERENCES facebook_page_scope_users(id) ON DELETE CASCADE,
    FOREIGN KEY (owner_user_id) REFERENCES users(id) ON DELETE CASCADE
);


-- Global user-level memory for general agent
-- Similar to "ripley.md" - contains extracted information from conversations
-- Agent can read/write this memory via SQL queries
-- System will inject this memory into agent's system prompt
CREATE TABLE user_memory (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    
    -- Owner: User who owns this memory (user sử dụng app)
    -- If created_by_type = 'user', this is the user who created it
    -- If created_by_type = 'agent', this is the owner whose agent created it
    owner_user_id VARCHAR(36) NOT NULL,
    
    -- Creator tracking: Who created/modified this memory
    -- 'user' = Created by owner (owner_user_id) manually
    -- 'agent' = Created by AI agent of owner (owner_user_id) automatically
    created_by_type VARCHAR(20) NOT NULL CHECK (created_by_type IN ('user', 'agent')),
    
    -- Only one active memory per owner_user_id
    -- When updating, create new record with is_active=true and set old one to false
    is_active BOOLEAN NOT NULL DEFAULT TRUE,
    
    -- Immutable timestamp (no updated_at since we don't update)
    created_at BIGINT NOT NULL DEFAULT EXTRACT(EPOCH FROM NOW())::BIGINT * 1000,
    
    FOREIGN KEY (owner_user_id) REFERENCES users(id) ON DELETE CASCADE
);


-- Memory blocks for page prompts, user prompts, and user memory
-- Each prompt container can have multiple blocks that are rendered together
CREATE TABLE memory_blocks (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    
    -- Parent reference (polymorphic)
    -- 'page_prompt' = page_memory
    -- 'user_prompt' = page_scope_user_memory
    -- 'user_memory' = user_memory (global user memory for general agent)
    prompt_type VARCHAR(30) NOT NULL CHECK (prompt_type IN ('page_prompt', 'user_prompt', 'user_memory')),
    prompt_id UUID NOT NULL,
    
    -- Block identification
    block_key VARCHAR(100) NOT NULL,  -- Stable identifier for programmatic access
    
    -- Display metadata
    title VARCHAR(255) NOT NULL,      -- Human-readable title shown in rendered prompt
    
    -- Content
    content TEXT NOT NULL,
    
    -- Ordering (lower = first)
    display_order INTEGER NOT NULL DEFAULT 0,
    
    -- Audit (append-only: use latest per block_key)
    created_at BIGINT NOT NULL DEFAULT EXTRACT(EPOCH FROM NOW())::BIGINT * 1000,
    created_by_type VARCHAR(20) NOT NULL CHECK (created_by_type IN ('user', 'agent'))
);

-- NOTE: memory_block_media table is defined in 05_schema_media.sql
-- because it references media_assets table which is defined there.
