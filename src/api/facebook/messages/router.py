from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Request, Response

from .handler import MessagesHandler
from .schemas import (
    MarkAsReadRequest,
    ConversationResponse,
    ConversationsListResponse,
    SendMessageRequest,
    SendMessageResponse,
    MessagesListResponse,
)
from src.api.facebook.schemas import AgentBlockResponse, AgentBlockUpsertRequest
from src.middleware.auth_middleware import get_current_user_id

router = APIRouter(prefix="/messages", tags=["Facebook Messages"])


def get_messages_handler(request: Request) -> MessagesHandler:
    """Dependency to get messages handler instance."""
    message_api_handler = request.app.state.message_api_handler
    agent_block_service = getattr(request.app.state, "agent_block_service", None)
    return MessagesHandler(message_api_handler, agent_block_service)


@router.get(
    "/conversations",
    response_model=ConversationsListResponse,
    summary="Get user conversations",
    description="Get all conversations for the current user across all pages they admin, cursor-based",
)
async def get_user_conversations(
    limit: int = 20,
    cursor: Optional[str] = None,
    handler: MessagesHandler = Depends(get_messages_handler),
    user_id: str = Depends(get_current_user_id),
):
    """
    Get all conversations for the current user using cursor-based pagination.

    - **limit**: Maximum number of items to return (default: 20, max: 100)
    - **cursor**: Cursor token from the previous response to fetch the next batch
    """
    return await handler.get_user_conversations(
        user_id=user_id,
        limit=limit,
        cursor=cursor,
    )


@router.get(
    "/conversations/{conversation_id}/messages",
    response_model=MessagesListResponse,
    summary="Get conversation messages",
    description="Get all messages for a specific conversation with pagination",
)
async def get_conversation_messages(
    conversation_id: str,
    limit: int = 20,
    cursor: Optional[str] = None,
    handler: MessagesHandler = Depends(get_messages_handler),
    user_id: str = Depends(get_current_user_id),
):
    """
    Get messages for a conversation using cursor-based pagination.

    - **limit**: Maximum number of items to return (default: 20, max: 100)
    - **cursor**: Cursor token from the previous response to fetch the next batch
    """
    return await handler.get_conversation_messages(
        user_id=user_id,
        conversation_id=conversation_id,
        limit=limit,
        cursor=cursor,
    )


@router.patch(
    "/conversations/{conversation_id}/mark-as-read",
    response_model=ConversationResponse,
    summary="Toggle mark_as_read status (UX feature)",
    description="Toggle the mark_as_read boolean status of a conversation (user management feature)",
)
async def mark_conversation_as_read(
    conversation_id: str,
    request: MarkAsReadRequest,
    response: Response,
    handler: MessagesHandler = Depends(get_messages_handler),
    user_id: str = Depends(get_current_user_id),
):
    """
    Toggle mark_as_read status for a conversation (UX feature).

    - **conversation_id**: The ID of the conversation to update
    - **mark_as_read**: True to mark as read, False to mark as unread

    This only updates the mark_as_read boolean flag for user convenience.
    It does NOT mark individual messages as seen (use mark-all-seen for that).
    Requires the user to be an admin of the page associated with the conversation.
    """
    return await handler.mark_conversation_as_read(
        conversation_id=conversation_id,
        user_id=user_id,
        mark_as_read=request.mark_as_read,
    )


@router.post(
    "/conversations/{conversation_id}/mark-all-seen",
    response_model=ConversationResponse,
    summary="Mark all messages as seen",
    description="Mark all user messages in a conversation as seen by setting page_seen_at",
)
async def mark_all_messages_as_seen(
    conversation_id: str,
    response: Response,
    handler: MessagesHandler = Depends(get_messages_handler),
    user_id: str = Depends(get_current_user_id),
):
    """
    Mark all user messages in a conversation as seen.

    - **conversation_id**: The ID of the conversation to update

    This sets page_seen_at timestamp for all user messages that haven't been seen yet.
    The unread_count will become 0 after this operation.
    Requires the user to be an admin of the page associated with the conversation.
    """
    return await handler.mark_all_messages_as_seen(
        conversation_id=conversation_id,
        user_id=user_id,
    )


@router.get(
    "/conversations/{conversation_id}/agent-block",
    response_model=AgentBlockResponse,
    summary="Get agent block status",
    description="Get whether suggest_response agent is blocked for this conversation",
)
async def get_conversation_agent_block(
    conversation_id: str,
    handler: MessagesHandler = Depends(get_messages_handler),
    user_id: str = Depends(get_current_user_id),
):
    """Get agent block status for a messages conversation."""
    try:
        return await handler.get_agent_block(user_id, conversation_id)
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))
    except PermissionError as e:
        raise HTTPException(status_code=403, detail=str(e))


@router.put(
    "/conversations/{conversation_id}/agent-block",
    response_model=AgentBlockResponse,
    summary="Upsert agent block",
    description="Block or unblock suggest_response agent for this conversation",
)
async def upsert_conversation_agent_block(
    conversation_id: str,
    request: AgentBlockUpsertRequest,
    handler: MessagesHandler = Depends(get_messages_handler),
    user_id: str = Depends(get_current_user_id),
):
    """Create or update agent block for a messages conversation."""
    try:
        return await handler.upsert_agent_block(
            user_id,
            conversation_id,
            is_active=request.is_active,
            reason=request.reason,
        )
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))
    except PermissionError as e:
        raise HTTPException(status_code=403, detail=str(e))


@router.post(
    "/send",
    response_model=SendMessageResponse,
    summary="Send message",
    description="Send a message to a user via Facebook Messenger",
)
async def send_message(
    request: SendMessageRequest,
    response: Response,
    handler: MessagesHandler = Depends(get_messages_handler),
    user_id: str = Depends(get_current_user_id),
):
    """
    Send a message to a user via Facebook Messenger.

    Message content:
    - **message**: Text message to send
    - **image_urls**: List of image URLs to send (must be publicly accessible)
    - **video_url**: Video URL to send (must be publicly accessible, max 25MB)
    - **metadata**: Optional metadata string to include with the message
    - Can send text, images, and videos together or separately

    Video requirements:
    - Maximum file size: 25MB per video
    - Supported formats: MP4, MOV, AVI, WMV, FLV, WebM
    - Upload timeout: 75 seconds

    Requires the user to be an admin of the page associated with the conversation.
    """
    return await handler.send_message(
        user_id=user_id,
        conversation_id=request.conversation_id,
        message=request.message,
        image_urls=request.image_urls,
        video_url=request.video_url,
        metadata=request.metadata,
        reply_to_message_id=request.reply_to_message_id,
    )
