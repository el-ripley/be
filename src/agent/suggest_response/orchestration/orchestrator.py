"""
Orchestrator for suggest response agent.
Handles lock/queue/hash management and delegates to runner for agent logic.
"""

import asyncio
import uuid
from dataclasses import dataclass, field
from typing import Any, Dict, List, Literal, Optional

from src.agent.suggest_response.core.runner import InsufficientBalanceError
from src.agent.suggest_response.orchestration.condition_checker import (
    check_trigger_conditions,
)
from src.agent.suggest_response.orchestration.graph_api_delivery import (
    deliver_via_graph_api,
)
from src.agent.suggest_response.orchestration.trigger_resolver import (
    resolve_trigger_type_and_settings,
)
from src.database.postgres.connection import async_db_transaction
from src.database.postgres.repositories import is_conversation_blocked
from src.utils.logger import get_logger

logger = get_logger()


@dataclass
class SuggestResponseResult:
    """Result from suggest response trigger."""

    history_id: Optional[str] = None
    suggestions: List[Dict[str, Any]] = field(default_factory=list)
    suggestion_count: int = 0
    skipped: bool = False
    locked: bool = False
    queued: bool = False
    debounced: bool = False
    skip_reason: Optional[str] = None
    playbook_system_reminder: Optional[str] = None


class SuggestResponseOrchestrator:
    """Orchestrates suggest response agent execution with lock/queue/hash management."""

    def __init__(
        self,
        runner: Any,
        suggest_response_cache: Any,
        session_manager: Any,
        comment_conversation_service: Optional[Any] = None,
        socket_service: Optional[Any] = None,
        page_service: Optional[Any] = None,
    ):
        self.runner = runner
        self.cache = suggest_response_cache
        self.session_manager = session_manager
        self.comment_conversation_service = comment_conversation_service
        self.socket_service = socket_service
        self.page_service = page_service

    async def trigger(
        self,
        user_id: str,
        conversation_type: str,
        conversation_id: str,
        fan_page_id: str,
        trigger_source: Literal["api_manual", "api_auto", "webhook", "general_agent"],
        page_admin_id: Optional[str] = None,
        page_admin: Optional[Dict[str, Any]] = None,
        facebook_page_scope_user_id: Optional[str] = None,
        webhook_delay_seconds: Optional[int] = None,
        auto_send: bool = False,
        hint: Optional[str] = None,
    ) -> SuggestResponseResult:
        """
        Trigger suggest response agent with full orchestration flow.

        Args:
            user_id: Internal user ID
            conversation_type: 'messages' or 'comments'
            conversation_id: Facebook conversation ID
            fan_page_id: Facebook page ID
            trigger_source: 'api_manual', 'api_auto', 'webhook', or 'general_agent'
            page_admin_id: Required for webhook
            page_admin: Page admin dict with access_token (for webhook/Graph API delivery)
            facebook_page_scope_user_id: PSID (for messages)
            auto_send: If True, deliver first suggestion via Graph API (general_agent only)
            hint: Optional raw instruction text to inject into suggest_response context (api/general_agent)

        Returns:
            SuggestResponseResult with history_id, suggestions, or skip/lock/queued flags
        """
        try:
            # 0. Check conversation block (guard)
            async with async_db_transaction() as conn:
                if await is_conversation_blocked(
                    conn, conversation_type, conversation_id, fan_page_id
                ):
                    return SuggestResponseResult(
                        skipped=True, skip_reason="conversation_blocked"
                    )

            # 1. Balance check (for all trigger types)
            from src.billing.credit_service import can_use_ai, get_balance
            from src.billing.repositories import billing_queries
            from src.database.postgres import get_async_connection

            async with get_async_connection() as conn:
                if not await can_use_ai(conn, user_id):
                    balance = await get_balance(conn, user_id)
                    min_balance_usd = await billing_queries.get_billing_setting(
                        conn, "min_balance_usd"
                    )
                    error_msg = (
                        f"Insufficient balance. Current balance: ${balance:.4f}. "
                        f"Minimum required: ${min_balance_usd:.4f}."
                    )
                    raise InsufficientBalanceError(error_msg)

            # 2. Resolve trigger_type, trigger_action, and settings
            async with async_db_transaction() as conn:
                resolved = await resolve_trigger_type_and_settings(
                    conn,
                    trigger_source,
                    user_id,
                    page_admin_id,
                    auto_send=auto_send,
                )

            if not resolved:
                return SuggestResponseResult(
                    skipped=True, skip_reason="config_disabled"
                )

            trigger_type = resolved.trigger_type
            trigger_action = resolved.trigger_action
            delivery_mode = resolved.delivery_mode
            effective_settings = resolved.settings
            num_suggestions = resolved.num_suggestions

            # 2. Check trigger conditions (e.g., admin online for webhook_suggest)
            should_proceed, skip_reason = await check_trigger_conditions(
                trigger_type, delivery_mode, user_id, self.session_manager
            )
            if not should_proceed:
                return SuggestResponseResult(skipped=True, skip_reason=skip_reason)

            # 2b. Debounce for webhook triggers
            if (
                trigger_source == "webhook"
                and webhook_delay_seconds is not None
                and webhook_delay_seconds > 0
                and self.cache
            ):
                return await self._handle_debounce(
                    user_id=user_id,
                    conversation_type=conversation_type,
                    conversation_id=conversation_id,
                    fan_page_id=fan_page_id,
                    trigger_type=trigger_type,
                    trigger_action=trigger_action,
                    delivery_mode=delivery_mode,
                    effective_settings=effective_settings,
                    num_suggestions=num_suggestions,
                    page_admin_id=page_admin_id,
                    page_admin=page_admin,
                    facebook_page_scope_user_id=facebook_page_scope_user_id,
                    delay_seconds=webhook_delay_seconds,
                    hint=hint,
                )

            # 3. Lock / run agent / delivery (shared path for API, webhook, general_agent)
            return await self._execute_trigger_internal(
                user_id=user_id,
                conversation_type=conversation_type,
                conversation_id=conversation_id,
                fan_page_id=fan_page_id,
                trigger_type=trigger_type,
                trigger_action=trigger_action,
                delivery_mode=delivery_mode,
                effective_settings=effective_settings,
                num_suggestions=num_suggestions,
                page_admin_id=page_admin_id,
                page_admin=page_admin,
                facebook_page_scope_user_id=facebook_page_scope_user_id,
                trigger_source=trigger_source,
                webhook_delay_seconds=webhook_delay_seconds,
                hint=hint,
            )

        except InsufficientBalanceError:
            raise
        except Exception as e:
            logger.error(f"Suggest response orchestrator error: {e}")
            raise

    async def _run_agent(
        self,
        user_id: str,
        conversation_type: str,
        conversation_id: str,
        fan_page_id: str,
        trigger_type: str,
        trigger_action: str,
        num_suggestions: int,
        effective_settings: Dict[str, Any],
        facebook_page_scope_user_id: Optional[str],
        delivery_mode: str = "suggest",
        hint: Optional[str] = None,
    ) -> Dict[str, Any]:
        """
        Run agent - delegates to runner.run() with pure agent logic.
        Hash check: bypass for manual (user) and general_agent, check for auto/webhook.
        """
        return await self.runner.run(
            user_id=user_id,
            conversation_type=conversation_type,
            conversation_id=conversation_id,
            fan_page_id=fan_page_id,
            trigger_type=trigger_type,
            trigger_action=trigger_action,
            num_suggestions=num_suggestions,
            settings=effective_settings,
            facebook_page_scope_user_id=facebook_page_scope_user_id,
            bypass_hash_check=(trigger_type in ("user", "general_agent")),
            suggest_response_cache=self.cache,
            delivery_mode=delivery_mode,
            hint=hint,
        )

    async def _handle_debounce(
        self,
        *,
        user_id: str,
        conversation_type: str,
        conversation_id: str,
        fan_page_id: str,
        trigger_type: str,
        trigger_action: str,
        delivery_mode: str,
        effective_settings: Dict[str, Any],
        num_suggestions: int,
        page_admin_id: Optional[str],
        page_admin: Optional[Dict[str, Any]],
        facebook_page_scope_user_id: Optional[str],
        delay_seconds: int,
        hint: Optional[str] = None,
    ) -> SuggestResponseResult:
        """Set debounce marker and schedule delayed execution."""
        marker_id = str(uuid.uuid4())
        ttl_seconds = delay_seconds + 5  # Buffer so marker still exists when task runs
        await self.cache.set_debounce_marker(
            user_id, conversation_type, conversation_id, marker_id, ttl_seconds
        )
        asyncio.create_task(
            self._execute_after_debounce(
                expected_marker=marker_id,
                delay_seconds=delay_seconds,
                user_id=user_id,
                conversation_type=conversation_type,
                conversation_id=conversation_id,
                fan_page_id=fan_page_id,
                trigger_type=trigger_type,
                trigger_action=trigger_action,
                delivery_mode=delivery_mode,
                effective_settings=effective_settings,
                num_suggestions=num_suggestions,
                page_admin_id=page_admin_id,
                page_admin=page_admin,
                facebook_page_scope_user_id=facebook_page_scope_user_id,
                hint=hint,
            )
        )
        return SuggestResponseResult(debounced=True)

    async def _execute_after_debounce(
        self,
        *,
        expected_marker: str,
        delay_seconds: int,
        user_id: str,
        conversation_type: str,
        conversation_id: str,
        fan_page_id: str,
        trigger_type: str,
        trigger_action: str,
        delivery_mode: str,
        effective_settings: Dict[str, Any],
        num_suggestions: int,
        page_admin_id: Optional[str],
        page_admin: Optional[Dict[str, Any]],
        facebook_page_scope_user_id: Optional[str],
        hint: Optional[str] = None,
    ) -> None:
        """Wait for delay, check marker, then execute if still valid."""
        await asyncio.sleep(delay_seconds)
        current_marker = await self.cache.get_debounce_marker(
            user_id, conversation_type, conversation_id
        )
        if current_marker != expected_marker:
            return  # Superseded by newer event
        try:
            await self._execute_trigger_internal(
                user_id=user_id,
                conversation_type=conversation_type,
                conversation_id=conversation_id,
                fan_page_id=fan_page_id,
                trigger_type=trigger_type,
                trigger_action=trigger_action,
                delivery_mode=delivery_mode,
                effective_settings=effective_settings,
                num_suggestions=num_suggestions,
                page_admin_id=page_admin_id,
                page_admin=page_admin,
                facebook_page_scope_user_id=facebook_page_scope_user_id,
                trigger_source="webhook",
                webhook_delay_seconds=None,  # Internal path: no debounce
                hint=hint,
            )
        except Exception as e:
            logger.error(
                f"Suggest response debounce task failed: {e}",
                exc_info=True,
            )

    async def _execute_trigger_internal(
        self,
        *,
        user_id: str,
        conversation_type: str,
        conversation_id: str,
        fan_page_id: str,
        trigger_type: str,
        trigger_action: str,
        delivery_mode: str,
        effective_settings: Dict[str, Any],
        num_suggestions: int,
        page_admin_id: Optional[str],
        page_admin: Optional[Dict[str, Any]],
        facebook_page_scope_user_id: Optional[str],
        trigger_source: Literal["api_manual", "api_auto", "webhook", "general_agent"],
        webhook_delay_seconds: Optional[int],
        hint: Optional[str] = None,
    ) -> SuggestResponseResult:
        """
        Execute trigger without debounce (lock/queue/agent/delivery).
        Used by trigger() for immediate path and by debounce delayed task.
        """
        lock_acquired = False
        if self.cache:
            lock_acquired = await self.cache.acquire_lock(
                user_id, conversation_type, conversation_id
            )
            if not lock_acquired:
                if trigger_source == "webhook":
                    request_data = {
                        "user_id": user_id,
                        "conversation_type": conversation_type,
                        "conversation_id": conversation_id,
                        "fan_page_id": fan_page_id,
                        "trigger_source": trigger_source,
                        "page_admin_id": page_admin_id,
                        "page_admin": page_admin,
                        "facebook_page_scope_user_id": facebook_page_scope_user_id,
                        "webhook_delay_seconds": webhook_delay_seconds,
                        "hint": hint,
                    }
                    await self.cache.enqueue_webhook_request(
                        user_id, conversation_type, conversation_id, request_data
                    )
                    return SuggestResponseResult(queued=True)
                return SuggestResponseResult(locked=True)

        try:
            result = await self._run_agent(
                user_id=user_id,
                conversation_type=conversation_type,
                conversation_id=conversation_id,
                fan_page_id=fan_page_id,
                trigger_type=trigger_type,
                trigger_action=trigger_action,
                num_suggestions=num_suggestions,
                effective_settings=effective_settings,
                facebook_page_scope_user_id=facebook_page_scope_user_id,
                delivery_mode=delivery_mode,
                hint=hint,
            )
            if result.get("skipped"):
                return SuggestResponseResult(
                    skipped=True,
                    skip_reason=result.get("skip_reason", "hash_unchanged"),
                )
            # Auto-deliver via Graph API when delivery_mode is 'respond'
            if delivery_mode == "respond" and page_admin and result.get("suggestions"):
                suggestions = result.get("suggestions", [])
                if suggestions:
                    await deliver_via_graph_api(
                        conversation_type=conversation_type,
                        conversation_id=conversation_id,
                        page_admin=page_admin,
                        suggestion=suggestions[0],
                        max_retries=3,
                        history_id=result.get("history_id"),
                        comment_conversation_service=self.comment_conversation_service,
                        socket_service=self.socket_service,
                        page_service=self.page_service,
                    )
            if lock_acquired and self.cache:
                self._process_queue_background(
                    user_id, conversation_type, conversation_id
                )
            return SuggestResponseResult(
                history_id=result.get("history_id"),
                suggestions=result.get("suggestions", []),
                suggestion_count=result.get("suggestion_count", 0),
                skipped=False,
                locked=False,
                playbook_system_reminder=result.get("playbook_system_reminder"),
            )
        finally:
            if lock_acquired and self.cache:
                await self.cache.release_lock(
                    user_id, conversation_type, conversation_id
                )

    def _process_queue_background(
        self,
        user_id: str,
        conversation_type: str,
        conversation_id: str,
    ) -> None:
        """Process next queued request in background (fire and forget)."""
        import asyncio

        async def process_next():
            if not self.cache:
                return
            request_data = await self.cache.dequeue_webhook_request(
                user_id, conversation_type, conversation_id
            )
            if request_data:
                try:
                    await self.trigger(
                        user_id=request_data.get("user_id"),
                        conversation_type=request_data.get("conversation_type"),
                        conversation_id=request_data.get("conversation_id"),
                        fan_page_id=request_data.get("fan_page_id"),
                        trigger_source="webhook",
                        page_admin_id=request_data.get("page_admin_id"),
                        page_admin=request_data.get("page_admin"),
                        facebook_page_scope_user_id=request_data.get(
                            "facebook_page_scope_user_id"
                        ),
                        webhook_delay_seconds=0,  # Already debounced; execute immediately
                        hint=request_data.get("hint"),
                    )
                except Exception as e:
                    logger.error(f"Error processing queued suggest response: {e}")

        asyncio.create_task(process_next())
