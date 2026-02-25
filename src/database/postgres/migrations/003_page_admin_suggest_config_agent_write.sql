-- Migration: Grant general agent (agent_writer) INSERT and UPDATE on page_admin_suggest_config
-- No DELETE: agent can read, create, and update configs only.
-- Idempotent: safe to run multiple times (GRANT is idempotent; policies are dropped then recreated).

-- 1. Grant INSERT and UPDATE to agent_writer
GRANT INSERT, UPDATE ON page_admin_suggest_config TO agent_writer;

-- 2. RLS policies for INSERT and UPDATE (drop first for idempotency)
DROP POLICY IF EXISTS agent_page_admin_suggest_config_insert_policy ON page_admin_suggest_config;
DROP POLICY IF EXISTS agent_page_admin_suggest_config_update_policy ON page_admin_suggest_config;

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
