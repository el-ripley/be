"""
Suggest Response API Router.
"""

from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Request, status

from src.middleware.auth_middleware import get_current_user_id
from src.services.facebook.auth import FacebookPermissionService
from src.utils.logger import get_logger

from .handler import SuggestResponseHandler
from .schemas import (
    AgentSettingsResponse,
    AgentSettingsUpdate,
    AssignedPlaybooksResponse,
    GenerateSuggestionsRequest,
    GenerateSuggestionsResponse,
    PageAdminSuggestConfigResponse,
    PageAdminSuggestConfigUpdate,
    PageMemoryResponse,
    SuggestResponseHistoryListResponse,
    SuggestResponseHistoryResponse,
    SuggestResponseMessageListResponse,
    UpdateSuggestResponseHistoryRequest,
    UserMemoryResponse,
)

logger = get_logger()

router = APIRouter(prefix="/suggest-response", tags=["Suggest Response"])


def get_suggest_response_handler(request: Request) -> SuggestResponseHandler:
    """Get SuggestResponseHandler from app state."""
    return request.app.state.suggest_response_handler


def get_permission_service(request: Request) -> FacebookPermissionService:
    """Resolve FacebookPermissionService from app.state."""
    service = getattr(request.app.state, "facebook_permission_service", None)
    if service is None:
        raise HTTPException(
            status_code=500,
            detail="Permission service is not initialized",
        )
    return service


# ================================================================
# AGENT SETTINGS ENDPOINTS
# ================================================================


@router.get("/settings", response_model=AgentSettingsResponse)
async def get_agent_settings(
    current_user_id: str = Depends(get_current_user_id),
    handler: SuggestResponseHandler = Depends(get_suggest_response_handler),
) -> AgentSettingsResponse:
    """
    Get suggest response agent settings for the current user.
    Returns defaults if no settings exist (lazy creation pattern).
    """
    try:
        result = await handler.get_settings(current_user_id)
        return AgentSettingsResponse(**result)

    except Exception as e:
        logger.error(f"❌ SUGGEST RESPONSE ROUTER: Error getting settings: {str(e)}")
        raise HTTPException(status_code=500, detail="Internal server error")


@router.put("/settings", response_model=AgentSettingsResponse)
async def update_agent_settings(
    settings_data: AgentSettingsUpdate,
    current_user_id: str = Depends(get_current_user_id),
    handler: SuggestResponseHandler = Depends(get_suggest_response_handler),
) -> AgentSettingsResponse:
    """
    Update suggest response agent settings.
    Uses upsert pattern (lazy creation).
    """
    try:
        result = await handler.update_settings(
            user_id=current_user_id,
            settings=settings_data.settings,
            allow_auto_suggest=settings_data.allow_auto_suggest,
            num_suggest_response=settings_data.num_suggest_response,
        )
        return AgentSettingsResponse(**result)

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"❌ SUGGEST RESPONSE ROUTER: Error updating settings: {str(e)}")
        raise HTTPException(status_code=500, detail="Internal server error")


# ================================================================
# PAGE ADMIN CONFIG ENDPOINTS
# ================================================================


@router.get(
    "/page-config/{page_id}",
    response_model=Optional[PageAdminSuggestConfigResponse],
)
async def get_page_admin_config(
    page_id: str,
    current_user_id: str = Depends(get_current_user_id),
    handler: SuggestResponseHandler = Depends(get_suggest_response_handler),
) -> Optional[PageAdminSuggestConfigResponse]:
    """
    Get suggest response config for a page (webhook automation settings).
    Path param is Facebook page_id; backend resolves to page_admin_id for current user.
    Get-or-create: if no config exists, creates default and returns.
    """
    try:
        result = await handler.get_page_admin_config_by_page_id(
            page_id=page_id,
            user_id=current_user_id,
        )
        if not result:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Page not found or you do not have permission. Please ensure you are an admin of the page.",
            )
        return PageAdminSuggestConfigResponse(**result)
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"❌ SUGGEST RESPONSE ROUTER: Error getting page config: {e}")
        raise HTTPException(status_code=500, detail="Internal server error")


@router.put(
    "/page-config/{page_id}",
    response_model=PageAdminSuggestConfigResponse,
)
async def update_page_admin_config(
    page_id: str,
    config_data: PageAdminSuggestConfigUpdate,
    current_user_id: str = Depends(get_current_user_id),
    handler: SuggestResponseHandler = Depends(get_suggest_response_handler),
) -> PageAdminSuggestConfigResponse:
    """Update suggest response config for a page (webhook automation settings). Path param is Facebook page_id."""
    try:
        result = await handler.update_page_admin_config_by_page_id(
            page_id=page_id,
            user_id=current_user_id,
            settings=config_data.settings,
            auto_webhook_suggest=config_data.auto_webhook_suggest,
            auto_webhook_graph_api=config_data.auto_webhook_graph_api,
            webhook_delay_seconds=config_data.webhook_delay_seconds,
        )
        return PageAdminSuggestConfigResponse(**result)
    except ValueError as e:
        raise HTTPException(status_code=403, detail=str(e))
    except Exception as e:
        logger.error(f"❌ SUGGEST RESPONSE ROUTER: Error updating page config: {e}")
        raise HTTPException(status_code=500, detail="Internal server error")


# ================================================================
# PAGE MEMORY ENDPOINTS (READ-ONLY)
# ================================================================


@router.get(
    "/pages/{fan_page_id}/memory/{prompt_type}",
    response_model=Optional[PageMemoryResponse],
)
async def get_page_memory(
    fan_page_id: str,
    prompt_type: str,
    current_user_id: str = Depends(get_current_user_id),
    handler: SuggestResponseHandler = Depends(get_suggest_response_handler),
    permission_service: FacebookPermissionService = Depends(get_permission_service),
) -> Optional[PageMemoryResponse]:
    """
    Get active page memory with rendered content (same format as agent sees).
    READ-ONLY: Memory is managed by agent through tools, not through API.

    Args:
        fan_page_id: Facebook page ID
        prompt_type: 'messages' or 'comments'
    """
    try:
        # Check permission: user must be admin of the page
        has_permission = await permission_service.check_user_page_admin_permission(
            current_user_id, fan_page_id
        )
        if not has_permission:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="You do not have permission to access this page",
            )

        result = await handler.get_page_memory(
            fan_page_id=fan_page_id,
            prompt_type=prompt_type,
            owner_user_id=current_user_id,
        )

        if result:
            return PageMemoryResponse(**result)
        return None

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"❌ SUGGEST RESPONSE ROUTER: Error getting page memory: {str(e)}")
        raise HTTPException(status_code=500, detail="Internal server error")


# ================================================================
# USER MEMORY ENDPOINTS (READ-ONLY)
# ================================================================


@router.get(
    "/pages/{fan_page_id}/users/{psid}/memory",
    response_model=Optional[UserMemoryResponse],
)
async def get_user_memory(
    fan_page_id: str,
    psid: str,
    current_user_id: str = Depends(get_current_user_id),
    handler: SuggestResponseHandler = Depends(get_suggest_response_handler),
    permission_service: FacebookPermissionService = Depends(get_permission_service),
) -> Optional[UserMemoryResponse]:
    """
    Get active user memory with rendered content (same format as agent sees).
    READ-ONLY: Memory is managed by agent through tools, not through API.
    Only applicable for messages (not comments).

    Args:
        fan_page_id: Facebook page ID
        psid: Page-scoped user ID (PSID)
    """
    try:
        # Check permission: user must be admin of the page
        has_permission = await permission_service.check_user_page_admin_permission(
            current_user_id, fan_page_id
        )
        if not has_permission:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="You do not have permission to access this page",
            )

        result = await handler.get_user_memory(
            fan_page_id=fan_page_id,
            facebook_page_scope_user_id=psid,
            owner_user_id=current_user_id,
        )

        if result:
            return UserMemoryResponse(**result)
        return None

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"❌ SUGGEST RESPONSE ROUTER: Error getting user memory: {str(e)}")
        raise HTTPException(status_code=500, detail="Internal server error")


# ================================================================
# ASSIGNED PLAYBOOKS ENDPOINT (READ-ONLY)
# ================================================================


@router.get(
    "/pages/{fan_page_id}/playbooks",
    response_model=AssignedPlaybooksResponse,
)
async def get_assigned_playbooks(
    fan_page_id: str,
    conversation_type: str,
    current_user_id: str = Depends(get_current_user_id),
    handler: SuggestResponseHandler = Depends(get_suggest_response_handler),
    permission_service: FacebookPermissionService = Depends(get_permission_service),
) -> AssignedPlaybooksResponse:
    """
    Get playbooks assigned to the page for the given conversation type.

    READ-ONLY: Assignments are managed by the agent through tools, not via this API.

    Args:
        fan_page_id: Facebook page ID
        conversation_type: 'messages' or 'comments'
    """
    try:
        if conversation_type not in ("messages", "comments"):
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="conversation_type must be 'messages' or 'comments'",
            )
        has_permission = await permission_service.check_user_page_admin_permission(
            current_user_id, fan_page_id
        )
        if not has_permission:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="You do not have permission to access this page",
            )
        result = await handler.get_assigned_playbooks(
            fan_page_id=fan_page_id,
            conversation_type=conversation_type,
            owner_user_id=current_user_id,
        )
        return AssignedPlaybooksResponse(**result)
    except HTTPException:
        raise
    except Exception as e:
        logger.error(
            f"❌ SUGGEST RESPONSE ROUTER: Error getting assigned playbooks: {str(e)}"
        )
        raise HTTPException(status_code=500, detail="Internal server error")


# ================================================================
# GENERATE SUGGESTIONS ENDPOINT
# ================================================================


@router.post("/generate", response_model=GenerateSuggestionsResponse)
async def generate_suggestions(
    request: GenerateSuggestionsRequest,
    current_user_id: str = Depends(get_current_user_id),
    handler: SuggestResponseHandler = Depends(get_suggest_response_handler),
    permission_service: FacebookPermissionService = Depends(get_permission_service),
) -> GenerateSuggestionsResponse:
    """
    Generate response suggestions for a conversation.

    Args:
        request: Request body with conversation_type, conversation_id, and trigger_type
        current_user_id: Current authenticated user ID
        handler: Suggest response handler
        permission_service: Permission service for validation

    Returns:
        GenerateSuggestionsResponse with suggestions array
    """
    try:
        # Validate conversation_type
        if request.conversation_type not in ["messages", "comments"]:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="conversation_type must be 'messages' or 'comments'",
            )

        # 1st condition for auto trigger: user must have allow_auto_suggest enabled
        if request.trigger_type == "auto":
            from src.database.postgres.connection import async_db_transaction
            from src.database.postgres.repositories.suggest_response_queries import (
                get_agent_settings,
            )

            async with async_db_transaction() as conn:
                agent_settings = await get_agent_settings(conn, current_user_id)
                allow_auto = (
                    agent_settings.get("allow_auto_suggest", False)
                    if agent_settings
                    else False
                )
            if not allow_auto:
                raise HTTPException(
                    status_code=status.HTTP_403_FORBIDDEN,
                    detail="Auto suggest is disabled. Enable it in agent settings to use automatic suggestions.",
                )

        # Get conversation details and check permissions (single transaction)
        import re

        from src.database.postgres.connection import async_db_transaction
        from src.database.postgres.repositories.facebook_queries.comments.comment_conversations import (
            get_conversation_by_id as get_comment_conversation_by_id,
        )
        from src.database.postgres.repositories.facebook_queries.comments.comment_conversations import (
            get_conversation_by_root_comment_id,
        )
        from src.database.postgres.repositories.facebook_queries.messages.conversations import (
            get_conversation_metadata_with_media,
        )

        async with async_db_transaction() as conn:
            if request.conversation_type == "messages":
                conv_data = await get_conversation_metadata_with_media(
                    conn, request.conversation_id
                )
                if not conv_data:
                    raise HTTPException(
                        status_code=status.HTTP_404_NOT_FOUND,
                        detail=f"Conversation {request.conversation_id} not found",
                    )
                fan_page_id = conv_data.get("fan_page_id")
                facebook_page_scope_user_id = conv_data.get(
                    "facebook_page_scope_user_id"
                )
                conversation_uuid = request.conversation_id  # Messages already use UUID
            else:  # comments
                # Support both UUID and Facebook comment ID format
                # UUID format: "550e8400-e29b-41d4-a716-446655440000"
                # Facebook comment ID: "785876317536759_833659686155206"
                is_uuid = bool(
                    re.match(
                        r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$",
                        request.conversation_id,
                        re.I,
                    )
                )

                if is_uuid:
                    # Use UUID to find conversation by id
                    conv_data = await get_comment_conversation_by_id(
                        conn, request.conversation_id
                    )
                else:
                    # Use Facebook comment ID to find conversation by root_comment_id
                    conv_data = await get_conversation_by_root_comment_id(
                        conn, request.conversation_id
                    )

                if not conv_data:
                    raise HTTPException(
                        status_code=status.HTTP_404_NOT_FOUND,
                        detail=f"Comment conversation {request.conversation_id} not found",
                    )
                fan_page_id = conv_data.get("fan_page_id")
                facebook_page_scope_user_id = None  # Comments don't have PSID
                # Use UUID of conversation record (not original conversation_id)
                conversation_uuid = conv_data.get("id")

        # Check permission: user must be admin of the page
        has_permission = await permission_service.check_user_page_admin_permission(
            current_user_id, fan_page_id
        )
        if not has_permission:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="You do not have permission to access this page",
            )

        # Generate suggestions - pass UUID of conversation record to handler
        result = await handler.generate_suggestions(
            user_id=current_user_id,
            conversation_type=request.conversation_type,
            conversation_id=(
                conversation_uuid
                if request.conversation_type == "comments"
                else request.conversation_id
            ),
            fan_page_id=fan_page_id,
            facebook_page_scope_user_id=facebook_page_scope_user_id,
            trigger_type=request.trigger_type,
            hint=request.hint,
        )

        return GenerateSuggestionsResponse(**result)

    except HTTPException:
        raise
    except Exception as e:
        logger.error(
            f"❌ SUGGEST RESPONSE ROUTER: Error generating suggestions: {str(e)}"
        )
        raise HTTPException(status_code=500, detail="Internal server error")


# ================================================================
# SUGGEST RESPONSE HISTORY ENDPOINTS
# ================================================================


@router.put(
    "/history/{history_id}",
    response_model=SuggestResponseHistoryResponse,
)
async def update_suggest_response_history(
    history_id: str,
    request: UpdateSuggestResponseHistoryRequest,
    current_user_id: str = Depends(get_current_user_id),
    handler: SuggestResponseHandler = Depends(get_suggest_response_handler),
    permission_service: FacebookPermissionService = Depends(get_permission_service),
) -> SuggestResponseHistoryResponse:
    """
    Update suggest response history record with selected_suggestion_index and/or reaction.

    Args:
        history_id: History record UUID
        request: Update request with selected_suggestion_index and/or reaction
    """
    try:
        # First get the history record to check permissions
        history = await handler.get_history_by_id(history_id)
        if not history or "history" not in history:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"History record {history_id} not found",
            )

        history_record = history["history"]
        record_user_id = history_record.get("user_id")
        record_fan_page_id = history_record.get("fan_page_id")

        # Check permission: user must be the owner of the record
        if record_user_id != current_user_id:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="You do not have permission to update this history record",
            )

        # Verify user has access to the page
        from src.database.postgres.connection import async_db_transaction
        from src.database.postgres.repositories.facebook_queries.pages import (
            get_facebook_page_admins_by_user_id,
        )

        async with async_db_transaction() as conn:
            page_admins = await get_facebook_page_admins_by_user_id(
                conn, current_user_id
            )
            has_access = any(
                admin.get("page_id") == record_fan_page_id for admin in page_admins
            )

            if not has_access:
                raise HTTPException(
                    status_code=status.HTTP_403_FORBIDDEN,
                    detail="You do not have permission to update this history record",
                )

        # Check permission: user must be admin of the page
        has_permission = await permission_service.check_user_page_admin_permission(
            current_user_id, record_fan_page_id
        )
        if not has_permission:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="You do not have permission to access this page",
            )

        # Update history
        result = await handler.update_history(
            history_id=history_id,
            selected_suggestion_index=request.selected_suggestion_index,
            reaction=request.reaction,
        )

        return SuggestResponseHistoryResponse(**result)

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"❌ SUGGEST RESPONSE ROUTER: Error updating history: {str(e)}")
        raise HTTPException(status_code=500, detail="Internal server error")


@router.get(
    "/history/{history_id}/messages",
    response_model=SuggestResponseMessageListResponse,
)
async def get_suggest_response_history_messages(
    history_id: str,
    current_user_id: str = Depends(get_current_user_id),
    handler: SuggestResponseHandler = Depends(get_suggest_response_handler),
) -> SuggestResponseMessageListResponse:
    """
    Get message items (agent execution steps) for a suggest response history record.

    Returns reasoning, tool calls, tool outputs for the agent run.
    User must own the history record.
    """
    try:
        result = await handler.get_history_messages(
            history_id=history_id, user_id=current_user_id
        )
        if result is None:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"History record {history_id} not found or access denied",
            )
        return SuggestResponseMessageListResponse(**result)
    except HTTPException:
        raise
    except Exception as e:
        logger.error(
            f"❌ SUGGEST RESPONSE ROUTER: Error getting history messages: {str(e)}"
        )
        raise HTTPException(status_code=500, detail="Internal server error")


@router.get(
    "/history",
    response_model=SuggestResponseHistoryListResponse,
)
async def get_suggest_response_history_list(
    fan_page_id: Optional[str] = None,
    conversation_type: Optional[str] = None,
    facebook_conversation_messages_id: Optional[str] = None,
    facebook_conversation_comments_id: Optional[str] = None,
    page_prompt_id: Optional[str] = None,
    page_scope_user_prompt_id: Optional[str] = None,
    suggestion_count: Optional[int] = None,
    trigger_type: Optional[str] = None,
    reaction: Optional[str] = None,
    limit: int = 20,
    offset: int = 0,
    current_user_id: str = Depends(get_current_user_id),
    handler: SuggestResponseHandler = Depends(get_suggest_response_handler),
    permission_service: FacebookPermissionService = Depends(get_permission_service),
) -> SuggestResponseHistoryListResponse:
    """
    Get suggest response history records with comprehensive filters.

    Always filters by current user. All other filters are optional.

    Args:
        fan_page_id: Optional filter by Facebook page ID (requires permission check)
        conversation_type: Optional filter by 'messages' or 'comments'
        facebook_conversation_messages_id: Optional filter by messages conversation ID
        facebook_conversation_comments_id: Optional filter by comments conversation ID (UUID)
        page_prompt_id: Optional filter by page prompt ID (UUID)
        page_scope_user_prompt_id: Optional filter by page scope user prompt ID (UUID)
        suggestion_count: Optional filter by exact suggestion count
        trigger_type: Optional filter by 'user', 'auto', 'webhook_suggest', or 'webhook_auto_reply'
        reaction: Optional filter by 'like' or 'dislike'
        limit: Number of records to return (1-100, default: 20)
        offset: Number of records to skip (default: 0)
    """
    try:
        # Check permission if fan_page_id is provided
        if fan_page_id:
            has_permission = await permission_service.check_user_page_admin_permission(
                current_user_id, fan_page_id
            )
            if not has_permission:
                raise HTTPException(
                    status_code=status.HTTP_403_FORBIDDEN,
                    detail="You do not have permission to access this page",
                )

        # Get history with filters
        result = await handler.get_history_with_filters(
            user_id=current_user_id,
            fan_page_id=fan_page_id,
            conversation_type=conversation_type,
            facebook_conversation_messages_id=facebook_conversation_messages_id,
            facebook_conversation_comments_id=facebook_conversation_comments_id,
            page_prompt_id=page_prompt_id,
            page_scope_user_prompt_id=page_scope_user_prompt_id,
            suggestion_count=suggestion_count,
            trigger_type=trigger_type,
            reaction=reaction,
            limit=limit,
            offset=offset,
        )

        return SuggestResponseHistoryListResponse(**result)

    except HTTPException:
        raise
    except Exception as e:
        logger.error(
            f"❌ SUGGEST RESPONSE ROUTER: Error getting history with filters: {str(e)}"
        )
        raise HTTPException(status_code=500, detail="Internal server error")
