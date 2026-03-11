"""Iteration runner for executing single LLM iterations."""

from dataclasses import dataclass
from typing import TYPE_CHECKING, Dict, List, Literal

from src.agent.general_agent.context.general_agent_system_prompt_logger import (
    logs_general_agent_system_prompt,
)
from src.agent.general_agent.context.manager import AgentContextManager
from src.agent.general_agent.core.run_config import RunConfig
from src.agent.general_agent.llm_stream_handler import LLMStreamHandler
from src.agent.general_agent.tool_executor import ToolExecutor
from src.agent.general_agent.utils.response_analyzer import ResponseAnalyzer
from src.database.postgres.connection import async_db_transaction
from src.database.postgres.repositories.agent_queries import set_agent_response_waiting
from src.socket_service import SocketService
from src.utils.logger import get_logger

if TYPE_CHECKING:
    from src.agent.general_agent.core.agent_runner import BranchContext

logger = get_logger()


@dataclass
class IterationResult:
    """Result of a single LLM iteration."""

    should_stop: bool
    reason: Literal["completed", "waiting_for_user", "error", "continue"]


class IterationRunner:
    """Execute single LLM iteration."""

    def __init__(
        self,
        stream_handler: LLMStreamHandler,
        tool_executor: ToolExecutor,
        context_manager: AgentContextManager,
        socket_service: SocketService,
    ):
        self.stream_handler = stream_handler
        self.tool_executor = tool_executor
        self.context_manager = context_manager
        self.socket_service = socket_service

    async def run(
        self,
        run_config: RunConfig,
        branch_context: "BranchContext",
        tools: List[Dict],
        current_iteration: int,
        max_iteration: int,
    ) -> IterationResult:
        """Execute a single LLM iteration and persist its outcome."""
        temp_context = await self.context_manager.get_temp_context_for_current_branch(
            run_config.user_id,
            run_config.conversation_id,
            branch_context.agent_response_id,
        )
        logs_general_agent_system_prompt(temp_context)

        if not temp_context:
            logger.error("Failed to get temp context")
            return IterationResult(should_stop=True, reason="error")

        stream_result = await self.stream_handler.stream(
            run_config,
            branch_context,
            temp_context,
            tools,
        )

        response_dict = stream_result.response_dict
        accumulator = stream_result.accumulator
        stream_status = stream_result.status
        error_details = stream_result.error_details

        # Extract and store input_tokens for context tracking
        usage = response_dict.get("usage", {})
        input_tokens = usage.get("input_tokens", 0)
        run_config.current_context_tokens = (
            input_tokens  # Update for system prompt display
        )

        # Handle error cases - emit events and stop iteration
        # Note: incomplete and refusal also use "failed" status in DB but have type in error_details
        if stream_status == "failed":
            # Error/warning already emitted in stream handler
            # Still save to DB for tracking
            async with async_db_transaction() as conn:
                temp_messages_list = accumulator.to_sorted_messages()
                response_dict["_status"] = "failed"
                response_dict["_error_details"] = error_details
                await self.context_manager.process_agent_iteration(
                    conn,
                    run_config.user_id,
                    run_config.conversation_id,
                    branch_context.current_branch_id,
                    branch_context.agent_response_id,
                    temp_messages_list,
                    response_dict,
                    is_final=True,
                    model=run_config.model,
                    tools=tools,
                    input_messages=temp_context,
                    current_iteration=current_iteration,
                    max_iteration=max_iteration,
                )
            return IterationResult(should_stop=True, reason="error")

        # Normal flow - completed response
        is_final = ResponseAnalyzer.is_final(response_dict)

        # Check if response contains ask_user_question tool call
        # If so, pause execution and wait for user input
        if not is_final and ResponseAnalyzer.has_ask_user_question(response_dict):
            async with async_db_transaction() as conn:
                # Save messages including the ask_user_question function_call
                # But DON'T execute the tool - wait for user answer
                temp_messages_list = accumulator.to_sorted_messages()
                response_dict["_status"] = "completed"
                response_dict["_error_details"] = None

                await self.context_manager.process_agent_iteration(
                    conn,
                    run_config.user_id,
                    run_config.conversation_id,
                    branch_context.current_branch_id,
                    branch_context.agent_response_id,
                    temp_messages_list,
                    response_dict,
                    is_final=False,  # Not final, waiting for user
                    model=run_config.model,
                    tools=tools,
                    input_messages=temp_context,
                    current_iteration=current_iteration,
                    max_iteration=max_iteration,
                )

                # Set status to waiting_for_user
                await set_agent_response_waiting(conn, branch_context.agent_response_id)

            # Frontend will detect ask_user_question from agent.event (function_call message)
            # No need to emit separate waiting_for_user event

            # Return special flag to exit loop WITHOUT finalizing
            # This prevents billing finalization until user answers
            return IterationResult(should_stop=True, reason="waiting_for_user")

        try:
            async with async_db_transaction() as conn:
                if not is_final:
                    await self.tool_executor.execute_tool_calls(
                        conn=conn,
                        user_id=run_config.user_id,
                        conv_id=run_config.conversation_id,
                        branch_id=branch_context.current_branch_id,
                        agent_resp_id=branch_context.agent_response_id,
                        response_dict=response_dict,
                        accumulator=accumulator,
                    )

                temp_messages_list = accumulator.to_sorted_messages()
                response_dict["_status"] = "completed"
                response_dict["_error_details"] = None

                await self.context_manager.process_agent_iteration(
                    conn,
                    run_config.user_id,
                    run_config.conversation_id,
                    branch_context.current_branch_id,
                    branch_context.agent_response_id,
                    temp_messages_list,
                    response_dict,
                    is_final,
                    run_config.model,
                    tools,
                    temp_context,
                    current_iteration=current_iteration,
                    max_iteration=max_iteration,
                )
        except Exception as e:
            # Handle iteration persistence errors gracefully
            # Log the error and emit warning to user, but don't crash the agent
            logger.error(f"Failed to persist iteration: {e}")

            # Emit error to frontend so user knows something went wrong
            # Wrap in try/except to ensure we always return gracefully even if emit fails
            try:
                await self.socket_service.emit_agent_error(
                    user_id=run_config.user_id,
                    conv_id=run_config.conversation_id,
                    error_type="iteration_error",
                    code="ITERATION_PERSISTENCE_ERROR",
                    message=f"Internal error while processing response: {str(e)}",
                    branch_id=branch_context.current_branch_id,
                    agent_response_id=branch_context.agent_response_id,
                )
            except Exception as emit_err:
                logger.error(f"Failed to emit iteration error to frontend: {emit_err}")
            return IterationResult(should_stop=True, reason="error")

        # CHECK: After billing is saved (safe point - all tokens billed)
        # Note: This check happens inside _run_single_iteration, but we handle
        # the stop in _iterate_agent_responses to avoid breaking the return contract
        # The check in _iterate_agent_responses will catch it on the next loop iteration

        return IterationResult(
            should_stop=is_final, reason="completed" if is_final else "continue"
        )
