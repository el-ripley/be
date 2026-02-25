from typing import Any, Dict, Optional
from fastapi import HTTPException
from src.services.facebook.messages.api_handler import MessageAPIHandler
from src.common.clients.facebook_graph_page_client import FacebookAPIError
from src.utils.logger import get_logger

logger = get_logger()


class MessagesHandler:
    """Handler for Facebook message-related API endpoints."""

    def __init__(
        self,
        message_api_handler: MessageAPIHandler,
        agent_block_service: Optional[Any] = None,
    ):
        self.message_api_handler = message_api_handler
        self.agent_block_service = agent_block_service

    async def mark_conversation_as_read(
        self,
        conversation_id: str,
        user_id: str,
        mark_as_read: bool = True,
    ) -> Dict[str, Any]:
        """
        Toggle mark_as_read status for a conversation (UX feature).

        Args:
            conversation_id: ID of the conversation to update
            user_id: ID of the current user (for authorization)
            mark_as_read: True to mark as read, False to mark as unread

        Returns:
            Updated conversation data with full details

        Raises:
            HTTPException: If conversation not found, unauthorized, or update fails
        """
        try:
            return await self.message_api_handler.mark_conversation_as_read(
                conversation_id=conversation_id,
                user_id=user_id,
                mark_as_read=mark_as_read,
            )

        except ValueError as e:
            # Conversation not found
            raise HTTPException(status_code=404, detail=str(e))
        except PermissionError as e:
            # User not authorized
            raise HTTPException(status_code=403, detail=str(e))
        except RuntimeError as e:
            # Server error
            raise HTTPException(status_code=500, detail=str(e))
        except Exception as e:
            logger.error(f"❌ Unexpected error in mark_conversation_as_read: {e}")
            raise HTTPException(
                status_code=500,
                detail="Internal server error while updating conversation",
            )

    async def mark_all_messages_as_seen(
        self,
        conversation_id: str,
        user_id: str,
    ) -> Dict[str, Any]:
        """
        Mark all user messages in a conversation as seen.

        Args:
            conversation_id: ID of the conversation to update
            user_id: ID of the current user (for authorization)

        Returns:
            Updated conversation data with full details

        Raises:
            HTTPException: If conversation not found, unauthorized, or update fails
        """
        try:
            return await self.message_api_handler.mark_all_messages_as_seen(
                conversation_id=conversation_id,
                user_id=user_id,
            )

        except ValueError as e:
            # Conversation not found
            raise HTTPException(status_code=404, detail=str(e))
        except PermissionError as e:
            # User not authorized
            raise HTTPException(status_code=403, detail=str(e))
        except RuntimeError as e:
            # Server error
            raise HTTPException(status_code=500, detail=str(e))
        except Exception as e:
            logger.error(f"❌ Unexpected error in mark_all_messages_as_seen: {e}")
            raise HTTPException(
                status_code=500,
                detail="Internal server error while marking messages as seen",
            )

    async def get_user_conversations(
        self,
        user_id: str,
        limit: int = 20,
        cursor: Optional[str] = None,
    ) -> Dict[str, Any]:
        """
        Get all conversations for a user across all pages they admin.

        Args:
            user_id: ID of the current user
            page: Page number for pagination (default: 1)
            page_size: Number of items per page (default: 20)

        Returns:
            Paginated conversation data

        Raises:
            HTTPException: If database error occurs
        """
        try:
            return await self.message_api_handler.get_user_conversations(
                user_id=user_id,
                limit=limit,
                cursor=cursor,
            )

        except RuntimeError as e:
            # Server error
            raise HTTPException(status_code=500, detail=str(e))
        except Exception as e:
            logger.error(f"❌ Unexpected error in get_user_conversations: {e}")
            raise HTTPException(
                status_code=500,
                detail="Internal server error while retrieving conversations",
            )

    async def send_message(
        self,
        user_id: str,
        conversation_id: str,
        message: str = None,
        image_urls: list = None,
        video_url: str = None,
        metadata: str = None,
        reply_to_message_id: Optional[str] = None,
    ) -> Dict[str, Any]:
        """
        Send a message to a user via Facebook Messenger.

        Args:
            user_id: ID of the current user (for authorization)
            conversation_id: ID of the conversation to send message to
            message: Text message to send
            image_urls: List of image URLs to send
            video_url: Video URL to send
            metadata: Optional metadata to include with the message
            reply_to_message_id: Optional Facebook message id (mid) to reply to

        Returns:
            Send message response

        Raises:
            HTTPException: If validation, authorization, or sending fails
        """
        try:
            return await self.message_api_handler.send_message(
                user_id=user_id,
                conversation_id=conversation_id,
                text=message,  # Pass message as text to service
                image_urls=image_urls,
                video_urls=(
                    [video_url] if video_url else None
                ),  # Convert single video_url to list for service
                metadata=metadata,
                reply_to_message_id=reply_to_message_id,
            )

        except FacebookAPIError as e:
            # Facebook API error with user-friendly message
            raise HTTPException(status_code=400, detail=e.user_message)
        except ValueError as e:
            # Validation error
            raise HTTPException(status_code=400, detail=str(e))
        except PermissionError as e:
            # User not authorized
            raise HTTPException(status_code=403, detail=str(e))
        except RuntimeError as e:
            # Server error
            raise HTTPException(status_code=500, detail=str(e))
        except Exception as e:
            logger.error(f"❌ Unexpected error in send_message: {e}")
            raise HTTPException(
                status_code=500,
                detail="Internal server error while sending message",
            )

    async def get_conversation_messages(
        self,
        user_id: str,
        conversation_id: str,
        limit: int = 20,
        cursor: Optional[str] = None,
    ) -> Dict[str, Any]:
        try:
            return await self.message_api_handler.get_conversation_messages(
                user_id=user_id,
                conversation_id=conversation_id,
                limit=limit,
                cursor=cursor,
            )

        except ValueError as e:
            # Conversation not found
            raise HTTPException(status_code=404, detail=str(e))
        except PermissionError as e:
            # User not authorized
            raise HTTPException(status_code=403, detail=str(e))
        except RuntimeError as e:
            # Server error
            raise HTTPException(status_code=500, detail=str(e))
        except Exception as e:
            logger.error(f"❌ Unexpected error in get_conversation_messages: {e}")
            raise HTTPException(
                status_code=500,
                detail="Internal server error while retrieving messages",
            )

    async def get_agent_block(
        self, user_id: str, conversation_id: str
    ) -> Dict[str, Any]:
        """Get agent block status for a messages conversation."""
        if not self.agent_block_service:
            raise HTTPException(
                status_code=500,
                detail="Agent block service not available",
            )
        fan_page_id = await self.message_api_handler.get_conversation_fan_page_id(
            user_id, conversation_id
        )
        block = await self.agent_block_service.get_block_status(
            user_id, "messages", conversation_id, fan_page_id
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
        conversation_id: str,
        is_active: bool,
        reason: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Upsert agent block for a messages conversation."""
        if not self.agent_block_service:
            raise HTTPException(
                status_code=500,
                detail="Agent block service not available",
            )
        fan_page_id = await self.message_api_handler.get_conversation_fan_page_id(
            user_id, conversation_id
        )
        result = await self.agent_block_service.upsert_block(
            user_id, "messages", conversation_id, fan_page_id, is_active, reason
        )
        return {
            "id": result.get("id"),
            "is_blocked": result.get("is_active", False),
            "blocked_by": result.get("blocked_by"),
            "reason": result.get("reason"),
            "created_at": result.get("created_at"),
        }
