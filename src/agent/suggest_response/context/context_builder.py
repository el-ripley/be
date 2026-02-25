"""Context builder for Suggest Response Agent."""

from datetime import datetime
from typing import Dict, Any, Optional, List, Tuple
import asyncpg
import json

from src.services.facebook.messages.message_read_service import MessageReadService
from src.services.facebook.comments.comment_read_service import CommentReadService
from src.services.facebook.media import MediaAssetService
from src.agent.suggest_response.context.formatter import FacebookContentFormatter
from src.database.postgres.repositories import (
    get_active_page_prompt_with_media,
    get_active_page_scope_user_prompt_with_media,
    get_escalations_for_context,
    get_escalation_list_minimal,
)
from src.agent.suggest_response.context.prompts.prompt_loader import (
    build_system_prompt,
)
from src.agent.common.api_key_resolver_service import get_system_api_key
from src.utils.logger import get_logger

logger = get_logger()


class SuggestResponseContextBuilder:
    """Builds system and human prompts for suggest response agent."""

    def __init__(
        self,
        message_read_service: Optional[MessageReadService] = None,
        comment_read_service: Optional[CommentReadService] = None,
        media_service: Optional[MediaAssetService] = None,
        formatter: Optional[FacebookContentFormatter] = None,
    ):
        self.message_read_service = message_read_service or MessageReadService()
        self.comment_read_service = comment_read_service or CommentReadService()
        self.media_service = media_service or MediaAssetService()
        self.formatter = formatter or FacebookContentFormatter(self.media_service)

    async def build_context(
        self,
        conn: asyncpg.Connection,
        conversation_type: str,
        conversation_id: str,
        fan_page_id: str,
        owner_user_id: str,
        facebook_page_scope_user_id: Optional[str] = None,
        delivery_mode: str = "suggest",
        trigger_action: str = "routine_check",
        hint: Optional[str] = None,
    ) -> Tuple[List[Dict[str, Any]], Dict[str, Any]]:
        """
        Build context for suggest response agent.

        Args:
            conn: Database connection
            conversation_type: 'messages' or 'comments'
            conversation_id: Conversation ID (fb_conversation_messages.id or fb_conversation_comments.id)
            fan_page_id: Facebook page ID
            owner_user_id: User who owns this page in the app
            facebook_page_scope_user_id: PSID (only for messages)
            delivery_mode: 'suggest' or 'respond'
            trigger_action: Why agent was triggered — 'new_customer_message', 'operator_request', 'escalation_update', 'routine_check'
            hint: Optional raw instruction text to inject as system-reminder (e.g. from API or general_agent trigger)

        Returns:
            Tuple of (input_messages, metadata)
            input_messages: List of message dicts for LLM input
            metadata: Dict with conversation info for history saving
        """
        if conversation_type == "comments":
            return await self._build_comments_context(
                conn=conn,
                conversation_id=conversation_id,
                fan_page_id=fan_page_id,
                owner_user_id=owner_user_id,
                delivery_mode=delivery_mode,
                trigger_action=trigger_action,
                hint=hint,
            )
        else:
            return await self._build_messages_context(
                conn=conn,
                conversation_id=conversation_id,
                fan_page_id=fan_page_id,
                owner_user_id=owner_user_id,
                facebook_page_scope_user_id=facebook_page_scope_user_id,
                delivery_mode=delivery_mode,
                trigger_action=trigger_action,
                hint=hint,
            )

    async def _build_comments_context(
        self,
        conn: asyncpg.Connection,
        conversation_id: str,
        fan_page_id: str,
        owner_user_id: str,
        delivery_mode: str = "suggest",
        trigger_action: str = "routine_check",
        hint: Optional[str] = None,
    ) -> Tuple[List[Dict[str, Any]], Dict[str, Any]]:
        """Build context for comments: system prompt with escalation_list, single user message with array content."""
        fb_data, formatted_data = await self._fetch_and_format_comments(
            conn, conversation_id, owner_user_id
        )
        if not fb_data or not formatted_data:
            raise ValueError(
                f"Could not fetch conversation data for comments {conversation_id}"
            )

        page_prompt = await get_active_page_prompt_with_media(
            conn, fan_page_id, "comments", owner_user_id
        )

        # System message = build dynamic system prompt with page_memory + escalation_list + conversation_info
        page_memory_prompt = await self._build_page_memory_prompt(conn, page_prompt, owner_user_id)
        escalation_list_prompt = await self._build_escalation_list_prompt(
            conn, "comments", conversation_id, fan_page_id, owner_user_id
        )
        conversation_info = self.formatter.format_comment_thread_identity(fb_data)
        system_content = build_system_prompt(
            conversation_type="comments",
            page_memory=page_memory_prompt,
            user_memory="",
            conversation_info=conversation_info,
            escalation_list=escalation_list_prompt,
            delivery_mode=delivery_mode,
        )

        # Build user message parts: system-reminder context + conversation_data
        conversation_prompt = self._build_conversation_prompt(formatted_data)
        escalation_prompt = await self._build_escalation_context_prompt(
            conn, "comments", conversation_id, fan_page_id, owner_user_id
        )
        hint_prompt = self._build_hint_prompt(hint or "")

        user_parts = []
        if escalation_prompt:
            user_parts.append(escalation_prompt)
        if hint_prompt:
            user_parts.append(hint_prompt)
        user_parts.append(conversation_prompt)

        input_messages: List[Dict[str, Any]] = [
            {"role": "system", "content": self._content_blocks([system_content])},
            {"role": "user", "content": self._content_blocks(user_parts)},
        ]

        metadata = self._extract_metadata(
            "comments", conversation_id, fb_data, page_prompt, None
        )
        # Include raw memory texts for playbook retriever (avoids re-fetching)
        metadata["page_memory_text"] = page_memory_prompt
        metadata["user_memory_text"] = ""
        return input_messages, metadata

    async def _build_messages_context(
        self,
        conn: asyncpg.Connection,
        conversation_id: str,
        fan_page_id: str,
        owner_user_id: str,
        facebook_page_scope_user_id: Optional[str] = None,
        delivery_mode: str = "suggest",
        trigger_action: str = "routine_check",
        hint: Optional[str] = None,
    ) -> Tuple[List[Dict[str, Any]], Dict[str, Any]]:
        """Build context for messages: system prompt with conversation_info + escalation_list, array content blocks."""
        fb_data, turn_data = await self._fetch_and_format_messages_as_turns(
            conn, conversation_id, owner_user_id
        )
        if not fb_data or not turn_data:
            raise ValueError(
                f"Could not fetch conversation data for messages {conversation_id}"
            )

        page_prompt = await get_active_page_prompt_with_media(
            conn, fan_page_id, "messages", owner_user_id
        )
        page_scope_user_prompt = None
        if facebook_page_scope_user_id:
            page_scope_user_prompt = await get_active_page_scope_user_prompt_with_media(
                conn, fan_page_id, facebook_page_scope_user_id, owner_user_id
            )

        # System message = build dynamic system prompt with page_memory + user_memory + conversation_info + escalation_list
        page_memory_prompt = await self._build_page_memory_prompt(conn, page_prompt, owner_user_id)
        user_memory_prompt = await self._build_user_memory_prompt(conn, page_scope_user_prompt)
        conversation_info = turn_data.get("conversation_info", "")
        conversation_info_prompt = conversation_info.strip() if conversation_info else ""
        escalation_list_prompt = await self._build_escalation_list_prompt(
            conn, "messages", conversation_id, fan_page_id, owner_user_id
        )
        system_content = build_system_prompt(
            conversation_type="messages",
            page_memory=page_memory_prompt,
            user_memory=user_memory_prompt,
            conversation_info=conversation_info_prompt,
            escalation_list=escalation_list_prompt,
            delivery_mode=delivery_mode,
        )

        # Build trailing content for last user message
        ad_context_str = turn_data.get("ad_context", "")
        ad_context_prompt = self._build_ad_context_prompt(ad_context_str)
        escalation_prompt = await self._build_escalation_context_prompt(
            conn, "messages", conversation_id, fan_page_id, owner_user_id
        )
        hint_prompt = self._build_hint_prompt(hint or "")

        turns = turn_data.get("turns", [])

        input_messages: List[Dict[str, Any]] = [
            {"role": "system", "content": self._content_blocks([system_content])}
        ]

        if not turns:
            trailing_parts: List[str] = []
            if escalation_prompt:
                trailing_parts.append(escalation_prompt)
            if ad_context_prompt:
                trailing_parts.append(ad_context_prompt)
            if hint_prompt:
                trailing_parts.append(hint_prompt)
            trailing_parts.append("[Empty conversation — no customer messages yet.]")
            input_messages.append({"role": "user", "content": self._content_blocks(trailing_parts)})
        else:
            # Add all turns except the last one (pure dialogue, each message = separate block)
            for turn in turns[:-1]:
                input_messages.append({
                    "role": turn["role"],
                    "content": self._content_blocks(turn["content_parts"], role=turn["role"]),
                })

            last_turn = turns[-1]
            if last_turn["role"] == "user":
                last_parts: List[str] = []
                # Inject image-matching hint when user sent an image and page memory has product images
                image_hint = self._build_image_matching_hint(turns, page_memory_prompt)
                if image_hint:
                    last_parts.append(image_hint)
                if escalation_prompt:
                    last_parts.append(escalation_prompt)
                if ad_context_prompt:
                    last_parts.append(ad_context_prompt)
                if hint_prompt:
                    last_parts.append(hint_prompt)
                last_parts.extend(last_turn["content_parts"])
                input_messages.append({"role": "user", "content": self._content_blocks(last_parts)})
            else:
                input_messages.append({
                    "role": last_turn["role"],
                    "content": self._content_blocks(last_turn["content_parts"], role="assistant"),
                })
                trailing_parts: List[str] = []
                if escalation_prompt:
                    trailing_parts.append(escalation_prompt)
                if ad_context_prompt:
                    trailing_parts.append(ad_context_prompt)
                if hint_prompt:
                    trailing_parts.append(hint_prompt)
                if not trailing_parts:
                    trailing_parts.append("[No new customer activity.]")
                input_messages.append({"role": "user", "content": self._content_blocks(trailing_parts)})

        metadata = self._extract_metadata(
            "messages", conversation_id, fb_data, page_prompt, page_scope_user_prompt
        )
        # Include raw memory texts for playbook retriever (avoids re-fetching)
        metadata["page_memory_text"] = page_memory_prompt
        metadata["user_memory_text"] = user_memory_prompt
        # Pass message #N → mid mapping for reply_to_ref resolution in the runner
        metadata["message_ref_map"] = turn_data.get("message_ref_map", {})
        return input_messages, metadata

    async def _fetch_and_format_comments(
        self,
        conn: asyncpg.Connection,
        conversation_id: str,
        owner_user_id: str,
    ) -> Tuple[Optional[Dict[str, Any]], Optional[Dict[str, Any]]]:
        """Fetch and format comment thread data."""
        try:
            system_api_key = get_system_api_key()

            from src.database.postgres.repositories.facebook_queries.comments.comment_conversations import (
                get_conversation_by_id,
            )

            conv_record = await get_conversation_by_id(conn, conversation_id)
            if not conv_record:
                return None, None
            root_comment_id = conv_record.get("root_comment_id")
            if not root_comment_id:
                return None, None

            fb_data, total_count, has_next_page = (
                await self.comment_read_service.get_comment_thread_paginated(
                    conn=conn,
                    root_comment_id=root_comment_id,
                    page=1,
                    page_size=50,
                )
            )
            if fb_data:
                await self.media_service.ensure_comment_assets(
                    conn,
                    owner_user_id,
                    root_comment_id,
                    fb_data,
                    should_describe=True,
                    user_api_key=system_api_key,
                    parent_agent_response_id=None,
                    conversation_id=None,
                    branch_id=None,
                )
                formatted_data = self.formatter.format_conversation_comments(
                    fb_data, root_comment_id
                )
                return fb_data, formatted_data

            return None, None
        except Exception as e:
            logger.error(f"Error fetching comment data: {str(e)}")
            raise

    async def _fetch_and_format_messages_as_turns(
        self,
        conn: asyncpg.Connection,
        conversation_id: str,
        owner_user_id: str,
    ) -> Tuple[Optional[Dict[str, Any]], Optional[Dict[str, Any]]]:
        """Fetch messages and format as structured turns."""
        try:
            system_api_key = get_system_api_key()

            fb_data, total_count, has_next_page = (
                await self.message_read_service.get_conversation_messages_paginated(
                    conn=conn,
                    conversation_id=conversation_id,
                    page=1,
                    page_size=50,
                )
            )
            if fb_data:
                await self.media_service.ensure_conversation_assets(
                    conn=conn,
                    user_id=owner_user_id,
                    fb_conversation_id=conversation_id,
                    fb_data=fb_data,
                    should_describe=True,
                    user_api_key=system_api_key,
                    parent_agent_response_id=None,
                    conversation_id=None,
                    branch_id=None,
                )
                turn_data = self.formatter.format_messages_as_turns(
                    fb_data, conversation_id
                )
                return fb_data, turn_data

            return None, None
        except Exception as e:
            logger.error(f"Error fetching message data: {str(e)}")
            raise

    def _build_conversation_prompt(self, formatted_data: Dict[str, Any]) -> str:
        """
        PART 2: Build formatted conversation prompt (messages or comments).
        This contains the actual conversation content and media.

        Note: In description mode, media descriptions are embedded in fb_content JSON,
        so no separate media_entries are needed.
        """
        fb_content = formatted_data.get("fb_content") or ""
        media_entries = formatted_data.get("media_entries", []) or []

        # Keep everything as plain text (no array-of-objects content).
        # Wrap the entire conversation payload inside <conversation_data> tags.
        extra = ""
        if media_entries:
            # Preserve any fallback media entries in text form.
            extra = (
                "\n\n<media_entries>\n"
                + json.dumps(media_entries, ensure_ascii=False, indent=2)
                + "\n</media_entries>"
            )

        return f"<conversation_data>\n{fb_content}{extra}\n</conversation_data>"

    async def _build_page_memory_prompt(
        self,
        conn: asyncpg.Connection,
        page_prompt: Optional[Dict[str, Any]],
        owner_user_id: str,
    ) -> str:
        """
        Build page memory from memory_blocks.
        Returns raw rendered memory blocks (no outer XML wrapper).
        """
        if not page_prompt or not isinstance(page_prompt, dict):
            return ""

        prompt_id = page_prompt.get("id")
        if not prompt_id:
            return ""

        # Use memory blocks service to render memory
        from src.services.suggest_response.memory_blocks_service import (
            MemoryBlocksService,
        )

        memory_service = MemoryBlocksService()
        rendered_text = await memory_service.render_memory(
            "page_prompt", str(prompt_id)
        )

        if not rendered_text.strip():
            return ""

        return rendered_text

    async def _build_user_memory_prompt(
        self, conn: asyncpg.Connection, page_scope_user_prompt: Optional[Dict[str, Any]]
    ) -> str:
        """
        Build user memory from memory_blocks.
        Returns raw rendered memory blocks (no outer XML wrapper).

        When container exists but has no blocks, returns a hint with the
        container's prompt_id so the agent can add blocks directly without
        trying to create a new container (which would violate the unique
        constraint).
        """
        if not page_scope_user_prompt or not isinstance(page_scope_user_prompt, dict):
            return ""

        prompt_id = page_scope_user_prompt.get("id")
        if not prompt_id:
            return ""

        # Use memory blocks service to render memory
        from src.services.suggest_response.memory_blocks_service import (
            MemoryBlocksService,
        )

        memory_service = MemoryBlocksService()
        rendered_text = await memory_service.render_memory(
            "user_prompt", str(prompt_id)
        )

        if not rendered_text.strip():
            # Container exists but has no blocks yet — hint the agent with
            # the prompt_id so it can INSERT blocks directly without trying
            # to create a new container.
            return (
                f"_No memory blocks yet._\n\n"
                f"Container already exists — use `prompt_id = '{prompt_id}'::uuid` "
                f"when inserting new memory_blocks. Do NOT create a new container."
            )

        return rendered_text

    async def _build_escalation_list_prompt(
        self,
        conn: asyncpg.Connection,
        conversation_type: str,
        conversation_id: str,
        fan_page_id: str,
        owner_user_id: str,
    ) -> str:
        """
        Build minimal escalation list (at most 10, ordered by updated_at DESC) for system prompt.
        Returns raw <escalation .../> lines; if total > 10, appends "+N more case...".
        Returns empty string if no escalations (template handles the fallback text).
        """
        escalations, total_count = await get_escalation_list_minimal(
            conn,
            conversation_type=conversation_type,
            conversation_id=conversation_id,
            fan_page_id=fan_page_id,
            owner_user_id=owner_user_id,
            limit=10,
        )
        if not escalations:
            return ""

        parts = []
        for esc in escalations:
            updated_at_str = self._format_timestamp_ms(esc.get("updated_at"))
            subject_safe = (esc.get("subject") or "").replace('"', "&quot;")
            parts.append(
                f'<escalation id="{esc.get("id", "")}" subject="{subject_safe}" '
                f'priority="{esc.get("priority", "normal")}" status="{esc.get("status", "open")}" '
                f'updated_at="{updated_at_str}"/>'
            )
        if total_count > 10:
            parts.append(f"+{total_count - 10} more case...")
        return "\n".join(parts)

    def _build_image_matching_hint(
        self, turns: List[Dict[str, Any]], page_memory_prompt: str
    ) -> str:
        """Inject a system-reminder when the latest user turn contains an image
        AND page memory has images (media_id refs).

        This nudges the agent to compare the customer's image against
        page memory descriptions and use view_media for confirmation.
        Returns empty string when no hint is needed.
        """
        if not turns:
            return ""

        # Find the last user turn
        last_user_turn = None
        for turn in reversed(turns):
            if turn.get("role") == "user":
                last_user_turn = turn
                break

        if not last_user_turn:
            return ""

        # Check if any content part in the last user turn has an image
        has_user_image = any(
            isinstance(part, dict) and part.get("image_url")
            for part in last_user_turn.get("content_parts", [])
        )
        if not has_user_image:
            return ""

        # Check if page memory contains images (media_id references)
        has_memory_images = 'media_id="' in page_memory_prompt if page_memory_prompt else False
        if not has_memory_images:
            return ""

        return (
            "<system-reminder>"
            "The customer sent an image. Page memory contains images with descriptions. "
            "If the customer's image relates to something in page memory, "
            "compare what you see with the image descriptions to shortlist 2-3 candidates, "
            "then use view_media on those candidates to visually confirm. "
            "Do not load all images — only the most likely matches."
            "</system-reminder>"
        )

    def _build_ad_context_prompt(self, ad_context_str: str) -> str:
        """Wrap ad context in a compact system-reminder (background only)."""
        if not ad_context_str or not ad_context_str.strip():
            return ""
        return (
            "<system-reminder>"
            "Ad entry point — background only, ignore if unrelated to customer's current question.\n"
            f"{ad_context_str.strip()}"
            "</system-reminder>"
        )

    def _build_hint_prompt(self, hint: str) -> str:
        """Wrap hint (raw instruction from API/general_agent) in a system-reminder."""
        if not hint or not hint.strip():
            return ""
        return (
            "<system-reminder>\n"
            "## Instruction Hint\n\n"
            "The following guidance was provided for this conversation. "
            "Follow it as if it were part of your page policy.\n\n"
            f"{hint.strip()}\n"
            "</system-reminder>"
        )

    def _content_blocks(
        self, parts: List[Any], role: str = "user"
    ) -> List[Dict[str, Any]]:
        """Build array-of-objects content for OpenAI Responses API.

        Uses 'input_text' for system/user roles, 'output_text' for assistant role.
        For user messages, a part may be a dict with "text" and optional "image_url":
        when image_url is present (live image), an input_image block is added.
        Assistant messages are always text-only (no image blocks).
        """
        block_type = "output_text" if role == "assistant" else "input_text"
        result: List[Dict[str, Any]] = []
        for p in parts:
            if isinstance(p, dict):
                text = (p.get("text") or "").strip()
                image_url = p.get("image_url") or ""
                if not text and not image_url:
                    continue
                if text and role != "assistant":
                    result.append({"type": "input_text", "text": text})
                elif text:
                    result.append({"type": "output_text", "text": text})
                if image_url and role == "user":
                    result.append({"type": "input_image", "image_url": image_url})
                continue
            s = (p if isinstance(p, str) else "").strip()
            if not s:
                continue
            result.append({"type": block_type, "text": s})
        return result

    def _format_timestamp_ms(self, ts_ms: Optional[int]) -> str:
        """Format BIGINT ms timestamp as YYYY-MM-DD HH:MM."""
        if ts_ms is None:
            return ""
        try:
            dt = datetime.utcfromtimestamp(ts_ms / 1000.0)
            return dt.strftime("%Y-%m-%d %H:%M")
        except (ValueError, TypeError, OSError):
            return ""

    async def _build_escalation_context_prompt(
        self,
        conn: asyncpg.Connection,
        conversation_type: str,
        conversation_id: str,
        fan_page_id: str,
        owner_user_id: str,
    ) -> str:
        """
        Build escalation history from open escalations only.
        Format: <system-reminder> with markdown heading + inner XML.
        Returns empty string if no open escalations.
        """
        escalations, total_count = await get_escalations_for_context(
            conn,
            conversation_type=conversation_type,
            conversation_id=conversation_id,
            fan_page_id=fan_page_id,
            owner_user_id=owner_user_id,
            limit=10,
        )
        if not escalations:
            return ""

        parts = []
        for esc in escalations:
            updated_at_str = self._format_timestamp_ms(esc.get("updated_at"))
            subject_safe = (esc.get("subject") or "").replace('"', "&quot;")
            esc_attrs = f'id="{esc.get("id", "")}" subject="{subject_safe}" priority="{esc.get("priority", "normal")}" status="open" updated_at="{updated_at_str}"'

            msg_lines = []
            for msg in esc.get("messages", []):
                time_str = self._format_timestamp_ms(msg.get("created_at"))
                sender = msg.get("sender_type", "")
                content = (
                    (msg.get("content") or "").replace("<", "&lt;").replace(">", "&gt;")
                )
                msg_lines.append(
                    f'<message sender="{sender}" time="{time_str}">{content}</message>'
                )
            inner = "\n".join(msg_lines) if msg_lines else ""
            parts.append(f"<escalation {esc_attrs}>\n{inner}\n</escalation>")

        content = "\n".join(parts)
        if total_count > 10:
            content += f"\n...+{total_count - 10} case"

        return (
            "<system-reminder>\n"
            "## Escalation History\n\n"
            "Open escalation threads. Resolve if the current situation allows. "
            "Do not reference or respond to escalation content unless it is "
            "directly relevant to the customer's current message.\n\n"
            f"{content}\n"
            "</system-reminder>"
        )

    def _extract_metadata(
        self,
        conversation_type: str,
        conversation_id: str,
        fb_data: Dict[str, Any],
        page_prompt: Optional[Dict[str, Any]],
        page_scope_user_prompt: Optional[Dict[str, Any]],
    ) -> Dict[str, Any]:
        """Extract metadata for history saving."""
        metadata = {
            "conversation_type": conversation_type,
            "conversation_id": conversation_id,
        }

        # Get latest item info
        if conversation_type == "messages":
            metadata["latest_item_id"] = fb_data.get("latest_message_id", "")
            metadata["latest_item_facebook_time"] = fb_data.get(
                "latest_message_facebook_time", 0
            )
        else:  # comments
            metadata["latest_item_id"] = fb_data.get("latest_comment_id", "")
            # Convert seconds to milliseconds for consistency
            latest_time_seconds = fb_data.get("latest_comment_facebook_time", 0)
            metadata["latest_item_facebook_time"] = (
                latest_time_seconds * 1000 if latest_time_seconds else 0
            )

        # Prompt IDs
        if page_prompt:
            metadata["page_prompt_id"] = str(page_prompt.get("id", ""))
        if page_scope_user_prompt:
            metadata["page_scope_user_prompt_id"] = str(
                page_scope_user_prompt.get("id", "")
            )

        return metadata
