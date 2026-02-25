"""
Condition checker for suggest response agent.
Validates trigger conditions (e.g., admin online for suggest delivery_mode via webhook).
"""

from typing import Any, Optional, Tuple


async def check_trigger_conditions(
    trigger_type: str,
    delivery_mode: str,
    user_id: str,
    session_manager: Any,
) -> Tuple[bool, Optional[str]]:
    """
    Check if trigger should proceed based on conditions.

    Args:
        trigger_type: 'user', 'auto', 'webhook_suggest', 'webhook_auto_reply', or 'general_agent'
        delivery_mode: 'suggest' or 'respond'
        user_id: Internal user ID (admin)
        session_manager: RedisUserSessions instance for online check

    Returns:
        Tuple of (should_proceed, skip_reason)
        - webhook_suggest (delivery_mode='suggest'): requires admin online
        - All other cases: always proceed
    """
    # webhook_suggest needs admin online to receive suggestion via socket
    if trigger_type == "webhook_suggest" and delivery_mode == "suggest":
        is_online = await session_manager.is_user_online(user_id)
        if not is_online:
            return (False, "no_admin_online")

    return (True, None)
