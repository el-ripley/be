Manage playbooks — situational coaching guidance for suggest_response_agent.
Playbooks are matched semantically (vector search) to customer conversations
and injected as behavioral guidance when the situation matches.

## MODES

### CREATE mode
Create a new playbook. The system automatically embeds the text and stores
vectors for semantic matching.

Required: title, situation, content
Optional: tags

### UPDATE mode
Update an existing playbook. Only provided (non-null) fields are changed.
If title/situation/content changes, vectors are automatically re-embedded.

Required: playbook_id
Optional: title, situation, content, tags (null = keep current value)

### DELETE mode
Soft-delete a playbook (sets deleted_at). Also removes vectors from
the search index.

Required: playbook_id

### SEARCH mode
Semantic search across playbooks using vector similarity.

Required: query
Optional: limit (default: 3), playbook_ids (filter to specific IDs)

## FIELDS

- **title**: Human-readable label. Concatenated with `situation` and embedded as vectors for semantic search.
- **situation**: WHEN to apply — trigger condition in natural language. Concatenated with `title` and embedded as vectors.
- **content**: HOW to handle — guidance injected into suggest_response_agent's context when matched.
- **tags**: Optional categorization for SQL-based filtering, e.g. ["pricing", "sales"].

> `title` + `situation` are the retrieval key (vectors). `content` is the payload (injected at runtime).
> For guidance on writing effective playbooks, load the `playbook_writing_guide` skill.

## PLAYBOOK ASSIGNMENTS

Playbooks are **owner-scoped** (tied to owner_user_id, not to any
specific page). To activate a playbook for a specific page + conversation
type, create an assignment via sql_query:

```sql
INSERT INTO page_playbook_assignments (playbook_id, page_admin_id, conversation_type)
VALUES ('playbook-uuid'::uuid, 'page-admin-id', 'messages');
```

To find assigned playbook IDs (useful as search filter):

```sql
SELECT playbook_id FROM page_playbook_assignments
WHERE page_admin_id = 'page-admin-id'
  AND conversation_type = 'messages'
  AND deleted_at IS NULL;
```

To remove an assignment (soft-delete):

```sql
UPDATE page_playbook_assignments
SET deleted_at = EXTRACT(EPOCH FROM NOW())::BIGINT * 1000
WHERE playbook_id = 'playbook-uuid'::uuid
  AND page_admin_id = 'page-admin-id'
  AND conversation_type = 'messages';
```

## RETURNS

- **CREATE**: `{"success": true, "playbook_id": "uuid", "title": "..."}`
- **UPDATE**: `{"success": true, "playbook_id": "uuid", "updated_fields": ["title", "content"]}`
- **DELETE**: `{"success": true, "playbook_id": "uuid"}`
- **SEARCH**: `{"success": true, "results": [{"playbook_id": "uuid", "title": "...", "situation": "...", "content": "...", "score": 0.85, "tags": [...]}], "result_count": N}`
- **Error**: `{"success": false, "error": "message", "error_type": "ValueError|PostgresError|EmbeddingError|QdrantError"}`
