import json
from typing import Dict, Any, Optional, Tuple, List, TYPE_CHECKING

from src.database.postgres.connection import async_db_transaction
from src.database.postgres.repositories.facebook_queries import (
    get_comment,
    get_comments_by_ids,
    get_post_by_id,
)
from src.database.postgres.repositories.facebook_queries.comments.comment_conversations import (
    list_conversations_for_pages,
    list_thread_comments,
    get_conversation_by_root_comment_id,
    mark_all_comments_as_seen,
    update_conversation_mark_as_read,
    get_conversation_with_unread_count,
)
from src.common.clients.facebook_graph_page_client import FacebookGraphPageClient
from src.utils.logger import get_logger
from src.services.facebook.auth import FacebookPageService, FacebookPermissionService
from src.services.facebook.comments._internal.immediate_emit import (
    process_outgoing_comment_reply,
)

if TYPE_CHECKING:
    from src.socket_service import SocketService

logger = get_logger()


class CommentAPIHandler:
    """
    Handler for Facebook comment API operations.
    Handles all user-facing API requests for comment interactions.
    """

    def __init__(
        self,
        page_service: FacebookPageService,
        permission_service: FacebookPermissionService,
        comment_conversation_service: Optional[Any] = None,
        socket_service: Optional["SocketService"] = None,
    ):
        self.page_service = page_service
        self.permission_service = permission_service
        self.comment_conversation_service = comment_conversation_service
        self.socket_service = socket_service

    async def interact_with_comment_by_user(
        self,
        user_id: str,
        comment_id: str,
        action: str,
        message: Optional[str] = None,
        attachment_url: Optional[str] = None,
    ) -> Dict[str, Any]:
        """
        Interact with a comment via Facebook Graph API with user authorization.
        This method handles the complete flow including authorization checks.

        Args:
            user_id: Internal user ID for authorization
            comment_id: Facebook comment ID
            action: Action to perform (reply, hide, unhide, delete)
            message: Message for reply action
            attachment_url: Optional attachment URL for reply action

        Returns:
            Dictionary with success status and API response
        """
        try:
            # Get comment data from database
            async with async_db_transaction() as conn:
                comment_info = await get_comment(conn, comment_id)

            if not comment_info:
                logger.warning(
                    f"❌ Comment not found | User: {user_id} | Comment: {comment_id} | Action: {action}"
                )
                return {
                    "success": False,
                    "message": "Comment không tồn tại trong hệ thống",
                    "api_response": None,
                }

            # Get user's page admin permissions
            page_admins = await self.page_service.get_facebook_page_admins_by_user_id(
                user_id=user_id
            )

            if not page_admins:
                logger.warning(
                    f"❌ No page admin permissions | User: {user_id} | Comment: {comment_id} | Action: {action}"
                )
                return {
                    "success": False,
                    "message": "Bạn không có quyền quản lý bất kỳ Facebook page nào",
                    "api_response": None,
                }

            # Check if user has permission for this specific comment's page
            correct_page_admin = next(
                (
                    admin
                    for admin in page_admins
                    if admin["page_id"] == comment_info["fan_page_id"]
                ),
                None,
            )

            if not correct_page_admin:
                logger.warning(
                    f"❌ Authorization failed | User: {user_id} | Comment: {comment_id} | Action: {action}"
                )
                return {
                    "success": False,
                    "message": "Bạn không có quyền thực hiện action này với comment này",
                    "api_response": None,
                }

            return await self.interact_with_comment(
                page_admin=correct_page_admin,
                comment_info=comment_info,
                action=action,
                message=message,
                attachment_url=attachment_url,
            )

        except Exception as e:
            logger.error(
                f"❌ Error in comment interaction by user | User: {user_id} | Action: {action} | "
                f"Comment: {comment_id} | Error: {str(e)}"
            )
            return {
                "success": False,
                "message": f"Lỗi khi thực hiện {action}: {str(e)}",
                "api_response": None,
            }

    async def interact_with_comment(
        self,
        page_admin: Dict[str, Any],
        comment_info: Dict[str, Any],
        action: str,
        message: Optional[str] = None,
        attachment_url: Optional[str] = None,
    ) -> Dict[str, Any]:
        """
        Interact with a comment via Facebook Graph API.
        Actions: reply, hide, unhide, delete

        Args:
            page_admin: Page admin information
            comment_info: Comment information
            action: Action to perform (reply, hide, unhide, delete)
            message: Message for reply action
            attachment_url: Optional attachment URL for reply action

        Returns:
            Dictionary with success status and API response
        """
        try:
            graph_client = FacebookGraphPageClient(
                page_access_token=page_admin["access_token"]
            )

            api_response = None

            if action == "reply":
                api_response = await graph_client.reply_to_comment(
                    comment_info["id"], message, attachment_url
                )
            elif action == "hide":
                api_response = await graph_client.hide_comment(comment_info["id"])
            elif action == "unhide":
                api_response = await graph_client.unhide_comment(comment_info["id"])
            elif action == "delete":
                api_response = await graph_client.delete_comment(comment_info["id"])
            else:
                raise ValueError(f"Unknown action: {action}")

            if api_response:
                new_comment_id = api_response.get("id") if action == "reply" else None
                if (
                    action == "reply"
                    and new_comment_id
                    and self.comment_conversation_service
                    and self.socket_service
                ):
                    try:
                        async with async_db_transaction() as conn:
                            page_admins = (
                                await self.page_service.get_facebook_page_admins_by_page_id(
                                    conn, comment_info["fan_page_id"]
                                )
                            )
                            await process_outgoing_comment_reply(
                                conn,
                                new_comment_id=new_comment_id,
                                parent_comment_id=comment_info["id"],
                                post_id=comment_info["post_id"],
                                fan_page_id=comment_info["fan_page_id"],
                                message=message,
                                attachment_url=attachment_url,
                                metadata={"sent_by": "admin"},
                                page_admins=page_admins,
                                socket_service=self.socket_service,
                                comment_conversation_service=self.comment_conversation_service,
                            )
                    except Exception as e:
                        logger.error(
                            f"❌ Immediate emit for admin reply failed: {e}",
                            exc_info=True,
                        )
                return {
                    "success": True,
                    "message": f"Thành công {action} comment",
                    "api_response": api_response,
                    "new_comment_id": new_comment_id,
                }
            else:
                logger.error(
                    f"❌ Comment {action} failed | Comment: {comment_info['id']}"
                )
                return {
                    "success": False,
                    "message": f"Không thể {action} comment",
                    "api_response": None,
                    "new_comment_id": None,
                }

        except ValueError as e:
            logger.error(f"❌ Invalid parameters for comment {action}: {str(e)}")
            return {
                "success": False,
                "message": f"Tham số không hợp lệ: {str(e)}",
                "api_response": None,
                "new_comment_id": None,
            }
        except Exception as e:
            logger.error(
                f"❌ Failed to {action} comment {comment_info['id']}: {str(e)}"
            )
            return {
                "success": False,
                "message": f"Lỗi khi {action} comment: {str(e)}",
                "api_response": None,
                "new_comment_id": None,
            }

    async def update_comment_mark_as_read(
        self,
        user_id: str,
        root_comment_id: str,
        mark_as_read: bool = True,
    ) -> Dict[str, Any]:
        """
        Toggle mark_as_read boolean status for a conversation (UX feature).
        This only updates the mark_as_read flag, not the page_seen_at timestamps.
        """
        try:
            has_permission, page_id = (
                await self.permission_service.check_user_comment_permission(
                    user_id, root_comment_id
                )
            )

            if not has_permission:
                logger.warning(
                    f"❌ User {user_id} does not have permission to update comment {root_comment_id}"
                )
                raise PermissionError("Bạn không có quyền cập nhật comment này")

            async with async_db_transaction() as conn:
                conversation = await get_conversation_by_root_comment_id(
                    conn, root_comment_id
                )

                if not conversation:
                    logger.warning(f"⚠️ Conversation not found for {root_comment_id}")
                    raise ValueError("Không tìm thấy conversation cho comment này")

                # Update mark_as_read boolean status
                await update_conversation_mark_as_read(
                    conn, conversation["id"], mark_as_read
                )

                # Get updated conversation with computed counts
                updated_conversation = await get_conversation_with_unread_count(
                    conn, conversation["id"]
                )

            status_text = "đã đọc" if mark_as_read else "chưa đọc"
            return {
                "success": True,
                "comment_id": root_comment_id,
                "conversation_id": str(conversation["id"]),
                "mark_as_read": mark_as_read,
                "message": f"Conversation đã được đánh dấu là {status_text}",
                "unread_count": (
                    updated_conversation.get("unread_count", 0)
                    if updated_conversation
                    else 0
                ),
                "total_comments": (
                    updated_conversation.get("total_comments", 0)
                    if updated_conversation
                    else 0
                ),
            }

        except (PermissionError, ValueError):
            raise
        except Exception as e:
            logger.error(
                f"❌ Failed to update mark_as_read status for comment {root_comment_id} by user {user_id}: {str(e)}"
            )
            raise

    async def mark_all_comments_as_seen(
        self,
        user_id: str,
        root_comment_id: str,
    ) -> Dict[str, Any]:
        """
        Mark all user comments in a conversation as seen by setting page_seen_at.
        """
        try:
            has_permission, page_id = (
                await self.permission_service.check_user_comment_permission(
                    user_id, root_comment_id
                )
            )

            if not has_permission:
                logger.warning(
                    f"❌ User {user_id} does not have permission to update comment {root_comment_id}"
                )
                raise PermissionError("Bạn không có quyền cập nhật comment này")

            async with async_db_transaction() as conn:
                conversation = await get_conversation_by_root_comment_id(
                    conn, root_comment_id
                )

                if not conversation:
                    logger.warning(f"⚠️ Conversation not found for {root_comment_id}")
                    raise ValueError("Không tìm thấy conversation cho comment này")

                # Mark all comments as seen
                await mark_all_comments_as_seen(conn, conversation["id"])

                # Get updated conversation with computed counts
                updated_conversation = await get_conversation_with_unread_count(
                    conn, conversation["id"]
                )

            return {
                "success": True,
                "comment_id": root_comment_id,
                "conversation_id": str(conversation["id"]),
                "message": "Tất cả bình luận đã được đánh dấu là đã xem",
                "unread_count": 0,
                "total_comments": (
                    updated_conversation.get("total_comments", 0)
                    if updated_conversation
                    else 0
                ),
            }

        except (PermissionError, ValueError):
            raise
        except Exception as e:
            logger.error(
                f"❌ Failed to mark comments as seen for {root_comment_id} by user {user_id}: {str(e)}"
            )
            raise

    async def send_message_to_commenter(
        self,
        user_id: str,
        comment_id: str,
        message: str,
    ) -> Dict[str, Any]:
        """
        Send a message to the commenter via Facebook Messenger.

        Args:
            user_id: Internal user ID for authorization
            comment_id: Facebook comment ID
            message: Message content to send

        Returns:
            Dictionary with operation result and API response

        Raises:
            PermissionError: If user doesn't have permission to access this comment's page
            ValueError: If comment not found or invalid
        """
        try:
            async with async_db_transaction() as conn:
                # Get comment information from database
                comment_info = await get_comment(conn, comment_id)

                if not comment_info:
                    logger.warning(
                        f"❌ Comment not found | User: {user_id} | Comment: {comment_id}"
                    )
                    raise ValueError("Comment không tồn tại trong hệ thống")

                # Check if comment is from a page (can't send message to pages)
                if comment_info.get("is_from_page"):
                    logger.warning(
                        f"❌ Cannot send message to page comment | User: {user_id} | Comment: {comment_id}"
                    )
                    raise ValueError("Không thể gửi tin nhắn cho comment từ page")

                # Get commenter ID (facebook_page_scope_user_id)
                commenter_id = comment_info.get("facebook_page_scope_user_id")
                if not commenter_id:
                    logger.warning(
                        f"❌ No commenter ID found | User: {user_id} | Comment: {comment_id}"
                    )
                    raise ValueError("Không tìm thấy thông tin người bình luận")

                # Get page ID and check user permission
                page_id = comment_info.get("fan_page_id")
                if not page_id:
                    logger.warning(
                        f"❌ No page ID found | User: {user_id} | Comment: {comment_id}"
                    )
                    raise ValueError("Không tìm thấy thông tin page")

                # Check user permission for this comment's page
                has_permission, _ = (
                    await self.permission_service.check_user_comment_permission(
                        user_id, comment_id
                    )
                )

                if not has_permission:
                    logger.warning(
                        f"❌ Authorization failed | User: {user_id} | Comment: {comment_id} | Page: {page_id}"
                    )
                    raise PermissionError("Bạn không có quyền quản lý page này")

                # Get user's page admin permissions
                page_admins = (
                    await self.page_service.get_facebook_page_admins_by_user_id(
                        user_id=user_id
                    )
                )

                # Find the correct page admin
                page_admin = next(
                    (admin for admin in page_admins if admin["page_id"] == page_id),
                    None,
                )

                if not page_admin:
                    logger.error(
                        f"❌ No page admin found | User: {user_id} | Page: {page_id}"
                    )
                    raise PermissionError("Bạn không có quyền quản lý page này")

                # Get access token for the page
                access_token = page_admin.get("access_token")
                if not access_token:
                    logger.error(
                        f"❌ No access token found | User: {user_id} | Page: {page_id}"
                    )
                    raise ValueError("Không tìm thấy access token cho page này")

            # Initialize Facebook Graph Page Client and send message
            facebook_client = FacebookGraphPageClient(page_access_token=access_token)

            # Send message to commenter
            api_response = await facebook_client.send_message(
                user_id=commenter_id,
                message=message,
            )

            return {
                "success": True,
                "comment_id": comment_id,
                "commenter_id": commenter_id,
                "message": "Tin nhắn đã được gửi thành công",
                "api_response": api_response,
            }

        except (PermissionError, ValueError):
            # Re-raise these specific exceptions
            raise
        except Exception as e:
            logger.error(
                f"❌ Error sending message to commenter | User: {user_id} | Comment: {comment_id} | Error: {str(e)}"
            )
            raise

    def _decode_thread_cursor(self, cursor: Optional[str]) -> Optional[Tuple[int, str]]:
        if not cursor:
            return None
        try:
            sort_str, conversation_id = cursor.split(":", 1)
            return int(sort_str), conversation_id
        except ValueError:
            logger.warning(f"⚠️ Invalid thread cursor received: {cursor}")
            return None

    def _encode_thread_cursor(self, cursor: Optional[Tuple[int, str]]) -> Optional[str]:
        if not cursor:
            return None
        sort_value, conversation_id = cursor
        return f"{sort_value}:{conversation_id}"

    def _decode_comment_cursor(
        self, cursor: Optional[str]
    ) -> Optional[Tuple[int, str]]:
        if not cursor:
            return None
        try:
            created_at_str, comment_id = cursor.split(":", 1)
            return int(created_at_str), comment_id
        except ValueError:
            logger.warning(f"⚠️ Invalid comment cursor received: {cursor}")
            return None

    def _encode_comment_cursor(
        self, cursor: Optional[Tuple[int, str]]
    ) -> Optional[str]:
        if not cursor:
            return None
        created_at, comment_id = cursor
        return f"{created_at}:{comment_id}"

    async def get_root_comments_with_latest_replies(
        self, user_id: str, limit: int = 20, cursor: Optional[str] = None
    ) -> Dict[str, Any]:
        """Get root comment threads with cursor-based pagination."""
        try:
            page_admins = await self.page_service.get_facebook_page_admins_by_user_id(
                user_id
            )

            if not page_admins:
                return {"items": [], "has_more": False, "next_cursor": None}

            page_ids = [
                admin.get("page_id") for admin in page_admins if admin.get("page_id")
            ]

            if not page_ids:
                return {"items": [], "has_more": False, "next_cursor": None}

            async with async_db_transaction() as conn:
                cursor_tuple = self._decode_thread_cursor(cursor)
                rows, has_more, next_cursor_tuple = await list_conversations_for_pages(
                    conn, page_ids, limit, cursor_tuple
                )

                comment_ids: List[str] = []
                for row in rows:
                    root_id = row.get("root_comment_id")
                    latest_id = row.get("latest_comment_id")
                    if root_id:
                        comment_ids.append(root_id)
                    if latest_id:
                        comment_ids.append(latest_id)

                comments_map = await get_comments_by_ids(conn, list(set(comment_ids)))

            items = []
            for record in rows:
                row = dict(record)
                page_info = {
                    "id": row["fan_page_id"],
                    "name": row.get("page_name"),
                    "avatar": row.get("page_avatar"),
                    "category": row.get("page_category"),
                    "created_at": row.get("page_created_at"),
                    "updated_at": row.get("page_updated_at"),
                }
                post_info = {
                    "id": row["post_id"],
                    "fan_page_id": row["fan_page_id"],
                    "message": row.get("post_message"),
                    "video_link": row.get("post_video_link"),
                    "photo_link": row.get("post_photo_link"),
                    "facebook_created_time": row.get("post_facebook_created_time"),
                    "reaction_total_count": row.get("post_reaction_total_count", 0),
                    "reaction_like_count": row.get("post_reaction_like_count", 0),
                    "reaction_love_count": row.get("post_reaction_love_count", 0),
                    "reaction_haha_count": row.get("post_reaction_haha_count", 0),
                    "reaction_wow_count": row.get("post_reaction_wow_count", 0),
                    "reaction_sad_count": row.get("post_reaction_sad_count", 0),
                    "reaction_angry_count": row.get("post_reaction_angry_count", 0),
                    "reaction_care_count": row.get("post_reaction_care_count", 0),
                    "share_count": row.get("post_share_count", 0),
                    "comment_count": row.get("post_comment_count", 0),
                    "full_picture": row.get("post_full_picture"),
                    "permalink_url": row.get("post_permalink_url"),
                    "status_type": row.get("post_status_type"),
                    "is_published": row.get("post_is_published", True),
                    "reactions_fetched_at": row.get("post_reactions_fetched_at"),
                    "engagement_fetched_at": row.get("post_engagement_fetched_at"),
                    "created_at": row.get("post_created_at"),
                    "updated_at": row.get("post_updated_at"),
                }

                root_comment = comments_map.get(row["root_comment_id"])
                if root_comment:
                    root_comment = dict(root_comment)
                    root_comment["root_comment_id"] = row["root_comment_id"]

                latest_comment = None
                if row.get("latest_comment_id"):
                    latest_comment = comments_map.get(row["latest_comment_id"])
                    if latest_comment:
                        latest_comment = dict(latest_comment)
                        latest_comment["root_comment_id"] = row["root_comment_id"]

                participants = row.get("participant_scope_users") or []
                if isinstance(participants, str):
                    try:
                        participants = json.loads(participants)
                    except json.JSONDecodeError:
                        participants = []

                items.append(
                    {
                        "conversation_id": str(row["id"]),
                        "root_comment_id": row["root_comment_id"],
                        "fan_page_id": row["fan_page_id"],
                        "post_id": row["post_id"],
                        "total_comments": row["total_comments"],
                        "unread_count": row["unread_count"],
                        "mark_as_read": row.get("mark_as_read", False),
                        "has_page_reply": row["has_page_reply"],
                        "latest_comment_is_from_page": row.get(
                            "latest_comment_is_from_page"
                        ),
                        "page": page_info,
                        "post": post_info,
                        "root_comment": root_comment,
                        "latest_comment": latest_comment,
                        "participants": participants,
                    }
                )

            next_cursor = (
                self._encode_thread_cursor(next_cursor_tuple)
                if next_cursor_tuple
                else None
            )

            return {
                "items": items,
                "has_more": has_more,
                "next_cursor": next_cursor,
            }

        except Exception as e:
            logger.error(f"❌ Failed to get root comments for user {user_id}: {str(e)}")
            raise

    async def get_comments_by_root_comment_id(
        self,
        user_id: str,
        root_comment_id: str,
        limit: int = 50,
        cursor: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Get conversation comments with cursor pagination."""
        try:
            has_permission, page_id = (
                await self.permission_service.check_user_comment_permission(
                    user_id, root_comment_id
                )
            )

            if not has_permission:
                logger.warning(
                    f"❌ User {user_id} does not have permission to access comment thread {root_comment_id}"
                )
                raise PermissionError("Bạn không có quyền truy cập comment thread này")

            async with async_db_transaction() as conn:
                conversation = await get_conversation_by_root_comment_id(
                    conn, root_comment_id
                )

                if not conversation:
                    raise ValueError("Không tìm thấy conversation cho root_comment_id")

                cursor_tuple = self._decode_comment_cursor(cursor)
                rows, has_more, next_cursor_tuple = await list_thread_comments(
                    conn, conversation["id"], limit, cursor_tuple
                )

                page_data = await self.page_service.get_page_by_id(
                    conn, conversation["fan_page_id"]
                )
                post_data = await get_post_by_id(conn, conversation["post_id"])

            if not page_data:
                raise ValueError("Không tìm thấy thông tin page")

            if not post_data:
                raise ValueError("Không tìm thấy thông tin post")

            comments = []
            for row in rows:
                data = dict(row)
                data["root_comment_id"] = conversation["root_comment_id"]
                comments.append(data)

            next_cursor = (
                self._encode_comment_cursor(next_cursor_tuple)
                if next_cursor_tuple
                else None
            )

            return {
                "comments": comments,
                "page": page_data,
                "post": post_data,
                "total_count": conversation["total_comments"],
                "has_more": has_more,
                "next_cursor": next_cursor,
            }

        except (PermissionError, ValueError):
            raise
        except Exception as e:
            logger.error(
                f"❌ Failed to get comment thread {root_comment_id} for user {user_id}: {str(e)}"
            )
            raise

    async def get_conversation_info_by_root_comment_id(
        self, user_id: str, root_comment_id: str
    ) -> Dict[str, str]:
        """
        Get conversation_id and fan_page_id for a comment thread after permission check.
        Used by agent-block and other conversation-scoped operations.
        Returns dict with keys: conversation_id, fan_page_id.
        """
        has_permission, _page_id = (
            await self.permission_service.check_user_comment_permission(
                user_id, root_comment_id
            )
        )
        if not has_permission:
            raise PermissionError("Bạn không có quyền truy cập comment thread này")
        async with async_db_transaction() as conn:
            conversation = await get_conversation_by_root_comment_id(
                conn, root_comment_id
            )
            if not conversation:
                raise ValueError("Không tìm thấy conversation cho root_comment_id")
            return {
                "conversation_id": str(conversation["id"]),
                "fan_page_id": conversation["fan_page_id"],
            }
