"""
Helper functions to build enriched socket event data for comment webhooks.
Transforms raw DB data to match API schema format for frontend consistency.
"""

import json
from typing import Dict, Any, Optional, List
from datetime import datetime

from src.api.facebook.comments.schemas import (
    Comment,
    CommentThreadSummary,
    CommentSocketEventData,
    PageInfo,
    PostInfo,
    ConversationParticipant,
)
from src.utils.logger import get_logger

logger = get_logger()


def _parse_user_info(user_info: Any) -> Dict[str, Any]:
    """Parse user_info which may be dict or JSON string."""
    if isinstance(user_info, dict):
        return user_info
    if isinstance(user_info, str):
        try:
            parsed = json.loads(user_info)
            return parsed if isinstance(parsed, dict) else {}
        except (json.JSONDecodeError, TypeError):
            return {}
    return {}


def _parse_participants(raw_participants: Any) -> List[Dict[str, Any]]:
    """Parse participant_scope_users which may be list or JSON string."""
    if isinstance(raw_participants, list):
        return raw_participants
    if isinstance(raw_participants, str):
        try:
            parsed = json.loads(raw_participants)
            return parsed if isinstance(parsed, list) else []
        except (json.JSONDecodeError, TypeError):
            return []
    return []


def _enrich_comment(
    raw_comment: Dict[str, Any],
    root_comment_id: str,
) -> Comment:
    """
    Transform raw comment DB record to enriched Comment schema.

    Args:
        raw_comment: Raw comment dict from database (with JOINed data)
        root_comment_id: Root comment ID for the conversation

    Returns:
        Comment Pydantic model
    """
    user_info = _parse_user_info(raw_comment.get("user_info"))
    metadata_raw = raw_comment.get("metadata")
    metadata_parsed: Optional[Dict[str, Any]] = None
    if metadata_raw is not None:
        if isinstance(metadata_raw, dict):
            metadata_parsed = metadata_raw
        elif isinstance(metadata_raw, str):
            try:
                parsed = json.loads(metadata_raw)
                metadata_parsed = parsed if isinstance(parsed, dict) else None
            except (json.JSONDecodeError, TypeError):
                pass

    return Comment(
        id=raw_comment["id"],
        post_id=raw_comment["post_id"],
        fan_page_id=raw_comment["fan_page_id"],
        parent_comment_id=raw_comment.get("parent_comment_id"),
        root_comment_id=root_comment_id,
        is_from_page=raw_comment.get("is_from_page", False),
        facebook_page_scope_user_id=raw_comment.get("facebook_page_scope_user_id"),
        message=raw_comment.get("message"),
        photo_url=raw_comment.get("photo_url"),
        video_url=raw_comment.get("video_url"),
        facebook_created_time=raw_comment.get("facebook_created_time"),
        like_count=raw_comment.get("like_count", 0),
        reply_count=raw_comment.get("reply_count", 0),
        reactions_fetched_at=raw_comment.get("reactions_fetched_at"),
        is_hidden=raw_comment.get("is_hidden", False),
        page_seen_at=raw_comment.get("page_seen_at"),
        deleted_at=raw_comment.get("deleted_at"),
        created_at=raw_comment["created_at"],
        updated_at=raw_comment["updated_at"],
        author_kind="page" if raw_comment.get("is_from_page") else "user",
        fpsu_id=raw_comment.get("fpsu_id")
        or raw_comment.get("facebook_page_scope_user_id"),
        fpsu_name=raw_comment.get("fpsu_name") or user_info.get("name"),
        fpsu_profile_pic=raw_comment.get("fpsu_profile_pic")
        or user_info.get("profile_pic"),
        page_name=raw_comment.get("page_name"),
        page_avatar=raw_comment.get("page_avatar"),
        page_category=raw_comment.get("page_category"),
        post_message=raw_comment.get("post_message"),
        metadata=metadata_parsed,
    )


def _build_conversation_summary(
    conversation: Dict[str, Any],
    root_comment: Dict[str, Any],
    latest_comment: Optional[Dict[str, Any]] = None,
) -> CommentThreadSummary:
    """
    Transform raw conversation DB record to CommentThreadSummary schema.

    Args:
        conversation: Raw conversation dict from get_conversation_with_unread_count
        root_comment: Raw root comment dict (enriched with JOINs)
        latest_comment: Optional latest comment dict (enriched with JOINs)

    Returns:
        CommentThreadSummary Pydantic model
    """
    root_comment_id = conversation["root_comment_id"]

    # Build nested PageInfo
    page_info = PageInfo(
        id=conversation["fan_page_id"],
        name=conversation.get("page_name"),
        avatar=conversation.get("page_avatar"),
        category=conversation.get("page_category"),
        created_at=conversation.get("page_created_at", 0),
        updated_at=conversation.get("page_updated_at", 0),
    )

    # Build nested PostInfo
    post_info = PostInfo(
        id=conversation["post_id"],
        fan_page_id=conversation["fan_page_id"],
        message=conversation.get("post_message"),
        video_link=conversation.get("post_video_link"),
        photo_link=conversation.get("post_photo_link"),
        facebook_created_time=conversation.get("post_facebook_created_time"),
        reaction_total_count=conversation.get("post_reaction_total_count", 0),
        reaction_like_count=conversation.get("post_reaction_like_count", 0),
        reaction_love_count=conversation.get("post_reaction_love_count", 0),
        reaction_haha_count=conversation.get("post_reaction_haha_count", 0),
        reaction_wow_count=conversation.get("post_reaction_wow_count", 0),
        reaction_sad_count=conversation.get("post_reaction_sad_count", 0),
        reaction_angry_count=conversation.get("post_reaction_angry_count", 0),
        reaction_care_count=conversation.get("post_reaction_care_count", 0),
        share_count=conversation.get("post_share_count", 0),
        comment_count=conversation.get("post_comment_count", 0),
        full_picture=conversation.get("post_full_picture"),
        permalink_url=conversation.get("post_permalink_url"),
        status_type=conversation.get("post_status_type"),
        is_published=conversation.get("post_is_published", True),
        reactions_fetched_at=conversation.get("post_reactions_fetched_at"),
        engagement_fetched_at=conversation.get("post_engagement_fetched_at"),
        created_at=conversation.get("post_created_at", 0),
        updated_at=conversation.get("post_updated_at", 0),
    )

    # Build enriched root comment
    enriched_root_comment = _enrich_comment(root_comment, root_comment_id)

    # Build enriched latest comment if exists
    enriched_latest_comment = None
    if latest_comment:
        enriched_latest_comment = _enrich_comment(latest_comment, root_comment_id)

    # Parse participants
    raw_participants = _parse_participants(conversation.get("participant_scope_users"))
    participants = [
        ConversationParticipant(
            facebook_page_scope_user_id=p.get("facebook_page_scope_user_id"),
            name=p.get("name"),
            avatar=p.get("avatar") or p.get("profile_pic"),
            last_comment_id=p.get("last_comment_id"),
            last_comment_time=p.get("last_comment_time"),
        )
        for p in raw_participants
    ]

    return CommentThreadSummary(
        conversation_id=str(conversation["id"]),
        root_comment_id=root_comment_id,
        fan_page_id=conversation["fan_page_id"],
        post_id=conversation["post_id"],
        total_comments=conversation.get("total_comments", 0),
        unread_count=conversation.get("unread_count", 0),
        mark_as_read=conversation.get("mark_as_read", False),
        has_page_reply=conversation.get("has_page_reply", False),
        latest_comment_is_from_page=conversation.get("latest_comment_is_from_page"),
        page=page_info,
        post=post_info,
        root_comment=enriched_root_comment,
        latest_comment=enriched_latest_comment,
        participants=participants,
    )


def build_comment_socket_event(
    page_id: str,
    action: str,
    conversation: Dict[str, Any],
    mutated_comment: Dict[str, Any],
    root_comment: Dict[str, Any],
    latest_comment: Optional[Dict[str, Any]] = None,
) -> CommentSocketEventData:
    """
    Build the complete socket event data with Pydantic validation.

    Args:
        page_id: Facebook page ID
        action: Action type (add, edited, remove, hide, unhide)
        conversation: Raw conversation dict from get_conversation_with_unread_count
        mutated_comment: The comment that was mutated (raw dict with JOINs)
        root_comment: Root comment dict (raw dict with JOINs)
        latest_comment: Optional latest comment dict

    Returns:
        CommentSocketEventData Pydantic model (validated)
    """
    root_comment_id = conversation["root_comment_id"]

    # Build conversation summary with nested objects
    conversation_summary = _build_conversation_summary(
        conversation=conversation,
        root_comment=root_comment,
        latest_comment=latest_comment,
    )

    # Enrich the mutated comment
    enriched_mutated_comment = _enrich_comment(mutated_comment, root_comment_id)

    return CommentSocketEventData(
        page_id=page_id,
        conversation=conversation_summary,
        mutated_comment=enriched_mutated_comment,
        action=action,
        timestamp=int(datetime.now().timestamp()),
    )
