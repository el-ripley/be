"""Tool definitions for the playbook selection agent."""

from typing import Any, Dict, List

from src.agent.suggest_response.playbook.tools.search_playbooks_tool import (
    SearchPlaybooksTool,
)
from src.agent.suggest_response.playbook.tools.select_playbooks_tool import (
    SelectPlaybooksTool,
)

_search_tool = SearchPlaybooksTool()
_select_tool = SelectPlaybooksTool()


def get_playbook_tool_definitions() -> List[Dict[str, Any]]:
    """Return tool definitions for the playbook selection agent (OpenAI Responses API format)."""
    return [_search_tool.definition, _select_tool.definition]


def get_playbook_tool_definitions_select_only() -> List[Dict[str, Any]]:
    """Return only select_playbooks tool (used after MAX_SEARCHES reached)."""
    return [_select_tool.definition]
