Execute SQL queries on the database. Data is automatically filtered by Row-Level Security (RLS) - you only see data belonging to pages the current user manages.

## MODES

### READ mode
- SELECT queries only
- Returns rows and columns
- Use for data exploration, analysis, reporting

### WRITE mode
- INSERT/UPDATE/DELETE queries
- **ATOMIC TRANSACTION**: All queries succeed or all fail together (rollback on any error)
- Supports RETURNING clause to get affected rows
- **Can execute multiple queries in one call** - pass array of SQL statements for related operations
- Use for memory management: create/update/delete memory blocks, attach/detach media

## PERFORMANCE TIPS
- **Batch related queries**: Pass multiple queries in one call when they're related (e.g., insert memory block + attach media)
- **Use LIMIT**: Always limit results to avoid large payloads
- **Filter early**: Use WHERE clauses to narrow down data before JOINs
- **Index columns**: fan_page_id, post_id, conversation_id, owner_user_id are indexed - filter on these first

## SECURITY & PERMISSIONS
- READ: SELECT on all accessible tables
- WRITE: 
  - Memory tables: page_memory, page_scope_user_memory, user_memory, memory_blocks, memory_block_media
  - Suggest config: page_admin_suggest_config (INSERT, UPDATE — no DELETE)
  - Agent communication tables: conversation_agent_blocks (INSERT, UPDATE), agent_escalations (UPDATE only), agent_escalation_messages (INSERT)
- DDL (CREATE/DROP/ALTER): Blocked
- RLS: Auto-filters to user's accessible pages/data

## DATABASE SCHEMA

### 1. FACEBOOK TABLES (Read-only)

#### fan_pages
Columns: id (page_id), name, avatar, category, fan_count, followers_count, rating_count, overall_star_rating, about, description, link, website, phone, emails (JSONB), location (JSONB), cover, hours (JSONB), is_verified, created_at, updated_at

#### posts
Columns: id (post_id), fan_page_id, message, video_link, photo_link, facebook_created_time, reaction_total_count, reaction_like/love/haha/wow/sad/angry/care_count, share_count, comment_count, full_picture, permalink_url, status_type, is_published, reactions_fetched_at, engagement_fetched_at, created_at, updated_at

#### comments
Columns: id, post_id, parent_comment_id (NULL=top-level), **is_from_page** (BOOL), fan_page_id, facebook_page_scope_user_id (NULL if from page), message, photo_url, video_url, facebook_created_time, like_count, reply_count, reactions_fetched_at, **is_hidden**, page_seen_at, **deleted_at**, created_at, updated_at

#### messages
Columns: id (mid), conversation_id, **is_echo** (TRUE=from page), text, photo_url, video_url, audio_url, template_data (JSONB), facebook_timestamp (BIGINT ms), page_seen_at, **reply_to_message_id** (VARCHAR, nullable — mid of the message this one replies to; only present for realtime webhook messages, NULL for Graph API synced history), **deleted_at**, created_at, updated_at

#### facebook_page_scope_users (PSID)
Columns: id (PSID), fan_page_id, user_info (JSONB: name, profile_pic), created_at, updated_at

#### facebook_page_admins
Columns: id, facebook_user_id (FK→facebook_app_scope_users), page_id (FK→fan_pages), access_token, tasks (JSONB: e.g., ["MANAGE", "MESSAGING", "CREATE_CONTENT"]), created_at, updated_at
- Links Facebook users to pages they manage
- UNIQUE(facebook_user_id, page_id)

#### facebook_conversation_messages
Columns: id (t_*), fan_page_id, facebook_page_scope_user_id, participants_snapshot (JSONB), latest_message_is_from_page, latest_message_id, latest_message_facebook_time (BIGINT), page_last_seen_message_id, page_last_seen_at, user_seen_at, mark_as_read, ad_context (JSONB), **deleted_at**, created_at, updated_at

#### facebook_conversation_comments
Columns: id (UUID), root_comment_id, fan_page_id, post_id, participant_scope_users (JSONB), has_page_reply, latest_comment_is_from_page, latest_comment_id, latest_comment_facebook_time, page_last_seen_comment_id, page_last_seen_at, mark_as_read, created_at, updated_at

#### facebook_conversation_comment_entries
Columns: id (UUID), conversation_id (FK→facebook_conversation_comments), comment_id (FK→comments), is_root_comment (BOOL), created_at, updated_at
- Junction table linking comment conversations to individual comments
- UNIQUE(conversation_id, comment_id)

#### post_reactions
Columns: id, post_id, fan_page_id, reactor_id (PSID, NULL=page), reactor_name, reactor_profile_pic, reaction_type (LIKE/LOVE/HAHA/WOW/SAD/ANGRY/CARE), created_at, updated_at

#### comment_reactions
Columns: id, comment_id, post_id, fan_page_id, reactor_id (PSID, NULL=page), reactor_name, reaction_type (LIKE/LOVE/HAHA/WOW/SAD/ANGRY/CARE), created_at, updated_at

#### Sync state tables (facebook_post_sync_states, facebook_post_comment_sync_states, facebook_inbox_sync_states)
Track sync progress: status (idle/in_progress/completed), cursors, counts, last_sync_at

### 2. MEMORY TABLES (Writable)

**Append-only pattern**: Update memory = create new record with is_active=TRUE, then set old record is_active=FALSE

#### page_memory
Columns: id, fan_page_id, owner_user_id, prompt_type ('messages'|'comments'), created_by_type, is_active, created_at

#### page_scope_user_memory
Columns: id, fan_page_id, facebook_page_scope_user_id, owner_user_id, created_by_type, is_active, created_at
- Messages only (not comments)

#### user_memory
Columns: id, owner_user_id, created_by_type, is_active, created_at
- Global memory for general agent

#### memory_blocks
Columns: id, prompt_type ('page_prompt'|'user_prompt'|'user_memory'), prompt_id, block_key, title, content, display_order, created_at, created_by_type
- Polymorphic: prompt_type determines parent table

#### memory_block_media
Columns: id, block_id, media_id, display_order, created_at
- INSERT to attach, DELETE to detach (media file preserved)

### 3. SUGGEST RESPONSE TABLES

#### suggest_response_agent (Read-only)
Columns: id, user_id (UNIQUE), settings (JSONB), allow_auto_suggest, num_suggest_response, created_at, updated_at

#### suggest_response_history (Read-only)
Columns: id, user_id, fan_page_id, conversation_type, facebook_conversation_messages_id, facebook_conversation_comments_id, latest_item_id, latest_item_facebook_time (BIGINT), page_prompt_id (UUID, FK→page_memory), page_scope_user_prompt_id (UUID, FK→page_scope_user_memory, messages only), suggestions (JSONB array), suggestion_count, agent_response_id (UUID, FK→agent_response), trigger_type ('user'|'auto'|'webhook_suggest'|'webhook_auto_reply'), selected_suggestion_index, reaction ('like'|'dislike'), created_at, updated_at

#### page_admin_suggest_config (Writable: INSERT, UPDATE — no DELETE)
Columns: id (UUID, auto-generated), page_admin_id (FK→facebook_page_admins, UNIQUE), settings (JSONB), auto_webhook_suggest (BOOL), auto_webhook_graph_api (BOOL), webhook_delay_seconds (INT, default 5), created_at, updated_at
- Per-page admin suggest response configuration
- auto_webhook_suggest: auto-trigger suggest on webhook (requires admin online)
- auto_webhook_graph_api: auto-trigger suggest AND send reply via Graph API (no admin online required)
- Priority: auto_webhook_graph_api > auto_webhook_suggest
- RLS: Can only INSERT/UPDATE configs for your own page_admin records

**Common queries:**
```sql
-- Read current config for a page admin
SELECT * FROM page_admin_suggest_config WHERE page_admin_id = 'PAGE_ADMIN_ID';

-- Create config for a page admin (id is auto-generated)
INSERT INTO page_admin_suggest_config (page_admin_id, auto_webhook_suggest, auto_webhook_graph_api, webhook_delay_seconds)
VALUES ('PAGE_ADMIN_ID', TRUE, FALSE, 5)
RETURNING *;

-- Update webhook settings
UPDATE page_admin_suggest_config
SET auto_webhook_suggest = TRUE,
    auto_webhook_graph_api = FALSE,
    webhook_delay_seconds = 10,
    updated_at = EXTRACT(EPOCH FROM NOW())::BIGINT * 1000
WHERE page_admin_id = 'PAGE_ADMIN_ID'
RETURNING *;

-- Update agent settings (model, reasoning, verbosity)
UPDATE page_admin_suggest_config
SET settings = '{"model": "gpt-5.2", "reasoning": "medium", "verbosity": "medium"}'::jsonb,
    updated_at = EXTRACT(EPOCH FROM NOW())::BIGINT * 1000
WHERE page_admin_id = 'PAGE_ADMIN_ID'
RETURNING *;

-- Upsert: create if not exists, update if exists
INSERT INTO page_admin_suggest_config (page_admin_id, auto_webhook_suggest, webhook_delay_seconds)
VALUES ('PAGE_ADMIN_ID', TRUE, 5)
ON CONFLICT (page_admin_id) DO UPDATE SET
    auto_webhook_suggest = EXCLUDED.auto_webhook_suggest,
    webhook_delay_seconds = EXCLUDED.webhook_delay_seconds,
    updated_at = EXTRACT(EPOCH FROM NOW())::BIGINT * 1000
RETURNING *;
```

#### suggest_response_message (Read-only)
Columns: id, history_id (FK→suggest_response_history), sequence_number (INT), role ('assistant'|'tool'), type ('reasoning'|'function_call'|'function_call_output'|'text'), content (JSONB), metadata (JSONB), reasoning_summary (JSONB), call_id, function_name, function_arguments (JSONB), function_output (JSONB), web_search_action (JSONB), status, created_at
- Detailed message breakdown of suggest_response_agent execution steps
- UNIQUE(history_id, sequence_number)

### 4. AGENT COMMUNICATION TABLES (Writable)

These tables enable communication between you (general_agent) and suggest_response_agent.

#### conversation_agent_blocks
Block conversations from triggering suggest_response_agent. Use when conversation is spam, abuse, or should not receive AI suggestions.

Columns: id, conversation_type ('messages'|'comments'), facebook_conversation_messages_id, facebook_conversation_comments_id, fan_page_id, blocked_by ('suggest_response_agent'|'general_agent'|'user'), reason (TEXT), is_active (BOOL, default TRUE), created_at, updated_at

**Notes:**
- Set `is_active = FALSE` to unblock
- Either `facebook_conversation_messages_id` OR `facebook_conversation_comments_id` is set based on `conversation_type`

#### agent_escalations (UPDATE only - you cannot INSERT)
Escalation thread headers. suggest_response_agent creates these when it needs help. You read them and respond via `agent_escalation_messages`.

Columns: id, conversation_type ('messages'|'comments'), facebook_conversation_messages_id, facebook_conversation_comments_id, fan_page_id, owner_user_id, created_by ('suggest_response_agent'|'general_agent'|'user'), subject (VARCHAR 500), priority ('low'|'normal'|'high'|'urgent'), status ('open'|'closed'), suggest_response_history_id (UUID, optional), created_at, updated_at

**Notes:**
- Status model: `open` = actively needs attention (loaded into agent context), `closed` = resolved/stale (NOT loaded into context)
- You can only UPDATE (close escalations by setting `status = 'closed'`)
- You CANNOT INSERT new escalations — only suggest_response_agent creates them

#### agent_escalation_messages (INSERT only)
Messages within an escalation thread. Enables two-way communication: each side sends messages with its own sender_type.

Columns: id, escalation_id (FK→agent_escalations), sender_type ('suggest_response_agent'|'general_agent'|'user'), content (TEXT), context_snapshot (JSONB, optional), created_at

**Notes:**
- RLS enforces: you can only INSERT with `sender_type IN ('general_agent', 'user')` — cannot impersonate suggest_response_agent
- Read all messages (from all sender_types) for escalations you have access to

**Workflow:**
1. suggest_response_agent creates escalation (subject, priority) and first message
2. You query open escalations: `WHERE status = 'open'`
3. You read the thread: SELECT from `agent_escalation_messages WHERE escalation_id = ...`
4. You respond: INSERT into `agent_escalation_messages` with your `content` and `sender_type = 'general_agent'`
5. Optionally close: UPDATE `agent_escalations SET status = 'closed'`
6. suggest_response_agent reads your messages in future triggers

**Common queries:**
```sql
-- List open escalations for a page
SELECT id, conversation_type, subject, priority, created_at
FROM agent_escalations
WHERE fan_page_id = 'PAGE_ID' AND status = 'open'
ORDER BY CASE priority WHEN 'urgent' THEN 1 WHEN 'high' THEN 2 WHEN 'normal' THEN 3 ELSE 4 END, created_at;

-- Read messages in an escalation thread
SELECT sender_type, content, created_at
FROM agent_escalation_messages
WHERE escalation_id = 'ESCALATION_ID'::uuid
ORDER BY created_at;

-- Respond to an escalation
INSERT INTO agent_escalation_messages (escalation_id, sender_type, content)
VALUES ('ESCALATION_ID'::uuid, 'general_agent', 'Your response here');

-- Close an escalation after responding
UPDATE agent_escalations
SET status = 'closed',
    updated_at = EXTRACT(EPOCH FROM NOW())::BIGINT * 1000
WHERE id = 'ESCALATION_ID'::uuid;

-- Block a problematic conversation
INSERT INTO conversation_agent_blocks (conversation_type, facebook_conversation_messages_id, fan_page_id, blocked_by, reason)
VALUES ('messages', 'CONV_ID', 'PAGE_ID', 'general_agent', 'Spam detected');
```

### 5. MEDIA ASSETS TABLES

#### media_assets (Read-only)
Columns: id, user_id, source_type, media_type, s3_url, description (AI-generated), status ('pending'|'ready'|'failed'), retention_policy, created_at, updated_at

## TECHNICAL NOTES

### Current User ID
Reference current user ID in queries:
```sql
current_setting('app.current_user_id', true)
```
Use when filtering/inserting by `owner_user_id` or `user_id` columns.

### Timestamps
| Context | Type | Unit |
|---------|------|------|
| Facebook tables created_at/updated_at | INTEGER | seconds |
| Memory/Suggest/Media created_at | BIGINT | milliseconds |
| facebook_timestamp (messages) | BIGINT | milliseconds |
| facebook_created_time (posts/comments) | INTEGER | seconds |

### Soft Delete
Tables with `deleted_at` column: messages, comments, facebook_conversation_messages
- Filter active records: `WHERE deleted_at IS NULL`

### Boolean Flags
- `is_from_page` / `is_echo`: TRUE = from page, FALSE = from user
- `is_hidden`: Comment hidden by moderation
- `is_active`: Memory version active (append-only pattern)
- `mark_as_read`: User manually toggled read state

### UUID Columns
Memory tables use UUID type for `id`, `block_id`, `media_id`, `prompt_id` columns.
When inserting/updating UUID columns with string literals, you MUST cast them:
```sql
-- WRONG: INSERT INTO memory_block_media (block_id, media_id, display_order) VALUES ('abc-123', 'def-456', 1);
-- CORRECT: Cast string literals to UUID
INSERT INTO memory_block_media (block_id, media_id, display_order)
VALUES ('abc-123'::uuid, 'def-456'::uuid, 1);
```

### JSONB Queries
```sql
-- Extract field: user_info->>'name'
-- Check key exists: user_info ? 'name'
-- Contains: settings @> '{"model": "gpt-5"}'
```

### Returns
- **READ**: `{"success": true, "row_count": N, "rows": [...], "columns": [...]}`
- **WRITE**: `{"success": true, "results": [{"affected": N, "rows": [...], "columns": [...]}, ...]}`
  - One result per SQL statement; `rows`/`columns` included if RETURNING clause used
- **Error**: `{"success": false, "error": "message", "error_type": "PostgresError|ValueError"}`
