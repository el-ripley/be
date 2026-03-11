"""Tool registry for Explore subagent (read-only)."""

from src.agent.tools.registry import ToolRegistry


def create_explore_registry(sync_job_manager=None) -> ToolRegistry:
    """
    Create registry with read-only tools for Explore subagent.

    Includes only these 6 tools (NO sync tools - read-only):
    1. view_media
    2. describe_media
    3. mirror_and_describe_entity_media
    4. todo_write
    5. Skills (get_skill)
    6. sql_query
    """
    from src.agent.tools.manage_media import (
        DescribeMediaTool,
        MirrorAndDescribeEntityMediaTool,
        ViewMediaTool,
    )
    from src.agent.tools.orechetrate_tool import GetSkillTool, TodoWriteTool
    from src.agent.tools.sql_query import SqlQueryTool

    registry = ToolRegistry()

    # Media Tools
    registry.register(ViewMediaTool())
    registry.register(DescribeMediaTool())
    registry.register(MirrorAndDescribeEntityMediaTool())

    # Task Management Tool
    registry.register(TodoWriteTool())

    # Skills Tool
    registry.register(GetSkillTool())

    # SQL Query Tool (read-only queries)
    registry.register(SqlQueryTool())

    return registry
