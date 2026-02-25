-- ElRipley Database Schema - OpenAI Integration Domain
-- Generated from SQLAlchemy models: facebook.py and user.py
-- Database: PostgreSQL
-- ================================================================
-- OPENAI INTEGRATION TABLES
-- ================================================================
-- OpenAI conversations table - represents conversation threads
-- Also used for subagent contexts (is_subagent=true) with isolated message history
CREATE TABLE openai_conversation (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id VARCHAR(36) NOT NULL REFERENCES users(id),
    title VARCHAR(500), -- Optional conversation title
    current_branch_id UUID, -- Current active branch (constraint added later)
    message_sequence_counter INTEGER NOT NULL DEFAULT 0, -- Counter for message sequence numbers (auto-increment on each new message)
    oldest_message_id UUID, -- Reference to the oldest message in conversation
    settings JSONB DEFAULT '{"model": "gpt-5.2", "reasoning": "low", "verbosity": "low"}'::jsonb, -- Conversation model settings
    
    -- Subagent support: conversations can be subagent contexts with isolated message history
    parent_conversation_id UUID REFERENCES openai_conversation(id) ON DELETE CASCADE, -- Parent conversation (for subagents)
    parent_agent_response_id UUID, -- Agent response that spawned this subagent (FK added after agent_response table)
    subagent_type VARCHAR(64), -- 'explore' (only type for now)
    is_subagent BOOLEAN DEFAULT FALSE, -- TRUE if this is a subagent context
    task_call_id VARCHAR(255), -- call_id of task function_call in parent conversation (for FE to link UI)
    
    created_at BIGINT NOT NULL DEFAULT EXTRACT(EPOCH FROM NOW())::BIGINT * 1000,
    updated_at BIGINT NOT NULL DEFAULT EXTRACT(EPOCH FROM NOW())::BIGINT * 1000
);

-- OpenAI messages table - supports all Response API message types
-- Types: message, reasoning, function_call, function_call_output
CREATE TABLE openai_message (
    -- Primary identification
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    conversation_id UUID NOT NULL REFERENCES openai_conversation(id) ON DELETE CASCADE,
    
    -- Sequence number for ordering messages across all branches
    -- Example: Branch 1: a-0, b-1, c-2; Branch 2 from b: a-0, d-3, e-4
    sequence_number INTEGER NOT NULL, -- Global sequence within conversation for ordering
    
    -- Message type determines which fields are relevant
    role VARCHAR(50) NOT NULL,  -- Recommended roles: 'system', 'developer', 'user', 'assistant', 'tool'
    type VARCHAR(50) NOT NULL, -- Recommended types: 'message', 'reasoning', 'function_call', 'function_call_output', 'user_input'
   
    content JSONB, -- Content fields (for type='message' or 'user_input') - nullable
    metadata JSONB, -- Optional metadata for tagging message source or flags
    
    reasoning_summary JSONB, -- Reasoning fields (for type='reasoning') - nullable
    
    -- For function_call and function_call_output
    call_id VARCHAR(255), -- Links function_call to function_call_output - nullable
    function_name VARCHAR(255), -- nullable
    function_arguments JSONB, -- Function arguments (for type='function_call') - nullable
    function_output JSONB, -- Function output (for type='function_call_output') - nullable
    
    -- For web_search_call
    web_search_action JSONB, -- Web search action details (type, query, sources) - nullable
    
    -- Status - nullable
    status VARCHAR(50),
    
    -- Timestamps
    created_at BIGINT NOT NULL DEFAULT EXTRACT(EPOCH FROM NOW())::BIGINT * 1000,
    updated_at BIGINT NOT NULL DEFAULT EXTRACT(EPOCH FROM NOW())::BIGINT * 1000,
    
    -- Ensure unique sequence per conversation
    UNIQUE(conversation_id, sequence_number)
);

-- OpenAI conversation branch table
CREATE TABLE openai_conversation_branch (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    conversation_id UUID NOT NULL REFERENCES openai_conversation(id) ON DELETE CASCADE,
    created_from_message_id UUID REFERENCES openai_message(id) ON DELETE SET NULL, -- Message tạo branch từ đó
    created_from_branch_id UUID REFERENCES openai_conversation_branch(id) ON DELETE SET NULL, -- Branch cũ chứa message tạo branch
    message_ids UUID[] NOT NULL DEFAULT '{}', -- Array chứa message IDs theo thứ tự
    branch_name VARCHAR(255), -- Optional branch name
    is_active BOOLEAN DEFAULT FALSE, -- Current active branch
    created_at BIGINT NOT NULL DEFAULT EXTRACT(EPOCH FROM NOW())::BIGINT * 1000,
    updated_at BIGINT NOT NULL DEFAULT EXTRACT(EPOCH FROM NOW())::BIGINT * 1000
);

-- Add foreign key constraint for current_branch_id
ALTER TABLE openai_conversation 
ADD CONSTRAINT fk_openai_conversation_current_branch_id 
FOREIGN KEY (current_branch_id) REFERENCES openai_conversation_branch(id) ON DELETE SET NULL;

-- OpenAI responses table - tracks API responses for cost monitoring
CREATE TABLE openai_response (
    -- Primary identification
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    response_id VARCHAR(255) UNIQUE NOT NULL, -- OpenAI response ID
    
    -- User and conversation context
    user_id VARCHAR(36) NOT NULL REFERENCES users(id),
    conversation_id UUID REFERENCES openai_conversation(id) ON DELETE SET NULL,
    
    -- Branch tracking
    branch_id UUID REFERENCES openai_conversation_branch(id) ON DELETE SET NULL,
    
    -- API Key tracking - REMOVED: BYOK support has been removed
    -- All requests now use system API key from environment settings
    -- Note: api_key_type and user_api_key_id columns can be dropped via migration script
    
    -- Model & timing
    model VARCHAR(100) NOT NULL, -- e.g., 'gpt-5-mini', 'gpt-4o'
    created_at BIGINT NOT NULL, -- OpenAI timestamp (milliseconds)
    latency_ms INTEGER, -- Time taken for API call
    
    -- Token usage (from response.usage)
    input_tokens INTEGER NOT NULL DEFAULT 0,
    cached_tokens INTEGER DEFAULT 0, -- For prompt caching
    output_tokens INTEGER NOT NULL DEFAULT 0,
    reasoning_tokens INTEGER DEFAULT 0, -- For reasoning models (o1)
    total_tokens INTEGER NOT NULL DEFAULT 0,
    
    -- Cost tracking (USD)
    input_cost DECIMAL(10, 6) DEFAULT 0,
    output_cost DECIMAL(10, 6) DEFAULT 0,
    total_cost DECIMAL(10, 6) DEFAULT 0,
    
    -- Request/Response data (JSONB for flexibility)
    input JSONB, -- Full input messages/prompts
    output JSONB, -- Full response output
    tools JSONB, -- Tools used in this call
    metadata JSONB, -- Additional context
    
    -- Status tracking
    status VARCHAR(50) NOT NULL, -- 'completed', 'failed', 'in_progress'
    error JSONB, -- Error details if failed
    
    -- Timestamps
    logged_at BIGINT NOT NULL DEFAULT EXTRACT(EPOCH FROM NOW())::BIGINT * 1000
);

-- OpenAI branch message mapping table
CREATE TABLE openai_branch_message_mapping (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    message_id UUID NOT NULL REFERENCES openai_message(id) ON DELETE CASCADE,
    branch_id UUID NOT NULL REFERENCES openai_conversation_branch(id) ON DELETE CASCADE,
    is_modified BOOLEAN DEFAULT FALSE, -- Message có bị modify trong branch này không
    modified_content JSONB, -- Nội dung đã modify (nếu có)
    modified_reasoning_summary JSONB, -- Reasoning summary đã modify (nếu có)
    modified_function_arguments JSONB, -- Function arguments đã modify (nếu có)
    modified_function_output JSONB, -- Function output đã modify (nếu có)
    is_hidden BOOLEAN DEFAULT FALSE, -- Message có bị ẩn trong branch này không
    created_at BIGINT NOT NULL DEFAULT EXTRACT(EPOCH FROM NOW())::BIGINT * 1000,
    UNIQUE(message_id, branch_id)
);

-- Agent response table - tracks each time the agent is triggered
-- conversation_id and branch_id can be NULL for standalone agents (e.g., suggest_response_agent)
-- NULL = oneshot/standalone agent execution outside conversation context
-- NOT NULL = agent execution within conversation context (e.g., conversation_agent)
-- parent_agent_response_id links sub-agents (summarization, media_description) to their parent agent_response
CREATE TABLE agent_response (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id VARCHAR(36) NOT NULL REFERENCES users(id),
    conversation_id UUID REFERENCES openai_conversation(id) ON DELETE SET NULL,
    branch_id UUID REFERENCES openai_conversation_branch(id) ON DELETE SET NULL,
    agent_type VARCHAR(64) NOT NULL DEFAULT 'general_agent',
    message_ids UUID[] NOT NULL DEFAULT '{}',
    
    -- API Key tracking - REMOVED: BYOK support has been removed
    -- All requests now use system API key from environment settings
    -- Note: api_key_type and user_api_key_id columns can be dropped via migration script
    
    parent_agent_response_id UUID REFERENCES agent_response(id) ON DELETE CASCADE,
    
    model VARCHAR(100),
    total_input_tokens INTEGER DEFAULT 0,
    total_output_tokens INTEGER DEFAULT 0,
    total_tokens INTEGER DEFAULT 0,
    total_latency_ms INTEGER,
    total_cost DECIMAL(10, 6) DEFAULT 0,
    call_count INTEGER DEFAULT 0,
    status VARCHAR(50) NOT NULL DEFAULT 'in_progress',
    error JSONB,
    
    created_at BIGINT NOT NULL DEFAULT EXTRACT(EPOCH FROM NOW())::BIGINT * 1000,
    updated_at BIGINT NOT NULL DEFAULT EXTRACT(EPOCH FROM NOW())::BIGINT * 1000
);

-- Add agent_response_id column to openai_response table
ALTER TABLE openai_response 
ADD COLUMN agent_response_id UUID REFERENCES agent_response(id) ON DELETE SET NULL;

-- Add foreign key constraint for parent_agent_response_id in openai_conversation (for subagents)
ALTER TABLE openai_conversation 
ADD CONSTRAINT fk_openai_conversation_parent_agent_response_id 
FOREIGN KEY (parent_agent_response_id) REFERENCES agent_response(id) ON DELETE SET NULL;

