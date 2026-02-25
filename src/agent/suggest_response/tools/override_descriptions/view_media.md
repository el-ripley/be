Load images into context for visual analysis using LLM vision capabilities.

Each image adds tokens to context. Use this tool when text descriptions aren't enough — don't load images you can already understand from their descriptions.

## When to use

- You have a `media_id` from your context (memory blocks, conversation attachments, comment attachments, info lines)
- AND the text description is insufficient for your task:
  - Need to identify or match a specific product, item, or design
  - Need to read text, labels, or details visible in the image
  - Description is vague, missing, or says "[Image unavailable]"
  - Customer is asking about visual details of an attachment

### Visual matching workflow

When a customer sends an image and you need to find or compare it with images in your context (page memory, conversation history, etc.):

1. **Observe** — note key visual traits from the customer's image: colors, shapes, text/labels, patterns, distinctive features
2. **Narrow down** — compare those traits against `<image>` tag descriptions to shortlist 2-3 best candidates
3. **Verify** — use view_media to load only those candidates and visually confirm the match
4. **Don't guess** — if descriptions alone aren't enough to confidently identify a match, always view_media to verify before responding

This applies to any matching scenario: products, designs, documents, menus, references, before/after comparisons, etc.

## When NOT to use

- The description already tells you what you need to know
- No `media_id` is available
- For avatars — their descriptions are sufficient for conversation context

## Where to find media_id

- Memory blocks: `<image index="N" media_id="uuid">description</image>`
- Conversation attachments: `[Attachment: type - description, media_id: uuid]`
- Info lines: `Post image: description, media_id: uuid`

## Parameters

```json
{
  "media_ids": [
    {"index": 1, "media_id": "uuid-here"}
  ]
}
```

- `index`: Reference number (1-based)
- `media_id`: UUID from your context

If multiple images are referenced, only load the ones relevant to your current task.
