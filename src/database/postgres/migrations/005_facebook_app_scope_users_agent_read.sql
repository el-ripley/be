-- Migration: Grant general agent (agent_reader, agent_writer) SELECT on facebook_app_scope_users
-- RLS filters by user_id = current_setting('app.current_user_id') so agent only sees its own record.
-- Idempotent: safe to run multiple times (GRANT is idempotent; policy is dropped then recreated).

-- 1. Grant SELECT to both agent roles
GRANT SELECT ON facebook_app_scope_users TO agent_reader, agent_writer;

-- 2. Enable RLS on facebook_app_scope_users (idempotent)
ALTER TABLE facebook_app_scope_users ENABLE ROW LEVEL SECURITY;
ALTER TABLE facebook_app_scope_users FORCE ROW LEVEL SECURITY;

-- 3. RLS SELECT policy (drop first for idempotency)
DROP POLICY IF EXISTS agent_app_scope_users_policy ON facebook_app_scope_users;

CREATE POLICY agent_app_scope_users_policy ON facebook_app_scope_users
    FOR SELECT
    TO agent_reader, agent_writer
    USING (user_id = current_setting('app.current_user_id', true));
