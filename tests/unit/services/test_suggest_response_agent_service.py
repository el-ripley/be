"""Unit tests for SuggestResponseAgentService (_format_agent_record and validation)."""

import pytest
from unittest.mock import patch, AsyncMock

from src.services.suggest_response.suggest_response_agent_service import (
    SuggestResponseAgentService,
)


def test_format_agent_record_none_returns_defaults() -> None:
    result = SuggestResponseAgentService._format_agent_record(None, "user-1")
    assert result["user_id"] == "user-1"
    assert "settings" in result
    assert result["allow_auto_suggest"] is False
    assert result["num_suggest_response"] == 3


def test_format_agent_record_with_record() -> None:
    record = {
        "id": "id-1",
        "user_id": "user-1",
        "settings": {"context_token_limit": 10000},
        "allow_auto_suggest": True,
        "num_suggest_response": 5,
    }
    result = SuggestResponseAgentService._format_agent_record(record, "user-1")
    assert result["user_id"] == "user-1"
    assert result["settings"]["context_token_limit"] == 10000
    assert result["allow_auto_suggest"] is True
    assert result["num_suggest_response"] == 5
    assert result["id"] == "id-1"


def test_format_agent_record_missing_user_id_uses_param() -> None:
    record = {"id": "id-1", "settings": {}, "allow_auto_suggest": False, "num_suggest_response": 3}
    result = SuggestResponseAgentService._format_agent_record(record, "user-99")
    assert result["user_id"] == "user-99"


@pytest.mark.asyncio
async def test_update_settings_num_suggest_response_below_one_raises() -> None:
    svc = SuggestResponseAgentService()
    with patch.object(svc, "get_settings", new_callable=AsyncMock, return_value={
        "user_id": "u1", "settings": {}, "allow_auto_suggest": False, "num_suggest_response": 3
    }):
        with pytest.raises(ValueError, match="at least 1"):
            await svc.update_settings("u1", num_suggest_response=0)


@pytest.mark.asyncio
async def test_update_settings_num_suggest_response_above_10_raises() -> None:
    svc = SuggestResponseAgentService()
    with patch.object(svc, "get_settings", new_callable=AsyncMock, return_value={
        "user_id": "u1", "settings": {}, "allow_auto_suggest": False, "num_suggest_response": 3
    }):
        with pytest.raises(ValueError, match="cannot exceed 10"):
            await svc.update_settings("u1", num_suggest_response=11)
