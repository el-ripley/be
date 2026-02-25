"""
Trigger resolver for suggest response agent.
Resolves trigger_type, delivery_mode, and effective settings based on source and config.

delivery_mode determines what happens AFTER the agent generates suggestions:
  - 'suggest': suggestions returned to caller (socket/API) for human review
  - 'respond': first suggestion auto-delivered to customer via Graph API
"""

from dataclasses import dataclass
from typing import Any, Dict, Optional

from src.agent.common.conversation_settings import (
    get_default_settings,
    normalize_settings,
)
from src.database.postgres.repositories import (
    get_agent_settings,
    get_page_admin_suggest_config,
)


@dataclass
class TriggerResolution:
    """Result of trigger resolution."""

    trigger_type: str  # For logging/persistence: 'user', 'auto', 'webhook_suggest', 'webhook_auto_reply', 'general_agent'
    trigger_action: str  # For agent context: 'new_customer_message', 'operator_request', 'escalation_update', 'routine_check'
    delivery_mode: str  # 'suggest' or 'respond'
    settings: Dict[str, Any]
    num_suggestions: int


async def resolve_trigger_type_and_settings(
    conn: Any,
    trigger_source: str,
    user_id: str,
    page_admin_id: Optional[str],
    auto_send: bool = False,
) -> Optional[TriggerResolution]:
    """
    Resolve trigger_type, trigger_action, delivery_mode, effective_settings, and num_suggestions.

    Args:
        conn: Database connection
        trigger_source: 'api_manual', 'api_auto', 'webhook', or 'general_agent'
        user_id: Internal user ID
        page_admin_id: Required for webhook, optional for API
        auto_send: If True and general_agent, delivery_mode='respond'

    Returns:
        TriggerResolution or None if should skip
    """
    if trigger_source == "general_agent":
        agent_settings = await get_agent_settings(conn, user_id)
        if agent_settings:
            settings = normalize_settings(agent_settings.get("settings", {}))
            num_suggestions = agent_settings.get("num_suggest_response", 1)
        else:
            settings = get_default_settings()
            num_suggestions = 1
        trigger_action = "routine_check"
        return TriggerResolution(
            trigger_type="general_agent",
            trigger_action=trigger_action,
            delivery_mode="respond" if auto_send else "suggest",
            settings=settings,
            num_suggestions=num_suggestions,
        )

    if trigger_source in ("api_manual", "api_auto"):
        agent_settings = await get_agent_settings(conn, user_id)
        if agent_settings:
            settings = normalize_settings(agent_settings.get("settings", {}))
            num_suggestions = agent_settings.get("num_suggest_response", 3)
        else:
            settings = get_default_settings()
            num_suggestions = 3

        trigger_type = "user" if trigger_source == "api_manual" else "auto"
        trigger_action = "operator_request" if trigger_source == "api_manual" else "routine_check"
        return TriggerResolution(
            trigger_type=trigger_type,
            trigger_action=trigger_action,
            delivery_mode="suggest",
            settings=settings,
            num_suggestions=num_suggestions,
        )

    if trigger_source == "webhook":
        if not page_admin_id:
            return None

        page_config = await get_page_admin_suggest_config(conn, page_admin_id)
        if not page_config:
            return None

        # Priority: auto_webhook_graph_api > auto_webhook_suggest
        if page_config.get("auto_webhook_graph_api"):
            trigger_type = "webhook_auto_reply"
            delivery_mode = "respond"
        elif page_config.get("auto_webhook_suggest"):
            trigger_type = "webhook_suggest"
            delivery_mode = "suggest"
        else:
            return None

        settings = normalize_settings(page_config.get("settings") or {})
        num_suggestions = 1
        trigger_action = "new_customer_message"  # Webhooks fire on new messages
        return TriggerResolution(
            trigger_type=trigger_type,
            trigger_action=trigger_action,
            delivery_mode=delivery_mode,
            settings=settings,
            num_suggestions=num_suggestions,
        )

    return None
