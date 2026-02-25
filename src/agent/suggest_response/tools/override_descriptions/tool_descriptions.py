"""Custom tool descriptions for suggest_response_agent scope.

These override the default descriptions to limit tool scope and avoid
affecting the general_agent which uses full descriptions.

Descriptions are loaded from markdown files in this directory:
- sql_query_messages.md  (messages-specific: includes customer memory tables)
- sql_query_comments.md  (comments-specific: escalations and blocking only)
- view_media.md

IMPORTANT: suggest_response_agent is "locked" to ONE Facebook conversation.
Row-Level Security (RLS) automatically filters all data to that conversation.
Many columns have DEFAULT values from session - agent does NOT need to provide them.
"""

from functools import lru_cache
from pathlib import Path

_DIR = Path(__file__).parent


@lru_cache(maxsize=8)
def _load_description(name: str) -> str:
    """Load tool description from {this_dir}/{name}.md"""
    path = _DIR / f"{name}.md"
    return path.read_text(encoding="utf-8").strip() + "\n"


SR_SQL_QUERY_MESSAGES_DESCRIPTION = _load_description("sql_query_messages")
SR_SQL_QUERY_COMMENTS_DESCRIPTION = _load_description("sql_query_comments")
SR_VIEW_MEDIA_DESCRIPTION = _load_description("view_media")
SR_MANAGE_PLAYBOOK_DESCRIPTION = _load_description("manage_playbook_search")
