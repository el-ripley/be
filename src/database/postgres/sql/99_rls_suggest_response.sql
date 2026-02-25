-- ================================================================
-- ROW LEVEL SECURITY (RLS) POLICIES FOR SUGGEST RESPONSE AGENT
-- ================================================================
-- This file enables RLS policies for suggest_response_reader and suggest_response_writer.
-- These roles have MINIMAL access - only 7 tables within conversation scope.
--
-- TABLES ACCESSIBLE:
-- 1. page_scope_user_memory - Customer memory for current PSID (messages only)
-- 2. memory_blocks - Memory blocks for page_scope_user_memory only
-- 3. memory_block_media - Media attachments for memory blocks
-- 4. media_assets - Media details (S3 URLs) for view_media tool
-- 5. conversation_agent_blocks - Block current conversation from agent triggers
-- 6. agent_escalations - Escalation thread headers (create + close)
-- 7. agent_escalation_messages - Messages within escalation threads
--
-- ALL OTHER DATA (messages, comments, page_memory, etc.) is pre-built
-- into the agent's context by context_builder.py using system connection.
--
-- USAGE:
-- Before executing queries, set these session variables:
--   SET app.current_user_id = 'user-uuid-here';
--   SET app.current_conversation_type = 'messages'; -- or 'comments'
--   SET app.current_conversation_id = 'conversation-id-here';
--   SET app.current_fan_page_id = 'page-id-here';
--   SET app.current_page_scope_user_id = 'psid-here'; -- only for messages type
-- ================================================================


-- ================================================================
-- HELPER FUNCTIONS FOR SUGGEST RESPONSE AGENT
-- ================================================================

-- Get the current fan_page_id
CREATE OR REPLACE FUNCTION get_sr_current_fan_page_id()
RETURNS VARCHAR(255) AS $$
BEGIN
    RETURN current_setting('app.current_fan_page_id', true);
END;
$$ LANGUAGE plpgsql SECURITY DEFINER STABLE;

-- Get the current conversation ID for messages type
CREATE OR REPLACE FUNCTION get_sr_current_messages_conversation_id()
RETURNS VARCHAR(255) AS $$
BEGIN
    IF current_setting('app.current_conversation_type', true) = 'messages' THEN
        RETURN current_setting('app.current_conversation_id', true);
    END IF;
    RETURN NULL;
END;
$$ LANGUAGE plpgsql SECURITY DEFINER STABLE;

-- Get the current conversation ID for comments type (returns UUID)
CREATE OR REPLACE FUNCTION get_sr_current_comments_conversation_id()
RETURNS UUID AS $$
BEGIN
    IF current_setting('app.current_conversation_type', true) = 'comments' THEN
        RETURN current_setting('app.current_conversation_id', true)::UUID;
    END IF;
    RETURN NULL;
END;
$$ LANGUAGE plpgsql SECURITY DEFINER STABLE;

-- Get the current page scope user ID (PSID) - only for messages
CREATE OR REPLACE FUNCTION get_sr_current_page_scope_user_id()
RETURNS VARCHAR(255) AS $$
BEGIN
    IF current_setting('app.current_conversation_type', true) = 'messages' THEN
        RETURN current_setting('app.current_page_scope_user_id', true);
    END IF;
    RETURN NULL;
END;
$$ LANGUAGE plpgsql SECURITY DEFINER STABLE;

-- Get the current owner user ID (for agent_escalations.owner_user_id DEFAULT)
CREATE OR REPLACE FUNCTION get_sr_current_owner_user_id()
RETURNS VARCHAR(36) AS $$
BEGIN
    RETURN current_setting('app.current_user_id', true);
END;
$$ LANGUAGE plpgsql SECURITY DEFINER STABLE;

-- Get the current FB conversation type ('messages' | 'comments') for DEFAULT columns
CREATE OR REPLACE FUNCTION get_sr_current_conversation_type()
RETURNS VARCHAR(20) AS $$
BEGIN
    RETURN current_setting('app.current_conversation_type', true);
END;
$$ LANGUAGE plpgsql SECURITY DEFINER STABLE;

-- Grant execute to suggest response roles (và agent_* để general_agent dùng DEFAULT khi insert user_memory)
GRANT EXECUTE ON FUNCTION get_sr_current_fan_page_id() TO suggest_response_reader, suggest_response_writer;
GRANT EXECUTE ON FUNCTION get_sr_current_messages_conversation_id() TO suggest_response_reader, suggest_response_writer;
GRANT EXECUTE ON FUNCTION get_sr_current_comments_conversation_id() TO suggest_response_reader, suggest_response_writer;
GRANT EXECUTE ON FUNCTION get_sr_current_page_scope_user_id() TO suggest_response_reader, suggest_response_writer;
GRANT EXECUTE ON FUNCTION get_sr_current_owner_user_id() TO suggest_response_reader, suggest_response_writer, agent_reader, agent_writer;
GRANT EXECUTE ON FUNCTION get_sr_current_conversation_type() TO suggest_response_reader, suggest_response_writer;

-- DEFAULT từ session: agent lock-in 1 fb_conv, không cần truyền tránh nhầm
ALTER TABLE agent_escalations
    ALTER COLUMN owner_user_id SET DEFAULT get_sr_current_owner_user_id(),
    ALTER COLUMN fan_page_id SET DEFAULT get_sr_current_fan_page_id(),
    ALTER COLUMN conversation_type SET DEFAULT get_sr_current_conversation_type(),
    ALTER COLUMN facebook_conversation_messages_id SET DEFAULT get_sr_current_messages_conversation_id(),
    ALTER COLUMN facebook_conversation_comments_id SET DEFAULT get_sr_current_comments_conversation_id();

-- agent_escalation_messages: sender_type defaults to suggest_response_agent.
-- This is safe: if agent_writer doesn't override, RLS blocks 'suggest_response_agent' → forces explicit value.
ALTER TABLE agent_escalation_messages
    ALTER COLUMN sender_type SET DEFAULT 'suggest_response_agent';

ALTER TABLE conversation_agent_blocks
    ALTER COLUMN fan_page_id SET DEFAULT get_sr_current_fan_page_id(),
    ALTER COLUMN conversation_type SET DEFAULT get_sr_current_conversation_type(),
    ALTER COLUMN facebook_conversation_messages_id SET DEFAULT get_sr_current_messages_conversation_id(),
    ALTER COLUMN facebook_conversation_comments_id SET DEFAULT get_sr_current_comments_conversation_id();

-- user_memory: owner_user_id từ session; created_by_type mặc định 'agent'
ALTER TABLE user_memory
    ALTER COLUMN owner_user_id SET DEFAULT get_sr_current_owner_user_id(),
    ALTER COLUMN created_by_type SET DEFAULT 'agent';

-- memory_blocks: suggest_response chỉ ghi user_prompt; created_by_type mặc định 'agent'
-- (general_agent khi insert user_memory block phải truyền rõ prompt_type = 'user_memory')
ALTER TABLE memory_blocks
    ALTER COLUMN prompt_type SET DEFAULT 'user_prompt',
    ALTER COLUMN created_by_type SET DEFAULT 'agent';

-- page_scope_user_memory: auto-fill from session to prevent agent from providing wrong IDs
-- Without these defaults, lower models often hallucinate IDs and get RLS rejections
ALTER TABLE page_scope_user_memory
    ALTER COLUMN fan_page_id SET DEFAULT get_sr_current_fan_page_id(),
    ALTER COLUMN facebook_page_scope_user_id SET DEFAULT get_sr_current_page_scope_user_id(),
    ALTER COLUMN owner_user_id SET DEFAULT get_sr_current_owner_user_id();


-- ================================================================
-- SECTION 1: PAGE_SCOPE_USER_MEMORY (Customer memory)
-- ================================================================
-- Only accessible for messages conversations (has PSID)
-- Scoped to current conversation's PSID only

-- SELECT: only memory for current PSID
CREATE POLICY sr_page_scope_user_memory_select_policy ON page_scope_user_memory
    FOR SELECT
    TO suggest_response_reader, suggest_response_writer
    USING (
        fan_page_id = get_sr_current_fan_page_id()
        AND facebook_page_scope_user_id = get_sr_current_page_scope_user_id()
        AND owner_user_id = current_setting('app.current_user_id', true)
    );

-- INSERT: only for current PSID
CREATE POLICY sr_page_scope_user_memory_insert_policy ON page_scope_user_memory
    FOR INSERT
    TO suggest_response_writer
    WITH CHECK (
        fan_page_id = get_sr_current_fan_page_id()
        AND facebook_page_scope_user_id = get_sr_current_page_scope_user_id()
        AND owner_user_id = current_setting('app.current_user_id', true)
    );

-- UPDATE: only for current PSID (for is_active flag)
CREATE POLICY sr_page_scope_user_memory_update_policy ON page_scope_user_memory
    FOR UPDATE
    TO suggest_response_writer
    USING (
        fan_page_id = get_sr_current_fan_page_id()
        AND facebook_page_scope_user_id = get_sr_current_page_scope_user_id()
        AND owner_user_id = current_setting('app.current_user_id', true)
    )
    WITH CHECK (
        fan_page_id = get_sr_current_fan_page_id()
        AND facebook_page_scope_user_id = get_sr_current_page_scope_user_id()
        AND owner_user_id = current_setting('app.current_user_id', true)
    );


-- ================================================================
-- SECTION 2: MEMORY_BLOCKS (for page_scope_user_memory only)
-- ================================================================
-- Agent can only manipulate memory blocks for current PSID's memory
-- Cannot access page_memory or user_memory blocks

-- SELECT: only blocks for current PSID's memory
CREATE POLICY sr_memory_blocks_select_policy ON memory_blocks
    FOR SELECT
    TO suggest_response_reader, suggest_response_writer
    USING (
        prompt_type = 'user_prompt' AND prompt_id IN (
            SELECT id FROM page_scope_user_memory
            WHERE fan_page_id = get_sr_current_fan_page_id()
            AND facebook_page_scope_user_id = get_sr_current_page_scope_user_id()
            AND owner_user_id = current_setting('app.current_user_id', true)
        )
    );

-- INSERT: only blocks for current PSID's memory
CREATE POLICY sr_memory_blocks_insert_policy ON memory_blocks
    FOR INSERT
    TO suggest_response_writer
    WITH CHECK (
        prompt_type = 'user_prompt' AND prompt_id IN (
            SELECT id FROM page_scope_user_memory
            WHERE fan_page_id = get_sr_current_fan_page_id()
            AND facebook_page_scope_user_id = get_sr_current_page_scope_user_id()
            AND owner_user_id = current_setting('app.current_user_id', true)
        )
    );

-- UPDATE: only blocks for current PSID's memory
CREATE POLICY sr_memory_blocks_update_policy ON memory_blocks
    FOR UPDATE
    TO suggest_response_writer
    USING (
        prompt_type = 'user_prompt' AND prompt_id IN (
            SELECT id FROM page_scope_user_memory
            WHERE fan_page_id = get_sr_current_fan_page_id()
            AND facebook_page_scope_user_id = get_sr_current_page_scope_user_id()
            AND owner_user_id = current_setting('app.current_user_id', true)
        )
    )
    WITH CHECK (
        prompt_type = 'user_prompt' AND prompt_id IN (
            SELECT id FROM page_scope_user_memory
            WHERE fan_page_id = get_sr_current_fan_page_id()
            AND facebook_page_scope_user_id = get_sr_current_page_scope_user_id()
            AND owner_user_id = current_setting('app.current_user_id', true)
        )
    );

-- DELETE: only blocks for current PSID's memory
CREATE POLICY sr_memory_blocks_delete_policy ON memory_blocks
    FOR DELETE
    TO suggest_response_writer
    USING (
        prompt_type = 'user_prompt' AND prompt_id IN (
            SELECT id FROM page_scope_user_memory
            WHERE fan_page_id = get_sr_current_fan_page_id()
            AND facebook_page_scope_user_id = get_sr_current_page_scope_user_id()
            AND owner_user_id = current_setting('app.current_user_id', true)
        )
    );


-- ================================================================
-- SECTION 2b: MEMORY_BLOCK_MEDIA (media attachments for memory_blocks)
-- ================================================================
-- Agent can attach/detach media to memory blocks for current PSID's memory
-- Only for blocks that belong to user_prompt type (page_scope_user_memory)

-- SELECT: only media for current PSID's memory blocks
CREATE POLICY sr_memory_block_media_select_policy ON memory_block_media
    FOR SELECT
    TO suggest_response_reader, suggest_response_writer
    USING (
        block_id IN (
            SELECT mb.id FROM memory_blocks mb
            WHERE mb.prompt_type = 'user_prompt' AND mb.prompt_id IN (
                SELECT id FROM page_scope_user_memory
                WHERE fan_page_id = get_sr_current_fan_page_id()
                AND facebook_page_scope_user_id = get_sr_current_page_scope_user_id()
                AND owner_user_id = current_setting('app.current_user_id', true)
            )
        )
    );

-- INSERT: only attach media to current PSID's memory blocks
CREATE POLICY sr_memory_block_media_insert_policy ON memory_block_media
    FOR INSERT
    TO suggest_response_writer
    WITH CHECK (
        block_id IN (
            SELECT mb.id FROM memory_blocks mb
            WHERE mb.prompt_type = 'user_prompt' AND mb.prompt_id IN (
                SELECT id FROM page_scope_user_memory
                WHERE fan_page_id = get_sr_current_fan_page_id()
                AND facebook_page_scope_user_id = get_sr_current_page_scope_user_id()
                AND owner_user_id = current_setting('app.current_user_id', true)
            )
        )
    );

-- DELETE: only detach media from current PSID's memory blocks
CREATE POLICY sr_memory_block_media_delete_policy ON memory_block_media
    FOR DELETE
    TO suggest_response_writer
    USING (
        block_id IN (
            SELECT mb.id FROM memory_blocks mb
            WHERE mb.prompt_type = 'user_prompt' AND mb.prompt_id IN (
                SELECT id FROM page_scope_user_memory
                WHERE fan_page_id = get_sr_current_fan_page_id()
                AND facebook_page_scope_user_id = get_sr_current_page_scope_user_id()
                AND owner_user_id = current_setting('app.current_user_id', true)
            )
        )
    );


-- ================================================================
-- SECTION 2c: MEDIA_ASSETS (for view_media tool)
-- ================================================================
-- Agent can read media_assets to get S3 URLs for viewing images.
-- Media IDs are provided in pre-built context (message/comment attachments).
-- S3 URLs are public, so SELECT all is safe (no sensitive data exposed).

-- SELECT: allow reading any media_asset (agent only knows IDs from context)
CREATE POLICY sr_media_assets_select_policy ON media_assets
    FOR SELECT
    TO suggest_response_reader, suggest_response_writer
    USING (true);


-- ================================================================
-- SECTION 3: CONVERSATION_AGENT_BLOCKS
-- ================================================================
-- Agent can block current conversation from future triggers
-- NOTE: RLS already enabled in 99_rls_policies.sql

-- SELECT: only blocks for current conversation
CREATE POLICY sr_conversation_agent_blocks_select_policy ON conversation_agent_blocks
    FOR SELECT
    TO suggest_response_reader, suggest_response_writer
    USING (
        fan_page_id = get_sr_current_fan_page_id()
        AND (
            (conversation_type = 'messages' AND facebook_conversation_messages_id = get_sr_current_messages_conversation_id())
            OR
            (conversation_type = 'comments' AND facebook_conversation_comments_id = get_sr_current_comments_conversation_id())
        )
    );

-- INSERT: can create block for current conversation only
CREATE POLICY sr_conversation_agent_blocks_insert_policy ON conversation_agent_blocks
    FOR INSERT
    TO suggest_response_writer
    WITH CHECK (
        fan_page_id = get_sr_current_fan_page_id()
        AND (
            (conversation_type = 'messages' AND facebook_conversation_messages_id = get_sr_current_messages_conversation_id())
            OR
            (conversation_type = 'comments' AND facebook_conversation_comments_id = get_sr_current_comments_conversation_id())
        )
    );


-- ================================================================
-- SECTION 4: AGENT_ESCALATIONS + AGENT_ESCALATION_MESSAGES
-- ================================================================
-- Agent can create escalation threads, send messages, and close them.
-- Messages table enforces sender_type = 'suggest_response_agent' via RLS.
-- NOTE: RLS already enabled in 99_rls_policies.sql

-- 4.1 agent_escalations (thread headers)
-- ================================================================

-- SELECT: only escalations for current conversation
CREATE POLICY sr_agent_escalations_select_policy ON agent_escalations
    FOR SELECT
    TO suggest_response_reader, suggest_response_writer
    USING (
        fan_page_id = get_sr_current_fan_page_id()
        AND owner_user_id = current_setting('app.current_user_id', true)
        AND (
            (conversation_type = 'messages' AND facebook_conversation_messages_id = get_sr_current_messages_conversation_id())
            OR
            (conversation_type = 'comments' AND facebook_conversation_comments_id = get_sr_current_comments_conversation_id())
        )
    );

-- INSERT: can create escalation for current conversation only
CREATE POLICY sr_agent_escalations_insert_policy ON agent_escalations
    FOR INSERT
    TO suggest_response_writer
    WITH CHECK (
        fan_page_id = get_sr_current_fan_page_id()
        AND owner_user_id = current_setting('app.current_user_id', true)
        AND (
            (conversation_type = 'messages' AND facebook_conversation_messages_id = get_sr_current_messages_conversation_id())
            OR
            (conversation_type = 'comments' AND facebook_conversation_comments_id = get_sr_current_comments_conversation_id())
        )
    );

-- UPDATE: can close escalations for current conversation (status open→closed)
CREATE POLICY sr_agent_escalations_update_policy ON agent_escalations
    FOR UPDATE
    TO suggest_response_writer
    USING (
        fan_page_id = get_sr_current_fan_page_id()
        AND owner_user_id = current_setting('app.current_user_id', true)
        AND (
            (conversation_type = 'messages' AND facebook_conversation_messages_id = get_sr_current_messages_conversation_id())
            OR
            (conversation_type = 'comments' AND facebook_conversation_comments_id = get_sr_current_comments_conversation_id())
        )
    )
    WITH CHECK (
        fan_page_id = get_sr_current_fan_page_id()
        AND owner_user_id = current_setting('app.current_user_id', true)
        AND (
            (conversation_type = 'messages' AND facebook_conversation_messages_id = get_sr_current_messages_conversation_id())
            OR
            (conversation_type = 'comments' AND facebook_conversation_comments_id = get_sr_current_comments_conversation_id())
        )
    );

-- 4.2 agent_escalation_messages (messages within threads)
-- ================================================================
-- NOTE: RLS already enabled in 99_rls_policies.sql

-- SELECT: messages for current conversation's escalations
CREATE POLICY sr_escalation_messages_select_policy ON agent_escalation_messages
    FOR SELECT
    TO suggest_response_reader, suggest_response_writer
    USING (
        escalation_id IN (
            SELECT id FROM agent_escalations
            WHERE fan_page_id = get_sr_current_fan_page_id()
            AND owner_user_id = current_setting('app.current_user_id', true)
            AND (
                (conversation_type = 'messages' AND facebook_conversation_messages_id = get_sr_current_messages_conversation_id())
                OR
                (conversation_type = 'comments' AND facebook_conversation_comments_id = get_sr_current_comments_conversation_id())
            )
        )
    );

-- INSERT: can only send messages as suggest_response_agent (not as general_agent or user)
CREATE POLICY sr_escalation_messages_insert_policy ON agent_escalation_messages
    FOR INSERT
    TO suggest_response_writer
    WITH CHECK (
        sender_type = 'suggest_response_agent'
        AND escalation_id IN (
            SELECT id FROM agent_escalations
            WHERE fan_page_id = get_sr_current_fan_page_id()
            AND owner_user_id = current_setting('app.current_user_id', true)
            AND (
                (conversation_type = 'messages' AND facebook_conversation_messages_id = get_sr_current_messages_conversation_id())
                OR
                (conversation_type = 'comments' AND facebook_conversation_comments_id = get_sr_current_comments_conversation_id())
            )
        )
    );


-- ================================================================
-- SECTION 5: PAGE_PLAYBOOKS + PAGE_PLAYBOOK_ASSIGNMENTS (READ-ONLY)
-- ================================================================
-- suggest_response_agent can query playbooks to find situational guidance.
-- READ-ONLY: playbooks are created/managed by general_agent (agent_writer role).
-- Scoped: agent can only see playbooks assigned to the current page's admin.
-- NOTE: RLS already enabled in 99_rls_policies.sql

-- SELECT page_playbooks: only playbooks owned by current user (and not soft-deleted)
CREATE POLICY sr_page_playbooks_select_policy ON page_playbooks
    FOR SELECT
    TO suggest_response_reader, suggest_response_writer
    USING (
        owner_user_id = current_setting('app.current_user_id', true)
        AND deleted_at IS NULL
    );

-- SELECT page_playbook_assignments: only assignments for current page's admins
CREATE POLICY sr_playbook_assignments_select_policy ON page_playbook_assignments
    FOR SELECT
    TO suggest_response_reader, suggest_response_writer
    USING (
        deleted_at IS NULL
        AND page_admin_id IN (
            SELECT id FROM facebook_page_admins
            WHERE page_id = get_sr_current_fan_page_id()
        )
    );


-- ================================================================
-- NOTES
-- ================================================================
-- 
-- CONTEXT VARIABLES REQUIRED:
-- - app.current_user_id: Owner user ID (always required)
-- - app.current_conversation_type: 'messages' or 'comments'
-- - app.current_conversation_id: The conversation ID being served
-- - app.current_fan_page_id: The fan page ID
-- - app.current_page_scope_user_id: PSID (only for messages type)
--
-- TABLES ACCESSIBLE BY SUGGEST_RESPONSE ROLES:
-- - page_scope_user_memory (READ/WRITE for current PSID only)
-- - memory_blocks (READ/WRITE for user_prompt type, current PSID only)
-- - memory_block_media (READ/INSERT/DELETE for current PSID's memory blocks)
-- - media_assets (READ all - for view_media tool, S3 URLs are public)
-- - conversation_agent_blocks (READ/INSERT for current conversation)
-- - agent_escalations (READ/INSERT/UPDATE for current conversation)
-- - agent_escalation_messages (READ/INSERT for current conversation, sender_type enforced)
-- - page_playbooks (READ only - query playbooks for situational guidance)
-- - page_playbook_assignments (READ only - find which playbooks apply to current page)
--
-- TABLES NOT ACCESSIBLE (data pre-built into context):
-- - fan_pages, posts, comments, messages
-- - facebook_conversation_messages, facebook_conversation_comments
-- - facebook_page_scope_users, facebook_conversation_comment_entries
-- - page_memory, user_memory
-- - media_assets, memory_block_media (for page_memory)
-- - All other tables
--
-- VERIFICATION QUERIES:
-- 1. Connect as suggest_response_writer
-- 2. SET app.current_user_id = 'user-id';
-- 3. SET app.current_conversation_type = 'messages';
-- 4. SET app.current_conversation_id = 't_123456789';
-- 5. SET app.current_fan_page_id = '12345';
-- 6. SET app.current_page_scope_user_id = '67890';
-- 7. SELECT * FROM page_scope_user_memory; -- Should only show memory for PSID 67890
-- 8. SELECT * FROM messages; -- Should FAIL (no access)
-- ================================================================
