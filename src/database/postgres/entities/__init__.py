"""
Database entity models for PostgreSQL.

These models represent the complete structure of database tables and are used
by the repository layer for data access operations. They contain only the
full database record representations, not validation or DTO variants.
"""

from .user_entities import (
    Role,
    User,
)

from .facebook_entities import (
    FacebookAppScopeUser,
    FanPage,
    FacebookPageAdmin,
    FacebookPageScopeUser,
)

from .comments_entities import (
    Post,
    Comment,
)

from .messages_entities import (
    Message,
)

from .agent_entities import (
    OpenAIResponse,
    OpenAIConversation,
    OpenAIMessage,
)


__all__ = [
    # User entities
    "Role",
    "User",
    # Facebook entities
    "FacebookAppScopeUser",
    "FanPage",
    "FacebookPageAdmin",
    "FacebookPageScopeUser",
    # Comments domain entities (Posts + Comments)
    "Post",
    "Comment",
    # Messages domain entities
    "Message",
    # Agent entities
    "OpenAIResponse",
    "OpenAIConversation",
    "OpenAIMessage",
]
