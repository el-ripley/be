from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, status, Request, Response

from src.middleware.auth_middleware import get_current_user_id
from src.utils.logger import get_logger

from .handler import CommentsHandler
from .schemas import (
    CommentInteractionRequest,
    CommentInteractionResponse,
    CommentThreadListResponse,
    CommentThreadResponse,
    UpdateCommentMarkAsReadRequest,
    UpdateCommentMarkAsReadResponse,
    SendMessageToCommenterRequest,
    SendMessageToCommenterResponse,
)
from src.api.facebook.schemas import AgentBlockResponse, AgentBlockUpsertRequest

logger = get_logger()

# Create comments router
comments_router = APIRouter(prefix="/comments", tags=["Facebook", "Comments"])


def get_comments_handler(request: Request) -> CommentsHandler:
    """Get CommentsHandler from app state."""
    agent_block_service = getattr(request.app.state, "agent_block_service", None)
    return CommentsHandler(
        comment_api_handler=request.app.state.comment_api_handler,
        agent_block_service=agent_block_service,
    )


@comments_router.get("/root-comments", response_model=CommentThreadListResponse)
async def get_root_comments_with_latest_replies(
    limit: int = 20,
    cursor: Optional[str] = None,
    user_id: str = Depends(get_current_user_id),
    comments_handler: CommentsHandler = Depends(get_comments_handler),
):
    try:
        if limit < 1 or limit > 50:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Giới hạn mỗi lần tải phải từ 1 đến 50",
            )

        result = await comments_handler.get_root_comments_with_latest_replies(
            user_id=user_id, limit=limit, cursor=cursor
        )

        return CommentThreadListResponse(**result)

    except Exception as e:
        logger.error(f"❌ Unexpected error in get_root_comments endpoint: {str(e)}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Lỗi hệ thống khi lấy danh sách root comments",
        )


@comments_router.get("/thread/{root_comment_id}", response_model=CommentThreadResponse)
async def get_comments_by_root_comment_id(
    root_comment_id: str,
    cursor: Optional[str] = None,
    limit: int = 50,
    user_id: str = Depends(get_current_user_id),
    comments_handler: CommentsHandler = Depends(get_comments_handler),
):
    try:
        if not root_comment_id.strip():
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="root_comment_id không được để trống",
            )

        if limit < 1 or limit > 100:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Giới hạn mỗi lần tải phải từ 1 đến 100",
            )

        thread_data = await comments_handler.get_comments_by_root_comment_id(
            user_id=user_id,
            root_comment_id=root_comment_id,
            limit=limit,
            cursor=cursor,
        )

        return CommentThreadResponse(**thread_data)

    except PermissionError as e:
        logger.warning(f"❌ Permission denied for comment thread: {str(e)}")
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail=str(e),
        )
    except ValueError as e:
        logger.warning(f"❌ Validation error for comment thread: {str(e)}")
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=str(e),
        )
    except Exception as e:
        logger.error(f"❌ Unexpected error in get_comment_thread endpoint: {str(e)}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Lỗi hệ thống khi lấy comment thread",
        )


@comments_router.put(
    "/mark-as-read/{root_comment_id}", response_model=UpdateCommentMarkAsReadResponse
)
async def update_comment_mark_as_read(
    root_comment_id: str,
    request: UpdateCommentMarkAsReadRequest,
    user_id: str = Depends(get_current_user_id),
    comments_handler: CommentsHandler = Depends(get_comments_handler),
):
    """
    Toggle the mark_as_read status of a comment conversation (UX feature).

    This endpoint allows users to toggle the mark_as_read flag for conversation management.
    This only updates the mark_as_read boolean, not the page_seen_at timestamps.
    Use the mark-all-seen endpoint to mark individual comments as seen.

    Args:
        root_comment_id: Root comment ID to update
        request: Request body containing mark_as_read status
        user_id: Current authenticated user ID from middleware
        comments_handler: Comments handler from dependency injection

    Returns:
        UpdateCommentMarkAsReadResponse with operation result

    Raises:
        HTTPException: If the operation fails or user lacks permission
    """
    try:
        logger.info(
            f"🔄 Updating mark_as_read status | User: {user_id} | Comment: {root_comment_id} | Status: {request.mark_as_read}"
        )

        # Validate root_comment_id
        if not root_comment_id.strip():
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="root_comment_id không được để trống",
            )

        # Update mark_as_read status
        result = await comments_handler.update_comment_mark_as_read(
            user_id=user_id,
            root_comment_id=root_comment_id,
            request=request,
        )

        # Check if it's an authorization error
        if not result.success and (
            "quyền" in result.message.lower() or "permission" in result.message.lower()
        ):
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail=result.message,
            )

        # Check if it's a not found error
        if not result.success and (
            "không tìm thấy" in result.message.lower()
            or "not found" in result.message.lower()
        ):
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=result.message,
            )

        # Return the result (success or failure)
        status_text = "đã đọc" if request.mark_as_read else "chưa đọc"
        logger.info(
            f"✅ Successfully updated mark_as_read | User: {user_id} | Comment: {root_comment_id} | Status: {status_text}"
        )

        return result

    except HTTPException:
        raise
    except Exception as e:
        logger.error(
            f"❌ Unexpected error in update_comment_mark_as_read endpoint: {str(e)}"
        )
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Lỗi hệ thống khi cập nhật trạng thái đã đọc",
        )


@comments_router.post(
    "/mark-all-seen/{root_comment_id}", response_model=UpdateCommentMarkAsReadResponse
)
async def mark_all_comments_as_seen(
    root_comment_id: str,
    user_id: str = Depends(get_current_user_id),
    comments_handler: CommentsHandler = Depends(get_comments_handler),
):
    """
    Mark all user comments in a conversation as seen.

    This endpoint sets page_seen_at timestamp for all user comments that haven't been seen yet.
    The unread_count will become 0 after this operation.

    Args:
        root_comment_id: Root comment ID of the conversation
        user_id: Current authenticated user ID from middleware
        comments_handler: Comments handler from dependency injection

    Returns:
        UpdateCommentMarkAsReadResponse with operation result

    Raises:
        HTTPException: If the operation fails or user lacks permission
    """
    try:
        logger.info(
            f"🔄 Marking all comments as seen | User: {user_id} | Root Comment: {root_comment_id}"
        )

        # Validate root_comment_id
        if not root_comment_id.strip():
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="root_comment_id không được để trống",
            )

        # Mark all comments as seen
        result = await comments_handler.mark_all_comments_as_seen(
            user_id=user_id,
            root_comment_id=root_comment_id,
        )

        # Check if it's an authorization error
        if not result.success and (
            "quyền" in result.message.lower() or "permission" in result.message.lower()
        ):
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail=result.message,
            )

        # Check if it's a not found error
        if not result.success and (
            "không tìm thấy" in result.message.lower()
            or "not found" in result.message.lower()
        ):
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=result.message,
            )

        logger.info(
            f"✅ Successfully marked all comments as seen | User: {user_id} | Root Comment: {root_comment_id}"
        )

        return result

    except HTTPException:
        raise
    except Exception as e:
        logger.error(
            f"❌ Unexpected error in mark_all_comments_as_seen endpoint: {str(e)}"
        )
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Lỗi hệ thống khi đánh dấu bình luận đã xem",
        )


@comments_router.get(
    "/thread/{root_comment_id}/agent-block",
    response_model=AgentBlockResponse,
)
async def get_comment_thread_agent_block(
    root_comment_id: str,
    user_id: str = Depends(get_current_user_id),
    comments_handler: CommentsHandler = Depends(get_comments_handler),
):
    """Get agent block status for a comment thread."""
    try:
        return await comments_handler.get_agent_block(user_id, root_comment_id)
    except PermissionError as e:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail=str(e),
        )
    except ValueError as e:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=str(e),
        )


@comments_router.put(
    "/thread/{root_comment_id}/agent-block",
    response_model=AgentBlockResponse,
)
async def upsert_comment_thread_agent_block(
    root_comment_id: str,
    request: AgentBlockUpsertRequest,
    user_id: str = Depends(get_current_user_id),
    comments_handler: CommentsHandler = Depends(get_comments_handler),
):
    """Create or update agent block for a comment thread."""
    try:
        return await comments_handler.upsert_agent_block(
            user_id,
            root_comment_id,
            is_active=request.is_active,
            reason=request.reason,
        )
    except PermissionError as e:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail=str(e),
        )
    except ValueError as e:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=str(e),
        )


@comments_router.post("/interact", response_model=CommentInteractionResponse)
async def interact_with_comment(
    request: CommentInteractionRequest,
    response: Response,
    user_id: str = Depends(get_current_user_id),
    comments_handler: CommentsHandler = Depends(get_comments_handler),
):
    """
    Interact with a comment via Facebook Graph API.

    Supported actions:
    - reply: Reply to a comment (requires message)
    - hide: Hide a comment
    - unhide: Unhide a comment
    - delete: Delete a comment

    The action will be sent to Facebook Graph API and the webhook will handle
    the database updates when Facebook confirms the action.

    Args:
        request: Request body containing comment_id, action, and optional parameters
        user_id: Current authenticated user ID from middleware
        comments_handler: Comments handler from dependency injection

    Returns:
        CommentInteractionResponse with operation result

    Raises:
        HTTPException: If the operation fails
    """
    try:
        # Validate action
        valid_actions = ["reply", "hide", "unhide", "delete"]
        if request.action not in valid_actions:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"Action không hợp lệ. Các action hỗ trợ: {', '.join(valid_actions)}",
            )

        # Validate message for reply
        if request.action == "reply" and not request.message:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Message bắt buộc phải có cho action reply",
            )

        # Validate comment_id
        if not request.comment_id.strip():
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="comment_id không được để trống",
            )

        # Interact with comment
        result = await comments_handler.interact_with_comment(
            request=request,
            user_id=user_id,
        )

        # Check if it's an authorization error
        if not result.success and "quyền" in result.message.lower():
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail=result.message,
            )

        return result

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"❌ Unexpected error in interact_with_comment endpoint: {str(e)}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Lỗi hệ thống khi thực hiện action với comment",
        )


@comments_router.post("/send-message", response_model=SendMessageToCommenterResponse)
async def send_message_to_commenter(
    request: SendMessageToCommenterRequest,
    user_id: str = Depends(get_current_user_id),
    comments_handler: CommentsHandler = Depends(get_comments_handler),
):
    """
    Send a message to the commenter via Facebook Messenger.

    This endpoint allows users to send private messages to users who commented
    on their Facebook pages. The message will be sent via Facebook Messenger.

    Args:
        request: Request body containing comment_id and message content
        user_id: Current authenticated user ID from middleware
        comments_handler: Comments handler from dependency injection

    Returns:
        SendMessageToCommenterResponse with operation result

    Raises:
        HTTPException: If the operation fails or user lacks permission
    """
    try:
        # Validate comment_id
        if not request.comment_id.strip():
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="comment_id không được để trống",
            )

        # Validate message
        if not request.message.strip():
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Message không được để trống",
            )

        logger.info(
            f"🔄 Sending message to commenter | User: {user_id} | Comment: {request.comment_id}"
        )

        # Send message to commenter
        result = await comments_handler.send_message_to_commenter(
            request=request,
            user_id=user_id,
        )

        # Check if it's an authorization error
        if not result.success and "quyền" in result.message.lower():
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail=result.message,
            )

        # Check if it's a not found error
        if not result.success and (
            "không tồn tại" in result.message.lower()
            or "không tìm thấy" in result.message.lower()
        ):
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=result.message,
            )

        # Return the result (success or failure)
        if result.success:
            logger.info(
                f"✅ Successfully sent message to commenter | User: {user_id} | Comment: {request.comment_id}"
            )
        else:
            logger.warning(
                f"⚠️ Failed to send message to commenter | User: {user_id} | Comment: {request.comment_id} | Reason: {result.message}"
            )

        return result

    except HTTPException:
        raise
    except Exception as e:
        logger.error(
            f"❌ Unexpected error in send_message_to_commenter endpoint: {str(e)}"
        )
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Lỗi hệ thống khi gửi tin nhắn cho người bình luận",
        )
