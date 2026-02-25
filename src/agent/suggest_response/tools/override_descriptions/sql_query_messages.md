Execute SQL queries within your conversation scope. You have access to **7 tables only**, all other data is pre-built into your context.

## MODES

### READ mode
- SELECT queries only

### WRITE mode
- INSERT/UPDATE/DELETE queries
- **ATOMIC TRANSACTION**: All queries succeed or all fail
- Supports RETURNING clause
- Can execute multiple **independent** queries in one call — but you CANNOT use the RETURNING value of one statement in another statement within the same call (see "Multi-Step Operations" below)

## AUTO-FILLED COLUMNS (DO NOT provide these)

RLS sets these columns automatically via session variables:

**For page_scope_user_memory:**
- `fan_page_id` - auto-filled
- `facebook_page_scope_user_id` - auto-filled (PSID)
- `owner_user_id` - auto-filled

**For agent_escalations:**
- `owner_user_id` - auto-filled
- `fan_page_id` - auto-filled
- `conversation_type` - auto-filled ('messages')
- `facebook_conversation_messages_id` - auto-filled
- `created_by` - auto-filled ('suggest_response_agent')

**For agent_escalation_messages:**
- `sender_type` - auto-filled ('suggest_response_agent')

**For conversation_agent_blocks:**
- `fan_page_id` - auto-filled
- `conversation_type` - auto-filled
- `facebook_conversation_messages_id` - auto-filled

**For memory_blocks:**
- `prompt_type` - defaults to 'user_prompt'
- `created_by_type` - defaults to 'agent'

## TABLE SCHEMAS

### page_scope_user_memory (SELECT, INSERT, UPDATE)
Customer memory for current PSID.
```
id: UUID (auto-generated)
fan_page_id: VARCHAR(255) - auto-filled
facebook_page_scope_user_id: VARCHAR(255) - PSID, auto-filled
owner_user_id: VARCHAR(36) - auto-filled
created_by_type: VARCHAR(20) - 'user' | 'agent'
is_active: BOOLEAN (default TRUE)
created_at: BIGINT (ms)
```

### memory_blocks (SELECT, INSERT, UPDATE, DELETE)
Content blocks for memory. **Has NO `is_active` column.** Only parent tables have `is_active`. When joining, filter by parent's `is_active = TRUE`.
```
id: UUID (auto-generated)
prompt_type: VARCHAR(30) - auto-filled ('user_prompt')
prompt_id: UUID - references parent memory table
block_key: VARCHAR(100) - stable identifier
title: VARCHAR(255) - human-readable title
content: TEXT - the actual content
display_order: INTEGER (default 0)
created_at: BIGINT (ms)
created_by_type: VARCHAR(20) - auto-filled ('agent')
```

### memory_block_media (SELECT, INSERT, DELETE)
Attach/detach media to memory blocks.
```
id: UUID (auto-generated)
block_id: UUID - references memory_blocks
media_id: UUID - references media_assets
display_order: INTEGER (default 0)
created_at: BIGINT (ms)
```

### media_assets (SELECT only)
Media details - use `s3_url` in suggestions.
```
id: UUID
user_id: VARCHAR(36)
source_type: VARCHAR(50)
media_type: VARCHAR(50)
s3_url: VARCHAR(1024) - use this URL in suggestions
description: TEXT - AI-generated description
status: VARCHAR(20) - 'pending' | 'ready' | 'failed'
retention_policy: VARCHAR(50)
created_at: BIGINT (ms)
updated_at: BIGINT (ms)
```

### conversation_agent_blocks (SELECT, INSERT)
Block this conversation from future agent triggers.
```
id: UUID (auto-generated)
conversation_type: VARCHAR(20) - auto-filled
facebook_conversation_messages_id: VARCHAR(255) - auto-filled
fan_page_id: VARCHAR(255) - auto-filled
blocked_by: VARCHAR(50) - 'suggest_response_agent' | 'general_agent' | 'user'
reason: TEXT - why blocked
is_active: BOOLEAN (default TRUE)
created_at: BIGINT (ms)
updated_at: BIGINT (ms)
```

### agent_escalations (SELECT, INSERT, UPDATE)
Escalation thread headers. You create threads, add messages, and close when done.
```
id: UUID (auto-generated)
conversation_type: VARCHAR(20) - auto-filled ('messages')
facebook_conversation_messages_id: VARCHAR(255) - auto-filled
fan_page_id: VARCHAR(255) - auto-filled
owner_user_id: VARCHAR(36) - auto-filled
created_by: VARCHAR(50) - auto-filled ('suggest_response_agent')
subject: VARCHAR(500) - brief summary for operator dashboard (required)
priority: VARCHAR(20) - 'low' | 'normal' | 'high' | 'urgent' (default 'normal')
status: VARCHAR(20) - 'open' | 'closed' (default 'open')
suggest_response_history_id: UUID - optional audit link
created_at: BIGINT (ms)
updated_at: BIGINT (ms)
```

### agent_escalation_messages (SELECT, INSERT)
Messages within escalation threads. Two-way: you send, operator/general_agent responds.
```
id: UUID (auto-generated)
escalation_id: UUID - FK to agent_escalations
sender_type: VARCHAR(50) - auto-filled ('suggest_response_agent' via RLS)
content: TEXT - message content (required)
context_snapshot: JSONB - optional data snapshot
created_at: BIGINT (ms)
```

## TECHNICAL NOTES

### Timestamps
All `created_at` / `updated_at` columns are **BIGINT milliseconds**.

### UUID Casting
When using UUID literals, cast them: `VALUES ('abc-123'::uuid, ...)`

### JSONB Queries
`context_snapshot->>'key'` to extract, `context_snapshot @> '{"key": "value"}'` to filter.

### Append-Only Pattern
To update memory: create new record with `is_active=TRUE`, then set old to `is_active=FALSE`.

### Performance
- **Batch independent queries**: Pass multiple queries in one WRITE call only when they do NOT depend on each other's RETURNING values
- **Use separate calls for dependent queries**: When query B needs the ID from query A's RETURNING clause, use two separate sql_query calls
- **Use LIMIT**: Always limit SELECT results
- **Don't over-query**: Your context already has conversation data and page_memory

### Returns
- **READ**: `{"success": true, "row_count": N, "rows": [...], "columns": [...]}`
- **WRITE**: `{"success": true, "results": [{"affected": N, "rows": [...], "columns": [...]}, ...]}`
- **Error**: `{"success": false, "error": "message", "error_type": "PostgresError|ValueError"}`

## IMPORTANT: Multi-Step Operations

**You CANNOT reference RETURNING values across statements in the same WRITE call.** Each statement runs independently — the result of statement 1 is NOT available to statement 2 within the same call.

When an operation needs an ID from a previous INSERT (e.g., creating memory then adding blocks), you MUST use **separate sql_query calls**:
1. **Call 1**: INSERT ... RETURNING id → read the actual UUID from the response
2. **Call 2**: Use that real UUID in the next INSERT

**NEVER use placeholders** like `{{id}}`, `RETURNED_ID`, `<id>`, or template syntax. Always use real UUID values from previous tool results.

## COMMON PATTERNS

### Customer Memory

**IMPORTANT: `memory_blocks` is append-only and has NO unique constraints. NEVER use `INSERT ... ON CONFLICT` on `memory_blocks`. To update a block, use `UPDATE ... SET content = '...' WHERE id = 'block-uuid'::uuid`.**

Check existing memory (filter by parent's `is_active`, memory_blocks has no `is_active` column):
```sql
SELECT mb.id, mb.block_key, mb.title, mb.content, mb.display_order
FROM memory_blocks mb
JOIN page_scope_user_memory psum ON mb.prompt_id = psum.id
WHERE psum.is_active = TRUE
ORDER BY mb.display_order;
```

#### Add blocks to existing container (no blocks yet)

If Current Memory shows "_No memory blocks yet._" with a `prompt_id`, the container already exists. **Do NOT create a new container** — add blocks directly using the provided `prompt_id`:

```sql
INSERT INTO memory_blocks (prompt_id, block_key, title, content)
VALUES ('existing-prompt-id'::uuid, 'customer_state', 'Customer State', 'Content here');
```

#### Create new memory — requires 2 separate sql_query calls

Only use this when NO container exists (Current Memory shows "_No memory blocks yet._" WITHOUT a `prompt_id`).

All scope IDs are auto-filled — do NOT provide fan_page_id/facebook_page_scope_user_id/owner_user_id.

**sql_query call 1** (WRITE) — create the container and get its ID:
```sql
INSERT INTO page_scope_user_memory (created_by_type) VALUES ('agent') RETURNING id;
```
→ Response contains `{"rows": [{"id": "xxxxxxxx-xxxx-xxxx-xxxx-xxxxxxxxxxxx"}]}`. Read this UUID.

**sql_query call 2** (WRITE) — add blocks using the real UUID from call 1:
```sql
INSERT INTO memory_blocks (prompt_id, block_key, title, content)
VALUES ('xxxxxxxx-xxxx-xxxx-xxxx-xxxxxxxxxxxx'::uuid, 'customer_notes', 'Customer Notes', 'Content here');
```

#### Update existing block content

Use UPDATE — do NOT use ON CONFLICT:
```sql
UPDATE memory_blocks SET content = 'Updated content...' WHERE id = 'block-uuid'::uuid;
```

#### Replace memory (deactivate old, create new) — requires 2 separate sql_query calls

Append-only pattern: deactivate old container, create new with updated content.

**sql_query call 1** (WRITE) — deactivate old + create new container:
```sql
UPDATE page_scope_user_memory SET is_active = FALSE WHERE id = 'old-memory-uuid'::uuid;
INSERT INTO page_scope_user_memory (created_by_type) VALUES ('agent') RETURNING id;
```
→ Read the new UUID from the RETURNING result.

**sql_query call 2** (WRITE) — add blocks with updated content using the new UUID:
```sql
INSERT INTO memory_blocks (prompt_id, block_key, title, content)
VALUES ('new-memory-uuid'::uuid, 'customer_notes', 'Customer Notes', 'Updated content here');
```

#### Media operations

Attach media to a memory block — after INSERT, call `change_media_retention` tool to set the media to `permanent`:
```sql
INSERT INTO memory_block_media (block_id, media_id, display_order)
VALUES ('actual-block-uuid'::uuid, 'actual-media-uuid'::uuid, 0);
```

Detach media from a memory block — after DELETE, call `change_media_retention` tool to set the media to `one_day` for cleanup:
```sql
DELETE FROM memory_block_media
WHERE block_id = 'actual-block-uuid'::uuid AND media_id = 'actual-media-uuid'::uuid;
```

Check media currently attached to a memory block:
```sql
SELECT mbm.media_id, ma.description, ma.s3_url, ma.media_type
FROM memory_block_media mbm
JOIN media_assets ma ON ma.id = mbm.media_id
WHERE mbm.block_id = 'actual-block-uuid'::uuid
ORDER BY mbm.display_order;
```

### Escalations

#### Create new escalation — requires 2 separate sql_query calls

**sql_query call 1** (WRITE) — create thread header:
```sql
INSERT INTO agent_escalations (subject, priority)
VALUES ('Brief summary for dashboard', 'normal')
RETURNING id;
```
→ Read the escalation UUID from the response.

**sql_query call 2** (WRITE) — add first message using that UUID:
```sql
INSERT INTO agent_escalation_messages (escalation_id, content)
VALUES ('actual-escalation-uuid'::uuid, 'Detailed explanation: what happened, what you need...');
```

With context snapshot (call 2 variant):
```sql
INSERT INTO agent_escalation_messages (escalation_id, content, context_snapshot)
VALUES ('actual-escalation-uuid'::uuid, 'Customer is asking about a product not in page_memory...',
  '{"customer_question": "Do you have product X?", "customer_name": "Nguyen Van A"}'::jsonb);
```

#### Other escalation operations (single call each)

Add follow-up message to existing open escalation:
```sql
INSERT INTO agent_escalation_messages (escalation_id, content)
VALUES ('existing-escalation-uuid'::uuid, 'Follow-up: customer provided more details...');
```

Close escalation after acting on response:
```sql
UPDATE agent_escalations SET status = 'closed',
  updated_at = EXTRACT(EPOCH FROM NOW())::BIGINT * 1000
WHERE id = 'existing-escalation-uuid'::uuid;
```

Query closed escalations (only if you need historical reference):
```sql
SELECT e.id, e.subject, e.priority, em.sender_type, em.content, em.created_at
FROM agent_escalations e
JOIN agent_escalation_messages em ON em.escalation_id = e.id
WHERE e.status = 'closed'
ORDER BY e.created_at DESC, em.created_at ASC
LIMIT 20;
```

Note: Open escalations are pre-loaded in `<escalation_history>` — no need to SELECT them.

### Block Conversation

Block problematic conversations (spam, abuse):
```sql
INSERT INTO conversation_agent_blocks (blocked_by, reason)
VALUES ('suggest_response_agent', 'Reason for blocking this conversation');
```
