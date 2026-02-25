"""
Conversation sync service for Facebook messages.

Handles realtime conversation sync: fetching from Facebook API when needed
and syncing message history.
"""

from typing import Dict, Any, List, Optional

from src.common.clients.facebook_graph_page_client import FacebookGraphPageClient
from src.database.postgres.repositories.facebook_queries.messages.conversations import (
    create_conversation,
    get_conversation_by_participants,
)
from src.services.facebook._core.helpers import execute_graph_client_with_random_tokens
from src.services.facebook.auth import FacebookPageService
from src.utils.logger import get_logger

logger = get_logger()


class ConversationSyncService:
    """
    Manages realtime conversation sync operations.

    Used by webhook handlers to ensure conversations exist in database,
    fetching from Facebook when needed.
    """

    def __init__(self, page_service: FacebookPageService):
        self.page_service = page_service

    async def ensure_conversation(
        self,
        conn,
        page_id: str,
        user_psid: str,
        page_admins: Optional[List[Dict[str, Any]]] = None,
        sync_history: bool = True,
    ) -> Dict[str, Any]:
        """
        Ensure a conversation exists in the database.

        If the conversation doesn't exist, it will be fetched from Facebook
        and optionally sync the message history.

        Args:
            conn: Database connection
            page_id: Facebook page ID
            user_psid: Page-scoped user ID
            page_admins: Optional list of page admins with access tokens
            sync_history: Whether to sync message history for new conversations

        Returns:
            Conversation data dict with additional '_is_new' flag indicating if conversation was just created
        """
        # Step 1: Check if conversation already exists
        existing = await get_conversation_by_participants(
            conn=conn,
            fan_page_id=page_id,
            facebook_page_scope_user_id=user_psid,
        )

        if existing:
            existing["_is_new"] = False
            return existing

        # Step 2: New conversation - fetch from Facebook
        logger.info(
            f"🆕 New conversation detected for page {page_id} and user {user_psid}, "
            "fetching from Facebook..."
        )

        fb_conversation = await self._fetch_facebook_conversation(
            conn=conn,
            page_id=page_id,
            user_psid=user_psid,
            page_admins=page_admins,
        )

        conversation_id = fb_conversation.get("id")
        if not conversation_id:
            raise RuntimeError(
                f"Unable to resolve Facebook conversation id for page {page_id} and user {user_psid}"
            )

        participants_snapshot = (fb_conversation.get("participants") or {}).get(
            "data", []
        )

        # Step 3: Create conversation in database
        conversation_data = await create_conversation(
            conn=conn,
            conversation_id=conversation_id,
            fan_page_id=page_id,
            facebook_page_scope_user_id=user_psid,
            participants_snapshot=participants_snapshot,
        )

        # Mark as new and store sync info for later
        conversation_data["_is_new"] = True
        conversation_data["_sync_history"] = sync_history
        conversation_data["_conversation_id"] = conversation_id
        conversation_data["_page_id"] = page_id
        conversation_data["_page_admins"] = page_admins

        return conversation_data

    async def _fetch_facebook_conversation(
        self,
        conn,
        page_id: str,
        user_psid: str,
        page_admins: Optional[List[Dict[str, Any]]] = None,
    ) -> Dict[str, Any]:
        """Fetch conversation metadata from Facebook Graph API."""
        admins = page_admins
        if admins is None:
            admins = await self.page_service.get_facebook_page_admins_by_page_id(
                conn, page_id
            )

        async def callback(client: FacebookGraphPageClient) -> Optional[Dict[str, Any]]:
            return await client.get_conversation_for_user(
                page_id=page_id,
                user_psid=user_psid,
            )

        conversation = await execute_graph_client_with_random_tokens(
            page_admins=admins,
            callback=callback,
            operation_name=f"get conversation for page {page_id} and user {user_psid}",
        )

        if not conversation:
            raise RuntimeError(
                f"Facebook conversation not found for page {page_id} and user {user_psid}"
            )

        return conversation
