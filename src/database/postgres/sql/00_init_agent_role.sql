-- ================================================================
-- AGENT ROLES SETUP (READ/WRITE SEPARATION)
-- ================================================================
-- This script creates agent roles for Row-Level Security (RLS):
--
-- GENERAL AGENT (full page access):
-- - agent_reader: SELECT only (read operations)
-- - agent_writer: INSERT/UPDATE/DELETE (write operations)
--
-- SUGGEST RESPONSE AGENT (conversation-scoped, restricted):
-- - suggest_response_reader: SELECT only, scoped to single conversation
-- - suggest_response_writer: Limited write, scoped to single conversation
--
-- SECURITY MODEL:
-- - General agent roles: Access all pages user has admin access to
-- - Suggest response roles: ONLY access data within the conversation being served
--   * Cannot access user_memory (global memory)
--   * Can only write to page_scope_user_memory (customer memory)
--   * Can insert escalations and blocks for its conversation
--
-- USAGE: Passwords are passed via psql variables
-- psql -v agent_reader_password="'pass'" -v agent_writer_password="'pass'" \
--      -v suggest_response_reader_password="'pass'" -v suggest_response_writer_password="'pass'" \
--      -f elripley.sql
-- ================================================================

-- ================================================================
-- GENERAL AGENT ROLES
-- ================================================================

-- Create agent_reader role if not exists
DO $$
BEGIN
    IF NOT EXISTS (SELECT FROM pg_catalog.pg_roles WHERE rolname = 'agent_reader') THEN
        CREATE ROLE agent_reader WITH LOGIN;
        RAISE NOTICE 'agent_reader role created';
    END IF;
END
$$;

-- Create agent_writer role if not exists
DO $$
BEGIN
    IF NOT EXISTS (SELECT FROM pg_catalog.pg_roles WHERE rolname = 'agent_writer') THEN
        CREATE ROLE agent_writer WITH LOGIN;
        RAISE NOTICE 'agent_writer role created';
    END IF;
END
$$;

-- Set passwords using psql variables (works outside DO block)
ALTER ROLE agent_reader WITH PASSWORD :agent_reader_password;
ALTER ROLE agent_writer WITH PASSWORD :agent_writer_password;

-- Grant connect to database (uses current database from psql variable)
GRANT CONNECT ON DATABASE :dbname TO agent_reader;
GRANT CONNECT ON DATABASE :dbname TO agent_writer;

-- Grant usage on public schema
GRANT USAGE ON SCHEMA public TO agent_reader;
GRANT USAGE ON SCHEMA public TO agent_writer;

-- ================================================================
-- SUGGEST RESPONSE AGENT ROLES (conversation-scoped)
-- ================================================================

-- Create suggest_response_reader role if not exists
DO $$
BEGIN
    IF NOT EXISTS (SELECT FROM pg_catalog.pg_roles WHERE rolname = 'suggest_response_reader') THEN
        CREATE ROLE suggest_response_reader WITH LOGIN;
        RAISE NOTICE 'suggest_response_reader role created';
    END IF;
END
$$;

-- Create suggest_response_writer role if not exists
DO $$
BEGIN
    IF NOT EXISTS (SELECT FROM pg_catalog.pg_roles WHERE rolname = 'suggest_response_writer') THEN
        CREATE ROLE suggest_response_writer WITH LOGIN;
        RAISE NOTICE 'suggest_response_writer role created';
    END IF;
END
$$;

-- Set passwords using psql variables
ALTER ROLE suggest_response_reader WITH PASSWORD :suggest_response_reader_password;
ALTER ROLE suggest_response_writer WITH PASSWORD :suggest_response_writer_password;

-- Grant connect to database
GRANT CONNECT ON DATABASE :dbname TO suggest_response_reader;
GRANT CONNECT ON DATABASE :dbname TO suggest_response_writer;

-- Grant usage on public schema
GRANT USAGE ON SCHEMA public TO suggest_response_reader;
GRANT USAGE ON SCHEMA public TO suggest_response_writer;

-- Note: SELECT/INSERT/UPDATE/DELETE grants on tables will be applied after tables are created
-- See end of elripley.sql for final grants
