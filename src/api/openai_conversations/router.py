"""
OpenAI Conversations API router.

FastAPI routes for OpenAI conversation management endpoints.
"""

from typing import Optional

from fastapi import APIRouter, Depends, Request, status, Query

from src.middleware.auth_middleware import get_current_user_id
from src.api.openai_conversations.schemas import (
    CreateConversationRequest,
    ConversationResponse,
    ConversationDetailResponse,
    ConversationsCursorResponse,
    MessagesCursorResponse,
    BranchResponse,
    UpdateBranchNameRequest,
    UpdateConversationRequest,
    UpdateConversationSettingsRequest,
)
from src.api.openai_conversations.handler import (
    create_conversation_handler,
    get_conversations_handler,
    get_conversation_detail_handler,
    get_conversation_messages_handler,
    update_conversation_handler,
    update_conversation_settings_handler,
    get_conversation_branches_handler,
    update_branch_name_handler,
    get_subagent_messages_handler,
)

router = APIRouter(prefix="/openai", tags=["OpenAI Conversations"])


# ================================================================
# CONVERSATION ROUTES
# ================================================================


@router.post(
    "/conversations",
    response_model=ConversationResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Create a new conversation",
    description="Create a new OpenAI conversation with optional title and developer message",
)
async def create_conversation(
    app_request: Request,
    request: CreateConversationRequest,
    user_id: str = Depends(get_current_user_id),
):
    return await create_conversation_handler(user_id, request, app_request)


@router.get(
    "/conversations",
    response_model=ConversationsCursorResponse,
    summary="Get user conversations",
    description="Get conversations for the authenticated user with cursor-based pagination, ordered by latest message activity",
)
async def get_conversations(
    app_request: Request,
    limit: int = Query(
        50, ge=1, le=100, description="Number of conversations to fetch"
    ),
    cursor: Optional[str] = Query(
        None,
        description="Cursor for pagination (conversation ID from previous response)",
    ),
    user_id: str = Depends(get_current_user_id),
):
    return await get_conversations_handler(user_id, limit, cursor, app_request)


@router.get(
    "/conversations/{conversation_id}",
    response_model=ConversationDetailResponse,
    summary="Get conversation detail",
    description="Get a single conversation with branches and linked Facebook data",
)
async def get_conversation_detail(
    conversation_id: str,
    app_request: Request,
    user_id: str = Depends(get_current_user_id),
):
    return await get_conversation_detail_handler(conversation_id, user_id, app_request)


@router.put(
    "/conversations/{conversation_id}",
    response_model=ConversationResponse,
    summary="Update conversation",
    description="Update conversation settings (switch branch, update title)",
)
async def update_conversation(
    conversation_id: str,
    app_request: Request,
    request: UpdateConversationRequest,
    user_id: str = Depends(get_current_user_id),
):
    return await update_conversation_handler(
        conversation_id, user_id, request, app_request
    )


@router.patch(
    "/conversations/{conversation_id}/settings",
    response_model=ConversationResponse,
    summary="Update conversation model settings",
    description="Update conversation model settings (model, reasoning, verbosity). Accepts string format: 'gpt-5-mini reasoning: high, verbosity: high' or structured fields.",
)
async def update_conversation_settings(
    conversation_id: str,
    app_request: Request,
    request: UpdateConversationSettingsRequest,
    user_id: str = Depends(get_current_user_id),
):
    return await update_conversation_settings_handler(
        conversation_id, user_id, request, app_request
    )


@router.get(
    "/conversations/{conversation_id}/branches",
    response_model=list[BranchResponse],
    summary="Get conversation branches",
    description="Get all branches for a conversation",
)
async def get_branches(
    conversation_id: str,
    user_id: str = Depends(get_current_user_id),
):
    return await get_conversation_branches_handler(conversation_id, user_id)


@router.put(
    "/conversations/{conversation_id}/branches/{branch_id}",
    response_model=BranchResponse,
    summary="Update conversation branch name",
    description="Update the name of a conversation branch",
)
async def update_branch_name_route(
    conversation_id: str,
    branch_id: str,
    app_request: Request,
    request: UpdateBranchNameRequest,
    user_id: str = Depends(get_current_user_id),
):
    return await update_branch_name_handler(
        conversation_id, branch_id, user_id, request
    )


# ================================================================
# MESSAGE ROUTES
# ================================================================


@router.get(
    "/conversations/{conversation_id}/branches/{branch_id}/messages",
    response_model=MessagesCursorResponse,
    summary="Get conversation messages",
    description="Get messages for a conversation branch with cursor-based pagination",
)
async def get_conversation_messages(
    conversation_id: str,
    branch_id: str,
    app_request: Request,
    limit: int = Query(50, ge=1, le=100, description="Number of messages to fetch"),
    cursor: Optional[int] = Query(
        None,
        description="Cursor for pagination (ordinal position from previous response)",
    ),
    user_id: str = Depends(get_current_user_id),
):
    return await get_conversation_messages_handler(
        conversation_id, branch_id, user_id, limit, cursor, app_request
    )


@router.get(
    "/conversations/{conversation_id}/subagents/{subagent_conversation_id}/messages",
    response_model=MessagesCursorResponse,
    summary="Get subagent conversation messages",
    description="Get messages for a subagent conversation (for expanding task tool_call UI)",
)
async def get_subagent_messages(
    conversation_id: str,  # Parent conversation (for authorization)
    subagent_conversation_id: str,  # Subagent conversation to fetch
    app_request: Request,
    limit: int = Query(50, ge=1, le=100, description="Number of messages to fetch"),
    cursor: Optional[int] = Query(
        None,
        description="Cursor for pagination (ordinal position from previous response)",
    ),
    user_id: str = Depends(get_current_user_id),
):
    """
    Get messages from a subagent conversation.

    Used by FE when user expands a task tool_call to see full subagent history.

    Authorization: Verifies subagent belongs to the parent conversation and user.
    """
    return await get_subagent_messages_handler(
        parent_conversation_id=conversation_id,
        subagent_conversation_id=subagent_conversation_id,
        user_id=user_id,
        limit=limit,
        cursor=cursor,
        app_request=app_request,
    )
