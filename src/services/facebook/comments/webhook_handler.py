"""
Webhook handler for Facebook comment events.
Orchestrates comment processing workflow including tree building, authorship inference, and socket emission.
"""

import asyncio
from typing import Any, Dict, List, Optional, TYPE_CHECKING

from src.database.postgres.connection import async_db_transaction
from src.database.postgres.repositories.facebook_queries import get_comment
from src.utils.logger import get_logger
from src.services.facebook.auth import FacebookPageService
from src.services.facebook.users.page_scope_user_service import PageScopeUserService
from src.services.facebook.posts.post_sync_service import PostSyncService
from ._internal.comment_service import CommentService
from .sync.comment_write_service import CommentWriteService
from .comment_conversation_service import CommentConversationService
from ._internal.helpers import infer_comment_authorship
from ._internal.models import CommentEventContext
from ._internal.hydrate_comment import hydrate_comment_payload
from ._internal.socket_emitter import emit_comment_event_to_page_admins
from src.database.postgres.repositories import get_page_admin_suggest_configs_by_page

if TYPE_CHECKING:
    from src.socket_service import SocketService

logger = get_logger()


def _comment_was_pre_inserted(processed_comment: Dict[str, Any]) -> bool:
    """True if comment already had metadata (from immediate emit), so we skip duplicate socket emit."""
    meta = processed_comment.get("metadata")
    if not meta:
        return False
    if isinstance(meta, dict):
        return bool(meta.get("sent_by"))
    return False


class CommentWebhookHandler:
    """
    Handler for processing Facebook comment webhook events.
    Orchestrates comment processing workflow including tree building, authorship inference, and socket emission.
    """

    def __init__(
        self,
        comment_service: CommentService,
        comment_write_service: CommentWriteService,
        comment_conversation_service: CommentConversationService,
        page_service: FacebookPageService,
        page_scope_user_service: PageScopeUserService,
        socket_service: "SocketService",
        suggest_response_orchestrator: Optional[Any] = None,
    ):
        self.comment_service = comment_service
        self.comment_write_service = comment_write_service
        self.comment_conversation_service = comment_conversation_service
        self.page_service = page_service
        self.page_scope_user_service = page_scope_user_service
        self.socket_service: "SocketService" = socket_service
        self.post_sync_service = PostSyncService(page_service)
        self._suggest_response_orchestrator = suggest_response_orchestrator

    async def process_comment_event(
        self,
        page_id: str,
        comment_data: Dict[str, Any],
        verb: str,
    ):
        """
        Process a Facebook comment event and store in database.

        Args:
            page_id: Facebook page ID
            comment_data: Comment data from webhook
            verb: Action type (add, edited, remove, hide, unhide)
        """
        try:
            async with async_db_transaction() as conn:
                facebook_page_admins = (
                    await self.page_service.get_facebook_page_admins_by_page_id(
                        conn, page_id
                    )
                )

                context = await self._build_comment_context(
                    conn=conn,
                    page_id=page_id,
                    comment_data=comment_data,
                    verb=verb,
                    page_admins=facebook_page_admins,
                )

                if not context:
                    return

                await self._ensure_page_scope_user(
                    conn=conn,
                    context=context,
                    original_comment_data=comment_data,
                )

                await self.post_sync_service.get_or_create_post(
                    conn=conn,
                    post_id=context.post_id,
                    page_id=page_id,
                    page_admins=facebook_page_admins,
                    context_data=comment_data,
                )

                tree_root_id = await self._ensure_root_comment_tree(
                    conn=conn,
                    context=context,
                    page_id=page_id,
                    post_id=context.post_id,
                )

                if not tree_root_id:
                    return

                context.tree_root_id = tree_root_id
                processed_comment = await self._persist_comment(conn, context)

                if not processed_comment:
                    return

                conversation_summary = None
                if self.comment_conversation_service:
                    conversation_summary = await self._sync_conversation(
                        conn=conn,
                        context=context,
                        processed_comment=processed_comment,
                    )

                # Emit socket event within transaction to ensure data consistency.
                # Skip emit if comment was pre-inserted (has metadata from immediate emit).
                if conversation_summary and not _comment_was_pre_inserted(
                    processed_comment
                ):
                    await emit_comment_event_to_page_admins(
                        conn,
                        socket_service=self.socket_service,
                        page_id=page_id,
                        action=verb,
                        page_admins=facebook_page_admins,
                        conversation=conversation_summary,
                        mutated_comment=processed_comment,
                    )

                # Trigger suggest response for admins with webhook automation enabled (background so socket emits flush to FE)
                if (
                    not context.is_comment_from_page
                    and self._suggest_response_orchestrator
                    and conversation_summary
                ):
                    conversation_id = conversation_summary.get("id")
                    if conversation_id:
                        asyncio.create_task(
                            self._run_suggest_response_webhook_background(
                                page_id=page_id,
                                conversation_id=conversation_id,
                            )
                        )

        except Exception as e:
            logger.error(
                f"❌ Failed to process comment event: {e} /n Comment data: {comment_data}"
            )
            raise

    async def _build_comment_context(
        self,
        conn,
        *,
        page_id: str,
        comment_data: Dict[str, Any],
        verb: str,
        page_admins: List[Dict[str, Any]],
    ) -> Optional[CommentEventContext]:
        """
        Build complete context for processing a comment event.

        Args:
            conn: Database connection
            page_id: Facebook page ID
            comment_data: Raw webhook comment data
            verb: Action type (add, edited, remove, hide, unhide)
            page_admins: List of page admins

        Returns:
            CommentEventContext or None if author cannot be determined
        """
        comment_id = comment_data.get("comment_id", "")
        post_id = comment_data.get("post_id", "")
        actor_id = comment_data.get("from", {}).get("id", "")
        facebook_created_time = comment_data.get("created_time")

        hydration = await hydrate_comment_payload(
            conn,
            comment_id=comment_id,
            post_id=post_id,
            page_admins=page_admins,
            comment_data=comment_data,
        )

        authorship = infer_comment_authorship(
            actor_id=actor_id,
            page_id=page_id,
            from_id=hydration.from_id,
            verb=verb,
        )

        if authorship["case"] == "user_unknown":
            logger.warning(
                f"⚠️ Unknown comment author | ID: {comment_id} | Post: {post_id}"
            )
            return None

        return CommentEventContext(
            page_id=page_id,
            post_id=post_id,
            comment_id=comment_id,
            verb=verb,
            actor_id=actor_id,
            page_admins=page_admins,
            message=hydration.message,
            photo_url=hydration.photo_url,
            video_url=hydration.video_url,
            facebook_created_time=facebook_created_time,
            parent_comment_id=(
                hydration.parent_comment_id if not hydration.is_root_comment else None
            ),
            is_root_comment=hydration.is_root_comment,
            fetched_comment_data=hydration.fetched_comment_data,
            from_id=hydration.from_id,
            facebook_page_scope_user_id=authorship["facebook_page_scope_user_id"],
            is_comment_from_page=authorship["is_comment_from_page"],
            tree_root_id=comment_id if hydration.is_root_comment else None,
        )

    async def _ensure_page_scope_user(
        self,
        conn,
        *,
        context: CommentEventContext,
        original_comment_data: Dict[str, Any],
    ):
        """
        Ensure page scope user exists in database.

        Creates user record if not exists, with name from fetched data or fallback.
        """
        psid = context.facebook_page_scope_user_id
        if not psid:
            return

        fetched_from = (context.fetched_comment_data or {}).get("from", {}) or {}
        name = (
            fetched_from.get("name")
            or original_comment_data.get("from", {}).get("name")
            or f"anonymous{psid}"
        )

        await self.page_scope_user_service.get_or_create_page_scope_user(
            conn=conn,
            psid=psid,
            page_id=context.page_id,
            page_admins=context.page_admins,
            additional_user_info={"id": psid, "name": name},
        )

    async def _ensure_root_comment_tree(
        self,
        conn,
        *,
        context: CommentEventContext,
        page_id: str,
        post_id: str,
    ) -> Optional[str]:
        """
        Ensure the root comment tree exists in the database.
        Returns the root comment ID for conversation grouping.
        """
        if context.is_root_comment:
            return context.comment_id

        root_comment_id = (
            context.tree_root_id
            or await self.comment_write_service.find_root_comment_id(
                context.comment_id, post_id, context.page_admins
            )
        )

        if not root_comment_id:
            logger.warning(
                f"⚠️ Cannot determine root comment for {context.comment_id} on post {post_id}"
            )
            return None

        existing_root_comment = await get_comment(conn, root_comment_id)
        if not existing_root_comment:
            inserted_comments = (
                await self.comment_write_service.create_comment_tree_from_facebook(
                    conn=conn,
                    page_admins=context.page_admins,
                    root_comment_id=root_comment_id,
                    exclude_comment_id=context.comment_id,
                    page_id=page_id,
                    post_id=post_id,
                )
            )
            if inserted_comments and self.comment_conversation_service:
                await self.comment_conversation_service.sync_backfill_comments_to_conversation(
                    conn=conn,
                    fan_page_id=page_id,
                    post_id=post_id,
                    root_comment_id=root_comment_id,
                    comments=inserted_comments,
                )

        return root_comment_id

    async def _persist_comment(
        self, conn, context: CommentEventContext
    ) -> Optional[Dict[str, Any]]:
        """Persist comment to database based on verb."""
        return await self.comment_service.process_comment_by_verb(
            conn=conn,
            verb=context.verb,
            comment_id=context.comment_id,
            post_id=context.post_id,
            page_id=context.page_id,
            parent_id=(
                context.parent_comment_id if not context.is_root_comment else None
            ),
            is_from_page=context.is_comment_from_page,
            facebook_page_scope_user_id=context.facebook_page_scope_user_id,
            message=context.message,
            photo_url=context.photo_url,
            video_url=context.video_url,
            facebook_created_time=context.facebook_created_time,
        )

    async def _run_suggest_response_webhook_background(
        self,
        page_id: str,
        conversation_id: str,
    ) -> None:
        """Run suggest response for comment webhook in background so socket emits can flush to FE."""
        try:
            async with async_db_transaction() as conn:
                await self._trigger_suggest_response_for_webhook(
                    conn=conn,
                    page_id=page_id,
                    conversation_id=conversation_id,
                    page_admins=[],
                )
        except Exception as e:
            logger.error(
                f"Suggest response comment webhook background task failed: {e}",
                exc_info=True,
            )

    async def _trigger_suggest_response_for_webhook(
        self,
        conn,
        *,
        page_id: str,
        conversation_id: str,
        page_admins: List[Dict[str, Any]],
    ) -> None:
        """Trigger suggest response for admins with webhook automation enabled."""
        if not self._suggest_response_orchestrator:
            return
        try:
            page_admins_with_config = await get_page_admin_suggest_configs_by_page(
                conn, page_id
            )
            for admin in page_admins_with_config:
                if not admin.get("auto_webhook_suggest") and not admin.get(
                    "auto_webhook_graph_api"
                ):
                    continue
                user_id = admin.get("user_id")
                page_admin_id = admin.get("page_admin_id")
                if not user_id or not page_admin_id:
                    continue
                page_admin = {
                    "id": page_admin_id,
                    "page_id": admin.get("page_id"),
                    "access_token": admin.get("access_token"),
                }
                try:
                    await self._suggest_response_orchestrator.trigger(
                        user_id=user_id,
                        conversation_type="comments",
                        conversation_id=conversation_id,
                        fan_page_id=page_id,
                        trigger_source="webhook",
                        page_admin_id=page_admin_id,
                        page_admin=page_admin,
                        facebook_page_scope_user_id=None,
                        webhook_delay_seconds=admin.get("webhook_delay_seconds", 5),
                    )
                except Exception as e:
                    logger.warning(
                        f"Suggest response webhook trigger failed for admin {user_id}: {e}"
                    )
        except Exception as e:
            logger.error(f"Failed to trigger suggest response for comment webhook: {e}")

    async def _sync_conversation(
        self,
        conn,
        *,
        context: CommentEventContext,
        processed_comment: Dict[str, Any],
    ) -> Dict[str, Any]:
        """Sync comment to conversation record."""
        root_comment_id = context.tree_root_id or context.comment_id
        return (
            await self.comment_conversation_service.sync_single_comment_to_conversation(
                conn=conn,
                fan_page_id=context.page_id,
                post_id=context.post_id,
                root_comment_id=root_comment_id,
                comment=processed_comment,
                verb=context.verb,
            )
        )
