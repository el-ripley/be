# Tune Suggest Response Quality

Guide for managing the two mechanisms that control how suggest_response_agent behaves — **memory** (persistent policies & customer data) and **playbooks** (situational coaching).

> For detailed writing guidance: load `memory_writing_guide` (memory structure/examples) or `playbook_writing_guide` (playbook quality/examples).

## Quality Control Architecture

```
suggest_response_agent context
├── Memory (ALWAYS in context)
│   ├── page_memory — hard policies for ALL conversations on a page
│   │   Tone, products, pricing, shipping, escalation rules, etc.
│   │   Think of it as the "constitution" — always applies, no conditions.
│   │   prompt_type: 'messages' | 'comments' (separate per conversation type)
│   │
│   └── page_scope_user_memory — per-customer info (messages only)
│       VIP status, sizes, preferences, order history, etc.
│       Think of it as "CRM notes" for one specific person.
│       Not available for comments (lack stable user identity).
│
└── Playbooks (loaded ON-DEMAND via semantic search)
    Coaching for specific situations — only injected when conversation
    matches the playbook's situation via vector similarity.
    Think of it as "if-then" training for edge cases and specific scenarios.
```

### When to Use What

| Signal | Action | Why |
|--------|--------|-----|
| User wants to set general tone, product info, pricing, policies | **page_memory** | These apply to ALL conversations — hard policy |
| User wants agent to remember a specific customer's info | **page_scope_user_memory** | Per-customer data, loaded every time that customer writes |
| User complains about AI response in a **specific situation** | **playbook** | Situation-specific coaching, only loaded when it matches |
| User says "always do X" or "never do Y" | **page_memory** | Unconditional rules = policy |
| User says "when customer does X, respond with Y" | **playbook** | Conditional behavior = situational coaching |

### Typical User Journey

1. **Initial setup** → create `page_memory` as the base foundation (tone, products, policies, essential sections)
2. **During usage** → user encounters specific cases where AI responds poorly → create **playbooks** for each case
3. **Per-customer** → agent learns individual customer info from conversations → `page_scope_user_memory`

---

## Memory Management

### Memory Architecture

```
page_memory (1 per page × prompt_type)
├── prompt_type: 'messages' | 'comments'        ← separate instructions per conversation type
├── fan_page_id, owner_user_id, is_active
└── memory_blocks (prompt_type = 'page_prompt')
       └── memory_block_media → media_assets

page_scope_user_memory (1 per page × PSID, messages only)
├── fan_page_id, facebook_page_scope_user_id, owner_user_id, is_active
└── memory_blocks (prompt_type = 'user_prompt')
       └── memory_block_media → media_assets
```

**Key facts:**
- `page_memory` affects ALL conversations on a page (for that prompt_type)
- `page_scope_user_memory` affects ONE specific customer (messages only — comments lack stable identity)
- Content lives in `memory_blocks` — polymorphic via `prompt_type` + `prompt_id`
- One active record per scope (append-only: create new → deactivate old via `is_active`)
- `memory_blocks` columns: `id`, `prompt_type`, `prompt_id`, `block_key`, `title`, `content`, `display_order`, `created_by_type`

### Reading Memory — Preview First, SQL for CRUD

**Always use `preview_suggest_response_context` to read current memory state** instead of SQL queries. It's faster (1 tool call vs multiple SQL joins) and shows the exact rendered output that suggest_response_agent receives — including all blocks, media, block_ids, and display_order.

The preview output contains everything you need to plan edits:
- `block_id` attribute → use for UPDATE/DELETE
- `index` attribute → current display_order
- `key` attribute → block_key
- `title` attribute → block title
- Full rendered content including `<images>` tags
- Missing sections are immediately visible

**Only use SQL queries when you need to:**
- INSERT/UPDATE/DELETE blocks (write operations)
- Get `prompt_id` for creating new blocks (if not already known)
- Query `page_memory` or `page_scope_user_memory` container IDs
- Check media retention details not shown in preview

#### Quick SQL for IDs (when preview isn't enough)

```sql
-- Get prompt_id for a page (messages) — needed for INSERT
SELECT id as prompt_id FROM page_memory
WHERE fan_page_id = '<page_id>' AND prompt_type = 'messages' AND is_active = TRUE;

-- Get prompt_id for a page (comments)
SELECT id as prompt_id FROM page_memory
WHERE fan_page_id = '<page_id>' AND prompt_type = 'comments' AND is_active = TRUE;

-- Get prompt_id for per-customer memory
SELECT id as prompt_id FROM page_scope_user_memory
WHERE fan_page_id = '<page_id>' AND facebook_page_scope_user_id = '<psid>' AND is_active = TRUE;

-- Check media retention (not shown in preview)
SELECT mbm.display_order, ma.id as media_id, ma.s3_url, ma.description, ma.retention_policy
FROM memory_block_media mbm
JOIN media_assets ma ON ma.id = mbm.media_id
WHERE mbm.block_id = '<block_id>'::uuid
ORDER BY mbm.display_order;
```

### SQL Patterns

#### Create page_memory (first time) — 2 separate sql_query calls

**IMPORTANT:** You cannot reference RETURNING values across statements in the same WRITE call. Use separate calls.

**sql_query call 1** (WRITE) — create the container:
```sql
INSERT INTO page_memory (fan_page_id, owner_user_id, prompt_type, created_by_type, is_active)
VALUES ('<page_id>', current_setting('app.current_user_id', true), 'messages', 'agent', TRUE)
RETURNING id;
```
→ Read the UUID from the response (e.g. `"id": "abc123-..."`)

**sql_query call 2** (WRITE) — add blocks using the real UUID from call 1:
```sql
INSERT INTO memory_blocks (prompt_type, prompt_id, block_key, title, content, display_order, created_by_type)
VALUES ('page_prompt', 'abc123-...'::uuid, 'brand_voice', 'Brand Voice', 'Friendly, casual tone. Use emoji occasionally. Always greet by name.', 1, 'agent')
RETURNING id;
```

#### Create page_scope_user_memory (first time) — 2 separate sql_query calls

**sql_query call 1** (WRITE) — create the container:
```sql
INSERT INTO page_scope_user_memory (fan_page_id, facebook_page_scope_user_id, owner_user_id, created_by_type, is_active)
VALUES ('<page_id>', '<psid>', current_setting('app.current_user_id', true), 'agent', TRUE)
RETURNING id;
```
→ Read the UUID from the response.

**sql_query call 2** (WRITE) — add blocks using the real UUID:
```sql
INSERT INTO memory_blocks (prompt_type, prompt_id, block_key, title, content, display_order, created_by_type)
VALUES ('user_prompt', 'actual-uuid-from-call-1'::uuid, 'customer_notes', 'Customer Notes', 'VIP customer since 2024. Size 42. Prefers DHL shipping.', 1, 'agent')
RETURNING id;
```

#### CRUD blocks

```sql
-- Add block
INSERT INTO memory_blocks (prompt_type, prompt_id, block_key, title, content, display_order, created_by_type)
VALUES ('page_prompt', '<prompt_id>'::uuid, 'faq', 'FAQ', 'Q: Shipping time? A: 2-3 days HCM, 3-5 days other provinces.', 3, 'agent')
RETURNING id;

-- Update block content
UPDATE memory_blocks SET content = 'Updated content...', title = 'Updated Title'
WHERE id = '<block_id>'::uuid;

-- Reorder blocks
UPDATE memory_blocks SET display_order = 2 WHERE id = '<block_id>'::uuid;

-- Remove block
DELETE FROM memory_blocks WHERE id = '<block_id>'::uuid;
```

### Media in Memory

#### Attach media to a block

```sql
INSERT INTO memory_block_media (block_id, media_id, display_order)
VALUES ('<block_id>'::uuid, '<media_id>'::uuid, 1);
```
Then **immediately** call `change_media_retention(media_ids=["<media_id>"], target_retention="permanent")`.

#### Detach media from a block

```sql
DELETE FROM memory_block_media WHERE block_id = '<block_id>'::uuid AND media_id = '<media_id>'::uuid;
```
Then call `change_media_retention(media_ids=["<media_id>"], target_retention="one_day")` to free storage.

#### Reference images in content

Use `image[N]` where N matches `display_order`:
```
Send image[1] when customer asks about the new model.
Compare image[1] (white) and image[2] (black) if customer is unsure.
```

suggest_response_agent sees this as `<image index="1" media_id="...">Description</image>` and can use the media URL in its response.

**Requirement:** Media MUST have a description before attaching. Use `describe_media` tool first if `description` is NULL.

---

## Playbook Management

Playbooks provide **situational coaching** — only injected when the current conversation semantically matches the playbook's `title + situation` via vector similarity.

- Use `manage_playbook` tool for CRUD + search. See tool description for modes, fields, and assignment SQL.
- Playbooks must be assigned to a page via `page_playbook_assignments` to be active (see `manage_playbook` tool).
- `preview_suggest_response_context` does NOT include playbooks — use `trigger_suggest_response` to verify playbook matching.

> For detailed guidance on writing effective playbooks (quality dimensions, verification workflow, examples), load the `playbook_writing_guide` skill.

### When to Create Playbooks

- User complains "AI responded badly when customer asked about X" → create playbook for situation X
- User wants specific handling for a scenario that doesn't apply to ALL conversations → playbook (not page_memory)
- Pattern of similar complaints → one well-written playbook covers all instances
- User says "when customer does X, the AI should do Y" → that's a playbook

---

## Workflows

### First-time setup for a page

1. **Explore the page** — query posts, inbox, page info to understand the business domain
2. **Ask the user** — use `ask_user_question`: tone? products/services? pricing? key policies? hours?
3. **Ask about essential policy sections** — if user didn't cover them, ask about user memory guidelines, escalation criteria, and block criteria. Load `memory_writing_guide` for details on these sections.
4. **Create page_memory** for `messages` (and optionally `comments` if different tone needed)
5. **Add blocks** — organize into logical sections:
   - `brand_voice` — tone, language, personality
   - `products` — catalog, pricing, availability
   - `policies` — returns, shipping, payment, warranty
   - `faq` — frequent questions and answers
   - `instructions` — do's and don'ts, special handling rules
   - `user_memory_policy` — what to remember about customers
   - `escalation_policy` — when and how to escalate
   - `block_policy` — when to block conversations
6. **Attach media** if relevant (product images, size charts, etc.)
7. **Preview** — use `preview_suggest_response_context` to verify rendering
8. **Show user** — present the preview, ask for feedback, iterate

### Improving existing quality

1. **Preview first** — use `preview_suggest_response_context` to see rendered memory context
2. **Diagnose the issue:**
   - Problem is **general** (wrong tone, missing info, bad policy)? → Fix `page_memory`
   - Problem is in a **specific situation** (AI handles case X badly)? → Create or update a `playbook` (load `playbook_writing_guide`)
   - Problem is about **one customer** (wrong info, missing context)? → Fix `page_scope_user_memory`
3. **Check for missing essential sections** — if `user_memory_policy`, `escalation_policy`, or `block_policy` blocks are missing, flag this to the user and suggest adding them (load `memory_writing_guide` for templates)
4. **Apply fixes:**
   - Memory: UPDATE/INSERT/DELETE blocks using block_ids from preview
   - Playbook: CREATE via `manage_playbook`, assign to page, verify (see `playbook_writing_guide`)
5. **Preview again** (for memory changes) → show user → feedback → iterate

### Per-customer memory (messages only)

1. User mentions something about a specific customer (or you notice patterns in conversation history)
2. Check if `page_scope_user_memory` exists for that PSID
3. If not, create it; if yes, add/update blocks
4. Common use cases: VIP status, size/preferences, order history, special agreements

## Principles

- **Concise** — one purpose per block; avoid wall of text
- **Specific** — "260k + ship 20k nội thành" > "giá hợp lý"
- **No fabrication** — only confirmed information from user or page data
- **Collaborate** — ask the user, show previews, listen to feedback, iterate
- **Separate concerns** — messages and comments may need different tones (professional inbox vs casual comments)
- **Media = context** — product images in memory let the agent reference them naturally in conversations
- **Right tool for the job** — policy changes go in memory, situation-specific coaching goes in playbooks
