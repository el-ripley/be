"""Tool registry for agent tools."""

from typing import Any, Dict, List

from src.agent.tools.base import BaseTool


class ToolRegistry:
    """Registry for agent tools."""

    def __init__(self):
        self._tools: Dict[str, BaseTool] = {}

    def register(self, tool: BaseTool) -> None:
        """Register a tool."""
        self._tools[tool.name] = tool

    def get(self, name: str) -> BaseTool | None:
        """Get a tool by name."""
        return self._tools.get(name)

    def get_all_definitions(self) -> List[Dict[str, Any]]:
        """Get all tool definitions for OpenAI API."""
        return [tool.definition for tool in self._tools.values()]


def create_default_registry(
    sync_job_manager=None,
    socket_service=None,
    context_manager=None,
    suggest_response_orchestrator=None,
) -> ToolRegistry:
    """
    Factory function to create registry with all tools.

    Args:
        sync_job_manager: Optional FacebookSyncJobManager for sync tools.
                          If not provided, sync tools will fall back to direct service calls.
        socket_service: Optional SocketService for task tool (required if task tool is needed).
        context_manager: Optional AgentContextManager for task tool (required if task tool is needed).
        suggest_response_orchestrator: Optional SuggestResponseOrchestrator for trigger_suggest_response tool.
    """
    from src.agent.tools.manage_media import (
        ChangeMediaRetentionTool,
        DescribeMediaTool,
        MirrorAndDescribeEntityMediaTool,
        ViewMediaTool,
    )
    from src.agent.tools.manage_playbook import ManagePlaybookTool
    from src.agent.tools.orechetrate_tool import (
        AskUserQuestionTool,
        GetSkillTool,
        TodoWriteTool,
    )
    from src.agent.tools.preview_context import PreviewSuggestResponseContextTool
    from src.agent.tools.sql_query import SqlQueryTool
    from src.agent.tools.sync import (
        ManagePageInboxSyncTool,
        ManagePagePostsSyncTool,
        ManagePostCommentsSyncTool,
    )

    registry = ToolRegistry()
    registry.register(ManagePagePostsSyncTool(sync_job_manager=sync_job_manager))
    registry.register(ManagePostCommentsSyncTool(sync_job_manager=sync_job_manager))
    registry.register(ManagePageInboxSyncTool(sync_job_manager=sync_job_manager))
    registry.register(PreviewSuggestResponseContextTool())
    # Media tools
    registry.register(ViewMediaTool())
    registry.register(DescribeMediaTool())
    registry.register(MirrorAndDescribeEntityMediaTool())
    registry.register(ChangeMediaRetentionTool())
    # HITL tool
    registry.register(AskUserQuestionTool())
    # Task management tool
    registry.register(TodoWriteTool())
    # SQL Query tool (RLS-protected)
    registry.register(SqlQueryTool())
    # Manage Playbook tool (create/update/delete/search with Qdrant)
    registry.register(ManagePlaybookTool())
    # Skills tool
    registry.register(GetSkillTool())

    # task tool (requires SubAgentRunner, only register if dependencies provided)
    if socket_service and context_manager:
        from src.agent.general_agent.subagent.subagent_runner import SubAgentRunner
        from src.agent.tools.orechetrate_tool import TaskTool

        subagent_runner = SubAgentRunner(
            socket_service=socket_service,
            context_manager=context_manager,
            sync_job_manager=sync_job_manager,
        )
        registry.register(TaskTool(subagent_runner=subagent_runner))

    # trigger_suggest_response tool (requires SuggestResponseOrchestrator)
    if suggest_response_orchestrator:
        from src.agent.tools.orechetrate_tool.trigger_suggest_response import (
            TriggerSuggestResponseTool,
        )

        registry.register(
            TriggerSuggestResponseTool(orchestrator=suggest_response_orchestrator)
        )

    return registry
