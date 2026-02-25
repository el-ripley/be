"""Resume handler for resuming agent after user answers a question."""

import json
from typing import Dict, List, Any, Optional, Tuple

import asyncpg

from src.database.postgres.repositories.agent_queries import (
    get_branch_messages,
    get_agent_response_for_user,
)
from src.agent.tools.base import ToolCallContext
from src.agent.tools.registry import ToolRegistry
from src.agent.general_agent.context.manager import AgentContextManager
from src.api.openai_conversations.schemas import MessageResponse
from src.socket_service import SocketService
from src.utils.logger import get_logger


logger = get_logger()


class ResumeHandler:
    """Handle resuming agent after user answers a question."""

    def __init__(
        self,
        socket_service: SocketService,
        context_manager: AgentContextManager,
        registry: ToolRegistry,
    ):
        self.socket_service = socket_service
        self.context_manager = context_manager
        self.registry = registry

    async def prepare_resume_context(
        self,
        conn: asyncpg.Connection,
        user_id: str,
        conversation_id: str,
        agent_response_id: str,
        answers: Dict[str, str],
        text: str,
        call_id: str,
        image_urls: Optional[List[str]] = None,
        active_tab: Optional[Dict[str, Any]] = None,
        max_iteration: int = 20,
    ) -> Tuple[str, Optional[MessageResponse]]:
        """Prepare context for resuming agent after user answers a question.

        Args:
            user_id: User ID
            conversation_id: Conversation ID
            agent_response_id: Agent response ID to resume
            answers: User answers dict with keys "0", "1", etc. mapping to question indices
            text: Optional user message text to add after settling the tool_call
            call_id: call_id of the ask_user_question function_call (required)
            image_urls: Optional list of image URLs to include with the user message
            active_tab: Optional active tab information
        """
        try:
            # Validate and get branch_id
            branch_id = await self._validate_and_get_branch_id(
                conn, user_id, conversation_id, agent_response_id
            )
            if not branch_id:
                return "", None

            # Find function call message and extract questions
            questions = await self._extract_questions_from_function_call(
                conn, branch_id, call_id, user_id, conversation_id, agent_response_id
            )
            if questions is None:
                return "", None

            # Execute tool to get formatted result
            output_message = await self._execute_ask_user_tool(
                conn,
                user_id,
                conversation_id,
                branch_id,
                agent_response_id,
                call_id,
                questions,
                answers,
            )
            if not output_message:
                return "", None

            # Persist tool result and rebuild temp context
            user_message_model = (
                await self.context_manager.prepare_temp_context_for_resume(
                    conn=conn,
                    user_id=user_id,
                    conversation_id=conversation_id,
                    branch_id=branch_id,
                    agent_response_id=agent_response_id,
                    tool_output_message=output_message,
                    text=text,
                    image_urls=image_urls,
                    active_tab=active_tab,
                    max_iteration=max_iteration,
                )
            )

            # Emit socket events
            await self._emit_resume_events(
                user_id,
                conversation_id,
                branch_id,
                agent_response_id,
                output_message,
                user_message_model,
            )

            logger.info("Prepared context for resume after user answer")
            return branch_id, user_message_model

        except Exception as e:
            logger.error(f"Error resuming agent with answer: {str(e)}")
            await self.socket_service.emit_agent_error(
                user_id=user_id,
                conv_id=conversation_id,
                error_type="internal_error",
                code="RESUME_ERROR",
                message=f"Error resuming agent: {str(e)}",
                agent_response_id=agent_response_id,
            )
            raise

    async def _validate_and_get_branch_id(
        self,
        conn: asyncpg.Connection,
        user_id: str,
        conversation_id: str,
        agent_response_id: str,
    ) -> Optional[str]:
        """Validate agent_response and return branch_id."""
        agent_response = await get_agent_response_for_user(
            conn, agent_response_id, user_id
        )
        if not agent_response:
            logger.error(
                f"Agent response {agent_response_id} not found for user {user_id}"
            )
            await self.socket_service.emit_agent_error(
                user_id=user_id,
                conv_id=conversation_id,
                error_type="validation_error",
                code="AGENT_RESPONSE_NOT_FOUND",
                message="Agent response not found",
                agent_response_id=agent_response_id,
            )
            return None

        branch_id = agent_response.get("branch_id")
        if not branch_id:
            logger.error(f"No branch_id found for agent_response {agent_response_id}")
            await self.socket_service.emit_agent_error(
                user_id=user_id,
                conv_id=conversation_id,
                error_type="validation_error",
                code="NO_BRANCH_ID",
                message="No branch ID found for agent response",
                agent_response_id=agent_response_id,
            )
            return None

        return branch_id

    async def _extract_questions_from_function_call(
        self,
        conn: asyncpg.Connection,
        branch_id: str,
        call_id: str,
        user_id: str,
        conversation_id: str,
        agent_response_id: str,
    ) -> Optional[List[str]]:
        """Find function call message and extract questions."""
        branch_messages, _ = await get_branch_messages(conn, branch_id)

        # Find the ask_user_question function_call message
        function_call_message = None
        for msg in branch_messages:
            if (
                msg.get("type") == "function_call"
                and msg.get("call_id") == call_id
                and msg.get("function_name") == "ask_user_question"
            ):
                function_call_message = msg
                break

        if not function_call_message:
            logger.error(f"Function call message not found for call_id {call_id}")
            await self.socket_service.emit_agent_error(
                user_id=user_id,
                conv_id=conversation_id,
                error_type="validation_error",
                code="FUNCTION_CALL_NOT_FOUND",
                message="Function call message not found",
                agent_response_id=agent_response_id,
            )
            return None

        # Extract questions from function_arguments
        function_arguments = function_call_message.get("function_arguments")
        if isinstance(function_arguments, str):
            function_arguments = json.loads(function_arguments)

        return function_arguments.get("questions", [])

    async def _execute_ask_user_tool(
        self,
        conn: asyncpg.Connection,
        user_id: str,
        conversation_id: str,
        branch_id: str,
        agent_response_id: str,
        call_id: str,
        questions: List[str],
        answers: Dict[str, str],
    ) -> Optional[MessageResponse]:
        """Execute ask_user_question tool and return output message."""
        ask_user_tool = self.registry.get("ask_user_question")
        if not ask_user_tool:
            logger.error("ask_user_question tool not found in registry")
            await self.socket_service.emit_agent_error(
                user_id=user_id,
                conv_id=conversation_id,
                error_type="internal_error",
                code="TOOL_NOT_FOUND",
                message="ask_user_question tool not found",
                agent_response_id=agent_response_id,
            )
            return None

        context = ToolCallContext(
            user_id=user_id,
            conv_id=conversation_id,
            branch_id=branch_id,
            agent_response_id=agent_response_id,
            call_id=call_id,
            tool_name="ask_user_question",
            arguments={"questions": questions, "answers": answers},
        )

        raw_result = await ask_user_tool.execute(conn, context, context.arguments)
        tool_result = ask_user_tool.process_result(context, raw_result)
        return tool_result.output_message

    async def _emit_resume_events(
        self,
        user_id: str,
        conversation_id: str,
        branch_id: str,
        agent_response_id: str,
        output_message: MessageResponse,
        user_message_model: Optional[MessageResponse],
    ) -> None:
        """Emit socket events for tool result and user message."""
        # Emit the tool_result message
        await self.socket_service.emit_agent_event(
            user_id=user_id,
            conv_id=conversation_id,
            branch_id=branch_id,
            agent_response_id=agent_response_id,
            msg_type="function_call_output",
            event_name=None,
            msg_item=output_message.model_dump(mode="json"),
        )

        # Emit user message event (if any)
        if user_message_model is not None:
            await self.socket_service.emit_agent_event(
                user_id=user_id,
                conv_id=conversation_id,
                branch_id=branch_id,
                agent_response_id=agent_response_id,
                msg_type="message",
                event_name="user_message.added",
                msg_item=user_message_model.model_dump(mode="json"),
                msg_id=user_message_model.id,
            )
