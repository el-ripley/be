"""Base protocol for all agent tools."""

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any, Dict, List, Optional

import asyncpg

if TYPE_CHECKING:
    from src.agent.general_agent.context.manager import AgentContextManager
    from src.agent.general_agent.utils.temp_message_accumulator import (
        TempMessageAccumulator,
    )
    from src.socket_service import SocketService


@dataclass
class ToolCallContext:
    """Context information for a tool call execution."""

    user_id: str
    conv_id: str
    branch_id: str
    agent_response_id: str
    call_id: str
    tool_name: str
    arguments: Dict[str, Any]

    # Suggest response specific (optional - None for general_agent)
    # Facebook conversation for RLS; không nhầm với conv_id (agent/internal).
    fb_conversation_type: Optional[str] = None  # 'messages' | 'comments'
    fb_conversation_id: Optional[str] = None  # ID cuộc hội thoại Facebook
    fan_page_id: Optional[str] = None
    page_scope_user_id: Optional[str] = None

    # Playbook retriever specific (optional - only set when calling playbook tools)
    playbook_assigned_ids: Optional[List[str]] = None
    playbook_agent_response_id: Optional[str] = None
    playbook_cache: Optional[Dict[str, Dict[str, Any]]] = None
    playbook_search_count: Optional[int] = None


@dataclass
class ToolResult:
    """Result from tool execution."""

    output_message: Any  # MessageResponse
    human_message: Optional[Any] = None  # Optional[MessageResponse]
    metadata: Optional[Dict[str, Any]] = None
    hidden_message_ids: List[str] = field(default_factory=list)


class BaseTool(ABC):
    """Base protocol for all agent tools."""

    def __init__(self, description_override: Optional[str] = None) -> None:
        """
        Args:
            description_override: Optional custom description for the tool.
                When set, overrides the default description in the tool definition.
                Used by suggest_response_agent to scope tools without affecting general_agent.
        """
        self._description_override = description_override

    def _apply_description_override(self, base_def: Dict[str, Any]) -> Dict[str, Any]:
        """Apply description override if set. Returns a copy with description updated."""
        if self._description_override is not None:
            result = dict(base_def)
            result["description"] = self._description_override
            return result
        return base_def

    @property
    @abstractmethod
    def name(self) -> str:
        """Tool name for function calls."""
        ...

    @property
    @abstractmethod
    def definition(self) -> Dict[str, Any]:
        """OpenAI function tool definition."""
        ...

    @abstractmethod
    async def execute(
        self,
        conn: asyncpg.Connection,
        context: ToolCallContext,
        arguments: Dict[str, Any],
    ) -> Any:
        """Execute tool, return raw result."""
        ...

    @abstractmethod
    def process_result(self, context: ToolCallContext, raw_result: Any) -> ToolResult:
        """Process raw result into ToolResult."""
        ...

    async def post_process(
        self,
        conn: asyncpg.Connection,
        context: ToolCallContext,
        result: ToolResult,
        socket_service: "SocketService",
        context_manager: "AgentContextManager",
        accumulator: "TempMessageAccumulator",
        subagent_metadata: Optional[Dict[str, Any]] = None,
    ) -> None:
        """Default post-process: emit output message and human message if exists."""
        # 1. Emit function_call_output (always)
        await socket_service.emit_agent_event(
            user_id=context.user_id,
            conv_id=context.conv_id,
            branch_id=context.branch_id,
            agent_response_id=context.agent_response_id,
            msg_type="function_call_output",
            event_name=None,
            msg_item=result.output_message.model_dump(mode="json"),
            subagent_metadata=subagent_metadata,
        )

        # 2. Emit human_message if exists
        if result.human_message:
            await socket_service.emit_agent_event(
                user_id=context.user_id,
                conv_id=context.conv_id,
                branch_id=context.branch_id,
                agent_response_id=context.agent_response_id,
                msg_type="message",
                event_name=None,
                msg_item=result.human_message.model_dump(mode="json"),
                subagent_metadata=subagent_metadata,
            )
