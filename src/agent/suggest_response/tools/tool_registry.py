"""Tool registry for suggest_response_agent with custom descriptions.

Each conversation_type has its own explicit tool set. The LLM only sees
tools registered for its conversation_type — it cannot call tools that
are not in its definition list.
"""

from typing import Any, Dict, List, Optional

from src.agent.suggest_response.tools.complete_task import (
    CompleteTaskTool,
    build_complete_task_definition,
)
from src.agent.suggest_response.tools.generate_suggestions import (
    GenerateSuggestionsTool,
    build_generate_suggestions_definition,
)
from src.agent.suggest_response.tools.override_descriptions import (
    SR_SQL_QUERY_COMMENTS_DESCRIPTION,
    SR_SQL_QUERY_MESSAGES_DESCRIPTION,
    SR_VIEW_MEDIA_DESCRIPTION,
)
from src.agent.tools.base import BaseTool
from src.agent.tools.manage_media import ChangeMediaRetentionTool, ViewMediaTool
from src.agent.tools.sql_query import SqlQueryTool


class SuggestResponseToolRegistry:
    """Registry for suggest_response_agent tools, scoped per conversation_type.

    Tool sets:
      messages: sql_query, view_media, change_media_retention, generate_suggestions
      comments: sql_query, view_media, generate_suggestions
    """

    def __init__(self) -> None:
        view_media = ViewMediaTool(description_override=SR_VIEW_MEDIA_DESCRIPTION)

        self._tools_by_type: Dict[str, Dict[str, BaseTool]] = {
            "messages": {
                "sql_query": SqlQueryTool(
                    description_override=SR_SQL_QUERY_MESSAGES_DESCRIPTION
                ),
                "view_media": view_media,
                "change_media_retention": ChangeMediaRetentionTool(),
            },
            "comments": {
                "sql_query": SqlQueryTool(
                    description_override=SR_SQL_QUERY_COMMENTS_DESCRIPTION
                ),
                "view_media": view_media,
            },
        }

    def get(
        self,
        name: str,
        conversation_type: Optional[str] = None,
        num_suggestions: Optional[int] = None,
    ) -> Optional[BaseTool]:
        """Get a tool by name for the given conversation_type."""
        if (
            name == "generate_suggestions"
            and conversation_type is not None
            and num_suggestions is not None
        ):
            return GenerateSuggestionsTool(conversation_type, num_suggestions)
        if name == "complete_task":
            return CompleteTaskTool()
        tools = self._tools_by_type.get(conversation_type or "", {})
        return tools.get(name)

    def get_tool_definitions(
        self,
        conversation_type: str,
        num_suggestions: int,
    ) -> List[Dict[str, Any]]:
        """Get all tool definitions for the OpenAI API.

        Only tools registered for this conversation_type are included,
        so the LLM cannot call tools outside its scope.
        Both terminal tools (generate_suggestions, complete_task) are always available.

        Args:
            conversation_type: 'messages' or 'comments'
            num_suggestions: Number of suggestions for generate_suggestions
        """
        tools = self._tools_by_type.get(conversation_type, {})
        definitions = [tool.definition for tool in tools.values()]
        definitions.append(
            build_generate_suggestions_definition(conversation_type, num_suggestions)
        )
        definitions.append(build_complete_task_definition())
        return definitions
