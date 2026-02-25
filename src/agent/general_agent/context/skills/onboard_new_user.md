# Onboard New User

Guide for helping first-time users understand what you can do and get them set up quickly.

## Opening Strategy

**Do NOT dump a wall of features.** Instead:
1. Greet warmly but concisely
2. Discover their pages first (shows you're useful, not just explaining)
3. Offer 2-3 concrete next steps based on what you find
4. Let them choose — don't prescribe

## Step 1: Discover Their Pages

```sql
SELECT fp.id, fp.name, fp.category, fp.fan_count, fp.followers_count, fp.about
FROM fan_pages fp
JOIN facebook_page_admins fpa ON fpa.page_id = fp.id
WHERE fpa.user_id = current_setting('app.current_user_id', true)
ORDER BY fp.followers_count DESC NULLS LAST;
```

This immediately shows you know their business. Present the results naturally:
- "You have **[Page Name]** ([category], [X] followers). Let me help you set it up!"
- If multiple pages: list them, ask which to focus on first

## Step 2: Check What's Already Set Up

Run these in parallel to assess the current state:

```sql
-- Check if data is synced
SELECT 'posts' as type, COUNT(*) as count FROM posts WHERE fan_page_id = '<page_id>'
UNION ALL
SELECT 'inbox_conversations', COUNT(*) FROM facebook_conversation_messages WHERE fan_page_id = '<page_id>'
UNION ALL
SELECT 'comment_threads', COUNT(*) FROM facebook_conversation_comments WHERE fan_page_id = '<page_id>';

-- Check if page_memory exists
SELECT pm.id, pm.prompt_type, COUNT(mb.id) as block_count
FROM page_memory pm
LEFT JOIN memory_blocks mb ON mb.prompt_id = pm.id AND mb.prompt_type = 'page_prompt'
WHERE pm.fan_page_id = '<page_id>' AND pm.is_active = TRUE
GROUP BY pm.id, pm.prompt_type;

-- Check suggest_response_agent config
SELECT sra.settings, sra.allow_auto_suggest, sra.num_suggest_response
FROM suggest_response_agent sra
WHERE sra.user_id = current_setting('app.current_user_id', true);

-- Check if playbooks are assigned
SELECT pp.id, pp.title, pp.situation, ppa.conversation_type
FROM page_playbooks pp
JOIN page_playbook_assignments ppa ON ppa.playbook_id = pp.id
JOIN facebook_page_admins fpa ON fpa.id = ppa.page_admin_id
WHERE fpa.user_id = current_setting('app.current_user_id', true)
  AND fpa.page_id = '<page_id>'
  AND pp.deleted_at IS NULL AND ppa.deleted_at IS NULL;

-- Check if auto-reply is configured
SELECT pasc.auto_webhook_suggest, pasc.auto_webhook_graph_api, pasc.webhook_delay_seconds
FROM page_admin_suggest_config pasc
JOIN facebook_page_admins fpa ON fpa.id = pasc.page_admin_id
WHERE fpa.user_id = current_setting('app.current_user_id', true)
  AND fpa.page_id = '<page_id>';
```

## Step 3: Offer Next Steps Based on State

### If no data synced yet:
Offer to sync their page data:
- "Want me to pull in your latest posts and messages? This takes a moment but gives me full context about your page."
- Use sync tools: `manage_page_posts_sync`, `manage_page_inbox_sync`

### If data exists but no page_memory:
This is the highest-value next step. Offer to set up AI response instructions:
- "I see your page has [X posts, Y conversations]. Want me to help set up AI response instructions? I'll look at your page style and create a prompt so the AI replies match your brand."
- Load `tune_suggest_response_quality` skill and follow the first-time setup workflow

### If page_memory already exists but no playbooks:
The base instructions are set. Offer to level up with situational playbooks:
- "Your page has AI instructions set up with [N] blocks — great foundation! Want me to also create some **situation playbooks**? These teach the AI how to handle specific cases like price objections, out-of-stock items, or return requests."
- Load `tune_suggest_response_quality` skill and follow the "improving existing quality" workflow

### If page_memory and playbooks already exist:
Show what's configured, offer to improve:
- "Your page has AI instructions ([N] blocks) and [M] situation playbooks. Want me to review and improve them?"
- Or offer other capabilities: data exploration, escalation check, etc.

## Capabilities to Introduce

Present these **progressively** — not all at once. Match to what the user seems interested in.

### Core capabilities (mention first):

| What | How you explain it | When relevant |
|------|-------------------|---------------|
| **AI Reply Suggestions** | "I manage an AI assistant that suggests replies to your customers' messages and comments" | Always — this is the main feature |
| **Memory/Instructions** | "I can teach the AI about your products, prices, policies, and tone — so replies are accurate" | When setting up or improving |
| **Situation Playbooks** | "I can create playbooks for specific situations — like when a customer complains about price or asks for a refund. The AI automatically detects the situation and follows the playbook" | After base instructions are set, or when user reports a specific bad response |
| **Data Exploration** | "I can analyze your posts, engagement, messages, and customer interactions" | When they ask about analytics |

### Advanced capabilities (mention later, when relevant):

| What | How you explain it | When relevant |
|------|-------------------|---------------|
| **Send Messages** | "I can send messages directly to customers through the AI, or get draft suggestions for you to review" | When they want to respond to someone |
| **Customer Memory** | "I can save notes about specific customers (preferences, VIP status) so the AI personalizes replies" | When discussing a specific customer |
| **Escalations** | "When the AI can't handle something (like a pricing question it doesn't know), it flags it for me — I answer, and the AI uses that answer going forward" | When they ask about edge cases |
| **Conversation Blocking** | "I can block specific conversations from getting AI suggestions — useful for spam or conversations you want to handle personally" | When they mention spam or manual control |
| **Auto-Reply** | "Your page can be set up to automatically reply to messages via webhook — I can help configure that" | When they ask about automation |
| **Media Management** | "I can mirror Facebook images, generate descriptions, and attach them to memory so the AI can reference product photos" | When discussing products with images |

## What NOT to Do

- **Don't overwhelm** — 2-3 options max per message
- **Don't explain internal architecture** — they don't care about "page_memory" or "memory_blocks"; say "AI instructions" or "response settings"
- **Don't assume they want everything** — ask first
- **Don't use technical jargon** — "PSID", "prompt_type", "RLS" mean nothing to users
- **Don't force a workflow** — if they have a specific question, answer it; don't redirect to onboarding

## User-Friendly Language Map

| Internal term | Say this instead |
|---------------|-----------------|
| page_memory | "AI instructions for your page" or "response settings" |
| page_scope_user_memory | "customer notes" or "notes about [customer name]" |
| memory_blocks | "instruction sections" or "settings" |
| page_playbooks | "situation playbooks" or "response playbooks" |
| page_playbook_assignments | "active playbooks for your page" |
| playbook title + situation | "when this playbook activates" or "trigger condition" |
| playbook content | "how the AI should respond in this situation" |
| suggest_response_agent | "the AI assistant" or "the reply AI" |
| escalation | "flagged question" or "something the AI needs help with" |
| conversation_agent_blocks | "blocked conversation" |
| trigger_suggest_response | "ask the AI to reply" or "send a message" |
| preview_suggest_response_context | "preview what the AI sees" |

## Conversation Flow Example

```
User: Hi!
Agent: Hey! 👋 Let me see what pages you're managing...
       [queries fan_pages + checks page_memory + checks playbooks]
       You have **Shoes Plus** (Retail, 15K followers). Nice!
       
       A few things I can help with right away:
       1. **Set up AI replies** — I'll learn about your products and create instructions so the AI matches your style
       2. **Explore your data** — see your top posts, recent messages, engagement trends
       3. **Something specific** — just tell me what you need!
       
       What sounds good?

User: Let's set up AI replies
Agent: [loads tune_suggest_response_quality skill, follows first-time setup workflow]
       [after base instructions are set up...]
       Great, your AI now knows about your products, policies, and tone!
       
       One more thing that can really help: **situation playbooks**.
       These teach the AI how to handle specific tricky cases — like when a customer
       says your price is too high, asks for a refund, or compares with competitors.
       Want me to create a few for common scenarios?

User: Sure!
Agent: [loads playbook_writing_guide, creates playbooks for common scenarios, assigns to page]
```

## After Onboarding

Once initial setup is done:
- Save key facts to user_memory (what pages they manage, preferences, setup state)
- Summarize what was set up
- Mention they can come back anytime to adjust settings or explore data
