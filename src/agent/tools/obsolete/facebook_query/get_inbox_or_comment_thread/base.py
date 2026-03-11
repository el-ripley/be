"""Base class for Facebook tools with shared logic."""

from typing import Any, Dict, Optional

from src.agent.common.metadata_types import MessageMetadata
from src.agent.tools.base import BaseTool

FACEBOOK_CONVERSATION_IMAGE_CONTEXT = "Conversation images indexed by type and index:"
FACEBOOK_COMMENT_IMAGE_CONTEXT = "Comment images indexed by type and index:"


def get_facebook_conversation_system_reminder() -> str:
    """Get system reminder text for conversation messages."""
    return "Facebook conversation context:"


def get_facebook_comment_system_reminder() -> str:
    """Get system reminder text for comment threads."""
    return "Facebook comment thread context:"


class FacebookBaseTool(BaseTool):
    """Base class for Facebook tools with shared logic."""

    def build_facebook_metadata(
        self, tool_result: Any, tool_args: Dict[str, Any]
    ) -> Optional[MessageMetadata]:
        """Build Facebook-specific metadata from tool result."""
        fb_meta: MessageMetadata = {
            "source": "fb_context_fetch",
            "tool_name": self.name,
            "item_id": (
                tool_result.get("item_id")
                if isinstance(tool_result, dict)
                else tool_args.get("item_id")
            ),
            "item_type": (
                tool_result.get("item_type")
                if isinstance(tool_result, dict)
                else tool_args.get("item_type")
            ),
            "normalized_item_type": (
                tool_result.get("normalized_item_type")
                if isinstance(tool_result, dict)
                else None
            ),
            "page": (
                tool_result.get("page")
                if isinstance(tool_result, dict)
                else tool_args.get("page")
            ),
            "page_size": (
                tool_result.get("page_size")
                if isinstance(tool_result, dict)
                else tool_args.get("limit")
            ),
            "total_count": (
                tool_result.get("total_count")
                if isinstance(tool_result, dict)
                else None
            ),
            "total_pages": (
                tool_result.get("total_pages")
                if isinstance(tool_result, dict)
                else None
            ),
            "has_next_page": (
                tool_result.get("has_next_page")
                if isinstance(tool_result, dict)
                else None
            ),
            "next_page": (
                tool_result.get("next_page") if isinstance(tool_result, dict) else None
            ),
        }

        # Clean up None values - remove all None fields to avoid Pydantic validation errors
        # MessageMetadata TypedDict fields are not Optional (except next_page), so None values cause validation errors
        fb_meta_cleaned: MessageMetadata = {}
        for key, value in fb_meta.items():
            if value is not None:
                fb_meta_cleaned[key] = value

        # If no item_id, treat metadata as unset
        if not fb_meta_cleaned.get("item_id"):
            return None

        return fb_meta_cleaned

    def build_facebook_summary(
        self,
        tool_result: Any,
        tool_args: Dict[str, Any],
        fb_content: Optional[str],
        media_entries: list,
        include_content_in_output: bool = False,
    ) -> str:
        """Create a natural language summary for the Facebook tool result.

        Args:
            tool_result: The raw tool result dict
            tool_args: The tool arguments
            fb_content: Formatted Facebook content text
            media_entries: List of media entries (images/videos)
            include_content_in_output: If True and fb_content exists but no media_entries,
                                       include fb_content directly in output instead of
                                       referencing human_message.
        """
        summary_item_type = None
        summary_item_id = None
        success_flag = None
        error_message = None
        if isinstance(tool_result, dict):
            summary_item_type = (
                tool_result.get("normalized_item_type")
                or tool_result.get("item_type")
                or tool_args.get("item_type")
            )
            summary_item_id = tool_result.get("item_id") or tool_args.get("item_id")
            success_flag = tool_result.get("success")
            error_message = tool_result.get("error")
        else:
            summary_item_type = tool_args.get("item_type")
            summary_item_id = tool_args.get("item_id")

        readable_type_map = {
            "conv_messages": "conversation messages",
            "fb_conv_messages": "conversation messages",
            "conv_comments": "comment thread",
            "fb_conv_comments": "comment thread",
        }
        readable_type = readable_type_map.get(summary_item_type, summary_item_type)
        if not readable_type:
            readable_type = "Facebook data"

        readable_id = summary_item_id or "the requested item"

        if success_flag is False:
            summary_text = (
                f"Attempted to fetch {readable_type} ({readable_id}) but success=False."
            )
            if error_message:
                summary_text += f" Error: {error_message}."
            summary_text += " No content generated because data was unavailable."
        else:
            summary_text = f"Fetched {readable_type} ({readable_id}) successfully."

        if media_entries:
            # When we have media entries, content is in human_message with images
            summary_text += (
                " Data includes images/media, so it is represented in the human_message "
                "below."
            )
        elif fb_content and include_content_in_output:
            # When no media but have content, include it directly in output
            summary_text += f"\n\n{fb_content}"
        elif fb_content:
            # Legacy behavior: reference human_message (for backward compatibility)
            summary_text += " Data content is provided in the human_message below."

        return summary_text
