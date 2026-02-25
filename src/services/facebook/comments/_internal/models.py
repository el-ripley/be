"""
Data models for comment webhook processing.
"""

from dataclasses import dataclass
from typing import Any, Dict, List, Optional


@dataclass
class CommentHydrationPayload:
    """
    Enriched comment data from various sources (webhook, Graph API, database).
    """

    message: str
    photo_url: Optional[str]
    video_url: Optional[str]
    from_id: Optional[str]
    parent_comment_id: Optional[str]
    is_root_comment: bool
    fetched_comment_data: Optional[Dict[str, Any]]


@dataclass
class CommentEventContext:
    """
    Complete context for processing a comment webhook event.
    """

    page_id: str
    post_id: str
    comment_id: str
    verb: str
    actor_id: str
    page_admins: List[Dict[str, Any]]
    message: str
    photo_url: Optional[str]
    video_url: Optional[str]
    facebook_created_time: Optional[int]
    parent_comment_id: Optional[str]
    is_root_comment: bool
    fetched_comment_data: Optional[Dict[str, Any]]
    from_id: Optional[str]
    facebook_page_scope_user_id: Optional[str]
    is_comment_from_page: bool
    tree_root_id: Optional[str] = None  # Root comment ID for conversation grouping
