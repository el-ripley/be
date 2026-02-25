from dataclasses import dataclass
from typing import Optional, Dict, Any, List

from src.database.postgres.connection import async_db_transaction, get_async_connection
from src.agent.tools.registry import create_default_registry
from src.agent.core.llm_call import LLM_call
from src.agent.general_agent.context.manager import AgentContextManager
from src.agent.general_agent.llm_stream_handler import LLMStreamHandler
from src.agent.general_agent.tool_executor import ToolExecutor
from src.agent.general_agent.utils.balance_guard import BalanceGuard
from src.agent.general_agent.stop_handler import StopHandler
from src.agent.general_agent.core.iteration_runner import IterationRunner
from src.agent.general_agent.resume_handler import ResumeHandler
from src.agent.common.agent_types import AGENT_TYPE_GENERAL_AGENT
from src.agent.common.api_key_resolver_service import get_system_api_key
from src.agent.common.conversation_settings import (
    get_default_settings,
    normalize_settings,
    get_effective_context_settings,
)
from src.database.postgres.repositories.agent_queries import (
    get_conversation_settings,
)

from src.socket_service import SocketService
from src.utils.logger import get_logger
from src.api.openai_conversations.schemas import MessageResponse
from src.agent.general_agent.core.run_config import RunConfig
from src.agent.general_agent.summarization.summarization_orchestrator import (
    SummarizationOrchestrator,
)

logger = get_logger()


@dataclass
class BranchContext:
    agent_response_id: str
    current_branch_id: str
    user_message_model: MessageResponse


class AgentRunner:
    def __init__(
        self,
        socket_service: SocketService,
        context_manager: AgentContextManager,
        sync_job_manager=None,
        suggest_response_orchestrator=None,
    ):
        self.max_iterations = 30
        self.default_model = "gpt-5-mini"

        self.socket_service = socket_service
        self.context_manager = context_manager
        self.registry = create_default_registry(
            sync_job_manager=sync_job_manager,
            socket_service=socket_service,
            context_manager=context_manager,
            suggest_response_orchestrator=suggest_response_orchestrator,
        )

        # Initialize handlers
        self.balance_guard = BalanceGuard(socket_service)
        self.stop_handler = StopHandler(socket_service)
        self.stream_handler = LLMStreamHandler(socket_service)
        self.tool_executor = ToolExecutor(
            socket_service, context_manager, self.registry
        )
        self.iteration_runner = IterationRunner(
            self.stream_handler,
            self.tool_executor,
            self.context_manager,
            socket_service,
        )
        self.resume_handler = ResumeHandler(
            socket_service, context_manager, self.registry
        )
        self.summarization_orchestrator = SummarizationOrchestrator(
            socket_service=socket_service
        )

    async def run(
        self,
        user_id: str,
        conversation_id: str,
        new_human_mes: str,
        agent_response_id: Optional[str] = None,
        active_tab: Optional[Dict[str, Any]] = None,
        image_urls: Optional[List[str]] = None,
    ):
        branch_context = None
        try:
            run_config = await self._prepare_run_config(
                user_id, conversation_id, active_tab
            )

            iteration_limit = run_config.settings.get(
                "max_iterations", self.max_iterations
            )

            # Check balance before running agent
            if not await self.balance_guard.check_can_run(user_id, conversation_id):
                return  # Stop execution, don't raise exception

            async with async_db_transaction() as conn:
                branch_context = await self._initialize_branch_context(
                    conn,
                    run_config,
                    new_human_mes,
                    agent_response_id,
                    iteration_limit,
                    image_urls,
                )

            user_message_payload = branch_context.user_message_model.model_dump(
                mode="json"
            )
            msg_id = user_message_payload.get("id")
            await self.socket_service.emit_agent_event(
                user_id=user_id,
                conv_id=conversation_id,
                branch_id=branch_context.current_branch_id,
                agent_response_id=branch_context.agent_response_id,
                msg_type=None,
                event_name="user.message.appended",
                msg_item=user_message_payload,
                msg_id=msg_id,
            )

            await self.summarization_orchestrator.check_and_trigger(
                user_id=user_id,
                conversation_id=conversation_id,
                branch_id=branch_context.current_branch_id,
                run_config=run_config,
                parent_agent_response_id=branch_context.agent_response_id,
            )

            await self._iterate_agent_responses(
                run_config,
                branch_context,
                iteration_limit,
            )

            # Finalize agent_response when agent run truly completes
            # This ensures status is only set to 'completed' when agent is done
            await self.balance_guard.finalize_and_deduct(
                branch_context.agent_response_id
            )

            await self.socket_service.emit_agent_event(
                user_id=run_config.user_id,
                conv_id=run_config.conversation_id,
                branch_id=branch_context.current_branch_id,
                agent_response_id=branch_context.agent_response_id,
                event_name="agent.run.completed",
            )

            logger.info("Agent run completed successfully")

        except Exception as e:
            logger.error(f"Error in agent run: {str(e)}")
            # Finalize agent_response on error to ensure billing is captured
            if branch_context:
                try:
                    await self.balance_guard.finalize_and_deduct(
                        branch_context.agent_response_id
                    )
                except Exception as finalize_error:
                    logger.error(
                        f"Error finalizing agent_response on error: {str(finalize_error)}"
                    )

            # Emit error to frontend so user gets informed instead of a silent hang
            try:
                await self.socket_service.emit_agent_error(
                    user_id=run_config.user_id,
                    conv_id=run_config.conversation_id,
                    error_type="agent_run_error",
                    code="AGENT_RUN_ERROR",
                    message=f"Agent encountered an error: {str(e)}",
                    branch_id=(
                        branch_context.current_branch_id if branch_context else None
                    ),
                    agent_response_id=(
                        branch_context.agent_response_id if branch_context else None
                    ),
                )
            except Exception as emit_err:
                logger.error(f"Failed to emit agent run error to frontend: {emit_err}")
            raise

    async def _prepare_run_config(
        self,
        user_id: str,
        conversation_id: str,
        active_tab: Optional[Dict[str, Any]] = None,
    ) -> RunConfig:
        """Resolve API credentials and model configuration."""
        # Get system API key
        api_key = get_system_api_key()

        # Get settings from conversation
        async with get_async_connection() as conn:
            conversation_settings = await get_conversation_settings(
                conn, conversation_id
            )

            # Get effective context settings (user settings merged with defaults)
            effective_context_settings = await get_effective_context_settings(
                user_id, conn
            )

        # Normalize settings with defaults
        if conversation_settings:
            settings = normalize_settings(conversation_settings)
        else:
            settings = get_default_settings()

        model = settings.get("model", self.default_model)
        llm_call = LLM_call(api_key=api_key)

        # Use effective context settings (user settings merged with system defaults)
        context_token_limit = effective_context_settings["context_token_limit"]
        context_buffer_percent = effective_context_settings["context_buffer_percent"]

        return RunConfig(
            user_id=user_id,
            conversation_id=conversation_id,
            api_key=api_key,
            settings=settings,
            model=model,
            llm_call=llm_call,
            active_tab=active_tab,
            context_token_limit=context_token_limit,
            context_buffer_percent=context_buffer_percent,
            current_context_tokens=0,  # Will be updated after checking latest response
        )

    async def _initialize_branch_context(
        self,
        conn,
        run_config: RunConfig,
        new_human_mes: str,
        agent_response_id: Optional[str],
        max_iteration: int,
        image_urls: Optional[List[str]] = None,
    ) -> BranchContext:
        """Create temp context and fetch current branch state."""
        (
            agent_resp_id,
            user_message_model,
        ) = await self.context_manager.create_temp_context_for_current_branch(
            conn,
            run_config.user_id,
            run_config.conversation_id,
            new_human_mes,
            AGENT_TYPE_GENERAL_AGENT,
            existing_agent_response_id=agent_response_id,
            active_tab=run_config.active_tab,
            max_iteration=max_iteration,
            image_urls=image_urls,
        )

        current_branch_id = await self.context_manager.context_builder.get_current_branch_id_for_conversation(
            run_config.conversation_id, conn=conn
        )

        return BranchContext(
            agent_response_id=agent_resp_id,
            current_branch_id=current_branch_id,
            user_message_model=user_message_model,
        )

    async def _iterate_agent_responses(
        self,
        run_config: RunConfig,
        branch_context: BranchContext,
        iteration_limit: Optional[int] = None,
    ) -> None:
        """Iterate through agent responses until completion or max iterations."""
        tools = self.registry.get_all_definitions()
        max_iters = iteration_limit or self.max_iterations
        iteration = 0

        while iteration < max_iters:
            # CHECK STOP SIGNAL 1: Before starting new iteration (safe point - no tokens consumed yet)
            if await self.stop_handler.should_stop(run_config, branch_context):
                await self.stop_handler.handle_stop(run_config, branch_context)
                return

            logger.info(f"=== Iteration {iteration + 1}/{max_iters} ===")

            result = await self.iteration_runner.run(
                run_config,
                branch_context,
                tools,
                current_iteration=iteration,
                max_iteration=max_iters,
            )

            iteration += 1

            # CHECK STOP SIGNAL 2: After billing is saved (safe point - all tokens billed)
            if await self.stop_handler.should_stop(run_config, branch_context):
                await self.stop_handler.handle_stop(run_config, branch_context)
                return

            # Handle special return value for waiting_for_user
            if result.reason == "waiting_for_user":
                # Exit loop without finalizing - agent is waiting for user input
                logger.info(
                    f"Agent paused waiting for user input: agent_response {branch_context.agent_response_id}"
                )
                return

            if result.should_stop:
                break

    async def resume_with_answer(
        self,
        user_id: str,
        conversation_id: str,
        agent_response_id: str,
        answers: Dict[str, str],
        text: str = "",
        call_id: str = "",
        image_urls: Optional[List[str]] = None,
        active_tab: Optional[Dict[str, Any]] = None,
    ):
        """Resume agent after user answers a question.

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
        branch_context = None
        try:
            # Prepare run configuration (model, settings, context limits)
            run_config = await self._prepare_run_config(
                user_id, conversation_id, active_tab
            )

            iteration_limit = run_config.settings.get(
                "max_iterations", self.max_iterations
            )

            # Check balance before resuming agent
            if not await self.balance_guard.check_can_run(user_id, conversation_id):
                return  # Stop execution, don't raise exception

            # Prepare context for resume inside a DB transaction
            async with async_db_transaction() as conn:
                branch_id, user_message_model = (
                    await self.resume_handler.prepare_resume_context(
                        conn=conn,
                        user_id=user_id,
                        conversation_id=conversation_id,
                        agent_response_id=agent_response_id,
                        answers=answers,
                        text=text or "",
                        call_id=call_id,
                        image_urls=image_urls,
                        active_tab=active_tab,
                        max_iteration=iteration_limit,
                    )
                )

                if not branch_id:
                    # Error already emitted by resume_handler
                    return

                # Build BranchContext for iteration loop
                # user_message_model is not strictly required for resume flow,
                # but we keep it for consistency with the main run flow
                branch_context = BranchContext(
                    agent_response_id=agent_response_id,
                    current_branch_id=branch_id,
                    user_message_model=user_message_model
                    or MessageResponse(
                        id="",
                        conversation_id=conversation_id,
                        sequence_number=0,
                        type="user_input",
                        role="user",
                        content="",
                        status="completed",
                        metadata=None,
                        created_at=0,
                        updated_at=0,
                    ),
                )

            # Continue iteration loop from updated context
            await self._iterate_agent_responses(
                run_config,
                branch_context,
                iteration_limit,
            )

            # Finalize agent_response when agent run truly completes
            await self.balance_guard.finalize_and_deduct(agent_response_id)

            await self.socket_service.emit_agent_event(
                user_id=run_config.user_id,
                conv_id=run_config.conversation_id,
                branch_id=branch_context.current_branch_id,
                agent_response_id=agent_response_id,
                event_name="agent.run.completed",
            )

            logger.info("Agent resumed and completed successfully after user answer")

        except Exception as e:
            logger.error(f"Error in agent resume_with_answer: {str(e)}")
            # Finalize agent_response on error to ensure billing is captured
            if branch_context:
                try:
                    await self.balance_guard.finalize_and_deduct(
                        branch_context.agent_response_id
                    )
                except Exception as finalize_error:
                    logger.error(
                        f"Error finalizing agent_response on resume error: {str(finalize_error)}"
                    )
            raise
