"""
Suggest Response API Handler.

Thin layer bridging FastAPI endpoints to service layer.
"""

from typing import Dict, Any, Optional
from fastapi import HTTPException, status
from src.utils.logger import get_logger
from src.services.suggest_response.suggest_response_agent_service import (
    SuggestResponseAgentService,
)
from src.services.suggest_response.suggest_response_prompts_service import (
    SuggestResponsePromptsService,
)
from src.services.suggest_response.suggest_response_history_service import (
    SuggestResponseHistoryService,
)
from src.services.suggest_response.memory_blocks_service import (
    MemoryBlocksService,
)

logger = get_logger()


class SuggestResponseHandler:
    """Handler for suggest response API endpoints."""

    def __init__(
        self,
        agent_service: SuggestResponseAgentService,
        prompts_service: SuggestResponsePromptsService,
        history_service: SuggestResponseHistoryService,
        orchestrator=None,  # SuggestResponseOrchestrator - required for API triggers
    ):
        self.agent_service = agent_service
        self.prompts_service = prompts_service
        self.history_service = history_service
        self._orchestrator = orchestrator
        self.memory_blocks_service = MemoryBlocksService()

    # ================================================================
    # HELPER METHODS
    # ================================================================

    @staticmethod
    def _convert_media_to_dict(media: Optional[list]) -> Optional[list]:
        """
        Convert media from Pydantic models to dicts for service layer.

        Args:
            media: Optional list of media items (Pydantic models or dicts)

        Returns:
            Optional[list]: List of dicts, empty list, or None
        """
        if media is None:
            return None
        if len(media) == 0:
            return []
        # Convert Pydantic models to dicts if needed
        return [
            item.model_dump() if hasattr(item, "model_dump") else item for item in media
        ]

    def _handle_service_error(self, error: Exception, operation: str) -> HTTPException:
        """
        Handle service layer errors and convert to HTTPException.

        Args:
            error: Exception from service layer
            operation: Description of the operation (for logging)

        Returns:
            HTTPException: Appropriate HTTP exception
        """
        from src.agent.suggest_response.core.runner import (
            InsufficientBalanceError,
        )

        if isinstance(error, InsufficientBalanceError):
            # Insufficient balance is intentional block, not an error
            return HTTPException(
                status_code=status.HTTP_402_PAYMENT_REQUIRED, detail=str(error)
            )
        elif isinstance(error, ValueError):
            logger.warning(
                f"⚠️ SUGGEST RESPONSE HANDLER: Validation error in {operation}: {str(error)}"
            )
            return HTTPException(status_code=400, detail=str(error))
        else:
            logger.error(
                f"❌ SUGGEST RESPONSE HANDLER: Error in {operation}: {str(error)}"
            )
            return HTTPException(status_code=500, detail="Internal server error")

    # ================================================================
    # AGENT SETTINGS HANDLERS
    # ================================================================

    async def get_settings(self, user_id: str) -> Dict[str, Any]:
        """Get suggest response agent settings."""
        try:
            result = await self.agent_service.get_settings(user_id)
            return result

        except Exception as e:
            logger.error(
                f"❌ SUGGEST RESPONSE HANDLER: Error getting settings: {str(e)}"
            )
            raise HTTPException(status_code=500, detail="Internal server error")

    async def update_settings(
        self,
        user_id: str,
        settings: Optional[Dict[str, Any]] = None,
        allow_auto_suggest: Optional[bool] = None,
        num_suggest_response: Optional[int] = None,
    ) -> Dict[str, Any]:
        """Update suggest response agent settings."""
        try:
            result = await self.agent_service.update_settings(
                user_id=user_id,
                settings=settings,
                allow_auto_suggest=allow_auto_suggest,
                num_suggest_response=num_suggest_response,
            )
            return result

        except ValueError as e:
            logger.warning(f"⚠️ SUGGEST RESPONSE HANDLER: Validation error: {str(e)}")
            raise HTTPException(status_code=400, detail=str(e))
        except Exception as e:
            logger.error(
                f"❌ SUGGEST RESPONSE HANDLER: Error updating settings: {str(e)}"
            )
            raise HTTPException(status_code=500, detail="Internal server error")

    # ================================================================
    # PAGE MEMORY HANDLERS (READ-ONLY, RENDERED FORMAT)
    # ================================================================

    async def get_page_memory(
        self,
        fan_page_id: str,
        prompt_type: str,
        owner_user_id: str,
    ) -> Optional[Dict[str, Any]]:
        """
        Get active page memory with rendered content (same format as agent sees).

        Returns rendered text from memory blocks, not structured data.
        """
        try:
            from src.database.postgres.connection import async_db_transaction
            from src.database.postgres.repositories.suggest_response_queries import (
                get_active_page_prompt,
            )

            async with async_db_transaction() as conn:
                # Get prompt container
                prompt = await get_active_page_prompt(
                    conn, fan_page_id, prompt_type, owner_user_id
                )

                if not prompt:
                    return None

                prompt_id = str(prompt["id"])

                # Render memory blocks to text
                rendered_content = await self.memory_blocks_service.render_memory(
                    memory_type="page_prompt", prompt_id=prompt_id
                )

                # Count blocks for metadata
                blocks = await self.memory_blocks_service.list_blocks(
                    memory_type="page_prompt", prompt_id=prompt_id
                )
                block_count = len(blocks)

                return {
                    "prompt_id": prompt_id,
                    "fan_page_id": fan_page_id,
                    "prompt_type": prompt_type,
                    "rendered_content": rendered_content,
                    "block_count": block_count,
                    "is_active": prompt.get("is_active", True),
                    "created_at": prompt.get("created_at"),
                }
        except Exception as e:
            raise self._handle_service_error(e, "getting page memory")

    # ================================================================
    # USER MEMORY HANDLERS (READ-ONLY, RENDERED FORMAT)
    # ================================================================

    async def get_user_memory(
        self,
        fan_page_id: str,
        facebook_page_scope_user_id: str,
        owner_user_id: str,
    ) -> Optional[Dict[str, Any]]:
        """
        Get active user memory with rendered content (same format as agent sees).

        Returns rendered text from memory blocks, not structured data.
        """
        try:
            from src.database.postgres.connection import async_db_transaction
            from src.database.postgres.repositories.suggest_response_queries import (
                get_active_page_scope_user_prompt,
            )

            async with async_db_transaction() as conn:
                # Get prompt container
                prompt = await get_active_page_scope_user_prompt(
                    conn, fan_page_id, facebook_page_scope_user_id, owner_user_id
                )

                if not prompt:
                    return None

                prompt_id = str(prompt["id"])

                # Render memory blocks to text
                rendered_content = await self.memory_blocks_service.render_memory(
                    memory_type="user_prompt", prompt_id=prompt_id
                )

                # Count blocks for metadata
                blocks = await self.memory_blocks_service.list_blocks(
                    memory_type="user_prompt", prompt_id=prompt_id
                )
                block_count = len(blocks)

                return {
                    "prompt_id": prompt_id,
                    "fan_page_id": fan_page_id,
                    "psid": facebook_page_scope_user_id,
                    "rendered_content": rendered_content,
                    "block_count": block_count,
                    "is_active": prompt.get("is_active", True),
                    "created_at": prompt.get("created_at"),
                }
        except Exception as e:
            raise self._handle_service_error(e, "getting user memory")

    # ================================================================
    # ASSIGNED PLAYBOOKS HANDLERS (READ-ONLY)
    # ================================================================

    async def get_assigned_playbooks(
        self,
        fan_page_id: str,
        conversation_type: str,
        owner_user_id: str,
    ) -> Dict[str, Any]:
        """
        Get playbooks assigned to the page for the given conversation type.

        Returns list of playbooks (id, title, situation) that can be used when
        suggesting responses for this page + type.
        """
        try:
            from src.database.postgres.connection import async_db_transaction
            from src.database.postgres.repositories import (
                get_assigned_playbook_ids,
                get_playbooks_by_ids,
            )

            page_admin_id = await self._resolve_page_id_to_page_admin_id(
                fan_page_id, owner_user_id
            )
            if not page_admin_id:
                return {"playbooks": []}

            async with async_db_transaction() as conn:
                assigned_ids = await get_assigned_playbook_ids(
                    conn, page_admin_id, conversation_type
                )
                if not assigned_ids:
                    return {"playbooks": []}
                playbooks = await get_playbooks_by_ids(conn, assigned_ids)
            return {"playbooks": playbooks}
        except Exception as e:
            raise self._handle_service_error(e, "getting assigned playbooks")

    # ================================================================
    # GENERATE SUGGESTIONS HANDLERS
    # ================================================================

    async def generate_suggestions(
        self,
        user_id: str,
        conversation_type: str,
        conversation_id: str,
        fan_page_id: str,
        facebook_page_scope_user_id: Optional[str] = None,
        trigger_type: str = "user",
        hint: Optional[str] = None,
    ) -> Dict[str, Any]:
        """
        Generate response suggestions for a conversation.

        Args:
            user_id: User ID requesting the suggestion
            conversation_type: 'messages' or 'comments'
            conversation_id: Conversation ID
            fan_page_id: Facebook page ID (passed from router to avoid duplicate fetch)
            facebook_page_scope_user_id: PSID (passed from router, only for messages)
            trigger_type: 'user' or 'auto'
            hint: Optional raw instruction text to inject into context as guidance

        Returns:
            Dict with history_id, suggestions, and suggestion_count
        """
        try:
            orchestrator = getattr(self, "_orchestrator", None)
            if not orchestrator:
                raise RuntimeError("SuggestResponseOrchestrator not initialized")
            trigger_source = "api_manual" if trigger_type == "user" else "api_auto"
            orchestrator_result = await orchestrator.trigger(
                user_id=user_id,
                conversation_type=conversation_type,
                conversation_id=conversation_id,
                fan_page_id=fan_page_id,
                trigger_source=trigger_source,
                facebook_page_scope_user_id=facebook_page_scope_user_id,
                hint=hint,
            )
            return {
                "history_id": orchestrator_result.history_id,
                "suggestions": orchestrator_result.suggestions,
                "suggestion_count": orchestrator_result.suggestion_count,
                "skipped": orchestrator_result.skipped,
                "locked": orchestrator_result.locked,
                "queued": orchestrator_result.queued,
            }

        except Exception as e:
            raise self._handle_service_error(e, "generating suggestions")

    # ================================================================
    # PAGE ADMIN CONFIG HANDLERS
    # ================================================================

    async def _resolve_page_id_to_page_admin_id(
        self, page_id: str, user_id: str
    ) -> Optional[str]:
        """Resolve page_id (Facebook page ID) to page_admin_id (facebook_page_admins.id) for current user. Returns None if user has no admin for that page."""
        from src.database.postgres.connection import async_db_transaction
        from src.database.postgres.repositories import (
            get_facebook_page_admins_by_user_id,
        )

        async with async_db_transaction() as conn:
            page_admins = await get_facebook_page_admins_by_user_id(conn, user_id)
            admin = next(
                (
                    a
                    for a in (page_admins or [])
                    if str(a.get("page_id")) == str(page_id)
                ),
                None,
            )
            return str(admin["id"]) if admin else None

    async def get_page_admin_config_by_page_id(
        self, page_id: str, user_id: str
    ) -> Optional[Dict[str, Any]]:
        """Get suggest response config by page_id (Facebook page ID). Resolves to page_admin_id then get-or-create config."""
        page_admin_id = await self._resolve_page_id_to_page_admin_id(page_id, user_id)
        if not page_admin_id:
            return None
        return await self.get_page_admin_config(page_admin_id, user_id)

    async def update_page_admin_config_by_page_id(
        self,
        page_id: str,
        user_id: str,
        settings: Optional[Dict[str, Any]] = None,
        auto_webhook_suggest: Optional[bool] = None,
        auto_webhook_graph_api: Optional[bool] = None,
        webhook_delay_seconds: Optional[int] = None,
    ) -> Dict[str, Any]:
        """Update suggest response config by page_id (Facebook page ID). Resolves to page_admin_id then upsert."""
        page_admin_id = await self._resolve_page_id_to_page_admin_id(page_id, user_id)
        if not page_admin_id:
            raise ValueError(
                "Page not found or you do not have permission for this page. Please ensure you are an admin of the page."
            )
        return await self.update_page_admin_config(
            page_admin_id=page_admin_id,
            user_id=user_id,
            settings=settings,
            auto_webhook_suggest=auto_webhook_suggest,
            auto_webhook_graph_api=auto_webhook_graph_api,
            webhook_delay_seconds=webhook_delay_seconds,
        )

    async def get_page_admin_config(
        self, page_admin_id: str, user_id: str
    ) -> Optional[Dict[str, Any]]:
        """Get page admin suggest config. Get-or-create: if no row exists, create default and return. Returns None only if user lacks permission."""
        try:
            from src.database.postgres.connection import async_db_transaction
            from src.database.postgres.repositories import (
                get_page_admin_suggest_config,
                upsert_page_admin_suggest_config,
                get_facebook_page_admins_by_user_id,
            )

            _default_settings = {
                "model": "gpt-5.2",
                "reasoning": "low",
                "verbosity": "low",
            }

            async with async_db_transaction() as conn:
                # Strict: page_admin_id must be one of this user's facebook_page_admins.id
                page_admins = await get_facebook_page_admins_by_user_id(conn, user_id)
                admin = next(
                    (
                        a
                        for a in (page_admins or [])
                        if str(a.get("id")) == str(page_admin_id)
                    ),
                    None,
                )
                if not admin:
                    return None

                config = await get_page_admin_suggest_config(conn, page_admin_id)
                if not config:
                    # Get-or-create: insert default row so FE always gets a real record
                    config = await upsert_page_admin_suggest_config(
                        conn=conn,
                        page_admin_id=page_admin_id,
                        settings=_default_settings,
                        auto_webhook_suggest=False,
                        auto_webhook_graph_api=False,
                        webhook_delay_seconds=5,
                    )
                return config
        except Exception as e:
            raise self._handle_service_error(e, "getting page admin config")

    async def update_page_admin_config(
        self,
        page_admin_id: str,
        user_id: str,
        settings: Optional[Dict[str, Any]] = None,
        auto_webhook_suggest: Optional[bool] = None,
        auto_webhook_graph_api: Optional[bool] = None,
        webhook_delay_seconds: Optional[int] = None,
    ) -> Dict[str, Any]:
        """Update page admin suggest config (upsert)."""
        try:
            from src.database.postgres.connection import async_db_transaction
            from src.database.postgres.repositories import (
                get_page_admin_suggest_config,
                upsert_page_admin_suggest_config,
                get_facebook_page_admins_by_user_id,
            )

            async with async_db_transaction() as conn:
                # Strict: page_admin_id must be one of this user's facebook_page_admins.id
                page_admins = await get_facebook_page_admins_by_user_id(conn, user_id)
                admin = next(
                    (
                        a
                        for a in (page_admins or [])
                        if str(a.get("id")) == str(page_admin_id)
                    ),
                    None,
                )
                if not admin:
                    raise ValueError(
                        "You do not have permission to update this page admin config"
                    )

                existing = await get_page_admin_suggest_config(conn, page_admin_id)
                if existing:
                    settings = settings or existing.get("settings", {})
                    auto_webhook_suggest = (
                        auto_webhook_suggest
                        if auto_webhook_suggest is not None
                        else existing.get("auto_webhook_suggest", False)
                    )
                    auto_webhook_graph_api = (
                        auto_webhook_graph_api
                        if auto_webhook_graph_api is not None
                        else existing.get("auto_webhook_graph_api", False)
                    )
                    delay = existing.get("webhook_delay_seconds", 5)
                    webhook_delay_seconds = (
                        webhook_delay_seconds
                        if webhook_delay_seconds is not None
                        else delay
                    )
                else:
                    settings = settings or {
                        "model": "gpt-5.2",
                        "reasoning": "low",
                        "verbosity": "low",
                    }
                    auto_webhook_suggest = (
                        auto_webhook_suggest
                        if auto_webhook_suggest is not None
                        else False
                    )
                    auto_webhook_graph_api = (
                        auto_webhook_graph_api
                        if auto_webhook_graph_api is not None
                        else False
                    )
                    webhook_delay_seconds = (
                        webhook_delay_seconds
                        if webhook_delay_seconds is not None
                        else 5
                    )

                return await upsert_page_admin_suggest_config(
                    conn=conn,
                    page_admin_id=page_admin_id,
                    settings=settings,
                    auto_webhook_suggest=auto_webhook_suggest,
                    auto_webhook_graph_api=auto_webhook_graph_api,
                    webhook_delay_seconds=webhook_delay_seconds,
                )
        except ValueError as e:
            raise e
        except Exception as e:
            raise self._handle_service_error(e, "updating page admin config")

    # ================================================================
    # SUGGEST RESPONSE HISTORY HANDLERS
    # ================================================================

    async def get_history_by_id(self, history_id: str) -> Dict[str, Any]:
        """Get a suggest response history record by ID."""
        try:
            result = await self.history_service.get_history_by_id(history_id)
            if not result:
                raise HTTPException(
                    status_code=404, detail=f"History record {history_id} not found"
                )
            return {"history": result}
        except HTTPException:
            raise
        except Exception as e:
            raise self._handle_service_error(e, "getting history by id")

    async def get_history_by_conversation(
        self,
        conversation_type: str,
        conversation_id: str,
        limit: int = 20,
        offset: int = 0,
    ) -> Dict[str, Any]:
        """Get suggest response history records for a specific conversation."""
        try:
            return await self.history_service.get_history_by_conversation(
                conversation_type=conversation_type,
                conversation_id=conversation_id,
                limit=limit,
                offset=offset,
            )
        except Exception as e:
            raise self._handle_service_error(e, "getting history by conversation")

    async def get_history_by_page(
        self,
        fan_page_id: str,
        user_id: str,
        conversation_type: Optional[str] = None,
        trigger_type: Optional[str] = None,
        limit: int = 20,
        offset: int = 0,
    ) -> Dict[str, Any]:
        """Get suggest response history records for a specific page."""
        try:
            return await self.history_service.get_history_by_page(
                fan_page_id=fan_page_id,
                user_id=user_id,
                conversation_type=conversation_type,
                trigger_type=trigger_type,
                limit=limit,
                offset=offset,
            )
        except Exception as e:
            raise self._handle_service_error(e, "getting history by page")

    async def get_history_with_filters(
        self,
        user_id: str,
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
    ) -> Dict[str, Any]:
        """Get suggest response history records with comprehensive filters."""
        try:
            return await self.history_service.get_history_with_filters(
                user_id=user_id,
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
        except Exception as e:
            raise self._handle_service_error(e, "getting history with filters")

    async def get_history_messages(
        self, history_id: str, user_id: str
    ) -> Optional[Dict[str, Any]]:
        """Get message items for a suggest response history record. Returns None if not found or user mismatch."""
        try:
            return await self.history_service.get_messages_by_history(
                history_id=history_id, user_id=user_id
            )
        except Exception as e:
            raise self._handle_service_error(e, "getting history messages")

    async def update_history(
        self,
        history_id: str,
        selected_suggestion_index: Optional[int] = None,
        reaction: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Update suggest response history record."""
        try:
            result = await self.history_service.update_history(
                history_id=history_id,
                selected_suggestion_index=selected_suggestion_index,
                reaction=reaction,
            )
            return {"history": result}
        except ValueError as e:
            logger.warning(f"⚠️ SUGGEST RESPONSE HANDLER: Validation error: {str(e)}")
            raise HTTPException(status_code=400, detail=str(e))
        except Exception as e:
            raise self._handle_service_error(e, "updating history")
