-- ElRipley Database Schema
-- Generated from SQLAlchemy models: facebook.py and user.py
-- Database: PostgreSQL
-- 
-- This is the main schema file that imports all domain-specific schema files
-- Individual domain files are maintained separately for easier maintenance:
--   - 00_init_agent_role.sql (agent_reader and agent_writer roles for RLS)
--   - 01_schema_users.sql (users, roles, refresh_tokens, user_files, user_storage_usage, user_api_keys, user_conversation_settings)
--   - 02_schema_facebook.sql (Facebook integration tables)
--   - 03_schema_openai.sql (OpenAI integration tables)
--   - 04_schema_memory.sql (Memory domain: page_memory, user_memory, memory_blocks)
--   - 04a_schema_suggest_response.sql (Suggest response config, history, messages)
--   - 04b_schema_agent_comm.sql (Agent communication: conversation_agent_blocks, agent_escalations)
--   - 04d_schema_playbooks.sql (Playbooks: situational coaching for suggest_response_agent)
--   - 05_schema_media.sql (Media assets domain tables)
--   - 06_schema_billing_core.sql (Core billing tables - shared across providers)
--   - 07_schema_stripe.sql (Stripe-specific payment tables)
--   - 08_schema_sepay.sql (SePay VND bank transfer payment tables)
--   - 10_schema_polar.sql (Polar payment gateway tables)
--   - indexes.sql (all indexes - created after all tables)
--   - 99_rls_policies.sql (Row Level Security policies for agent_reader and agent_writer)
-- ================================================================

-- Initialize Agent Reader Role (must be first, before tables)
\i 00_init_agent_role.sql

-- Import Users Domain Schema (includes user files)
\i 01_schema_users.sql

-- Import Facebook Integration Domain Schema
\i 02_schema_facebook.sql

-- Import OpenAI Integration Domain Schema
\i 03_schema_openai.sql

-- Import Memory Domain Schema (page_memory, user_memory, memory_blocks)
\i 04_schema_memory.sql

-- Import Suggest Response Domain Schema (config, history, messages)
\i 04a_schema_suggest_response.sql

-- Import Agent Communication Domain Schema (blocks, escalations)
\i 04b_schema_agent_comm.sql

-- Import Playbooks Domain Schema (situational coaching for suggest_response_agent)
\i 04d_schema_playbooks.sql

-- Import Notifications Domain Schema (in-app notifications)
\i 09_schema_notifications.sql

-- Import Media Assets Domain Schema
\i 05_schema_media.sql

-- Import Core Billing Domain Schema (provider-agnostic)
\i 06_schema_billing_core.sql

-- Import Stripe Payment Domain Schema
\i 07_schema_stripe.sql

-- Import SePay Payment Domain Schema
\i 08_schema_sepay.sql

-- Import Polar Payment Domain Schema
\i 10_schema_polar.sql

-- Import All Indexes (must be after all table definitions)
\i indexes.sql

-- ================================================================
-- INITIAL DATA
-- ================================================================
-- Insert default roles
INSERT INTO
    roles (id, name)
VALUES
    ('550e8400-e29b-41d4-a716-446655440000', 'user'),
    ('550e8400-e29b-41d4-a716-446655440001', 'father');

-- ================================================================
-- AGENT ROLES GRANTS & RLS POLICIES
-- ================================================================
-- Grant permissions to agent_reader and agent_writer roles
-- Sensitive tables (users, tokens, admins) are NOT granted
-- This ensures agent roles can only access tables with RLS protection

-- Facebook tables (15 tables) - READ ONLY for both roles
GRANT SELECT ON fan_pages TO agent_reader, agent_writer;
GRANT SELECT ON posts TO agent_reader, agent_writer;
GRANT SELECT ON comments TO agent_reader, agent_writer;
GRANT SELECT ON messages TO agent_reader, agent_writer;
GRANT SELECT ON facebook_page_scope_users TO agent_reader, agent_writer;
GRANT SELECT ON facebook_app_scope_users TO agent_reader, agent_writer;
GRANT SELECT ON facebook_page_admins TO agent_reader, agent_writer;
GRANT SELECT ON facebook_conversation_messages TO agent_reader, agent_writer;
GRANT SELECT ON facebook_conversation_comments TO agent_reader, agent_writer;
GRANT SELECT ON facebook_conversation_comment_entries TO agent_reader, agent_writer;
GRANT SELECT ON post_reactions TO agent_reader, agent_writer;
GRANT SELECT ON comment_reactions TO agent_reader, agent_writer;
GRANT SELECT ON facebook_post_sync_states TO agent_reader, agent_writer;
GRANT SELECT ON facebook_post_comment_sync_states TO agent_reader, agent_writer;
GRANT SELECT ON facebook_inbox_sync_states TO agent_reader, agent_writer;

-- Memory tables (4 tables) - READ for both, WRITE for agent_writer only
GRANT SELECT ON page_memory TO agent_reader, agent_writer;
GRANT SELECT ON page_scope_user_memory TO agent_reader, agent_writer;
GRANT SELECT ON user_memory TO agent_reader, agent_writer;
GRANT SELECT ON memory_blocks TO agent_reader, agent_writer;
GRANT INSERT ON page_memory TO agent_writer;
GRANT INSERT ON page_scope_user_memory TO agent_writer;
GRANT INSERT ON user_memory TO agent_writer;
GRANT INSERT ON memory_blocks TO agent_writer;
GRANT UPDATE ON page_memory TO agent_writer;
GRANT UPDATE ON page_scope_user_memory TO agent_writer;
GRANT UPDATE ON user_memory TO agent_writer;
GRANT UPDATE ON memory_blocks TO agent_writer;
GRANT DELETE ON memory_blocks TO agent_writer;

-- Suggest Response tables (4 tables) - READ for both roles, WRITE for agent_writer on page_admin_suggest_config
GRANT SELECT ON suggest_response_agent TO agent_reader, agent_writer;
GRANT SELECT ON page_admin_suggest_config TO agent_reader, agent_writer;
GRANT INSERT, UPDATE ON page_admin_suggest_config TO agent_writer;
GRANT SELECT ON suggest_response_history TO agent_reader, agent_writer;
GRANT SELECT ON suggest_response_message TO agent_reader, agent_writer;

-- Conversation Agent Blocks table - READ/WRITE for agent roles
GRANT SELECT ON conversation_agent_blocks TO agent_reader, agent_writer;
GRANT INSERT, UPDATE ON conversation_agent_blocks TO agent_writer;

-- Agent Escalations table - READ/UPDATE for agent roles (only suggest_response_agent creates escalations)
GRANT SELECT ON agent_escalations TO agent_reader, agent_writer;
GRANT UPDATE ON agent_escalations TO agent_writer;

-- Agent Escalation Messages table - READ/WRITE for agent roles
GRANT SELECT ON agent_escalation_messages TO agent_reader, agent_writer;
GRANT INSERT ON agent_escalation_messages TO agent_writer;

-- Playbook tables (2 tables) - READ for both, FULL CRUD for agent_writer
GRANT SELECT ON page_playbooks TO agent_reader, agent_writer;
GRANT SELECT ON page_playbook_assignments TO agent_reader, agent_writer;
GRANT INSERT, UPDATE ON page_playbooks TO agent_writer;
GRANT INSERT, UPDATE ON page_playbook_assignments TO agent_writer;

-- Media Assets tables (2 tables) - READ for both, WRITE for agent_writer only
GRANT SELECT ON media_assets TO agent_reader, agent_writer;
GRANT SELECT ON memory_block_media TO agent_reader, agent_writer;
GRANT INSERT ON memory_block_media TO agent_writer;
GRANT DELETE ON memory_block_media TO agent_writer;

-- Explicitly REVOKE access to sensitive tables (in case they were granted before)
REVOKE SELECT ON users FROM agent_reader, agent_writer;
REVOKE SELECT ON refresh_tokens FROM agent_reader, agent_writer;

-- ================================================================
-- SUGGEST RESPONSE AGENT ROLES GRANTS (MINIMAL ACCESS)
-- ================================================================
-- These roles have MINIMAL access - only 7 tables within conversation scope.
-- All other data (messages, comments, page_memory, etc.) is pre-built
-- into the agent's context by context_builder.py using system connection.
--
-- Tables accessible:
-- 1. page_scope_user_memory - Customer memory for current PSID
-- 2. memory_blocks - Memory blocks for page_scope_user_memory only
-- 3. memory_block_media - Media attachments for memory blocks
-- 4. media_assets - Media details (S3 URLs) for view_media tool
-- 5. conversation_agent_blocks - Block current conversation
-- 6. agent_escalations - Escalation thread headers (create + close)
-- 7. agent_escalation_messages - Messages within escalation threads

-- page_scope_user_memory - READ/WRITE for current PSID's memory
GRANT SELECT ON page_scope_user_memory TO suggest_response_reader, suggest_response_writer;
GRANT INSERT, UPDATE ON page_scope_user_memory TO suggest_response_writer;

-- memory_blocks - READ/WRITE for user_prompt type only (page_scope_user_memory)
GRANT SELECT ON memory_blocks TO suggest_response_reader, suggest_response_writer;
GRANT INSERT, UPDATE, DELETE ON memory_blocks TO suggest_response_writer;

-- memory_block_media - READ/WRITE for attaching media to memory blocks
GRANT SELECT ON memory_block_media TO suggest_response_reader, suggest_response_writer;
GRANT INSERT, DELETE ON memory_block_media TO suggest_response_writer;

-- media_assets - READ only (to view media details, S3 URLs)
GRANT SELECT ON media_assets TO suggest_response_reader, suggest_response_writer;

-- conversation_agent_blocks - READ/INSERT (can block current conversation)
GRANT SELECT ON conversation_agent_blocks TO suggest_response_reader, suggest_response_writer;
GRANT INSERT ON conversation_agent_blocks TO suggest_response_writer;

-- agent_escalations - READ/INSERT/UPDATE (can create escalations and close them)
GRANT SELECT ON agent_escalations TO suggest_response_reader, suggest_response_writer;
GRANT INSERT, UPDATE ON agent_escalations TO suggest_response_writer;

-- agent_escalation_messages - READ/INSERT (can send messages as suggest_response_agent)
GRANT SELECT ON agent_escalation_messages TO suggest_response_reader, suggest_response_writer;
GRANT INSERT ON agent_escalation_messages TO suggest_response_writer;

-- page_playbooks - READ only (agent can query playbooks for situational guidance)
GRANT SELECT ON page_playbooks TO suggest_response_reader, suggest_response_writer;

-- page_playbook_assignments - READ only (to find which playbooks apply to current page)
GRANT SELECT ON page_playbook_assignments TO suggest_response_reader, suggest_response_writer;

-- Apply Row Level Security policies for general agent
\i 99_rls_policies.sql

-- Apply Row Level Security policies for suggest response agent
\i 99_rls_suggest_response.sql

-- ================================================================
-- COMMENTS AND DOCUMENTATION
-- ================================================================
-- Table relationships:
-- 1. users (1) <-> (M) user_role <-> (M) roles (many-to-many)
-- 2. users (1) -> (1) facebook_app_scope_users (one-to-one)
-- 3. facebook_app_scope_users (1) -> (M) facebook_page_admins (one-to-many)
-- 4. fan_pages (1) -> (M) facebook_page_admins (one-to-many)
-- 5. fan_pages (1) -> (M) facebook_page_scope_users (one-to-many)
-- 6. fan_pages (1) -> (M) facebook_conversation_messages (one-to-many)
-- 7. facebook_page_scope_users (1) -> (M) facebook_conversation_messages (one-to-many)
-- 8. facebook_conversation_messages (1) -> (M) messages (one-to-many)
-- 9. fan_pages (1) -> (M) posts (one-to-many)
-- 10. posts (1) -> (M) comments (one-to-many)
-- 11. fan_pages (1) -> (M) comments (one-to-many, direct reference)
-- 12. comments (1) -> (M) comments (one-to-many, for replies)
-- 13. facebook_page_scope_users (1) -> (M) comments (one-to-many, optional, when not from page)
-- 14. users (1) -> (M) openai_response (one-to-many)
-- 15. users (1) -> (M) openai_conversation (one-to-many)
-- 16. openai_conversation (1) -> (M) openai_response (one-to-many)
-- 17. openai_conversation (1) -> (M) openai_message (one-to-many)
-- 18. openai_conversation (1) -> (M) openai_conversation_branch (one-to-many)
-- 19. openai_conversation_branch (1) -> (M) openai_conversation_branch (one-to-many, self-referencing for created_from_branch)
-- 20. openai_conversation_branch (1) -> (M) openai_branch_message_mapping (one-to-many)
-- 21. openai_message (1) -> (M) openai_branch_message_mapping (one-to-many)
-- (removed) openai_conversation_selected_fb_conv / openai_conversation_selected_fb_comm
-- 22. openai_conversation (1) -> (M) agent_response (one-to-many)
-- 23. openai_conversation_branch (1) -> (M) agent_response (one-to-many)
-- 24. agent_response (1) -> (M) openai_response (one-to-many)
-- 25. openai_conversation_branch (1) -> (M) openai_response (one-to-many, branch-level logging)
-- Conversations table design notes:
-- - Represents a conversation thread between a page and a user
-- - id column uses the native Graph conversation id (t_*)
-- - Unique constraint on (fan_page_id, facebook_page_scope_user_id) ensures one conversation per page-user pair
-- - participants_snapshot caches Graph participants payload for quick UI render
-- - unread_count: number of user messages that page admins still need to read
-- - user_unread_count: number of page messages that the end user hasn't read (from webhook read receipts)
-- - page_last_seen_message_id / user_last_seen_message_id: snapshot of reciprocal read positions
-- - facebook_updated_time / facebook_unread_count help reconcile with native inbox metrics
-- - Soft delete: deleted_at timestamp instead of hard delete
-- - CASCADE delete for page and user (remove conversation if either is deleted)
-- Posts table design notes:
-- - Simple design for message/comment management
-- - Created when comments are received, not when posts are published
-- - message: stores post text content
-- - video_link: stores video URL if post contains video
-- - photo_link: stores photo URL if post contains photo  
-- - facebook_created_time: uses webhook created_time (Unix timestamp)
-- Comments table design notes:
-- - Handles diverse content: text (message), photo (photo_url), video (video_url)
-- - Comment author identification:
--   * is_from_page = TRUE: comment from page itself, facebook_page_scope_user_id = NULL
--   * is_from_page = FALSE: comment from user, facebook_page_scope_user_id = PSID
-- - Soft delete: deleted_at timestamp instead of hard delete
-- - is_hidden: allows hiding comments without deleting (moderation)
-- - mark_as_read: tracks read status for comment management
-- - parent_comment_id: supports threaded/reply comments (self-referencing)
-- - fan_page_id: direct reference to fan page for performance (avoids join through posts)
-- - ON DELETE SET NULL for user reference (preserve comment if user deleted)
-- - CASCADE delete for post and fan page (remove comments if either deleted)
-- Messages table design notes:
-- - Handles diverse content: text, photos (images/gifs/stickers), audio, video
-- - conversation_id: references facebook_conversation_messages table (many-to-one relationship)
-- - Message sender identification:
--   * is_echo = TRUE: message from page itself
--   * is_echo = FALSE: message from user
-- - Content fields:
--   * text: text content
--   * photo_url: any image URL (photo/gif/sticker - no distinction needed)
--   * video_url: video URL
--   * audio_url: audio URL
--   * template_data: JSONB field for templates, quick replies, postbacks (optional)
-- - Regular messages: use text/photo_url/video_url/audio_url, template_data = NULL
-- - Interactive messages: use template_data to store full webhook data
-- - JSONB benefits: indexable, queryable with -> and ->> operators
-- - Read receipts:
--   * page_seen_at: when page admins viewed a user message
--   * user_seen_at: when the end user viewed a page message (from Facebook read webhook)
-- - Soft delete: deleted_at timestamp instead of hard delete
-- - facebook_timestamp: uses webhook timestamp (BIGINT milliseconds)
-- - CASCADE delete for conversation (remove messages if conversation deleted)
-- OpenAI response table design notes:
-- - Tracks OpenAI API responses for cost monitoring and billing
-- - response_id: unique OpenAI response identifier
-- - conversation_id: optional link to conversation (SET NULL on conversation delete)
-- - agent_response_id: optional link to agent_response (SET NULL on agent_response delete)
-- - Token usage tracking: input_tokens, output_tokens, reasoning_tokens, total_tokens
-- - Cost tracking: input_cost, output_cost, total_cost (in USD)
-- - JSONB fields: input (request data), output (response data), tools, metadata, error
-- - Status tracking: completed, failed, in_progress
-- - CASCADE delete for user (remove responses if user deleted)
-- - SET NULL for conversation and agent_response (preserve response history if deleted)
-- OpenAI conversation table design notes:
-- - Represents conversation threads between users and the assistant
-- - title: optional conversation title for user organization
-- - developer_message: developer message for conversation context
-- - created_at/updated_at: timestamps for conversation lifecycle
-- - CASCADE delete for user (remove facebook_conversation_messages if user deleted)
-- OpenAI message table design notes:
-- - Individual messages within conversations
-- - role: system, developer, user, assistant, or tool (with CHECK constraint)
-- - content: the actual message content
-- - recipient: optional field for tool messages (e.g., function name)
-- - settings: optional JSONB for message-specific settings
-- - end_turn: boolean indicating if assistant should yield control
-- - CASCADE delete for conversation (remove messages if conversation deleted)
-- OpenAI conversation branching system design notes:
-- - openai_conversation_branch: Manages conversation branches with hierarchical structure
--   * created_from_message_id: The message where this branch was created from
--   * created_from_branch_id: The branch that contains the message where this branch was created from
--   * is_active: Only one branch can be active per conversation
--   * branch_name: Optional human-readable name for the branch
--   * message_ids: Array of message IDs in order within this branch
-- - openai_branch_message_mapping: Many-to-many relationship between messages and branches
--   * UNIQUE(message_id, branch_id): Prevents duplicate mappings
--   * Messages can belong to multiple branches (shared ancestor messages)
--   * is_modified: Whether message is modified in this branch
--   * modified_content: Modified content (if is_modified = TRUE)
--   * modified_reasoning_summary: Modified reasoning summary (if present)
--   * modified_function_arguments: Modified function arguments (if present)
--   * modified_function_output: Modified function output (if present)
--   * is_hidden: Whether message is hidden in this branch
--   * CASCADE delete ensures cleanup when message or branch is deleted
-- - current_branch_id: Added to openai_conversation for quick active branch lookup
-- - Branching workflow:
--   1. Create branch from user message (only user messages can be branched from)
--   2. Copy all ancestor message IDs to new branch via message_ids array
--   3. Create mappings for all ancestor messages in openai_branch_message_mapping
--   4. New messages added to active branch only (append to message_ids array + create mapping)
--   5. Edit/hide operations apply only to specific branch via mapping modifications
--   6. Switch between branches to see different conversation paths
-- - Array operations:
--   * Create branch: Copy ancestor message IDs to message_ids array
--   * Add message: Append new message ID to message_ids array
--   * Query messages: Use array operations to get messages in order
-- - Performance considerations:
--   * GIN index on message_ids for fast array operations
--   * Array operations are efficient for branch creation and message addition
--   * Single mapping table handles both message relationships and modifications
-- Agent response table design notes:
-- - Tracks each time the agent is triggered/executed
-- - Represents a single agent execution within a conversation and branch context
-- - conversation_id: Links to the OpenAI conversation where agent was triggered
-- - branch_id: Links to the specific branch where agent was triggered (especially important for branching)
-- - message_ids: Array of message IDs generated during this agent response execution
-- - One agent_response can have many openai_response records (multiple API calls per agent execution)
-- - CASCADE delete for conversation and branch (remove agent responses if either deleted)
-- - Used for tracking agent execution context and grouping related API responses
-- Key constraints:
-- - Unique constraint on facebook_page_admins (facebook_user_id, page_id)
-- - Unique constraint on facebook_conversation_messages (fan_page_id, facebook_page_scope_user_id)
-- - Cascade deletes for all foreign key relationships
-- - Default values for roles
-- - CHECK constraint on openai_message.role for valid message roles
-- Data types:
-- - All IDs use VARCHAR for flexibility (UUIDs for internal, Facebook IDs for external)
-- - Timestamps stored as INTEGER (Unix timestamps)
-- - JSONB columns for flexible data storage (tasks, user_info, template_data)
-- - TEXT for potentially long access tokens and post content
