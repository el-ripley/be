"""Unit tests for NotificationService."""

import pytest
from unittest.mock import AsyncMock, MagicMock, patch

from src.services.notifications.notification_service import NotificationService


@pytest.fixture
def mock_socket_service() -> MagicMock:
    s = MagicMock()
    s.emit_notification = AsyncMock(return_value=None)
    return s


@pytest.fixture
def notification_service(mock_socket_service: MagicMock) -> NotificationService:
    return NotificationService(mock_socket_service)


@pytest.mark.asyncio
async def test_create_calls_insert_and_emit(notification_service: NotificationService) -> None:
    with patch(
        "src.services.notifications.notification_service.async_db_transaction"
    ) as mock_tx:
        mock_conn = AsyncMock()
        mock_tx.return_value.__aenter__ = AsyncMock(return_value=mock_conn)
        mock_tx.return_value.__aexit__ = AsyncMock(return_value=None)
        with patch(
            "src.services.notifications.notification_service.insert_notification",
            new_callable=AsyncMock,
            return_value={"id": "n1", "title": "Test", "type": "test"},
        ) as insert:
            result = await notification_service.create(
                owner_user_id="user-1",
                type="test.type",
                title="Title",
                body="Body",
            )
            insert.assert_called_once()
            notification_service.socket_service.emit_notification.assert_called_once_with(
                "user-1",
                {"id": "n1", "title": "Test", "type": "test"},
            )
            assert result["id"] == "n1"


@pytest.mark.asyncio
async def test_get_unread_count_returns_count(notification_service: NotificationService) -> None:
    with patch(
        "src.services.notifications.notification_service.async_db_transaction"
    ) as mock_tx:
        mock_conn = AsyncMock()
        mock_tx.return_value.__aenter__ = AsyncMock(return_value=mock_conn)
        mock_tx.return_value.__aexit__ = AsyncMock(return_value=None)
        with patch(
            "src.services.notifications.notification_service.count_unread_notifications",
            new_callable=AsyncMock,
            return_value=5,
        ) as count_mock:
            n = await notification_service.get_unread_count("user-1")
            assert n == 5
            count_mock.assert_called_once_with(mock_conn, "user-1")


@pytest.mark.asyncio
async def test_mark_all_read_calls_repo(notification_service: NotificationService) -> None:
    with patch(
        "src.services.notifications.notification_service.async_db_transaction"
    ) as mock_tx:
        mock_conn = AsyncMock()
        mock_tx.return_value.__aenter__ = AsyncMock(return_value=mock_conn)
        mock_tx.return_value.__aexit__ = AsyncMock(return_value=None)
        with patch(
            "src.services.notifications.notification_service.mark_all_notifications_read",
            new_callable=AsyncMock,
        ) as mark_mock:
            await notification_service.mark_all_read("user-1")
            mark_mock.assert_called_once_with(mock_conn, owner_user_id="user-1")
