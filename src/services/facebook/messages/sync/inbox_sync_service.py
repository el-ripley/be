"""
Facebook Inbox Sync Service.

Syncs Facebook Messenger inbox conversations and messages into Postgres.
"""

import asyncio
from typing import Any, Dict, List, Optional, Tuple

from src.database.postgres.connection import get_async_connection
from src.database.postgres.repositories.facebook_queries.inbox_sync_states import (
    get_sync_state,
    reset_sync_state,
    upsert_sync_state,
)
from src.database.postgres.repositories.facebook_queries.messages.conversations import (
    create_conversation,
)
from src.services.facebook.auth import FacebookPageService
from src.services.facebook.messages.sync.message_history_sync import (
    ConversationMessageHistorySync,
)
from src.services.facebook.users.page_scope_user_service import PageScopeUserService
from src.utils.logger import get_logger

logger = get_logger()


class InboxSyncService:
    """
    Service responsible for batch syncing Facebook inbox conversations.

    Dependencies are injected from the application composition root (see src.main).
    """

    def __init__(
        self,
        page_service: FacebookPageService,
        page_scope_user_service: PageScopeUserService,
    ) -> None:
        self.page_service = page_service
        self.page_scope_user_service = page_scope_user_service

    async def get_sync_status(self, conn, page_id: str) -> Dict[str, Any]:
        """
        Get current sync status for a page.
        """
        state = await get_sync_state(conn, page_id)
        if not state:
            return {
                "fan_page_id": page_id,
                "status": "idle",
                "fb_cursor": None,
                "total_synced_conversations": 0,
                "total_synced_messages": 0,
                "last_sync_at": None,
            }
        return state

    async def sync_inbox(
        self,
        conn,
        page_id: str,
        limit: int = 50,
        messages_per_conv: int = 250,  # Increased from 100 for better coverage (most conversations < 250 messages)
        continue_from_cursor: bool = True,
        max_concurrent: int = 10,  # Optimized: Lower concurrency reduces API rate limiting and DB contention
    ) -> Dict[str, Any]:
        """
        Sync a batch of inbox conversations and their messages.

        Args:
            conn: Database connection
            page_id: Facebook page ID
            limit: Max conversations to sync in this batch
            messages_per_conv: Max messages to sync per conversation
            continue_from_cursor: Whether to continue from saved cursor
            max_concurrent: Maximum number of conversations to process concurrently (default: 5)

        Returns:
            SyncResult-like dict with progress information.
        """
        limit = max(1, min(limit, 100))

        # Load page admins for token rotation
        page_admins = await self.page_service.get_facebook_page_admins_by_page_id(
            conn, page_id
        )
        if not page_admins:
            logger.warning(f"⚠️ No page admins found for page {page_id}, aborting sync")
            return {
                "fan_page_id": page_id,
                "synced_conversations": 0,
                "synced_messages": 0,
                "skipped_conversations": 0,
                "has_more": False,
                "cursor": None,
                "status": "error",
                "error": "no_page_admins",
            }

        # Current state
        state = await get_sync_state(conn, page_id)
        previous_total_conversations = int(
            (state or {}).get("total_synced_conversations", 0) or 0
        )
        previous_total_messages = int(
            (state or {}).get("total_synced_messages", 0) or 0
        )
        cursor = (state or {}).get("fb_cursor") if continue_from_cursor else None

        # First attempt: use existing cursor (if any)
        conversations_data: Optional[List[Dict[str, Any]]] = None
        next_cursor: Optional[str] = None
        cursor_was_reset = False

        for attempt in range(2):
            try:
                conversations_data, next_cursor = await self._fetch_conversations_batch(
                    conn=conn,
                    page_id=page_id,
                    page_admins=page_admins,
                    limit=limit,
                    after=cursor,
                )
                break
            except Exception as e:
                if attempt == 0 and self._is_cursor_error(e) and cursor:
                    logger.warning(
                        f"⚠️ Cursor expired/invalid for page {page_id}, resetting and retrying once"
                    )
                    await reset_sync_state(conn, page_id, clear_totals=False)
                    cursor = None
                    cursor_was_reset = True
                    continue

                # Non-cursor error or second failure
                logger.error(f"❌ Failed to fetch conversations for page {page_id}: {e}")
                return {
                    "fan_page_id": page_id,
                    "synced_conversations": 0,
                    "synced_messages": 0,
                    "skipped_conversations": 0,
                    "has_more": bool(cursor),
                    "cursor": cursor,
                    "status": "error",
                    "error": "fetch_conversations_failed",
                }

        if not conversations_data:
            # No conversations or end of inbox
            await upsert_sync_state(
                conn,
                fan_page_id=page_id,
                fb_cursor=None,
                total_synced_conversations=previous_total_conversations,
                total_synced_messages=previous_total_messages,
                status="completed",
            )
            return {
                "fan_page_id": page_id,
                "synced_conversations": 0,
                "synced_messages": 0,
                "skipped_conversations": 0,
                "has_more": False,
                "cursor": None,
                "status": "completed",
                "cursor_was_reset": cursor_was_reset,
            }

        history_sync = ConversationMessageHistorySync(self.page_service)

        # Semaphore để giới hạn concurrency và tránh quá tải API/database
        # Lower concurrency = less contention, better throughput
        semaphore = asyncio.Semaphore(max_concurrent)

        async def process_conversation(conv: Dict[str, Any]) -> Tuple[int, int]:
            """
            Process a single conversation.

            Mỗi conversation được xử lý với connection riêng để tránh conflict
            vì asyncpg không hỗ trợ nhiều queries đồng thời trên cùng một connection.

            Returns:
                Tuple of (synced_conversations, synced_messages) - (1, count) if success, (0, 0) if skipped
            """
            async with semaphore:
                conv_id = conv.get("id")
                if not conv_id:
                    return (0, 0)

                # Mỗi task có connection riêng từ pool để tránh "another operation is in progress"
                async with get_async_connection() as task_conn:
                    try:
                        user_psid = self._extract_user_psid_from_conversation(
                            conv, page_id
                        )
                        if not user_psid:
                            logger.warning(
                                f"⚠️ Unable to determine user PSID for conversation {conv_id} on page {page_id}"
                            )
                            return (0, 0)

                        # Ensure page scope user exists
                        await self.page_scope_user_service.get_or_create_page_scope_user(
                            conn=task_conn,
                            psid=user_psid,
                            page_id=page_id,
                            page_admins=page_admins,
                            additional_user_info=None,
                        )

                        participants_snapshot = (conv.get("participants") or {}).get(
                            "data", []
                        )

                        # Create or update conversation record
                        await create_conversation(
                            conn=task_conn,
                            conversation_id=conv_id,
                            fan_page_id=page_id,
                            facebook_page_scope_user_id=user_psid,
                            participants_snapshot=participants_snapshot,
                        )

                        # Sync message history (idempotent on message_id)
                        synced_count = await history_sync.sync_conversation_history(
                            conn=task_conn,
                            conversation_id=conv_id,
                            page_id=page_id,
                            page_admins=page_admins,
                            max_messages=messages_per_conv,
                        )

                        return (1, synced_count)

                    except Exception as e:
                        logger.warning(
                            f"⚠️ Failed to sync conversation {conv_id} for page {page_id}: {e}"
                        )
                        return (0, 0)

        # Process all conversations concurrently với error handling
        results = await asyncio.gather(
            *[process_conversation(conv) for conv in conversations_data],
            return_exceptions=True,
        )

        # Aggregate results
        synced_conversations = 0
        synced_messages_total = 0
        skipped_conversations = 0

        for result in results:
            if isinstance(result, Exception):
                logger.error(f"❌ Unexpected error processing conversation: {result}")
                skipped_conversations += 1
                continue

            synced_conv, synced_msg = result
            if synced_conv > 0:
                synced_conversations += synced_conv
                synced_messages_total += synced_msg
            else:
                skipped_conversations += 1

        new_total_conversations = previous_total_conversations + synced_conversations
        new_total_messages = previous_total_messages + synced_messages_total

        has_more = bool(next_cursor)
        status = "in_progress" if has_more else "completed"

        state_record = await upsert_sync_state(
            conn,
            fan_page_id=page_id,
            fb_cursor=next_cursor,
            total_synced_conversations=new_total_conversations,
            total_synced_messages=new_total_messages,
            status=status,
        )

        return {
            "fan_page_id": page_id,
            "synced_conversations": synced_conversations,
            "synced_messages": synced_messages_total,
            "skipped_conversations": skipped_conversations,
            "has_more": has_more,
            "cursor": state_record.get("fb_cursor"),
            "status": state_record.get("status", status),
            "cursor_was_reset": cursor_was_reset,
        }

    async def _fetch_conversations_batch(
        self,
        conn,
        page_id: str,
        page_admins: List[Dict[str, Any]],
        limit: int,
        after: Optional[str],
    ) -> Tuple[List[Dict[str, Any]], Optional[str]]:
        """
        Fetch a single page of conversations from Graph API.

        This method propagates exceptions (instead of swallowing them)
        to enable cursor error detection and automatic retry.
        """
        from src.common.clients.facebook_graph_page_client import (
            FacebookGraphPageClient,
        )

        last_exception: Optional[Exception] = None

        for admin in page_admins:
            access_token = admin.get("access_token")
            if not access_token:
                continue

            try:
                client = FacebookGraphPageClient(page_access_token=access_token)
                result = await client.get_conversations(
                    page_id=page_id,
                    folder="inbox",
                    limit=limit,
                    after=after,
                )

                data = result.get("data") or []
                paging = result.get("paging") or {}
                cursors = paging.get("cursors") or {}
                next_cursor = cursors.get("after")

                return data, next_cursor

            except Exception as e:
                last_exception = e
                continue

        # All tokens failed - raise the last exception for cursor error detection
        if last_exception:
            raise last_exception

        # No tokens available
        return [], None

    @staticmethod
    def _extract_user_psid_from_conversation(
        conversation: Dict[str, Any],
        page_id: str,
    ) -> Optional[str]:
        """
        Identify the PSID of the user participant in a conversation.

        We assume that participants contain the page id and one user id.
        """
        participants = (conversation.get("participants") or {}).get("data", [])
        for p in participants:
            pid = p.get("id")
            if pid and pid != page_id:
                return pid
        return None

    @staticmethod
    def _is_cursor_error(exc: Exception) -> bool:
        """
        Best-effort detection of Facebook cursor-related errors.

        We inspect the optional `response` attribute if available and try to
        parse Graph error payloads with code 100/190 and cursor-related messages.
        """
        response = getattr(exc, "response", None)
        if not response:
            return False

        try:
            payload = response.json()
        except Exception:
            return False

        error = (payload or {}).get("error") or {}
        code = error.get("code")
        message = (error.get("message") or "").lower()

        if code in (100, 190) and "cursor" in message:
            return True

        return False
