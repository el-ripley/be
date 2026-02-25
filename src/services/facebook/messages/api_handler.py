import json
from typing import Dict, Any, List, Optional, Tuple

from src.database.postgres.connection import async_db_transaction
from src.database.postgres.repositories.facebook_queries.messages import (
    mark_conversation_messages_as_seen,
    update_conversation_mark_as_read,
    get_conversation_with_details,
    list_conversations_by_page_ids,
    list_messages_by_conversation_id,
)
from src.services.facebook.auth import FacebookPageService, FacebookPermissionService
from src.common.clients.facebook_graph_page_client import (
    FacebookGraphPageClient,
    FacebookAPIError,
)
from src.utils.logger import get_logger

logger = get_logger()


class MessageAPIHandler:
    """
    Handler for Facebook message API operations.
    Handles all user-facing API requests for message interactions.
    """

    def __init__(
        self,
        page_service: FacebookPageService,
        permission_service: FacebookPermissionService,
    ):
        self.page_service = page_service
        self.permission_service = permission_service

    async def mark_conversation_as_read(
        self,
        conversation_id: str,
        user_id: str,
        mark_as_read: bool = True,
    ) -> Dict[str, Any]:
        """
        Toggle mark_as_read status for a conversation (UX feature).
        This only updates the mark_as_read boolean, not the page_seen_at timestamps.

        Args:
            conversation_id: Conversation ID to update
            user_id: Internal user ID for authorization
            mark_as_read: True to mark as read, False to mark as unread

        Returns:
            Dictionary with operation result and updated conversation data

        Raises:
            ValueError: If conversation not found
            PermissionError: If user doesn't have permission
            RuntimeError: If update fails
        """
        try:
            async with async_db_transaction() as conn:
                # First check if conversation exists
                conversation_details = await get_conversation_with_details(
                    conn, conversation_id
                )
                if not conversation_details:
                    raise ValueError(
                        f"Conversation with ID {conversation_id} not found"
                    )

                # Check if user has permission to manage this page
                page_id = conversation_details["fan_page_id"]
                has_permission = (
                    await self.permission_service.check_user_page_admin_permission(
                        user_id, page_id
                    )
                )

                if not has_permission:
                    raise PermissionError(
                        f"User does not have permission to manage page {page_id}"
                    )

                # Update the mark_as_read boolean status
                updated_conversation = await update_conversation_mark_as_read(
                    conn, conversation_id, mark_as_read
                )

                if not updated_conversation:
                    raise RuntimeError("Failed to update conversation read status")

                status_text = "read" if mark_as_read else "unread"
                return {
                    "success": True,
                    "message": f"Conversation marked as {status_text}",
                    "conversation": updated_conversation,
                }

        except (ValueError, PermissionError, RuntimeError):
            raise
        except Exception as e:
            logger.error(f"❌ Failed to mark conversation as read: {e}")
            raise RuntimeError("Internal server error while updating conversation")

    async def mark_all_messages_as_seen(
        self,
        conversation_id: str,
        user_id: str,
    ) -> Dict[str, Any]:
        """
        Mark all user messages in a conversation as seen by setting page_seen_at.

        Args:
            conversation_id: Conversation ID to update
            user_id: Internal user ID for authorization

        Returns:
            Dictionary with operation result and updated conversation data

        Raises:
            ValueError: If conversation not found
            PermissionError: If user doesn't have permission
            RuntimeError: If update fails
        """
        try:
            async with async_db_transaction() as conn:
                # First check if conversation exists
                conversation_details = await get_conversation_with_details(
                    conn, conversation_id
                )
                if not conversation_details:
                    raise ValueError(
                        f"Conversation with ID {conversation_id} not found"
                    )

                # Check if user has permission to manage this page
                page_id = conversation_details["fan_page_id"]
                has_permission = (
                    await self.permission_service.check_user_page_admin_permission(
                        user_id, page_id
                    )
                )

                if not has_permission:
                    raise PermissionError(
                        f"User does not have permission to manage page {page_id}"
                    )

                # Mark all messages as seen
                updated_conversation = await mark_conversation_messages_as_seen(
                    conn, conversation_id
                )

                if not updated_conversation:
                    raise RuntimeError("Failed to mark messages as seen")

                return {
                    "success": True,
                    "message": "All messages marked as seen",
                    "conversation": updated_conversation,
                }

        except (ValueError, PermissionError, RuntimeError):
            raise
        except Exception as e:
            logger.error(f"❌ Failed to mark messages as seen: {e}")
            raise RuntimeError("Internal server error while marking messages as seen")

    async def get_user_conversations(
        self,
        user_id: str,
        limit: int = 20,
        cursor: Optional[str] = None,
    ) -> Dict[str, Any]:
        """
        Get all conversations for a user across all pages they admin.

        Args:
            user_id: Internal user ID
            page: Page number (1-indexed)
            page_size: Number of items per page

        Returns:
            Dictionary with paginated conversations list

        Raises:
            RuntimeError: If retrieval fails
        """
        try:
            limit = max(1, min(limit, 100))

            async with async_db_transaction() as conn:
                # Get all pages that the user is admin of
                page_admins = (
                    await self.page_service.get_facebook_page_admins_by_user_id(user_id)
                )

                if not page_admins:
                    # User is not admin of any pages, return empty result
                    return {
                        "items": [],
                        "has_more": False,
                        "next_cursor": None,
                    }

                # Extract page_ids from page_admins
                page_ids = [
                    admin.get("page_id")
                    for admin in page_admins
                    if admin.get("page_id")
                ]

                if not page_ids:
                    # No valid page_ids found
                    return {
                        "items": [],
                        "has_more": False,
                        "next_cursor": None,
                    }

                cursor_tuple = self._decode_conversation_cursor(cursor)
                rows, has_more, next_cursor_tuple = (
                    await list_conversations_by_page_ids(
                        conn, page_ids, limit, cursor_tuple
                    )
                )

                # Transform flat results into nested conversation + latest_message structure
                transformed_items = []
                for item in rows:
                    participants = item.get("participants_snapshot") or []
                    if isinstance(participants, str):
                        try:
                            participants = json.loads(participants)
                        except (json.JSONDecodeError, TypeError):
                            participants = []

                    conversation_data = {
                        "conversation_id": item["conversation_id"],
                        "fan_page_id": item["fan_page_id"],
                        "facebook_page_scope_user_id": item[
                            "facebook_page_scope_user_id"
                        ],
                        "mark_as_read": item["mark_as_read"],
                        "conversation_created_at": item["conversation_created_at"],
                        "conversation_updated_at": item["conversation_updated_at"],
                        "page_name": item.get("page_name"),
                        "page_avatar": item.get("page_avatar"),
                        "page_category": item.get("page_category"),
                        "user_info": item.get("user_info"),
                        "total_messages": item.get("total_messages", 0),
                        "unread_count": item.get("unread_count", 0),
                        "participants": participants,
                        "ad_context": item.get("ad_context"),
                    }

                    latest_message_data = None
                    if item.get("latest_message_id"):
                        latest_message_data = {
                            "id": item["latest_message_id"],
                            "conversation_id": item["latest_message_conversation_id"],
                            "is_echo": item["latest_message_is_echo"],
                            "text": item.get("latest_message_text"),
                            "photo_url": item.get("latest_message_photo_url"),
                            "video_url": item.get("latest_message_video_url"),
                            "audio_url": item.get("latest_message_audio_url"),
                            "template_data": item.get("latest_message_template_data"),
                            "facebook_timestamp": item.get(
                                "latest_message_facebook_timestamp"
                            ),
                            "metadata": item.get("latest_message_metadata"),
                            "reply_to_message_id": item.get(
                                "latest_message_reply_to_message_id"
                            ),
                            "created_at": item["latest_message_created_at"],
                            "updated_at": item["latest_message_updated_at"],
                        }

                    transformed_items.append(
                        {
                            "conversation": conversation_data,
                            "latest_message": latest_message_data,
                        }
                    )

                return {
                    "items": transformed_items,
                    "has_more": has_more,
                    "next_cursor": self._encode_conversation_cursor(next_cursor_tuple),
                }

        except Exception as e:
            logger.error(f"❌ Failed to get user conversations for user {user_id}: {e}")
            raise RuntimeError("Internal server error while retrieving conversations")

    async def send_message(
        self,
        user_id: str,
        conversation_id: str,
        text: Optional[str] = None,
        image_urls: Optional[List[str]] = None,
        video_urls: Optional[List[str]] = None,
        metadata: Optional[str] = None,
        reply_to_message_id: Optional[str] = None,
    ) -> Dict[str, Any]:
        """
        Send a message via Facebook Messenger API.

        Args:
            user_id: Internal user ID for authorization
            conversation_id: Conversation ID to send message to
            text: Optional text message
            image_urls: Optional list of image URLs to send
            video_urls: Optional list of video URLs to send
            metadata: Optional metadata string
            reply_to_message_id: Optional Facebook message id (mid) to reply to; message will appear as reply in Messenger

        Returns:
            Dictionary with operation result and updated conversation data

        Raises:
            ValueError: If conversation not found
            PermissionError: If user doesn't have permission
            RuntimeError: If sending fails
        """
        # API (FE) trigger: always tag sent_by admin, keep FE metadata (e.g. optimistic_xxx)
        meta_dict: Dict[str, Any] = {"sent_by": "admin"}
        if metadata:
            if isinstance(metadata, str) and metadata.strip().startswith("{"):
                try:
                    parsed = json.loads(metadata)
                    if isinstance(parsed, dict):
                        meta_dict.update(parsed)
                except (json.JSONDecodeError, TypeError):
                    meta_dict["optimistic_id"] = metadata
            else:
                meta_dict["optimistic_id"] = (
                    metadata.strip() if isinstance(metadata, str) else str(metadata)
                )
        metadata = json.dumps(meta_dict)

        try:
            async with async_db_transaction() as conn:
                # Get conversation details to extract page_id and user_id
                conversation_details = await get_conversation_with_details(
                    conn, conversation_id
                )
                if not conversation_details:
                    raise ValueError(
                        f"Conversation with ID {conversation_id} not found"
                    )

                page_id = conversation_details["fan_page_id"]
                facebook_page_scope_user_id = conversation_details[
                    "facebook_page_scope_user_id"
                ]

                page_admins = (
                    await self.page_service.get_facebook_page_admins_by_user_id(user_id)
                )
                page_admin = None
                for admin in page_admins:
                    if admin.get("page_id") == page_id:
                        page_admin = admin
                        break

                if not page_admin:
                    raise PermissionError(
                        f"User does not have admin access to page {page_id}"
                    )

                access_token = page_admin.get("access_token")
                if not access_token:
                    raise RuntimeError(f"No access token found for page {page_id}")

                # Initialize Facebook Graph Page Client
                facebook_client = FacebookGraphPageClient(
                    page_access_token=access_token
                )

                # Send message via Facebook API (with retry after take_thread_control if another app controls thread)
                async def _do_send() -> None:
                    if text:
                        await facebook_client.send_message(
                            user_id=facebook_page_scope_user_id,
                            message=text,
                            metadata=metadata,
                            reply_to_message_id=reply_to_message_id,
                        )
                    if image_urls:
                        for image_url in image_urls:
                            await facebook_client.send_image_message(
                                user_id=facebook_page_scope_user_id,
                                image_url=image_url,
                                metadata=metadata,
                                reply_to_message_id=reply_to_message_id,
                            )
                    if video_urls:
                        for video_url in video_urls:
                            await facebook_client.send_video_message(
                                user_id=facebook_page_scope_user_id,
                                video_url=video_url,
                                metadata=metadata,
                                reply_to_message_id=reply_to_message_id,
                            )

                try:
                    await _do_send()
                except FacebookAPIError as e:
                    # Another app (e.g. Messenger AI) controls thread — take control then retry (requires Default routing app set in Page settings)
                    if e.error_code == 10 and e.error_subcode == 2018300:
                        logger.info(
                            f"Taking thread control for conversation {conversation_id} then retrying send"
                        )
                        try:
                            await facebook_client.take_thread_control(
                                recipient_id=facebook_page_scope_user_id,
                            )
                            await _do_send()
                        except FacebookAPIError:
                            raise
                    else:
                        raise
                except Exception as e:
                    logger.error(f"❌ Failed to send message via Facebook API: {e}")
                    raise RuntimeError(f"Failed to send message via Facebook: {str(e)}")

                # Get updated conversation details for response
                conversation_details = await get_conversation_with_details(
                    conn, conversation_id
                )

                return {
                    "success": True,
                    "message": "Message sent successfully",
                    "conversation": conversation_details,
                }

        except FacebookAPIError:
            # Re-raise FacebookAPIError as-is to preserve user-friendly message
            raise
        except (ValueError, PermissionError, RuntimeError):
            raise
        except Exception as e:
            logger.error(f"❌ Failed to send message: {e}")
            raise RuntimeError("Internal server error while sending message")

    async def get_conversation_fan_page_id(
        self, user_id: str, conversation_id: str
    ) -> str:
        """
        Get fan_page_id for a conversation after checking user has admin permission.
        Used by agent-block and other conversation-scoped operations.
        """
        async with async_db_transaction() as conn:
            conversation_details = await get_conversation_with_details(
                conn, conversation_id
            )
            if not conversation_details:
                raise ValueError(f"Conversation with ID {conversation_id} not found")
            page_id = conversation_details["fan_page_id"]
            has_permission = (
                await self.permission_service.check_user_page_admin_permission(
                    user_id, page_id
                )
            )
            if not has_permission:
                raise PermissionError(
                    f"User does not have permission to manage page {page_id}"
                )
            return page_id

    async def get_conversation_messages(
        self,
        user_id: str,
        conversation_id: str,
        limit: int = 20,
        cursor: Optional[str] = None,
    ) -> Dict[str, Any]:
        """
        Get messages for a conversation with pagination.

        Args:
            user_id: Internal user ID for authorization
            conversation_id: Conversation ID to get messages from
            page: Page number (1-indexed)
            page_size: Number of items per page

        Returns:
            Dictionary with paginated messages list

        Raises:
            ValueError: If conversation not found
            PermissionError: If user doesn't have permission
            RuntimeError: If retrieval fails
        """
        try:
            async with async_db_transaction() as conn:
                # First check if conversation exists and user has permission
                conversation_details = await get_conversation_with_details(
                    conn, conversation_id
                )
                if not conversation_details:
                    raise ValueError(
                        f"Conversation with ID {conversation_id} not found"
                    )

                # Check if user has permission to access this page's conversations
                page_id = conversation_details["fan_page_id"]
                has_permission = (
                    await self.permission_service.check_user_page_admin_permission(
                        user_id, page_id
                    )
                )

                if not has_permission:
                    raise PermissionError(
                        f"User does not have permission to access page {page_id} conversations"
                    )

                limit = max(1, min(limit, 100))
                cursor_tuple = self._decode_message_cursor(cursor)
                rows, has_more, next_cursor_tuple = (
                    await list_messages_by_conversation_id(
                        conn, conversation_id, limit, cursor_tuple
                    )
                )

                items: List[Dict[str, Any]] = []
                for row in rows:
                    data = dict(row)
                    items.append(data)

                return {
                    "items": items,
                    "has_more": has_more,
                    "next_cursor": self._encode_message_cursor(next_cursor_tuple),
                }

        except (ValueError, PermissionError):
            raise
        except Exception as e:
            logger.error(
                f"❌ Failed to get messages for conversation {conversation_id}: {e}"
            )
            raise RuntimeError("Internal server error while retrieving messages")

    def _decode_conversation_cursor(
        self, cursor: Optional[str]
    ) -> Optional[Tuple[int, str]]:
        if not cursor:
            return None
        try:
            sort_value, conversation_id = cursor.split(":", 1)
            return int(sort_value), conversation_id
        except ValueError:
            logger.warning(f"⚠️ Invalid conversation cursor received: {cursor}")
            return None

    def _encode_conversation_cursor(
        self, cursor: Optional[Tuple[int, str]]
    ) -> Optional[str]:
        if not cursor:
            return None
        sort_value, conversation_id = cursor
        return f"{sort_value}:{conversation_id}"

    def _decode_message_cursor(
        self, cursor: Optional[str]
    ) -> Optional[Tuple[int, str]]:
        if not cursor:
            return None
        try:
            created_at_str, message_id = cursor.split(":", 1)
            return int(created_at_str), message_id
        except ValueError:
            logger.warning(f"⚠️ Invalid message cursor received: {cursor}")
            return None

    def _encode_message_cursor(
        self, cursor: Optional[Tuple[int, str]]
    ) -> Optional[str]:
        if not cursor:
            return None
        created_at, message_id = cursor
        return f"{created_at}:{message_id}"
