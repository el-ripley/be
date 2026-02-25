-- ElRipley Database Schema - Suggest Response Domain
-- Purpose: Store configuration and history for AI-powered response suggestions
-- Philosophy: APPEND-ONLY for history (full reproducibility)
-- Database: PostgreSQL
-- ================================================================
-- SUGGEST RESPONSE TABLES
-- ================================================================

-- User-level agent configuration for suggest response
-- One record per user: stores agent settings and auto-suggest preferences
-- Note: Webhook auto-trigger settings are stored per page_admin in page_admin_suggest_config
CREATE TABLE suggest_response_agent (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id VARCHAR(36) NOT NULL UNIQUE REFERENCES users(id) ON DELETE CASCADE,
    
    -- Agent settings (same structure as openai_conversation.settings)
    settings JSONB DEFAULT '{"model": "gpt-5.2", "reasoning": "low", "verbosity": "low"}'::jsonb,
    
    -- Whether user allows automatic suggest response via API
    -- If true, agent can automatically generate suggestions when triggered via API with auto=true
    -- If false, suggestions are only generated when manually triggered by user
    allow_auto_suggest BOOLEAN NOT NULL DEFAULT FALSE,
    
    -- Number of suggest responses to generate per request (for API triggers)
    -- Note: webhook triggers always generate exactly 1 suggestion (hardcoded)
    num_suggest_response INTEGER NOT NULL DEFAULT 3,
    
    -- Timestamps
    created_at BIGINT NOT NULL DEFAULT EXTRACT(EPOCH FROM NOW())::BIGINT * 1000,
    updated_at BIGINT NOT NULL DEFAULT EXTRACT(EPOCH FROM NOW())::BIGINT * 1000
);


-- Page-level webhook auto-trigger configuration for suggest response
-- One record per page_admin: stores webhook automation settings for each admin-page combination
-- This allows different admins of the same page to have different automation preferences
CREATE TABLE page_admin_suggest_config (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    
    -- Reference to the page admin (links user to specific page)
    -- Each admin can have different settings per page they manage
    page_admin_id VARCHAR(36) NOT NULL UNIQUE REFERENCES facebook_page_admins(id) ON DELETE CASCADE,
    
    -- Agent settings (same structure as suggest_response_agent.settings)
    settings JSONB DEFAULT '{"model": "gpt-5.2", "reasoning": "low", "verbosity": "low"}'::jsonb,

    -- ================================================================
    -- WEBHOOK AUTO-TRIGGER SETTINGS
    -- ================================================================
    -- These settings control automatic agent triggering when Facebook webhooks arrive
    -- Priority: auto_webhook_graph_api > auto_webhook_suggest
    -- If both are true, auto_webhook_graph_api takes precedence
    
    -- Auto-trigger suggest when webhook arrives (suggest only, no auto-reply)
    -- Requires this admin to be online to receive the suggestion via socket
    -- If admin not online, the trigger is skipped for this admin (no compute wasted)
    auto_webhook_suggest BOOLEAN NOT NULL DEFAULT FALSE,
    
    -- Auto-trigger suggest AND send reply via Graph API when webhook arrives
    -- Does NOT require admin online - will execute and send reply automatically
    -- Result still streamed to socket if admin is online
    -- WARNING: This will automatically reply to customers on behalf of the page
    auto_webhook_graph_api BOOLEAN NOT NULL DEFAULT FALSE,
    
    -- Debounce delay for webhook triggers (seconds). 0 = no debounce, immediate trigger.
    -- When > 0, agent triggers only after conversation is silent for this many seconds.
    webhook_delay_seconds INTEGER NOT NULL DEFAULT 5,
    
    -- Timestamps
    created_at BIGINT NOT NULL DEFAULT EXTRACT(EPOCH FROM NOW())::BIGINT * 1000,
    updated_at BIGINT NOT NULL DEFAULT EXTRACT(EPOCH FROM NOW())::BIGINT * 1000
);


-- History of suggest response agent executions
-- Fully immutable: captures complete context at the moment of suggestion
-- Allows perfect reproduction of what the agent saw when generating suggestions
CREATE TABLE suggest_response_history (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    
    -- User who triggered this suggestion
    user_id VARCHAR(36) NOT NULL,
    
    -- Page where suggestion was created (for quick filtering)
    fan_page_id VARCHAR(255) NOT NULL,
    
    -- Which type of conversation this suggestion was for
    conversation_type VARCHAR(20) NOT NULL CHECK (conversation_type IN ('messages', 'comments')),
    
    -- Reference to the specific conversation (mutually exclusive based on type)
    -- For messages: references facebook_conversation_messages.id
    facebook_conversation_messages_id VARCHAR(255),
    -- For comments: references facebook_conversation_comments.id  
    facebook_conversation_comments_id UUID,
    
    -- Snapshot of conversation state at time of suggestion
    -- For messages: latest_message_id from facebook_conversation_messages
    -- For comments: latest_comment_id from facebook_conversation_comments
    latest_item_id VARCHAR(255) NOT NULL,
    
    -- Snapshot of the latest item's Facebook timestamp
    -- For messages: latest_message_facebook_time (BIGINT milliseconds)
    -- For comments: latest_comment_facebook_time (INTEGER seconds)
    latest_item_facebook_time BIGINT NOT NULL,
    
    -- Reference to the page prompt used (nullable: can suggest without custom page prompt)
    -- If NULL, uses default/system prompt
    page_prompt_id UUID,
    
    -- Reference to the page-scope user prompt used (nullable: can suggest without custom user prompt)
    -- Only applicable for messages (not comments)
    -- If NULL, uses default/system prompt
    page_scope_user_prompt_id UUID,
    
    -- The suggestions generated by the agent
    -- Format: [{"text": "suggestion 1", "confidence": 0.9}, ...]
    suggestions JSONB NOT NULL DEFAULT '[]'::jsonb,
    
    -- Number of suggestions generated
    suggestion_count INTEGER NOT NULL DEFAULT 0,
    
    -- Reference to agent_response for technical details (model, tokens, latency, cost, status, error)
    -- All technical metrics are stored in agent_response table
    agent_response_id UUID NOT NULL,
    
    -- Trigger type: How this suggestion was triggered
    -- 'user' = Manually triggered by user via API
    -- 'auto' = Automatically triggered via API (auto=true parameter)
    -- 'webhook_suggest' = Triggered by Facebook webhook, suggest only (no auto-reply)
    -- 'webhook_auto_reply' = Triggered by Facebook webhook, auto-reply via Graph API
    -- 'general_agent' = Triggered by general agent tool (agent calls suggest_response)
    trigger_type VARCHAR(30) NOT NULL CHECK (trigger_type IN ('user', 'auto', 'webhook_suggest', 'webhook_auto_reply', 'general_agent')),
    
    -- Which suggestion was selected/used by the user (0-based index)
    -- NULL = No suggestion was selected yet
    selected_suggestion_index INTEGER,
    
    -- User reaction to the suggestions (for the entire record, not individual suggestions)
    -- 'like' = User liked the suggestions
    -- 'dislike' = User disliked the suggestions
    -- NULL = No reaction yet
    reaction VARCHAR(20) CHECK (reaction IN ('like', 'dislike')),
    
    -- Immutable timestamp
    created_at BIGINT NOT NULL DEFAULT EXTRACT(EPOCH FROM NOW())::BIGINT * 1000,
    
    -- Timestamp when selected_suggestion_index or reaction was updated
    updated_at BIGINT NOT NULL DEFAULT EXTRACT(EPOCH FROM NOW())::BIGINT * 1000,
    
    -- Foreign keys
    FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE,
    FOREIGN KEY (fan_page_id) REFERENCES fan_pages(id) ON DELETE CASCADE,
    FOREIGN KEY (facebook_conversation_messages_id) REFERENCES facebook_conversation_messages(id) ON DELETE SET NULL,
    FOREIGN KEY (facebook_conversation_comments_id) REFERENCES facebook_conversation_comments(id) ON DELETE SET NULL,
    FOREIGN KEY (page_prompt_id) REFERENCES page_memory(id) ON DELETE RESTRICT,
    FOREIGN KEY (page_scope_user_prompt_id) REFERENCES page_scope_user_memory(id) ON DELETE RESTRICT,
    FOREIGN KEY (agent_response_id) REFERENCES agent_response(id) ON DELETE RESTRICT,
    
    -- Ensure conversation reference matches type
    CONSTRAINT chk_conversation_reference CHECK (
        (conversation_type = 'messages' AND facebook_conversation_messages_id IS NOT NULL AND facebook_conversation_comments_id IS NULL)
        OR
        (conversation_type = 'comments' AND facebook_conversation_comments_id IS NOT NULL AND facebook_conversation_messages_id IS NULL)
    ),
    
    -- Page-scope user prompt only allowed for messages
    CONSTRAINT chk_page_scope_user_prompt_type CHECK (
        conversation_type = 'messages' OR page_scope_user_prompt_id IS NULL
    )
);


-- Message items generated during suggest response agent execution
-- Stores reasoning, tool calls, and tool outputs for each agent run
-- Allows users to view the agent's "thinking process" and intermediate steps
-- Similar to openai_message but standalone (not linked to openai_conversation)
CREATE TABLE suggest_response_message (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    
    -- Link to the history record (one history can have many messages)
    history_id UUID NOT NULL REFERENCES suggest_response_history(id) ON DELETE CASCADE,
    
    -- Ordering within this agent run (0-based, ascending)
    sequence_number INTEGER NOT NULL,
    
    -- Message role and type (same convention as openai_message)
    -- role: 'assistant', 'tool'
    -- type: 'reasoning', 'function_call', 'function_call_output', 'text'
    role VARCHAR(50) NOT NULL,
    type VARCHAR(50) NOT NULL,
    
    -- Content fields (based on type)
    -- For type='text': plain text content
    -- For type='reasoning': reasoning/thinking content
    content JSONB,
    metadata JSONB, -- Optional metadata for tagging message source or flags
    
    -- Reasoning summary (for type='reasoning')
    -- Contains structured summary of agent's thinking
    reasoning_summary JSONB,
    
    -- Function call fields (for type='function_call' and 'function_call_output')
    call_id VARCHAR(255),           -- Links function_call to function_call_output
    function_name VARCHAR(255),      -- Name of the function called
    function_arguments JSONB,        -- Arguments passed to the function (for function_call)
    function_output JSONB,           -- Output from the function (for function_call_output)
    
    -- For web_search_call
    web_search_action JSONB, -- Web search action details (type, query, sources) - nullable

    -- Status tracking
    status VARCHAR(50),              -- 'completed', 'failed', 'in_progress'
    
    -- Which step produced this message ('playbook_retrieval' or 'response_generation')
    step VARCHAR(50) DEFAULT 'response_generation',
    
    -- Timestamp
    created_at BIGINT NOT NULL DEFAULT EXTRACT(EPOCH FROM NOW())::BIGINT * 1000,
    
    -- Ensure unique sequence per history
    UNIQUE(history_id, sequence_number)
);
