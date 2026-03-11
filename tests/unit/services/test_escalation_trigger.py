"""Unit tests for EscalationNotificationTrigger (pure helpers and check_and_notify)."""

import pytest
from unittest.mock import AsyncMock, patch

from src.services.notifications.escalation_trigger import (
    _sql_touches_escalations,
    _detect_operation_types,
    EscalationNotificationTrigger,
    TYPE_ESCALATION_CREATED,
    TYPE_ESCALATION_NEW_MESSAGE,
    TYPE_ESCALATION_CLOSED,
)


def test_sql_touches_escalations_true() -> None:
    assert _sql_touches_escalations(["INSERT INTO agent_escalations (id) VALUES (1)"]) is True
    assert _sql_touches_escalations(["SELECT * FROM agent_escalation_messages"]) is True
    assert _sql_touches_escalations(["UPDATE agent_escalations SET status = 'closed'"]) is True


def test_sql_touches_escalations_false() -> None:
    assert _sql_touches_escalations(["SELECT * FROM fan_pages"]) is False
    assert _sql_touches_escalations(["INSERT INTO posts (id) VALUES (1)"]) is False


def test_detect_operation_types_insert_escalations() -> None:
    types = _detect_operation_types(["INSERT INTO agent_escalations (id) VALUES (1)"])
    assert TYPE_ESCALATION_CREATED in types


def test_detect_operation_types_insert_messages() -> None:
    types = _detect_operation_types(["INSERT INTO agent_escalation_messages (id) VALUES (1)"])
    assert TYPE_ESCALATION_NEW_MESSAGE in types


def test_detect_operation_types_update_closed() -> None:
    types = _detect_operation_types(
        ["UPDATE agent_escalations SET status = 'closed' WHERE id = 1"]
    )
    assert TYPE_ESCALATION_CLOSED in types


def test_detect_operation_types_dedupe() -> None:
    types = _detect_operation_types([
        "INSERT INTO agent_escalations (id) VALUES (1)",
        "INSERT INTO agent_escalations (id) VALUES (2)",
    ])
    assert types.count(TYPE_ESCALATION_CREATED) == 1


@pytest.mark.asyncio
async def test_check_and_notify_no_escalation_sql_does_not_call_create() -> None:
    mock_notification = AsyncMock()
    trigger = EscalationNotificationTrigger(mock_notification)
    await trigger.check_and_notify(
        user_id="u1",
        conversation_type="comments",
        conversation_id="c1",
        fan_page_id="p1",
        sql_statements=["SELECT * FROM fan_pages"],
        raw_result=[],
    )
    mock_notification.create.assert_not_called()


@pytest.mark.asyncio
async def test_check_and_notify_with_escalation_sql_calls_create() -> None:
    mock_notification = AsyncMock()
    trigger = EscalationNotificationTrigger(mock_notification)
    with patch(
        "src.services.notifications.escalation_trigger._get_latest_escalation_for_conversation",
        new_callable=AsyncMock,
        return_value={"id": "e1", "subject": "Test"},
    ):
        await trigger.check_and_notify(
            user_id="u1",
            conversation_type="comments",
            conversation_id="c1",
            fan_page_id="p1",
            sql_statements=["INSERT INTO agent_escalations (id) VALUES ('e1')"],
            raw_result=[],
        )
    mock_notification.create.assert_called()
    call_kw = mock_notification.create.call_args[1]
    assert call_kw["owner_user_id"] == "u1"
    assert call_kw["reference_type"] == "agent_escalation"
