# Memory Writing Guide

How suggest_response_agent sees memory, how to organize memory blocks effectively, and essential policy sections that every page should have.

## How suggest_response_agent Sees Memory

Memory blocks are rendered directly (no outer XML wrapper) under markdown headings in the system prompt.

### page_memory renders as (under `# Page Memory (Policy)` heading):

```xml
<memory_block block_id="uuid" index="1" key="products" title="Products">
Shoe A: 260k + ship 20k. Shoe B: 350k.
Send image[1] when customer asks about new model.
<images>
<image index="1" media_id="uuid">White sneaker with gold accent</image>
</images>
</memory_block>

<memory_block block_id="uuid" index="2" key="policies" title="Policies">
Free return within 7 days. No COD for orders under 200k.
</memory_block>
```

Note: `index` attribute = `display_order` value from the database.

### page_scope_user_memory renders as (under `# User Memory` > `## Current Memory` heading):

```xml
<memory_block block_id="uuid" index="1" key="preferences" title="Customer Preferences">
VIP customer. Always wants fastest shipping. Shoe size 42.
</memory_block>
```

If no user memory exists, shows: `_No memory blocks yet._`

### System prompt structure (messages):

The system prompt is a single message containing all static context:

```
[Role intro: agent identity, page name, customer name, avatars as <image> tags]

# Workflow
[Steps: Analyze → Act (optional tool calls) → Finish]

# Page Memory (Policy)
[<memory_block> tags directly — no wrapper]

# Playbooks
[<playbook> tags — only if matched via semantic search, otherwise absent]

# User Memory
## Current Memory
[<memory_block> tags directly — or "_No memory blocks yet._"]

# Escalation System
## Recent Escalations
[<escalation> tags (minimal: id, subject, priority, status) — or "_No escalations._"]

# Block Conversation
# Context Tags Reference
# Rules
```

### User messages structure (messages):

For messages, conversation history is **multi-turn** (alternating user/assistant messages), NOT wrapped in `<conversation_data>`.

The **last user turn** may include `<system-reminder>` blocks before the customer's messages:
1. Escalation history — open threads with full messages (if any)
2. Ad context — ad info if customer came from an ad (if any)
3. Image matching hints — when customer sent an image and page memory has product images
4. Customer message(s) with timestamps

### System prompt structure (comments):

Similar to messages but:
- Uses `# Page Policy` instead of `# Page Memory (Policy)`
- No `# User Memory` section (comments lack stable identity)
- Conversation is wrapped in `<conversation_data>` tags in the user message (single user message, not multi-turn)

---

## Essential Policy Sections

Page memory (policy) is the **primary directive** for suggest_response_agent — it says "follow page policy" when deciding how to write user memory, when to escalate, and when to block. If the policy is silent on these topics, the agent has no guidance and these powerful features go unused.

When setting up or tuning page_memory, **always ensure these three sections exist** (or proactively ask the user about them):

### 1. User Memory Guidelines (`user_memory_policy`)

**What it is:** suggest_response_agent can write persistent per-customer notes (page_scope_user_memory) during conversations — things like name, preferences, sizes, VIP status, order history. This memory is loaded automatically in every future conversation with the same customer so the agent "remembers" them.

**Why it matters:** Without guidelines, the agent either stores nothing (wasting the feature) or stores everything (noise). The policy tells the agent *what* is worth remembering and *what to avoid*.

**What to include in the policy block:**
- What types of info to store (e.g. name, sizes, preferences, order history, special agreements)
- What NOT to store (e.g. sensitive data, one-off questions, complaints)
- When to update vs. append (e.g. update shoe size if customer corrects it)
- Language/format preferences (e.g. store in Vietnamese, keep it concise)

**Example content:**
```
Ghi nhớ thông tin khách hàng khi họ chia sẻ: tên, SĐT, size giày/dép, địa chỉ giao hàng, sở thích.
Không ghi nhớ: khiếu nại tạm thời, câu hỏi chung không liên quan đến khách cụ thể.
Nếu khách sửa thông tin cũ (ví dụ đổi size), cập nhật lại thay vì thêm mới.
```

### 2. Escalation Guidelines (`escalation_policy`)

**What it is:** suggest_response_agent can create "escalation threads" — internal tickets visible to the operator/general_agent but NOT sent to the customer. This is the agent's **only mechanism to communicate with the outside world** when it encounters something it can't handle alone. Without escalation, the agent is completely isolated — it cannot ask for help, report issues, or flag important situations.

**How it works:** The agent writes to `agent_escalations` + `agent_escalation_messages` via sql_query. The operator (or general_agent) sees the escalation and can respond. On the next trigger, the agent sees the response in `<escalation_history>` and acts accordingly. It's a two-way async channel.

**Why it matters:** Without escalation guidelines, the agent will either never escalate (leaving complex/risky situations unhandled) or escalate too aggressively (flooding the operator). The policy defines the boundary.

**What to include in the policy block:**
- When to escalate (e.g. price negotiation beyond X%, product not in memory, complaints, refund requests over X amount, technical issues)
- Priority levels: `low` (FYI), `normal` (needs attention), `high` (time-sensitive), `urgent` (immediate)
- What to include in the escalation message (context, customer question, what the agent already told the customer)
- How to behave while waiting for a response (e.g. tell customer "let me check", don't make promises)

**Example content:**
```
Escalation khi:
- Khách hỏi sản phẩm không có trong memory → priority: normal
- Khách muốn đổi/trả hàng → priority: high, kèm mã đơn hàng nếu có
- Khách phàn nàn nghiêm trọng hoặc đe dọa → priority: urgent
- Khách hỏi giá đặc biệt/giảm giá ngoài chính sách → priority: normal

Khi chờ phản hồi escalation: thông báo khách "em xác nhận lại với bên kho/quản lý và phản hồi sớm nhất ạ". Không hứa hẹn cụ thể.
```

### 3. Block Conversation Guidelines (`block_policy`)

**What it is:** suggest_response_agent can "block" a conversation — this inserts a record into `conversation_agent_blocks`, which prevents the agent from being triggered again for that conversation. The agent essentially removes itself from that conversation permanently.

**How it works:** Once blocked, any future webhook/trigger for that conversation is silently skipped. The block is permanent unless manually removed. This protects against token waste on spam/abuse conversations.

**Why it matters:** Without clear criteria, the agent won't know when blocking is appropriate. Too aggressive = legitimate customers get abandoned. Too passive = spam burns tokens.

**What to include in the policy block:**
- Criteria for blocking (e.g. repeated spam, abusive language, bot conversations, irrelevant content)
- Whether to escalate before blocking (recommended — let operator know)
- Whether to send a final message before blocking

**Example content:**
```
Block conversation khi:
- Tin nhắn spam rõ ràng (quảng cáo, link lạ, nội dung không liên quan lặp lại 3+ lần)
- Ngôn ngữ xúc phạm nghiêm trọng sau khi đã cảnh báo 1 lần
- Bot/automated messages (pattern lặp lại, không phải người thật)

Trước khi block: tạo escalation priority high để thông báo operator.
Không block chỉ vì khách hàng khó tính hoặc phàn nàn — đó là escalation, không phải block.
```

### Proactive Questioning

When the user asks to set up or tune page_memory and does NOT mention these topics, **proactively ask about them**. Use `ask_user_question` and explain clearly:

- **User memory:** "Bạn có muốn agent ghi nhớ thông tin khách hàng (tên, size, sở thích...) giữa các cuộc hội thoại không? Nếu có, agent cần biết nên ghi nhớ loại thông tin gì và không nên ghi nhớ gì."
- **Escalation:** "Agent có một cơ chế 'escalation' — khi gặp tình huống không tự xử lý được (ví dụ khách đòi hoàn tiền, hỏi sản phẩm không có trong danh sách), agent sẽ tạo ticket nội bộ để thông báo cho bạn. Đây là cách DUY NHẤT để agent liên lạc với bên ngoài khi cần hỗ trợ. Bạn muốn agent escalate trong những trường hợp nào?"
- **Block:** "Agent có thể tự 'block' conversation spam/lạm dụng để không bị trigger nữa (tiết kiệm token). Bạn muốn agent block trong trường hợp nào? Ví dụ: spam, ngôn ngữ xúc phạm, tin nhắn tự động..."

If the user is unsure or says "tùy bạn", provide sensible defaults based on the business domain you observed from the page.

---

## Concrete Examples

_Add real memory structure examples here as you discover well-organized ones. Each example should show a complete page_memory block set with notes explaining the organization._

<!-- Example format:
### [Business type]: [Page name or description]

**Why it works:** [1-2 sentences on what makes this memory structure effective]

**Blocks:**
1. `brand_voice` — ...
2. `products` — ...
3. `policies` — ...
4. `user_memory_policy` — ...
5. `escalation_policy` — ...
6. `block_policy` — ...
-->
