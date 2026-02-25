# Playbook Writing Guide

How to write effective playbooks and verify they work. A playbook has **two independent quality dimensions** — you must get BOTH right.

## How Playbooks Work at Runtime

1. Customer sends a message → suggest_response_agent is triggered
2. **PlaybookRetriever** takes recent conversation context, embeds it, and searches against stored playbook vectors
3. Playbooks whose `title + situation` vectors are similar enough to the conversation are injected into the agent's context as `<playbook>` tags
4. The agent uses the playbook's `content` as guidance for that specific response

**Critical implication**: The quality of `title` + `situation` directly determines whether the playbook will be found. If they're vague or generic, the playbook either matches everything (noise) or nothing (wasted). Write them as if describing the **exact moment** the playbook should activate.

---

## Dimension 1: Retrieval Quality — `title` + `situation`

These two fields are concatenated, embedded as vectors, and used for semantic search at runtime. They determine WHETHER the playbook gets found when a matching conversation occurs.

**title** — Short, descriptive label. Think of it as the playbook's name:
- Good: "Xử lý khách so sánh giá với đối thủ"
- Bad: "Pricing" (too vague, matches everything)

**situation** — Describe the trigger condition in natural language. Be specific:
- Good: "Khi khách hàng nói rằng shop khác bán rẻ hơn hoặc so sánh giá với đối thủ cạnh tranh"
- Bad: "Khách hỏi về giá" (too broad — overlaps with normal pricing questions)

Write `title` + `situation` as if describing the **exact moment** the playbook should activate — the words you use should closely match what customers actually say in that scenario.

## Dimension 2: Response Quality — `content`

This is the guidance injected into suggest_response_agent's context when the playbook is matched. It determines HOW WELL the agent responds in that situation. Even if retrieval is perfect, vague content = vague response.

**content** — Step-by-step guidance + concrete examples:
- Good: "1. Acknowledge the comparison. 2. Highlight unique value (quality, warranty, free shipping). 3. DO NOT badmouth competitors. Example: 'Dạ em hiểu ạ, bên em cam kết hàng chính hãng + bảo hành 12 tháng + free ship nội thành ạ'"
- Bad: "Handle it professionally" (too vague to be useful)

> **Debugging tip**: If `trigger_suggest_response` shows the playbook was matched (`playbook_system_reminder` is present) but suggestions are still bad → the problem is `content`, not `title`/`situation`. Rewrite the content with more specific instructions and examples.

---

## Verifying Playbooks

After creating a playbook, verify BOTH quality dimensions:

**Step 1 — Verify retrieval (does the playbook get FOUND?)**
Use `manage_playbook` SEARCH mode with a query that simulates a real customer message → verify the playbook appears in results with a good score. If score is low or playbook doesn't appear, rewrite `title` + `situation` to be more specific to the scenario.

**Step 2 — Verify end-to-end (does it get found AND produce good suggestions?)**
Use `trigger_suggest_response` on a real conversation that matches the playbook's scenario. The result returns BOTH:
- `suggestions` — the actual responses generated
- `playbook_system_reminder` — the exact playbook text that was injected into suggest_response_agent's context

This lets you verify: (a) the playbook was retrieved for that conversation, and (b) the suggestions follow the playbook's guidance. If suggestions are poor despite the playbook being matched, improve the `content`.

**Optional — Test guidance before saving as playbook:**
Use `trigger_suggest_response` with `hint` parameter to inject raw instruction text into suggest_response's context. This lets you experiment with different guidance wording before committing it as a playbook.

> **Note**: `preview_suggest_response_context` does NOT include playbooks — it only shows memory and conversation data. Use `trigger_suggest_response` to verify playbook matching.

---

## Common Playbook Patterns

| Situation pattern | Example title | Example situation |
|-------------------|---------------|-------------------|
| Price objection | "Xử lý khách chê giá đắt" | "Khi khách nói giá đắt, đắt quá, hoặc so sánh giá với shop khác" |
| Out-of-stock item | "Sản phẩm hết hàng" | "Khi khách hỏi mua sản phẩm đang hết hàng hoặc ngừng kinh doanh" |
| Refund/return request | "Xử lý yêu cầu đổi trả" | "Khi khách muốn đổi hàng, trả hàng, hoặc hoàn tiền" |
| Shipping delay | "Đơn hàng giao chậm" | "Khi khách phàn nàn đơn hàng giao chậm hoặc chưa nhận được hàng" |
| Aggressive/upset customer | "Khách hàng bức xúc" | "Khi khách hàng tỏ ra tức giận, dùng ngôn từ mạnh, hoặc đe dọa" |
| Upsell opportunity | "Gợi ý sản phẩm bổ sung" | "Khi khách đã chọn sản phẩm và có cơ hội gợi ý thêm phụ kiện hoặc combo" |

---

## Concrete Examples

_Add real playbook examples here as you discover well-structured ones. Each example should include the full playbook (title, situation, content) with a brief note explaining why it works well._

<!-- Example format:
### [Category]: [Brief description]

**Why it works:** [1-2 sentences on what makes this playbook effective]

- **title**: "..."
- **situation**: "..."
- **content**: "..."
-->
