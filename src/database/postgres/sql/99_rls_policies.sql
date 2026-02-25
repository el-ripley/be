-- ================================================================
-- ROW LEVEL SECURITY (RLS) POLICIES FOR AGENT ROLES
-- ================================================================
-- This file enables RLS on tables that agent_reader and agent_writer need to access
-- and creates policies that restrict access based on user context.
--
-- POLICY SCOPE:
-- - SELECT policies: agent_reader, agent_writer (read access)
-- - INSERT/UPDATE/DELETE policies: agent_writer only (write access)
--
-- USAGE:
-- Before executing queries as agent_reader or agent_writer, set the session variable:
--   SET app.current_user_id = 'user-uuid-here';
--
-- The policies will automatically filter all queries to only return
-- data the user has access to.
-- ================================================================

-- ================================================================
-- HELPER FUNCTION: Get accessible page IDs for current user
-- ================================================================
-- This function returns all fan_page IDs that the current user
-- has admin access to (via facebook_app_scope_users -> facebook_page_admins chain)
-- ================================================================
CREATE OR REPLACE FUNCTION get_user_accessible_page_ids()
RETURNS SETOF VARCHAR(255) AS $$
BEGIN
    RETURN QUERY
    SELECT DISTINCT fpa.page_id
    FROM facebook_page_admins fpa
    JOIN facebook_app_scope_users fasu ON fpa.facebook_user_id = fasu.id
    WHERE fasu.user_id = current_setting('app.current_user_id', true);
END;
$$ LANGUAGE plpgsql SECURITY DEFINER STABLE;

-- Grant execute to both agent roles
GRANT EXECUTE ON FUNCTION get_user_accessible_page_ids() TO agent_reader, agent_writer;


-- ================================================================
-- SECTION 1: FACEBOOK TABLES
-- ================================================================
-- These tables contain Facebook integration data.
-- Filter strategy: fan_page_id IN get_user_accessible_page_ids()
-- ================================================================

-- 1.1 Enable RLS on Facebook tables
-- ================================================================

ALTER TABLE fan_pages ENABLE ROW LEVEL SECURITY;
ALTER TABLE fan_pages FORCE ROW LEVEL SECURITY;

ALTER TABLE posts ENABLE ROW LEVEL SECURITY;
ALTER TABLE posts FORCE ROW LEVEL SECURITY;

ALTER TABLE comments ENABLE ROW LEVEL SECURITY;
ALTER TABLE comments FORCE ROW LEVEL SECURITY;

ALTER TABLE messages ENABLE ROW LEVEL SECURITY;
ALTER TABLE messages FORCE ROW LEVEL SECURITY;

ALTER TABLE facebook_page_scope_users ENABLE ROW LEVEL SECURITY;
ALTER TABLE facebook_page_scope_users FORCE ROW LEVEL SECURITY;

ALTER TABLE facebook_page_admins ENABLE ROW LEVEL SECURITY;
ALTER TABLE facebook_page_admins FORCE ROW LEVEL SECURITY;

ALTER TABLE facebook_app_scope_users ENABLE ROW LEVEL SECURITY;
ALTER TABLE facebook_app_scope_users FORCE ROW LEVEL SECURITY;

ALTER TABLE facebook_conversation_messages ENABLE ROW LEVEL SECURITY;
ALTER TABLE facebook_conversation_messages FORCE ROW LEVEL SECURITY;

ALTER TABLE facebook_conversation_comments ENABLE ROW LEVEL SECURITY;
ALTER TABLE facebook_conversation_comments FORCE ROW LEVEL SECURITY;

ALTER TABLE facebook_conversation_comment_entries ENABLE ROW LEVEL SECURITY;
ALTER TABLE facebook_conversation_comment_entries FORCE ROW LEVEL SECURITY;

ALTER TABLE post_reactions ENABLE ROW LEVEL SECURITY;
ALTER TABLE post_reactions FORCE ROW LEVEL SECURITY;

ALTER TABLE comment_reactions ENABLE ROW LEVEL SECURITY;
ALTER TABLE comment_reactions FORCE ROW LEVEL SECURITY;

ALTER TABLE facebook_post_sync_states ENABLE ROW LEVEL SECURITY;
ALTER TABLE facebook_post_sync_states FORCE ROW LEVEL SECURITY;

ALTER TABLE facebook_post_comment_sync_states ENABLE ROW LEVEL SECURITY;
ALTER TABLE facebook_post_comment_sync_states FORCE ROW LEVEL SECURITY;

ALTER TABLE facebook_inbox_sync_states ENABLE ROW LEVEL SECURITY;
ALTER TABLE facebook_inbox_sync_states FORCE ROW LEVEL SECURITY;

-- 1.2 RLS SELECT Policies for Facebook tables
-- ================================================================

-- fan_pages: only pages user has admin access to
CREATE POLICY agent_fan_pages_policy ON fan_pages
    FOR SELECT
    TO agent_reader, agent_writer
    USING (id IN (SELECT get_user_accessible_page_ids()));

-- posts: only posts from accessible pages
CREATE POLICY agent_posts_policy ON posts
    FOR SELECT
    TO agent_reader, agent_writer
    USING (fan_page_id IN (SELECT get_user_accessible_page_ids()));

-- comments: only comments from accessible pages
CREATE POLICY agent_comments_policy ON comments
    FOR SELECT
    TO agent_reader, agent_writer
    USING (fan_page_id IN (SELECT get_user_accessible_page_ids()));

-- messages: only messages from accessible pages' conversations
CREATE POLICY agent_messages_policy ON messages
    FOR SELECT
    TO agent_reader, agent_writer
    USING (
        conversation_id IN (
            SELECT id FROM facebook_conversation_messages
            WHERE fan_page_id IN (SELECT get_user_accessible_page_ids())
        )
    );

-- facebook_page_scope_users: only users who interacted with accessible pages
CREATE POLICY agent_page_scope_users_policy ON facebook_page_scope_users
    FOR SELECT
    TO agent_reader, agent_writer
    USING (fan_page_id IN (SELECT get_user_accessible_page_ids()));

-- facebook_page_admins: only admins for accessible pages
CREATE POLICY agent_page_admins_policy ON facebook_page_admins
    FOR SELECT
    TO agent_reader, agent_writer
    USING (page_id IN (SELECT get_user_accessible_page_ids()));

-- facebook_app_scope_users: only current user's own record
CREATE POLICY agent_app_scope_users_policy ON facebook_app_scope_users
    FOR SELECT
    TO agent_reader, agent_writer
    USING (user_id = current_setting('app.current_user_id', true));

-- facebook_conversation_messages: only conversations from accessible pages
CREATE POLICY agent_conversation_messages_policy ON facebook_conversation_messages
    FOR SELECT
    TO agent_reader, agent_writer
    USING (fan_page_id IN (SELECT get_user_accessible_page_ids()));

-- facebook_conversation_comments: only comment threads from accessible pages
CREATE POLICY agent_conversation_comments_policy ON facebook_conversation_comments
    FOR SELECT
    TO agent_reader, agent_writer
    USING (fan_page_id IN (SELECT get_user_accessible_page_ids()));

-- facebook_conversation_comment_entries: entries for accessible conversations
CREATE POLICY agent_conversation_comment_entries_policy ON facebook_conversation_comment_entries
    FOR SELECT
    TO agent_reader, agent_writer
    USING (
        conversation_id IN (
            SELECT id FROM facebook_conversation_comments
            WHERE fan_page_id IN (SELECT get_user_accessible_page_ids())
        )
    );

-- post_reactions: reactions on posts from accessible pages
CREATE POLICY agent_post_reactions_policy ON post_reactions
    FOR SELECT
    TO agent_reader, agent_writer
    USING (fan_page_id IN (SELECT get_user_accessible_page_ids()));

-- comment_reactions: reactions on comments from accessible pages
CREATE POLICY agent_comment_reactions_policy ON comment_reactions
    FOR SELECT
    TO agent_reader, agent_writer
    USING (fan_page_id IN (SELECT get_user_accessible_page_ids()));

-- facebook_post_sync_states: sync states for accessible pages
CREATE POLICY agent_post_sync_states_policy ON facebook_post_sync_states
    FOR SELECT
    TO agent_reader, agent_writer
    USING (fan_page_id IN (SELECT get_user_accessible_page_ids()));

-- facebook_post_comment_sync_states: sync states for accessible pages
CREATE POLICY agent_comment_sync_states_policy ON facebook_post_comment_sync_states
    FOR SELECT
    TO agent_reader, agent_writer
    USING (fan_page_id IN (SELECT get_user_accessible_page_ids()));

-- facebook_inbox_sync_states: sync states for accessible pages
CREATE POLICY agent_inbox_sync_states_policy ON facebook_inbox_sync_states
    FOR SELECT
    TO agent_reader, agent_writer
    USING (fan_page_id IN (SELECT get_user_accessible_page_ids()));


-- ================================================================
-- SECTION 2: MEMORY TABLES
-- ================================================================
-- These tables contain agent memory (page-level, user-level, global).
-- Filter strategy: 
--   - Tables with owner_user_id: filter by current_user_id directly
--   - Tables with fan_page_id: filter by accessible pages
--   - Junction tables: filter through parent tables
-- ================================================================

-- 2.1 Enable RLS on Memory tables
-- ================================================================

ALTER TABLE page_memory ENABLE ROW LEVEL SECURITY;
ALTER TABLE page_memory FORCE ROW LEVEL SECURITY;

ALTER TABLE page_scope_user_memory ENABLE ROW LEVEL SECURITY;
ALTER TABLE page_scope_user_memory FORCE ROW LEVEL SECURITY;

ALTER TABLE user_memory ENABLE ROW LEVEL SECURITY;
ALTER TABLE user_memory FORCE ROW LEVEL SECURITY;

ALTER TABLE memory_blocks ENABLE ROW LEVEL SECURITY;
ALTER TABLE memory_blocks FORCE ROW LEVEL SECURITY;

-- 2.2 RLS SELECT Policies for Memory tables
-- ================================================================

-- page_memory: only memory for accessible pages
CREATE POLICY agent_page_memory_select_policy ON page_memory
    FOR SELECT
    TO agent_reader, agent_writer
    USING (fan_page_id IN (SELECT get_user_accessible_page_ids()));

-- page_scope_user_memory: only memory for accessible pages
CREATE POLICY agent_page_scope_user_memory_select_policy ON page_scope_user_memory
    FOR SELECT
    TO agent_reader, agent_writer
    USING (fan_page_id IN (SELECT get_user_accessible_page_ids()));

-- user_memory: only current user's global memory
CREATE POLICY agent_user_memory_select_policy ON user_memory
    FOR SELECT
    TO agent_reader, agent_writer
    USING (owner_user_id = current_setting('app.current_user_id', true));

-- memory_blocks: blocks belonging to accessible prompts/memory
-- Uses polymorphic prompt_type to filter through correct parent table
CREATE POLICY agent_memory_blocks_select_policy ON memory_blocks
    FOR SELECT
    TO agent_reader, agent_writer
    USING (
        (prompt_type = 'page_prompt' AND prompt_id IN (
            SELECT id FROM page_memory 
            WHERE fan_page_id IN (SELECT get_user_accessible_page_ids())
        ))
        OR
        (prompt_type = 'user_prompt' AND prompt_id IN (
            SELECT id FROM page_scope_user_memory
            WHERE fan_page_id IN (SELECT get_user_accessible_page_ids())
        ))
        OR
        (prompt_type = 'user_memory' AND prompt_id IN (
            SELECT id FROM user_memory
            WHERE owner_user_id = current_setting('app.current_user_id', true)
        ))
    );

-- 2.3 RLS INSERT Policies for Memory tables
-- ================================================================

-- page_memory: only insert for accessible pages and current user as owner
CREATE POLICY agent_page_memory_insert_policy ON page_memory
    FOR INSERT
    TO agent_writer
    WITH CHECK (
        fan_page_id IN (SELECT get_user_accessible_page_ids())
        AND owner_user_id = current_setting('app.current_user_id', true)
    );

-- page_scope_user_memory: only insert for accessible pages and current user as owner
CREATE POLICY agent_page_scope_user_memory_insert_policy ON page_scope_user_memory
    FOR INSERT
    TO agent_writer
    WITH CHECK (
        fan_page_id IN (SELECT get_user_accessible_page_ids())
        AND owner_user_id = current_setting('app.current_user_id', true)
    );

-- user_memory: only insert for current user
CREATE POLICY agent_user_memory_insert_policy ON user_memory
    FOR INSERT
    TO agent_writer
    WITH CHECK (owner_user_id = current_setting('app.current_user_id', true));

-- memory_blocks: only insert blocks for accessible prompts/memory
CREATE POLICY agent_memory_blocks_insert_policy ON memory_blocks
    FOR INSERT
    TO agent_writer
    WITH CHECK (
        (prompt_type = 'page_prompt' AND prompt_id IN (
            SELECT id FROM page_memory 
            WHERE fan_page_id IN (SELECT get_user_accessible_page_ids())
            AND owner_user_id = current_setting('app.current_user_id', true)
        ))
        OR
        (prompt_type = 'user_prompt' AND prompt_id IN (
            SELECT id FROM page_scope_user_memory
            WHERE fan_page_id IN (SELECT get_user_accessible_page_ids())
            AND owner_user_id = current_setting('app.current_user_id', true)
        ))
        OR
        (prompt_type = 'user_memory' AND prompt_id IN (
            SELECT id FROM user_memory
            WHERE owner_user_id = current_setting('app.current_user_id', true)
        ))
    );

-- 2.4 RLS UPDATE Policies for Memory tables
-- ================================================================
-- Note: Memory tables are append-only, but we allow UPDATE for is_active flag
-- to support deactivating old versions when creating new ones

-- page_memory: only update for accessible pages and current user as owner
CREATE POLICY agent_page_memory_update_policy ON page_memory
    FOR UPDATE
    TO agent_writer
    USING (
        fan_page_id IN (SELECT get_user_accessible_page_ids())
        AND owner_user_id = current_setting('app.current_user_id', true)
    )
    WITH CHECK (
        fan_page_id IN (SELECT get_user_accessible_page_ids())
        AND owner_user_id = current_setting('app.current_user_id', true)
    );

-- page_scope_user_memory: only update for accessible pages and current user as owner
CREATE POLICY agent_page_scope_user_memory_update_policy ON page_scope_user_memory
    FOR UPDATE
    TO agent_writer
    USING (
        fan_page_id IN (SELECT get_user_accessible_page_ids())
        AND owner_user_id = current_setting('app.current_user_id', true)
    )
    WITH CHECK (
        fan_page_id IN (SELECT get_user_accessible_page_ids())
        AND owner_user_id = current_setting('app.current_user_id', true)
    );

-- user_memory: only update for current user
CREATE POLICY agent_user_memory_update_policy ON user_memory
    FOR UPDATE
    TO agent_writer
    USING (owner_user_id = current_setting('app.current_user_id', true))
    WITH CHECK (owner_user_id = current_setting('app.current_user_id', true));

-- memory_blocks: only update blocks for accessible prompts/memory
CREATE POLICY agent_memory_blocks_update_policy ON memory_blocks
    FOR UPDATE
    TO agent_writer
    USING (
        (prompt_type = 'page_prompt' AND prompt_id IN (
            SELECT id FROM page_memory 
            WHERE fan_page_id IN (SELECT get_user_accessible_page_ids())
            AND owner_user_id = current_setting('app.current_user_id', true)
        ))
        OR
        (prompt_type = 'user_prompt' AND prompt_id IN (
            SELECT id FROM page_scope_user_memory
            WHERE fan_page_id IN (SELECT get_user_accessible_page_ids())
            AND owner_user_id = current_setting('app.current_user_id', true)
        ))
        OR
        (prompt_type = 'user_memory' AND prompt_id IN (
            SELECT id FROM user_memory
            WHERE owner_user_id = current_setting('app.current_user_id', true)
        ))
    )
    WITH CHECK (
        (prompt_type = 'page_prompt' AND prompt_id IN (
            SELECT id FROM page_memory 
            WHERE fan_page_id IN (SELECT get_user_accessible_page_ids())
            AND owner_user_id = current_setting('app.current_user_id', true)
        ))
        OR
        (prompt_type = 'user_prompt' AND prompt_id IN (
            SELECT id FROM page_scope_user_memory
            WHERE fan_page_id IN (SELECT get_user_accessible_page_ids())
            AND owner_user_id = current_setting('app.current_user_id', true)
        ))
        OR
        (prompt_type = 'user_memory' AND prompt_id IN (
            SELECT id FROM user_memory
            WHERE owner_user_id = current_setting('app.current_user_id', true)
        ))
    );

-- 2.5 RLS DELETE Policy for memory_blocks
-- ================================================================
-- Agent can delete obsolete memory blocks that are no longer needed

-- memory_blocks: only delete blocks for accessible prompts/memory owned by current user
CREATE POLICY agent_memory_blocks_delete_policy ON memory_blocks
    FOR DELETE
    TO agent_writer
    USING (
        (prompt_type = 'page_prompt' AND prompt_id IN (
            SELECT id FROM page_memory 
            WHERE fan_page_id IN (SELECT get_user_accessible_page_ids())
            AND owner_user_id = current_setting('app.current_user_id', true)
        ))
        OR
        (prompt_type = 'user_prompt' AND prompt_id IN (
            SELECT id FROM page_scope_user_memory
            WHERE fan_page_id IN (SELECT get_user_accessible_page_ids())
            AND owner_user_id = current_setting('app.current_user_id', true)
        ))
        OR
        (prompt_type = 'user_memory' AND prompt_id IN (
            SELECT id FROM user_memory
            WHERE owner_user_id = current_setting('app.current_user_id', true)
        ))
    );


-- ================================================================
-- SECTION 3: SUGGEST RESPONSE TABLES
-- ================================================================
-- These tables contain AI suggestion configuration and history.
-- Filter strategy: 
--   - Tables with user_id: filter by current_user_id directly
--   - Tables with fan_page_id: filter by accessible pages
--   - page_admin_suggest_config: SELECT for accessible pages, INSERT/UPDATE for own page_admin records only (no DELETE)
-- ================================================================

-- 3.1 Enable RLS on Suggest Response tables
-- ================================================================

ALTER TABLE suggest_response_agent ENABLE ROW LEVEL SECURITY;
ALTER TABLE suggest_response_agent FORCE ROW LEVEL SECURITY;

ALTER TABLE suggest_response_history ENABLE ROW LEVEL SECURITY;
ALTER TABLE suggest_response_history FORCE ROW LEVEL SECURITY;

-- 3.2 RLS SELECT Policies for Suggest Response tables
-- ================================================================

-- suggest_response_agent: only current user's agent settings
CREATE POLICY agent_suggest_response_agent_policy ON suggest_response_agent
    FOR SELECT
    TO agent_reader, agent_writer
    USING (user_id = current_setting('app.current_user_id', true));

-- suggest_response_history: only current user's suggestion history
CREATE POLICY agent_suggest_response_history_policy ON suggest_response_history
    FOR SELECT
    TO agent_reader, agent_writer
    USING (user_id = current_setting('app.current_user_id', true));

-- 3.3 Enable RLS on additional Suggest Response tables
-- ================================================================

ALTER TABLE page_admin_suggest_config ENABLE ROW LEVEL SECURITY;
ALTER TABLE page_admin_suggest_config FORCE ROW LEVEL SECURITY;

ALTER TABLE suggest_response_message ENABLE ROW LEVEL SECURITY;
ALTER TABLE suggest_response_message FORCE ROW LEVEL SECURITY;

-- 3.4 RLS SELECT Policies for additional Suggest Response tables
-- ================================================================

-- page_admin_suggest_config: only config for accessible pages (via page_admin)
CREATE POLICY agent_page_admin_suggest_config_policy ON page_admin_suggest_config
    FOR SELECT
    TO agent_reader, agent_writer
    USING (
        page_admin_id IN (
            SELECT id FROM facebook_page_admins
            WHERE page_id IN (SELECT get_user_accessible_page_ids())
        )
    );

-- page_admin_suggest_config INSERT: only for current user's own page_admin records
CREATE POLICY agent_page_admin_suggest_config_insert_policy ON page_admin_suggest_config
    FOR INSERT
    TO agent_writer
    WITH CHECK (
        page_admin_id IN (
            SELECT fpa.id FROM facebook_page_admins fpa
            JOIN facebook_app_scope_users fasu ON fpa.facebook_user_id = fasu.id
            WHERE fasu.user_id = current_setting('app.current_user_id', true)
        )
    );

-- page_admin_suggest_config UPDATE: only for current user's own page_admin records
CREATE POLICY agent_page_admin_suggest_config_update_policy ON page_admin_suggest_config
    FOR UPDATE
    TO agent_writer
    USING (
        page_admin_id IN (
            SELECT fpa.id FROM facebook_page_admins fpa
            JOIN facebook_app_scope_users fasu ON fpa.facebook_user_id = fasu.id
            WHERE fasu.user_id = current_setting('app.current_user_id', true)
        )
    )
    WITH CHECK (
        page_admin_id IN (
            SELECT fpa.id FROM facebook_page_admins fpa
            JOIN facebook_app_scope_users fasu ON fpa.facebook_user_id = fasu.id
            WHERE fasu.user_id = current_setting('app.current_user_id', true)
        )
    );

-- suggest_response_message: only messages from current user's history
CREATE POLICY agent_suggest_response_message_policy ON suggest_response_message
    FOR SELECT
    TO agent_reader, agent_writer
    USING (
        history_id IN (
            SELECT id FROM suggest_response_history
            WHERE user_id = current_setting('app.current_user_id', true)
        )
    );


-- ================================================================
-- SECTION 4: MEDIA ASSETS TABLES
-- ================================================================
-- These tables contain media uploaded by users or mirrored from Facebook.
-- Filter strategy: user_id = current_user_id
-- Note: user_storage_quotas is NOT included (Agent doesn't need quota info)
-- ================================================================

-- 3.1 Enable RLS on Media tables
-- ================================================================

ALTER TABLE media_assets ENABLE ROW LEVEL SECURITY;
ALTER TABLE media_assets FORCE ROW LEVEL SECURITY;

ALTER TABLE memory_block_media ENABLE ROW LEVEL SECURITY;
ALTER TABLE memory_block_media FORCE ROW LEVEL SECURITY;

-- 4.2 RLS SELECT Policies for Media tables
-- ================================================================

-- media_assets: only current user's media
CREATE POLICY agent_media_assets_policy ON media_assets
    FOR SELECT
    TO agent_reader, agent_writer
    USING (user_id = current_setting('app.current_user_id', true));

-- memory_block_media: media attached to accessible memory blocks
CREATE POLICY agent_memory_block_media_select_policy ON memory_block_media
    FOR SELECT
    TO agent_reader, agent_writer
    USING (
        block_id IN (
            SELECT mb.id FROM memory_blocks mb
            WHERE (mb.prompt_type = 'page_prompt' AND mb.prompt_id IN (
                SELECT id FROM page_memory 
                WHERE fan_page_id IN (SELECT get_user_accessible_page_ids())
            ))
            OR (mb.prompt_type = 'user_prompt' AND mb.prompt_id IN (
                SELECT id FROM page_scope_user_memory
                WHERE fan_page_id IN (SELECT get_user_accessible_page_ids())
            ))
            OR (mb.prompt_type = 'user_memory' AND mb.prompt_id IN (
                SELECT id FROM user_memory
                WHERE owner_user_id = current_setting('app.current_user_id', true)
            ))
        )
    );

-- memory_block_media: insert media links to accessible memory blocks owned by current user
CREATE POLICY agent_memory_block_media_insert_policy ON memory_block_media
    FOR INSERT
    TO agent_writer
    WITH CHECK (
        block_id IN (
            SELECT mb.id FROM memory_blocks mb
            WHERE (mb.prompt_type = 'page_prompt' AND mb.prompt_id IN (
                SELECT id FROM page_memory 
                WHERE fan_page_id IN (SELECT get_user_accessible_page_ids())
                AND owner_user_id = current_setting('app.current_user_id', true)
            ))
            OR (mb.prompt_type = 'user_prompt' AND mb.prompt_id IN (
                SELECT id FROM page_scope_user_memory
                WHERE fan_page_id IN (SELECT get_user_accessible_page_ids())
                AND owner_user_id = current_setting('app.current_user_id', true)
            ))
            OR (mb.prompt_type = 'user_memory' AND mb.prompt_id IN (
                SELECT id FROM user_memory
                WHERE owner_user_id = current_setting('app.current_user_id', true)
            ))
        )
        AND media_id IN (
            SELECT id FROM media_assets
            WHERE user_id = current_setting('app.current_user_id', true)
        )
    );

-- memory_block_media: delete media links from accessible memory blocks owned by current user
CREATE POLICY agent_memory_block_media_delete_policy ON memory_block_media
    FOR DELETE
    TO agent_writer
    USING (
        block_id IN (
            SELECT mb.id FROM memory_blocks mb
            WHERE (mb.prompt_type = 'page_prompt' AND mb.prompt_id IN (
                SELECT id FROM page_memory 
                WHERE fan_page_id IN (SELECT get_user_accessible_page_ids())
                AND owner_user_id = current_setting('app.current_user_id', true)
            ))
            OR (mb.prompt_type = 'user_prompt' AND mb.prompt_id IN (
                SELECT id FROM page_scope_user_memory
                WHERE fan_page_id IN (SELECT get_user_accessible_page_ids())
                AND owner_user_id = current_setting('app.current_user_id', true)
            ))
            OR (mb.prompt_type = 'user_memory' AND mb.prompt_id IN (
                SELECT id FROM user_memory
                WHERE owner_user_id = current_setting('app.current_user_id', true)
            ))
        )
    );


-- ================================================================
-- SECTION 5: PLAYBOOK TABLES
-- ================================================================
-- page_playbooks: content owned by user, reusable across pages
-- page_playbook_assignments: links playbooks to page_admin + conversation_type
-- Filter strategy:
--   - page_playbooks: owner_user_id = current_user_id
--   - page_playbook_assignments: page_admin must belong to current user's accessible pages
-- ================================================================

-- 5.1 Enable RLS on Playbook tables
-- ================================================================

ALTER TABLE page_playbooks ENABLE ROW LEVEL SECURITY;
ALTER TABLE page_playbooks FORCE ROW LEVEL SECURITY;

ALTER TABLE page_playbook_assignments ENABLE ROW LEVEL SECURITY;
ALTER TABLE page_playbook_assignments FORCE ROW LEVEL SECURITY;

-- 5.2 RLS Policies for page_playbooks
-- ================================================================

-- SELECT: only playbooks owned by current user
CREATE POLICY agent_page_playbooks_select_policy ON page_playbooks
    FOR SELECT
    TO agent_reader, agent_writer
    USING (owner_user_id = current_setting('app.current_user_id', true));

-- INSERT: only insert playbooks as current user
CREATE POLICY agent_page_playbooks_insert_policy ON page_playbooks
    FOR INSERT
    TO agent_writer
    WITH CHECK (owner_user_id = current_setting('app.current_user_id', true));

-- UPDATE: only update own playbooks
CREATE POLICY agent_page_playbooks_update_policy ON page_playbooks
    FOR UPDATE
    TO agent_writer
    USING (owner_user_id = current_setting('app.current_user_id', true))
    WITH CHECK (owner_user_id = current_setting('app.current_user_id', true));

-- 5.3 RLS Policies for page_playbook_assignments
-- ================================================================

-- SELECT: only assignments for page_admins of accessible pages
CREATE POLICY agent_playbook_assignments_select_policy ON page_playbook_assignments
    FOR SELECT
    TO agent_reader, agent_writer
    USING (
        page_admin_id IN (
            SELECT id FROM facebook_page_admins
            WHERE page_id IN (SELECT get_user_accessible_page_ids())
        )
    );

-- INSERT: only assign to page_admins of current user's pages
CREATE POLICY agent_playbook_assignments_insert_policy ON page_playbook_assignments
    FOR INSERT
    TO agent_writer
    WITH CHECK (
        page_admin_id IN (
            SELECT fpa.id FROM facebook_page_admins fpa
            JOIN facebook_app_scope_users fasu ON fpa.facebook_user_id = fasu.id
            WHERE fasu.user_id = current_setting('app.current_user_id', true)
        )
        AND playbook_id IN (
            SELECT id FROM page_playbooks
            WHERE owner_user_id = current_setting('app.current_user_id', true)
        )
    );

-- UPDATE: only update assignments for current user's page_admins
CREATE POLICY agent_playbook_assignments_update_policy ON page_playbook_assignments
    FOR UPDATE
    TO agent_writer
    USING (
        page_admin_id IN (
            SELECT fpa.id FROM facebook_page_admins fpa
            JOIN facebook_app_scope_users fasu ON fpa.facebook_user_id = fasu.id
            WHERE fasu.user_id = current_setting('app.current_user_id', true)
        )
    )
    WITH CHECK (
        page_admin_id IN (
            SELECT fpa.id FROM facebook_page_admins fpa
            JOIN facebook_app_scope_users fasu ON fpa.facebook_user_id = fasu.id
            WHERE fasu.user_id = current_setting('app.current_user_id', true)
        )
    );


-- ================================================================
-- SECTION 6: CONVERSATION AGENT BLOCKS TABLE
-- ================================================================
-- Blocks agent from being triggered on specific conversations.
-- Filter strategy: fan_page_id IN get_user_accessible_page_ids()
-- ================================================================

-- 6.1 Enable RLS on conversation_agent_blocks
-- ================================================================

ALTER TABLE conversation_agent_blocks ENABLE ROW LEVEL SECURITY;
ALTER TABLE conversation_agent_blocks FORCE ROW LEVEL SECURITY;

-- 6.2 RLS Policies for conversation_agent_blocks
-- ================================================================

-- SELECT: only blocks for accessible pages
CREATE POLICY agent_conversation_agent_blocks_select_policy ON conversation_agent_blocks
    FOR SELECT
    TO agent_reader, agent_writer
    USING (fan_page_id IN (SELECT get_user_accessible_page_ids()));

-- INSERT: can create blocks for accessible pages
CREATE POLICY agent_conversation_agent_blocks_insert_policy ON conversation_agent_blocks
    FOR INSERT
    TO agent_writer
    WITH CHECK (fan_page_id IN (SELECT get_user_accessible_page_ids()));

-- UPDATE: can update blocks for accessible pages
CREATE POLICY agent_conversation_agent_blocks_update_policy ON conversation_agent_blocks
    FOR UPDATE
    TO agent_writer
    USING (fan_page_id IN (SELECT get_user_accessible_page_ids()))
    WITH CHECK (fan_page_id IN (SELECT get_user_accessible_page_ids()));


-- ================================================================
-- SECTION 7: AGENT ESCALATIONS TABLES
-- ================================================================
-- Two-way communication channel between suggest_response_agent and external.
-- Split into thread header (agent_escalations) and messages (agent_escalation_messages).
-- RLS naturally enforces field-level access: each role can only INSERT messages
-- with its own sender_type — no triggers needed.
-- Filter strategy: fan_page_id IN get_user_accessible_page_ids() AND owner_user_id
-- ================================================================

-- 7.1 Enable RLS on agent_escalations and agent_escalation_messages
-- ================================================================

ALTER TABLE agent_escalations ENABLE ROW LEVEL SECURITY;
ALTER TABLE agent_escalations FORCE ROW LEVEL SECURITY;

ALTER TABLE agent_escalation_messages ENABLE ROW LEVEL SECURITY;
ALTER TABLE agent_escalation_messages FORCE ROW LEVEL SECURITY;

-- 7.2 RLS Policies for agent_escalations (thread headers)
-- ================================================================

-- SELECT: only escalations for accessible pages owned by current user
CREATE POLICY agent_escalations_select_policy ON agent_escalations
    FOR SELECT
    TO agent_reader, agent_writer
    USING (
        fan_page_id IN (SELECT get_user_accessible_page_ids())
        AND owner_user_id = current_setting('app.current_user_id', true)
    );

-- NOTE: No INSERT policy — only suggest_response_agent creates escalations.
-- General agent communicates back via agent_escalation_messages.

-- UPDATE: can close escalations for accessible pages
CREATE POLICY agent_escalations_update_policy ON agent_escalations
    FOR UPDATE
    TO agent_writer
    USING (
        fan_page_id IN (SELECT get_user_accessible_page_ids())
        AND owner_user_id = current_setting('app.current_user_id', true)
    )
    WITH CHECK (
        fan_page_id IN (SELECT get_user_accessible_page_ids())
        AND owner_user_id = current_setting('app.current_user_id', true)
    );

-- 7.3 RLS Policies for agent_escalation_messages
-- ================================================================

-- SELECT: messages for escalations in accessible pages
CREATE POLICY agent_escalation_messages_select_policy ON agent_escalation_messages
    FOR SELECT
    TO agent_reader, agent_writer
    USING (
        escalation_id IN (
            SELECT id FROM agent_escalations
            WHERE fan_page_id IN (SELECT get_user_accessible_page_ids())
            AND owner_user_id = current_setting('app.current_user_id', true)
        )
    );

-- INSERT: general_agent/user can only send messages as themselves (not as suggest_response_agent)
CREATE POLICY agent_escalation_messages_insert_policy ON agent_escalation_messages
    FOR INSERT
    TO agent_writer
    WITH CHECK (
        sender_type IN ('general_agent', 'user')
        AND escalation_id IN (
            SELECT id FROM agent_escalations
            WHERE fan_page_id IN (SELECT get_user_accessible_page_ids())
            AND owner_user_id = current_setting('app.current_user_id', true)
        )
    );


-- ================================================================
-- NOTES
-- ================================================================
-- 
-- BYPASS POLICY FOR SUPERUSER/ADMIN:
-- The main postgres user (el-ripley-user) should bypass RLS.
-- This is automatic for superusers, but if using a non-superuser:
--   ALTER ROLE "el-ripley-user" BYPASSRLS;
--
-- TABLES NOT PROTECTED (no RLS needed for agent):
-- - users, roles, user_role, refresh_tokens (sensitive auth data)
-- - user_conversation_settings (user preferences, not needed by agent)
-- - user_storage_quotas (quota management, not needed by agent)
-- - openai_* tables (conversation history managed by app layer)
-- - agent_response (execution logs managed by app layer)
-- - billing_* tables (payment data, not needed by agent)
-- - suggest_response_message (history detail, accessed via history)
--
-- VERIFICATION QUERIES (for testing):
-- 1. Connect as agent_reader or agent_writer
-- 2. SET app.current_user_id = 'some-user-id';
-- 3. SELECT * FROM fan_pages; -- Should only show pages user has access to
-- 4. SELECT * FROM posts; -- Should only show posts from those pages
-- 5. SELECT * FROM suggest_response_agent; -- Should only show user's settings
-- 6. SELECT * FROM media_assets; -- Should only show user's media
-- ================================================================
