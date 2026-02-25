"""Iteration runner for subagent execution."""

from dataclasses import dataclass
from typing import Any, Dict, List, Optional, TYPE_CHECKING

from src.database.postgres.connection import async_db_transaction
from src.agent.general_agent.llm_stream_handler import LLMStreamHandler
from src.agent.general_agent.tool_executor import ToolExecutor
from src.agent.general_agent.core.run_config import RunConfig
from src.agent.general_agent.utils.response_analyzer import ResponseAnalyzer
from src.agent.general_agent.context.manager import AgentContextManager
from src.utils.logger import get_logger

if TYPE_CHECKING:
    from src.agent.general_agent.core.agent_runner import BranchContext
    from src.agent.general_agent.subagent.subagent_runner import SubAgentMetadata

logger = get_logger()


@dataclass
class SubAgentIterationResult:
    """Result of a single subagent iteration."""

    should_stop: bool
    tokens_used: int
    final_content: Optional[str] = None
    reason: Optional[str] = None  # "error", "parent_stopped", etc.


class SubAgentIterationRunner:
    """Execute single subagent iteration."""

    def __init__(
        self,
        stream_handler: LLMStreamHandler,
        tool_executor: ToolExecutor,
        context_manager: AgentContextManager,
    ):
        self.stream_handler = stream_handler
        self.tool_executor = tool_executor
        self.context_manager = context_manager

    async def run(
        self,
        run_config: RunConfig,
        branch_context: "BranchContext",
        tools: List[Dict[str, Any]],
        subagent_metadata: "SubAgentMetadata",
        current_turn: int,
        max_turns: int,
    ) -> SubAgentIterationResult:
        """Execute a single subagent iteration and persist its outcome."""

        # Get temp context from Redis (required for LLM stream handler)
        temp_context = await self.context_manager.get_temp_context_for_current_branch(
            run_config.user_id,
            run_config.conversation_id,
            branch_context.agent_response_id,
        )

        if not temp_context:
            logger.error("Failed to get temp context for subagent")
            return SubAgentIterationResult(
                should_stop=True, tokens_used=0, reason="error"
            )

        # Stream LLM response (with subagent_metadata)
        stream_result = await self.stream_handler.stream(
            run_config=run_config,
            branch_context=branch_context,
            temp_context=temp_context,
            tools=tools,
            subagent_metadata=(
                subagent_metadata.to_dict() if subagent_metadata else None
            ),
        )

        response_dict = stream_result.response_dict
        accumulator = stream_result.accumulator

        # Check if done (no tool calls)
        is_final = ResponseAnalyzer.is_final(response_dict)

        if not is_final:
            # Execute tool calls (with subagent_metadata)
            async with async_db_transaction() as conn:
                await self.tool_executor.execute_tool_calls(
                    conn=conn,
                    user_id=run_config.user_id,
                    conv_id=run_config.conversation_id,
                    branch_id=branch_context.current_branch_id,
                    agent_resp_id=branch_context.agent_response_id,
                    response_dict=response_dict,
                    accumulator=accumulator,
                    subagent_metadata=(
                        subagent_metadata.to_dict() if subagent_metadata else None
                    ),
                )

        # Save to DB
        try:
            async with async_db_transaction() as conn:
                await self.context_manager.process_agent_iteration(
                    conn=conn,
                    user_id=run_config.user_id,
                    conversation_id=run_config.conversation_id,
                    branch_id=branch_context.current_branch_id,
                    agent_resp_id=branch_context.agent_response_id,
                    temp_messages=accumulator.to_sorted_messages(),
                    openai_response_data=response_dict,
                    is_final=is_final,
                    model=run_config.model,
                    tools=tools,
                    input_messages=temp_context,
                    current_iteration=current_turn,
                    max_iteration=max_turns,
                )
        except Exception as e:
            # Handle iteration persistence errors gracefully
            logger.error(f"Subagent failed to persist iteration: {e}")
            return SubAgentIterationResult(
                should_stop=True,
                tokens_used=response_dict.get("usage", {}).get("total_tokens", 0),
                reason="error",
            )

        return SubAgentIterationResult(
            should_stop=is_final,
            tokens_used=response_dict.get("usage", {}).get("total_tokens", 0),
            final_content=ResponseAnalyzer.extract_final_content(response_dict),
        )
