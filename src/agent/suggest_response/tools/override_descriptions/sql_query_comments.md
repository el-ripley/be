Execute SQL queries within your conversation scope. You have access to **4 tables only**, all other data is pre-built into your context.

Comments don't have customer memory (no PSID). Focus on escalations and blocking.

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

**For agent_escalations:**
- `owner_user_id` - auto-filled
- `fan_page_id` - auto-filled
- `conversation_type` - auto-filled ('comments')
- `facebook_conversation_comments_id` - auto-filled
- `created_by` - auto-filled ('suggest_response_agent')

**For agent_escalation_messages:**
- `sender_type` - auto-filled ('suggest_response_agent')

**For conversation_agent_blocks:**
- `fan_page_id` - auto-filled
- `conversation_type` - auto-filled
- `facebook_conversation_comments_id` - auto-filled

## TABLE SCHEMAS

### media_assets (SELECT only)
Media details.
```
id: UUID
user_id: VARCHAR(36)
source_type: VARCHAR(50)
media_type: VARCHAR(50)
s3_url: VARCHAR(1024)
description: TEXT - AI-generated description
status: VARCHAR(20) - 'pending' | 'ready' | 'failed'
retention_policy: VARCHAR(50)
created_at: BIGINT (ms)
updated_at: BIGINT (ms)
```

### conversation_agent_blocks (SELECT, INSERT)
Block this comment thread from future agent triggers.
```
id: UUID (auto-generated)
conversation_type: VARCHAR(20) - auto-filled
facebook_conversation_comments_id: UUID - auto-filled
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
conversation_type: VARCHAR(20) - auto-filled ('comments')
facebook_conversation_comments_id: UUID - auto-filled
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

When an operation needs an ID from a previous INSERT (e.g., creating escalation then adding message), you MUST use **separate sql_query calls**:
1. **Call 1**: INSERT ... RETURNING id → read the actual UUID from the response
2. **Call 2**: Use that real UUID in the next INSERT

**NEVER use placeholders** like `{{id}}`, `RETURNED_ID`, `<id>`, or template syntax. Always use real UUID values from previous tool results.

## COMMON PATTERNS

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
VALUES ('actual-escalation-uuid'::uuid, 'Commenter is asking about a product not in page_memory...',
  '{"commenter_question": "Do you have product X?", "commenter_name": "Nguyen Van A"}'::jsonb);
```

#### Other escalation operations (single call each)

Add follow-up message to existing open escalation:
```sql
INSERT INTO agent_escalation_messages (escalation_id, content)
VALUES ('existing-escalation-uuid'::uuid, 'Follow-up: commenter provided more details...');
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

Block problematic comment threads (spam, abuse):
```sql
INSERT INTO conversation_agent_blocks (blocked_by, reason)
VALUES ('suggest_response_agent', 'Reason for blocking this comment thread');
```
