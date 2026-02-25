-- Migration: Grant general agent (agent_reader, agent_writer) SELECT on facebook_page_admins
-- RLS filters by page_id IN get_user_accessible_page_ids() so agent only sees admins for pages the user manages.
-- Idempotent: safe to run multiple times (GRANT is idempotent; policy is dropped then recreated).

-- 1. Grant SELECT to both agent roles
GRANT SELECT ON facebook_page_admins TO agent_reader, agent_writer;

-- 2. Enable RLS on facebook_page_admins (idempotent)
ALTER TABLE facebook_page_admins ENABLE ROW LEVEL SECURITY;
ALTER TABLE facebook_page_admins FORCE ROW LEVEL SECURITY;

-- 3. RLS SELECT policy (drop first for idempotency)
DROP POLICY IF EXISTS agent_page_admins_policy ON facebook_page_admins;

CREATE POLICY agent_page_admins_policy ON facebook_page_admins
    FOR SELECT
    TO agent_reader, agent_writer
    USING (page_id IN (SELECT get_user_accessible_page_ids()));
