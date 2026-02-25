Search playbooks for situational coaching guidance that matches the current
conversation. Uses semantic (vector) search to match your query against
playbook situation (title + situation).

Only `mode: "search"` is available. Set all write-related fields
(title, situation, content, tags, playbook_id) to null.

## PARAMETERS

- **mode**: Always "search"
- **query** (required): Natural language description of the situation.
  Example: "khách hỏi giá nhưng chưa nêu sản phẩm cụ thể"
- **limit**: Max results (default 3, max 10)
- **playbook_ids**: Filter to specific playbook UUIDs (from assignment
  lookup). Pass null to search all accessible playbooks.
- **description**: Brief note on why you're searching (for audit)

## RETURNS

```json
{
  "success": true,
  "results": [
    {
      "playbook_id": "uuid",
      "title": "Xử lý khách hỏi giá",
      "situation": "Khi khách hỏi giá chưa nêu rõ sản phẩm",
      "content": "Đừng vội báo giá. Hỏi lại sản phẩm cụ thể...",
      "score": 0.85,
      "tags": ["pricing", "sales"]
    }
  ],
  "result_count": 1
}
```

Error: `{"success": false, "error": "message", "error_type": "..."}`

## TIPS

- Be specific in your query — describe the customer's behavior and context
- Use playbook_ids filter when you know which playbooks are assigned
  to this page (query assignments via sql_query first)
- If no playbooks match well (low scores), rely on page_memory instead
