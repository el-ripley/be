from typing import Any, Dict, Optional
from src.services.facebook.comments.api_handler import CommentAPIHandler
from src.utils.logger import get_logger
from .schemas import (
    CommentInteractionRequest,
    CommentInteractionResponse,
    UpdateCommentMarkAsReadRequest,
    UpdateCommentMarkAsReadResponse,
    SendMessageToCommenterRequest,
    SendMessageToCommenterResponse,
)

logger = get_logger()


class CommentsHandler:
    """Handler for Facebook comments-related API operations."""

    def __init__(
        self,
        comment_api_handler: CommentAPIHandler,
        agent_block_service: Optional[Any] = None,
    ):
        self.comment_api_handler = comment_api_handler
        self.agent_block_service = agent_block_service

    async def interact_with_comment(
        self,
        request: CommentInteractionRequest,
        user_id: str,
    ) -> CommentInteractionResponse:
        """
        Interact with a comment via Facebook Graph API.
        Actions: reply, hide, unhide, delete

        Args:
            request: Request containing comment_id, action, and optional parameters
            user_id: Current authenticated user ID

        Returns:
            CommentInteractionResponse with operation result
        """
        try:
            logger.info(
                f"🔄 Comment interaction | User: {user_id} | Action: {request.action} | Comment: {request.comment_id}"
            )

            # Validate action
            valid_actions = ["reply", "hide", "unhide", "delete"]
            if request.action not in valid_actions:
                return CommentInteractionResponse(
                    success=False,
                    comment_id=request.comment_id,
                    action=request.action,
                    message=f"Action không hợp lệ. Các action hỗ trợ: {', '.join(valid_actions)}",
                    api_response=None,
                )

            # Validate message for reply action
            if request.action == "reply" and not request.message:
                return CommentInteractionResponse(
                    success=False,
                    comment_id=request.comment_id,
                    action=request.action,
                    message="Message bắt buộc phải có cho action reply",
                    api_response=None,
                )

            # Use comment API handler to interact with comment
            # The handler will handle authorization, data retrieval, and interaction
            result = await self.comment_api_handler.interact_with_comment_by_user(
                user_id=user_id,
                comment_id=request.comment_id,
                action=request.action,
                message=request.message,
                attachment_url=request.attachment_url,
            )

            logger.info(
                f"✅ Comment interaction result | User: {user_id} | Action: {request.action} | "
                f"Comment: {request.comment_id} | Success: {result['success']}"
            )

            return CommentInteractionResponse(
                success=result["success"],
                comment_id=request.comment_id,
                action=request.action,
                message=result["message"],
                api_response=result["api_response"],
                new_comment_id=result.get("new_comment_id"),
            )

        except Exception as e:
            logger.error(
                f"❌ Error in comment interaction | User: {user_id} | Action: {request.action} | "
                f"Comment: {request.comment_id} | Error: {str(e)}"
            )
            return CommentInteractionResponse(
                success=False,
                comment_id=request.comment_id,
                action=request.action,
                message=f"Lỗi khi thực hiện {request.action}: {str(e)}",
                api_response=None,
                new_comment_id=None,
            )

    async def get_root_comments_with_latest_replies(
        self,
        user_id: str,
        limit: int = 20,
        cursor: Optional[str] = None,
    ) -> Dict[str, Any]:
        try:
            return await self.comment_api_handler.get_root_comments_with_latest_replies(
                user_id=user_id, limit=limit, cursor=cursor
            )

        except Exception as e:
            logger.error(
                f"❌ Error getting root comments | User: {user_id} | Error: {str(e)}"
            )
            raise

    async def get_comments_by_root_comment_id(
        self,
        user_id: str,
        root_comment_id: str,
        limit: int = 50,
        cursor: Optional[str] = None,
    ) -> Dict[str, Any]:
        try:
            logger.info(
                f"🔄 Getting comment thread | User: {user_id} | Root Comment: {root_comment_id}"
            )

            thread_data = (
                await self.comment_api_handler.get_comments_by_root_comment_id(
                    user_id=user_id,
                    root_comment_id=root_comment_id,
                    limit=limit,
                    cursor=cursor,
                )
            )

            return thread_data

        except (PermissionError, ValueError) as e:
            logger.warning(
                f"⚠️ Authorization/validation error getting comment thread | User: {user_id} | Root Comment: {root_comment_id} | Error: {str(e)}"
            )
            raise
        except Exception as e:
            logger.error(
                f"❌ Error getting comment thread | User: {user_id} | Root Comment: {root_comment_id} | Error: {str(e)}"
            )
            raise

    async def update_comment_mark_as_read(
        self,
        user_id: str,
        root_comment_id: str,
        request: UpdateCommentMarkAsReadRequest,
    ) -> UpdateCommentMarkAsReadResponse:
        """
        Toggle mark_as_read status for a conversation (UX feature).

        Args:
            user_id: Current authenticated user ID
            root_comment_id: Root comment ID to update
            request: Request containing mark_as_read status

        Returns:
            UpdateCommentMarkAsReadResponse with operation result
        """
        try:
            logger.info(
                f"🔄 Updating mark_as_read | User: {user_id} | Comment: {root_comment_id} | Status: {request.mark_as_read}"
            )

            # Use comment API handler to update mark_as_read status
            result = await self.comment_api_handler.update_comment_mark_as_read(
                user_id=user_id,
                root_comment_id=root_comment_id,
                mark_as_read=request.mark_as_read,
            )

            response = UpdateCommentMarkAsReadResponse(**result)

            logger.info(
                f"✅ Successfully updated mark_as_read | User: {user_id} | Comment: {root_comment_id} | Status: {request.mark_as_read}"
            )

            return response

        except (PermissionError, ValueError) as e:
            logger.warning(
                f"❌ Authorization/validation error updating mark_as_read | User: {user_id} | Comment: {root_comment_id} | Error: {str(e)}"
            )
            return UpdateCommentMarkAsReadResponse(
                success=False,
                comment_id=root_comment_id,
                mark_as_read=request.mark_as_read,
                message=str(e),
            )
        except Exception as e:
            logger.error(
                f"❌ Error updating mark_as_read | User: {user_id} | Comment: {root_comment_id} | Error: {str(e)}"
            )
            return UpdateCommentMarkAsReadResponse(
                success=False,
                comment_id=root_comment_id,
                mark_as_read=request.mark_as_read,
                message=f"Lỗi khi cập nhật trạng thái đã đọc: {str(e)}",
            )

    async def mark_all_comments_as_seen(
        self,
        user_id: str,
        root_comment_id: str,
    ) -> UpdateCommentMarkAsReadResponse:
        """
        Mark all user comments in a conversation as seen.

        Args:
            user_id: Current authenticated user ID
            root_comment_id: Root comment ID of the conversation

        Returns:
            UpdateCommentMarkAsReadResponse with operation result
        """
        try:
            logger.info(
                f"🔄 Marking all comments as seen | User: {user_id} | Root Comment: {root_comment_id}"
            )

            # Use comment API handler to mark all comments as seen
            result = await self.comment_api_handler.mark_all_comments_as_seen(
                user_id=user_id,
                root_comment_id=root_comment_id,
            )

            response = UpdateCommentMarkAsReadResponse(
                success=result["success"],
                comment_id=result["comment_id"],
                conversation_id=result.get("conversation_id"),
                mark_as_read=True,  # After marking all as seen, effectively "read"
                message=result["message"],
                unread_count=result.get("unread_count", 0),
            )

            logger.info(
                f"✅ Successfully marked all comments as seen | User: {user_id} | Root Comment: {root_comment_id}"
            )

            return response

        except (PermissionError, ValueError) as e:
            logger.warning(
                f"❌ Authorization/validation error marking comments as seen | User: {user_id} | Root Comment: {root_comment_id} | Error: {str(e)}"
            )
            return UpdateCommentMarkAsReadResponse(
                success=False,
                comment_id=root_comment_id,
                mark_as_read=False,
                message=str(e),
            )
        except Exception as e:
            logger.error(
                f"❌ Error marking comments as seen | User: {user_id} | Root Comment: {root_comment_id} | Error: {str(e)}"
            )
            return UpdateCommentMarkAsReadResponse(
                success=False,
                comment_id=root_comment_id,
                mark_as_read=False,
                message=f"Lỗi khi đánh dấu bình luận đã xem: {str(e)}",
            )

    async def send_message_to_commenter(
        self,
        request: SendMessageToCommenterRequest,
        user_id: str,
    ) -> SendMessageToCommenterResponse:
        """
        Send a message to the commenter via Facebook Messenger.

        Args:
            request: Request containing comment_id and message content
            user_id: Current authenticated user ID

        Returns:
            SendMessageToCommenterResponse with operation result
        """
        try:
            logger.info(
                f"🔄 Sending message to commenter | User: {user_id} | Comment: {request.comment_id}"
            )

            # Use comment API handler to send message to commenter
            result = await self.comment_api_handler.send_message_to_commenter(
                user_id=user_id,
                comment_id=request.comment_id,
                message=request.message,
            )

            logger.info(
                f"✅ Successfully sent message to commenter | User: {user_id} | Comment: {request.comment_id}"
            )

            return SendMessageToCommenterResponse(
                success=result["success"],
                comment_id=result["comment_id"],
                commenter_id=result["commenter_id"],
                message=result["message"],
                api_response=result["api_response"],
            )

        except (PermissionError, ValueError) as e:
            logger.warning(
                f"❌ Authorization/validation error sending message to commenter | User: {user_id} | Comment: {request.comment_id} | Error: {str(e)}"
            )
            return SendMessageToCommenterResponse(
                success=False,
                comment_id=request.comment_id,
                commenter_id=None,
                message=str(e),
                api_response=None,
            )
        except Exception as e:
            logger.error(
                f"❌ Error sending message to commenter | User: {user_id} | Comment: {request.comment_id} | Error: {str(e)}"
            )
            return SendMessageToCommenterResponse(
                success=False,
                comment_id=request.comment_id,
                commenter_id=None,
                message=f"Lỗi khi gửi tin nhắn: {str(e)}",
                api_response=None,
            )

    async def get_agent_block(
        self, user_id: str, root_comment_id: str
    ) -> Dict[str, Any]:
        """Get agent block status for a comments thread (by root_comment_id)."""
        if not self.agent_block_service:
            from fastapi import HTTPException

            raise HTTPException(
                status_code=500,
                detail="Agent block service not available",
            )
        info = await self.comment_api_handler.get_conversation_info_by_root_comment_id(
            user_id, root_comment_id
        )
        conversation_id = info["conversation_id"]
        fan_page_id = info["fan_page_id"]
        block = await self.agent_block_service.get_block_status(
            user_id, "comments", conversation_id, fan_page_id
        )
        return {
            "id": block["id"] if block else None,
            "is_blocked": block is not None and block.get("is_active", True),
            "blocked_by": block.get("blocked_by") if block else None,
            "reason": block.get("reason") if block else None,
            "created_at": block.get("created_at") if block else None,
        }

    async def upsert_agent_block(
        self,
        user_id: str,
        root_comment_id: str,
        is_active: bool,
        reason: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Upsert agent block for a comments thread (by root_comment_id)."""
        if not self.agent_block_service:
            from fastapi import HTTPException

            raise HTTPException(
                status_code=500,
                detail="Agent block service not available",
            )
        info = await self.comment_api_handler.get_conversation_info_by_root_comment_id(
            user_id, root_comment_id
        )
        conversation_id = info["conversation_id"]
        fan_page_id = info["fan_page_id"]
        result = await self.agent_block_service.upsert_block(
            user_id, "comments", conversation_id, fan_page_id, is_active, reason
        )
        return {
            "id": result.get("id"),
            "is_blocked": result.get("is_active", False),
            "blocked_by": result.get("blocked_by"),
            "reason": result.get("reason"),
            "created_at": result.get("created_at"),
        }
