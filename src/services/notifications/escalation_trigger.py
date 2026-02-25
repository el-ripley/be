"""
Escalation notification trigger: when suggest_response_agent writes to
agent_escalations or agent_escalation_messages, create in-app notifications.
Uses simple SQL string matching to detect writes; NotificationService is generic.
"""

from typing import Any, Dict, List, Optional

from src.database.postgres.connection import async_db_transaction
from src.database.postgres.executor import execute_async_single
from src.database.postgres.repositories import get_escalation_by_id, get_escalations_with_filters
from src.services.notifications.notification_service import NotificationService
from src.utils.logger import get_logger

logger = get_logger()

# Notification types for escalation events
TYPE_ESCALATION_CREATED = "escalation.created"
TYPE_ESCALATION_NEW_MESSAGE = "escalation.new_message"
TYPE_ESCALATION_CLOSED = "escalation.closed"

REFERENCE_TYPE_ESCALATION = "agent_escalation"

# Table names (case-insensitive match)
TABLE_ESCALATIONS = "agent_escalations"
TABLE_MESSAGES = "agent_escalation_messages"


def _sql_touches_escalations(sql_statements: List[str]) -> bool:
    """True if any statement references agent_escalations or agent_escalation_messages."""
    combined = " ".join(sql_statements).lower()
    return TABLE_ESCALATIONS.lower() in combined or TABLE_MESSAGES.lower() in combined


def _detect_operation_types(sql_statements: List[str]) -> List[str]:
    """
    Determine which notification types to emit based on SQL content.
    Returns a list of types, e.g. ["escalation.created"] or ["escalation.new_message"].
    """
    types: List[str] = []
    for sql in sql_statements:
        sql_lower = sql.lower()
        if TABLE_ESCALATIONS.lower() not in sql_lower and TABLE_MESSAGES.lower() not in sql_lower:
            continue
        if "insert" in sql_lower and TABLE_ESCALATIONS.lower() in sql_lower:
            types.append(TYPE_ESCALATION_CREATED)
        if "update" in sql_lower and TABLE_ESCALATIONS.lower() in sql_lower and "closed" in sql_lower:
            types.append(TYPE_ESCALATION_CLOSED)
        if "insert" in sql_lower and TABLE_MESSAGES.lower() in sql_lower:
            types.append(TYPE_ESCALATION_NEW_MESSAGE)
    # Deduplicate while preserving order
    seen = set()
    unique = []
    for t in types:
        if t not in seen:
            seen.add(t)
            unique.append(t)
    # Suppress new_message when created is present (first message is part of creation)
    if TYPE_ESCALATION_CREATED in unique and TYPE_ESCALATION_NEW_MESSAGE in unique:
        unique.remove(TYPE_ESCALATION_NEW_MESSAGE)
    return unique


async def _get_latest_escalation_for_conversation(
    owner_user_id: str,
    conversation_type: str,
    conversation_id: str,
    fan_page_id: str,
    order_by_updated: bool = False,
) -> Optional[Dict[str, Any]]:
    """Get the most recent escalation for this conversation (main pool)."""
    msg_id = conversation_id if conversation_type == "messages" else None
    comment_id = conversation_id if conversation_type == "comments" else None
    async with async_db_transaction() as conn:
        if order_by_updated:
            if conversation_type == "messages":
                q = """
                    SELECT id, subject, priority, status, updated_at
                    FROM agent_escalations
                    WHERE owner_user_id = $1 AND fan_page_id = $2
                      AND conversation_type = 'messages'
                      AND facebook_conversation_messages_id = $3
                      AND status = 'closed'
                    ORDER BY updated_at DESC
                    LIMIT 1
                """
                row = await execute_async_single(
                    conn, q, owner_user_id, fan_page_id, conversation_id
                )
            else:
                q = """
                    SELECT id, subject, priority, status, updated_at
                    FROM agent_escalations
                    WHERE owner_user_id = $1 AND fan_page_id = $2
                      AND conversation_type = 'comments'
                      AND facebook_conversation_comments_id = $3::uuid
                      AND status = 'closed'
                    ORDER BY updated_at DESC
                    LIMIT 1
                """
                row = await execute_async_single(
                    conn, q, owner_user_id, fan_page_id, conversation_id
                )
            if row:
                full = await get_escalation_by_id(conn, str(row["id"]))
                return full
            return None
        items = await get_escalations_with_filters(
            conn,
            owner_user_id=owner_user_id,
            conversation_type=conversation_type,
            fan_page_id=fan_page_id,
            facebook_conversation_messages_id=msg_id,
            facebook_conversation_comments_id=comment_id,
            limit=1,
            offset=0,
        )
        return items[0] if items else None


async def _get_escalation_with_latest_message(
    owner_user_id: str,
    conversation_type: str,
    conversation_id: str,
    fan_page_id: str,
) -> Optional[Dict[str, Any]]:
    """Get the escalation that has the most recently created message (for new_message notification)."""
    msg_id = conversation_id if conversation_type == "messages" else None
    comment_id = conversation_id if conversation_type == "comments" else None
    async with async_db_transaction() as conn:
        if conversation_type == "messages":
            q = """
                SELECT e.id, e.subject, e.priority
                FROM agent_escalations e
                INNER JOIN agent_escalation_messages m ON m.escalation_id = e.id
                WHERE e.owner_user_id = $1 AND e.fan_page_id = $2
                  AND e.conversation_type = 'messages'
                  AND e.facebook_conversation_messages_id = $3
                ORDER BY m.created_at DESC
                LIMIT 1
            """
            row = await execute_async_single(conn, q, owner_user_id, fan_page_id, conversation_id)
        else:
            q = """
                SELECT e.id, e.subject, e.priority
                FROM agent_escalations e
                INNER JOIN agent_escalation_messages m ON m.escalation_id = e.id
                WHERE e.owner_user_id = $1 AND e.fan_page_id = $2
                  AND e.conversation_type = 'comments'
                  AND e.facebook_conversation_comments_id = $3::uuid
                ORDER BY m.created_at DESC
                LIMIT 1
            """
            row = await execute_async_single(conn, q, owner_user_id, fan_page_id, conversation_id)
        if not row:
            return None
        return await get_escalation_by_id(conn, str(row["id"]))


class EscalationNotificationTrigger:
    """Creates notifications when suggest_response_agent writes to escalation tables."""

    def __init__(self, notification_service: NotificationService):
        self.notification_service = notification_service

    async def check_and_notify(
        self,
        user_id: str,
        conversation_type: str,
        conversation_id: str,
        fan_page_id: str,
        sql_statements: List[str],
        raw_result: Dict[str, Any],
    ) -> None:
        """
        Called after a successful sql_query write by suggest_response_agent.
        If the SQL touched agent_escalations or agent_escalation_messages, create notifications.
        """
        if not sql_statements or not _sql_touches_escalations(sql_statements):
            return
        op_types = _detect_operation_types(sql_statements)
        if not op_types:
            return

        for ntype in op_types:
            try:
                if ntype == TYPE_ESCALATION_CREATED:
                    esc = await _get_latest_escalation_for_conversation(
                        user_id, conversation_type, conversation_id, fan_page_id
                    )
                    if not esc:
                        continue
                    title = f"New escalation: {esc.get('subject', '')[:80]}"
                    body = None
                    metadata = {
                        "fan_page_id": fan_page_id,
                        "conversation_type": conversation_type,
                        "conversation_id": conversation_id,
                        "priority": esc.get("priority"),
                        "subject": esc.get("subject"),
                    }
                    await self.notification_service.create(
                        owner_user_id=user_id,
                        type=ntype,
                        title=title,
                        body=body,
                        reference_type=REFERENCE_TYPE_ESCALATION,
                        reference_id=str(esc["id"]),
                        metadata=metadata,
                    )
                elif ntype == TYPE_ESCALATION_CLOSED:
                    esc = await _get_latest_escalation_for_conversation(
                        user_id,
                        conversation_type,
                        conversation_id,
                        fan_page_id,
                        order_by_updated=True,
                    )
                    if not esc:
                        continue
                    title = f"Escalation closed: {esc.get('subject', '')[:80]}"
                    await self.notification_service.create(
                        owner_user_id=user_id,
                        type=ntype,
                        title=title,
                        body=None,
                        reference_type=REFERENCE_TYPE_ESCALATION,
                        reference_id=str(esc["id"]),
                        metadata={
                            "fan_page_id": fan_page_id,
                            "conversation_type": conversation_type,
                            "conversation_id": conversation_id,
                            "subject": esc.get("subject"),
                        },
                    )
                elif ntype == TYPE_ESCALATION_NEW_MESSAGE:
                    esc = await _get_escalation_with_latest_message(
                        user_id, conversation_type, conversation_id, fan_page_id
                    )
                    if not esc:
                        continue
                    title = f"New message in: {esc.get('subject', '')[:80]}"
                    await self.notification_service.create(
                        owner_user_id=user_id,
                        type=ntype,
                        title=title,
                        body=None,
                        reference_type=REFERENCE_TYPE_ESCALATION,
                        reference_id=str(esc["id"]),
                        metadata={
                            "fan_page_id": fan_page_id,
                            "conversation_type": conversation_type,
                            "conversation_id": conversation_id,
                            "subject": esc.get("subject"),
                        },
                    )
            except Exception as e:
                logger.warning(
                    "Escalation notification trigger failed for type %s: %s",
                    ntype,
                    e,
                    exc_info=True,
                )
