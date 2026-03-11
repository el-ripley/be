"""
OpenAI Conversations API handler.

Business logic for OpenAI conversation management endpoints.
"""

import json as json_lib
from typing import Any, Dict, List, Optional

from fastapi import HTTPException, Request, status

from src.agent.common.conversation_settings import (
    normalize_settings,
    parse_settings_string,
    validate_settings,
)
from src.api.openai_conversations.schemas import (
    BranchResponse,
    ConversationDetailResponse,
    ConversationResponse,
    ConversationsCursorResponse,
    CreateConversationRequest,
    MessageResponse,
    MessagesCursorResponse,
    UpdateBranchNameRequest,
    UpdateConversationRequest,
    UpdateConversationSettingsRequest,
)
from src.database.postgres.connection import async_db_transaction, get_async_connection
from src.database.postgres.repositories.agent_queries import (
    create_conversation_with_master_branch,
    get_branch_info,
    get_branch_messages,
    get_conversation,
    get_conversation_branches,
    get_conversation_with_relations,
    get_user_conversations,
    update_branch_name,
    update_conversation,
    update_conversation_settings,
)

# ================================================================
# CONVERSATION HANDLERS AND BRANCHES
# ================================================================


async def create_conversation_handler(
    user_id: str,
    request: CreateConversationRequest,
    app_request: Request,
) -> ConversationResponse:
    _ = app_request  # request retained for interface compatibility
    async with async_db_transaction() as conn:
        conversation_id, branch_id = await create_conversation_with_master_branch(
            conn=conn,
            user_id=user_id,
            title=request.title,
        )

        # Get the created conversation
        conversation = await get_conversation(conn, conversation_id)
        if not conversation:
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail="Failed to create conversation",
            )

    return ConversationResponse(**conversation.model_dump())


async def get_conversations_handler(
    user_id: str,
    limit: int = 50,
    cursor: Optional[str] = None,
    app_request: Request = None,
) -> ConversationsCursorResponse:
    _ = app_request  # request retained for interface compatibility
    # Validate limit parameter
    if limit < 1 or limit > 100:
        limit = 50

    # Get conversations with cursor-based pagination
    async with get_async_connection() as conn:
        conversations_data, has_more = await get_user_conversations(
            conn=conn,
            user_id=user_id,
            limit=limit,
            cursor=cursor,
        )

    # Convert UUID objects to strings
    for conv in conversations_data:
        if conv.get("id"):
            conv["id"] = str(conv["id"])
        if conv.get("current_branch_id"):
            conv["current_branch_id"] = str(conv["current_branch_id"])

    # Convert to response format
    conversations = [ConversationResponse(**conv) for conv in conversations_data]

    # Get next cursor (last conversation ID if has_more)
    next_cursor = None
    if has_more and conversations:
        next_cursor = conversations[-1].id

    return ConversationsCursorResponse(
        items=conversations,
        has_more=has_more,
        next_cursor=next_cursor,
    )


async def get_conversation_detail_handler(
    conversation_id: str,
    user_id: str,
    app_request: Request | None = None,
) -> ConversationDetailResponse:
    _ = app_request  # request retained for interface compatibility
    """Get a single conversation with related data."""
    # app_request kept for parity with other handlers (e.g., caching in future)
    async with get_async_connection() as conn:
        conversation_data = await get_conversation_with_relations(
            conn, conversation_id, user_id
        )

    if not conversation_data:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Conversation not found",
        )

    return ConversationDetailResponse(**conversation_data)


async def update_conversation_settings_handler(
    conversation_id: str,
    user_id: str,
    request: UpdateConversationSettingsRequest,
    app_request: Request,
) -> ConversationResponse:
    """Update conversation model settings."""
    _ = app_request  # request retained for interface compatibility

    async with async_db_transaction() as conn:
        # First verify the conversation exists and belongs to the user
        conversation = await get_conversation(conn, conversation_id)
        if not conversation:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND, detail="Conversation not found"
            )

        if conversation.user_id != user_id:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Access denied to this conversation",
            )

        # Parse settings from request
        settings_dict: Dict[str, Any] = {}

        if request.settings:
            # Parse string format: "gpt-5-mini reasoning: high, verbosity: high"
            try:
                settings_dict = parse_settings_string(request.settings)
            except ValueError as e:
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail=f"Invalid settings format: {str(e)}",
                )
        else:
            # Use structured fields if provided
            if request.model:
                settings_dict["model"] = request.model
            if request.reasoning is not None:
                settings_dict["reasoning"] = request.reasoning
            if request.verbosity is not None:
                settings_dict["verbosity"] = request.verbosity
            if request.web_search_enabled is not None:
                settings_dict["web_search_enabled"] = request.web_search_enabled

        # Validate settings
        is_valid, error = validate_settings(settings_dict)
        if not is_valid:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"Invalid settings: {error}",
            )

        # Get current settings and merge
        current_settings = conversation.settings if conversation.settings else {}
        if isinstance(current_settings, str):
            try:
                current_settings = json_lib.loads(current_settings)
            except json_lib.JSONDecodeError:
                current_settings = {}

        merged_settings = dict(current_settings)
        merged_settings.update(settings_dict)

        # Normalize to ensure all required fields are present
        normalized_settings = normalize_settings(merged_settings)

        # Update conversation settings
        success = await update_conversation_settings(
            conn,
            conversation_id,
            normalized_settings,
        )

        if not success:
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail="Failed to update conversation settings",
            )

        # Get the updated conversation
        updated_conversation = await get_conversation(conn, conversation_id)
        if not updated_conversation:
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail="Failed to retrieve updated conversation",
            )

    return ConversationResponse(**updated_conversation.dict())


async def update_conversation_handler(
    conversation_id: str,
    user_id: str,
    request: UpdateConversationRequest,
    app_request: Request,
) -> ConversationResponse:
    _ = app_request  # request retained for interface compatibility
    """Update conversation settings (branch, title)."""
    async with async_db_transaction() as conn:
        # First verify the conversation exists and belongs to the user
        conversation = await get_conversation(conn, conversation_id)
        if not conversation:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND, detail="Conversation not found"
            )

        if conversation.user_id != user_id:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Access denied to this conversation",
            )

        success = await update_conversation(
            conn,
            conversation_id,
            request.branch_id,
            request.title,
        )
        if not success:
            error_message = "Failed to update conversation"
            if request.branch_id:
                error_message += ". Branch may not exist or belong to this conversation"
            else:
                error_message += ". No valid updates provided"
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=error_message,
            )

        # Get the updated conversation
        updated_conversation = await get_conversation(conn, conversation_id)
        if not updated_conversation:
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail="Failed to retrieve updated conversation",
            )

    return ConversationResponse(**updated_conversation.dict())


async def get_conversation_branches_handler(
    conversation_id: str,
    user_id: str,
) -> List[BranchResponse]:
    """Get all branches for a conversation."""
    async with get_async_connection() as conn:
        # First verify the conversation exists and belongs to the user
        conversation = await get_conversation(conn, conversation_id)
        if not conversation:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND, detail="Conversation not found"
            )

        if conversation.user_id != user_id:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Access denied to this conversation",
            )

        branches_data = await get_conversation_branches(conn, conversation_id)
        return [BranchResponse(**branch) for branch in branches_data]


async def update_branch_name_handler(
    conversation_id: str,
    branch_id: str,
    user_id: str,
    request: UpdateBranchNameRequest,
) -> BranchResponse:
    """Update the name of a conversation branch."""
    async with async_db_transaction() as conn:
        # First verify the conversation exists and belongs to the user
        conversation = await get_conversation(conn, conversation_id)
        if not conversation:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND, detail="Conversation not found"
            )

        if conversation.user_id != user_id:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Access denied to this conversation",
            )

        success = await update_branch_name(
            conn=conn,
            branch_id=branch_id,
            conversation_id=conversation_id,
            branch_name=request.branch_name,
        )

        if not success:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Branch not found for this conversation",
            )

        # Get the updated branch
        branches_data = await get_conversation_branches(conn, conversation_id)
        branch_data = next((b for b in branches_data if b["id"] == branch_id), None)

        if not branch_data:
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail="Failed to retrieve updated branch",
            )

    return BranchResponse(**branch_data)


# ================================================================
# MESSAGE HANDLERS
# ================================================================


async def get_conversation_messages_handler(
    conversation_id: str,
    branch_id: str,
    user_id: str,
    limit: int = 50,
    cursor: Optional[int] = None,
    app_request: Request = None,
) -> MessagesCursorResponse:
    _ = app_request  # request retained for interface compatibility
    # Validate limit parameter
    if limit < 1 or limit > 100:
        limit = 50

    async with get_async_connection() as conn:
        # First verify the conversation exists and belongs to the user
        conversation = await get_conversation(conn, conversation_id)
        if not conversation:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND, detail="Conversation not found"
            )

        if conversation.user_id != user_id:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Access denied to this conversation",
            )

        # Verify the branch belongs to this conversation
        branches = await get_conversation_branches(conn, conversation_id)
        branch_ids = {branch["id"] for branch in branches}

        if branch_id not in branch_ids:
            # Check if branch exists at all in database (even if wrong conversation)
            branch_info = await get_branch_info(conn, branch_id)

            # Provide more specific error message
            if branch_info:
                branch_conversation_id = branch_info["conversation_id"]
                if branch_conversation_id != conversation_id:
                    error_detail = (
                        f"Branch {branch_id} belongs to a different conversation "
                        f"({branch_conversation_id}), not the requested conversation ({conversation_id})"
                    )
                else:
                    error_detail = "Branch not found for this conversation"
            else:
                error_detail = f"Branch {branch_id} does not exist"

            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=error_detail,
            )
        messages_data, has_more = await get_branch_messages(
            conn=conn,
            branch_id=branch_id,
            limit=limit,
            cursor=cursor,
        )

    # Extract ord from messages_data before converting to response
    # We need ord to calculate next_cursor
    next_cursor = None
    if has_more and messages_data:
        # Get ord from the last message (it's still in the dict before conversion)
        last_message = messages_data[-1]
        if "ord" in last_message:
            next_cursor = last_message["ord"]
        # Remove ord from all messages before converting to response
        for msg in messages_data:
            msg.pop("ord", None)

    messages = [MessageResponse(**msg) for msg in messages_data]

    return MessagesCursorResponse(
        items=messages,
        has_more=has_more,
        next_cursor=next_cursor,
    )


async def get_subagent_messages_handler(
    parent_conversation_id: str,
    subagent_conversation_id: str,
    user_id: str,
    limit: int = 50,
    cursor: Optional[int] = None,
    app_request: Request = None,
) -> MessagesCursorResponse:
    """
    Get messages from a subagent conversation.

    Used by FE when user expands a task tool_call to see full subagent history.

    Authorization: Verifies subagent belongs to the parent conversation and user.
    """
    _ = app_request  # request retained for interface compatibility
    # Validate limit parameter
    if limit < 1 or limit > 100:
        limit = 50

    async with get_async_connection() as conn:
        # 1. Verify subagent belongs to parent conversation
        subagent_conv = await get_conversation(conn, subagent_conversation_id)

        if not subagent_conv:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Subagent conversation not found",
            )

        if subagent_conv.parent_conversation_id != parent_conversation_id:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Subagent does not belong to this conversation",
            )

        if subagent_conv.user_id != user_id:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN, detail="Not authorized"
            )

        # 2. Get messages (reuse existing logic)
        # Subagent conversations don't have branches, use default branch
        branch_id = subagent_conv.current_branch_id
        if not branch_id:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Subagent conversation has no active branch",
            )

        return await get_conversation_messages_handler(
            conversation_id=subagent_conversation_id,
            branch_id=branch_id,
            user_id=user_id,
            limit=limit,
            cursor=cursor,
            app_request=app_request,
        )
