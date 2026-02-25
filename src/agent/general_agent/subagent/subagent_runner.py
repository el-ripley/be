"""SubAgentRunner for context isolation."""

from dataclasses import dataclass
from typing import Any, Dict, List, Optional, Tuple

import asyncpg

from src.agent.general_agent.tool_executor import ToolExecutor
from src.agent.general_agent.core.run_config import RunConfig
from src.agent.general_agent.core.agent_runner import BranchContext
from src.agent.general_agent.context.manager import AgentContextManager
from src.agent.general_agent.subagent.registry import create_explore_registry
from src.agent.general_agent.subagent.prompts import EXPLORE_SYSTEM_PROMPT
from src.agent.general_agent.subagent.subagent_iteration_runner import (
    SubAgentIterationRunner,
)
from src.agent.common.conversation_settings import (
    get_default_settings,
    normalize_settings,
    get_effective_context_settings,
)
from src.agent.core.llm_call import LLM_call
from src.agent.common.agent_types import AGENT_TYPE_SUBAGENT_EXPLORE
from src.agent.common.api_key_resolver_service import get_system_api_key
from src.api.openai_conversations.schemas import MessageResponse
from src.database.postgres.utils import generate_uuid
from src.database.postgres.repositories.agent_queries import get_conversation_settings
from src.database.postgres.connection import get_async_connection
import time
from src.database.postgres.connection import async_db_transaction
from src.database.postgres.repositories.agent_queries import (
    create_agent_response,
    get_conversation,
    finalize_agent_response,
    create_subagent_conversation,
    stop_agent_response,
)
from src.billing.credit_service import deduct_credits_after_agent
from src.socket_service import SocketService
from src.utils.logger import get_logger
from src.database.postgres.entities.agent_entities import OpenAIConversation

logger = get_logger()


@dataclass
class SubAgentResult:
    """Result returned to main agent after subagent completes."""

    result: str  # Final text response
    conversation_id: str  # Subagent conversation ID (for resume)
    turns_used: int  # Number of iterations
    total_tokens: int  # Total tokens consumed


@dataclass
class SubAgentContext:
    """Context passed to subagent for execution."""

    user_id: str
    parent_conversation_id: str
    parent_agent_response_id: str
    parent_branch_id: str
    task_call_id: str  # call_id của task function_call
    model: str
    max_turns: int
    subagent_type: str = (
        "Explore"  # Type of subagent (currently only "Explore" is available)
    )


@dataclass
class SubAgentMetadata:
    """Metadata included in all subagent socket events."""

    is_subagent: bool = True
    parent_conversation_id: str = ""
    task_call_id: str = ""

    def to_dict(self) -> dict:
        return {
            "is_subagent": self.is_subagent,
            "parent_conversation_id": self.parent_conversation_id,
            "task_call_id": self.task_call_id,
        }


class SubAgentRunner:
    """
    Agent runner for subagents with streaming support.

    Key differences from main AgentRunner:
    - Streams events with subagent metadata (parent_conversation_id, task_call_id)
    - Uses limited tool set (Explore tools only)
    - Creates isolated conversation for context
    """

    def __init__(
        self,
        socket_service: SocketService,
        context_manager: AgentContextManager,
        sync_job_manager=None,
    ):
        self.socket_service = socket_service
        self.context_manager = context_manager
        self.registry = create_explore_registry(sync_job_manager=sync_job_manager)

        # Reuse components from agent_runner
        self.tool_executor = ToolExecutor(
            socket_service, context_manager, self.registry
        )

        # Create iteration runner
        from src.agent.general_agent.llm_stream_handler import LLMStreamHandler

        stream_handler = LLMStreamHandler(socket_service)
        self.iteration_runner = SubAgentIterationRunner(
            stream_handler=stream_handler,
            tool_executor=self.tool_executor,
            context_manager=context_manager,
        )

        self.default_max_turns = 20
        self.max_max_turns = 60

    async def run(
        self,
        ctx: SubAgentContext,
        prompt: str,
        resume_conversation_id: Optional[str] = None,
    ) -> SubAgentResult:
        """
        Run subagent to completion with streaming.

        Args:
            ctx: SubAgentContext with user_id, parent info, task_call_id
            prompt: Task prompt for subagent
            resume_conversation_id: Optional conversation ID to resume

        Returns:
            SubAgentResult with final response and metadata
        """
        max_turns = min(ctx.max_turns, self.max_max_turns)
        agent_response_id: Optional[str] = None

        try:
            # Initialize execution: conversation, agent_response, branch, temp_context
            conversation, agent_response_id, branch_id = (
                await self._initialize_execution(
                    ctx=ctx,
                    prompt=prompt,
                    resume_conversation_id=resume_conversation_id,
                )
            )

            # Prepare execution context: run_config and branch_context
            run_config, branch_context = (
                await self._prepare_run_config_and_branch_context(
                    ctx=ctx,
                    conversation_id=str(conversation.id),
                    branch_id=branch_id,
                    agent_response_id=agent_response_id,
                )
            )

            subagent_metadata = SubAgentMetadata(
                is_subagent=True,
                parent_conversation_id=ctx.parent_conversation_id,
                task_call_id=ctx.task_call_id,
            )

            tools = self.registry.get_all_definitions()
            turns_used, total_tokens, final_content = (
                await self._iterate_agent_responses(
                    run_config=run_config,
                    branch_context=branch_context,
                    tools=tools,
                    subagent_metadata=subagent_metadata,
                    parent_agent_response_id=ctx.parent_agent_response_id,
                    max_turns=max_turns,
                )
            )

            await self._finalize_and_deduct(agent_response_id)

            return SubAgentResult(
                result=final_content,
                conversation_id=str(conversation.id),
                turns_used=turns_used,
                total_tokens=total_tokens,
            )

        except Exception as e:
            logger.error(f"Error in subagent run: {str(e)}")
            # Finalize agent_response on error to ensure billing is captured
            if agent_response_id:
                try:
                    await self._finalize_and_deduct(agent_response_id)
                except Exception as finalize_error:
                    logger.error(
                        f"Error finalizing subagent agent_response on error: {str(finalize_error)}"
                    )
            raise

    # ------------------------------------------------------------
    # Initialization logic
    # ------------------------------------------------------------
    async def _initialize_execution(
        self,
        ctx: SubAgentContext,
        prompt: str,
        resume_conversation_id: Optional[str] = None,
    ) -> Tuple[OpenAIConversation, str, str]:
        """
        Initialize subagent execution: create/resume conversation, agent_response, branch, and temp_context.

        Returns:
            Tuple of (conversation, agent_response_id, branch_id)
        """
        async with async_db_transaction() as conn:
            if resume_conversation_id:
                conversation = await self._resume_conversation(
                    conn, resume_conversation_id
                )
            else:
                conversation = await self._create_conversation(conn, ctx)

            agent_response_id = await self._create_agent_response(
                conn, ctx, conversation.id
            )

            branch_id = await self._get_or_create_branch(conn, conversation.id)

            # Create temp context for subagent using context manager
            await self.context_manager.create_temp_context_for_subagent(
                user_id=ctx.user_id,
                conversation_id=str(conversation.id),
                agent_response_id=agent_response_id,
                system_prompt=EXPLORE_SYSTEM_PROMPT,
                user_prompt=prompt,
            )

        return conversation, agent_response_id, branch_id

    async def _prepare_run_config_and_branch_context(
        self,
        ctx: SubAgentContext,
        conversation_id: str,
        branch_id: str,
        agent_response_id: str,
    ) -> Tuple[RunConfig, BranchContext]:
        """Prepare run_config and branch_context once (outside iteration loop)."""
        # Get settings
        async with get_async_connection() as conn:
            conversation_settings = await get_conversation_settings(
                conn, conversation_id
            )
            if conversation_settings:
                settings = normalize_settings(conversation_settings)
            else:
                settings = get_default_settings()

            # Get effective context settings
            effective_context_settings = await get_effective_context_settings(
                ctx.user_id, conn
            )

        # Create run config
        api_key = get_system_api_key()
        run_config = RunConfig(
            user_id=ctx.user_id,
            conversation_id=conversation_id,
            api_key=api_key,
            settings=settings,
            model=ctx.model,
            llm_call=LLM_call(api_key=api_key),
            active_tab=None,
            context_token_limit=effective_context_settings["context_token_limit"],
            context_buffer_percent=effective_context_settings["context_buffer_percent"],
            current_context_tokens=0,
        )

        # Create branch context
        branch_context = BranchContext(
            agent_response_id=agent_response_id,
            current_branch_id=branch_id,
            user_message_model=MessageResponse(
                id=generate_uuid(),
                conversation_id=conversation_id,
                sequence_number=0,
                type="user_input",
                role="user",
                content="",
                created_at=int(time.time() * 1000),
                updated_at=int(time.time() * 1000),
            ),
        )

        return run_config, branch_context

    async def _create_conversation(
        self, conn: asyncpg.Connection, ctx: SubAgentContext
    ) -> Optional[OpenAIConversation]:
        """Create new subagent conversation."""
        settings = get_default_settings()
        conversation_id, branch_id = await create_subagent_conversation(
            conn=conn,
            user_id=ctx.user_id,
            parent_conversation_id=ctx.parent_conversation_id,
            parent_agent_response_id=ctx.parent_agent_response_id,
            task_call_id=ctx.task_call_id,
            subagent_type=ctx.subagent_type,
            settings=settings,
        )

        # Get conversation object
        conversation = await get_conversation(conn, conversation_id)

        return conversation

    async def _resume_conversation(
        self, conn: asyncpg.Connection, conversation_id: str
    ) -> Optional[OpenAIConversation]:
        """Resume existing subagent conversation."""
        conversation = await get_conversation(conn, conversation_id)
        if not conversation or not (conversation.is_subagent or False):
            raise ValueError("Invalid subagent conversation ID")

        return conversation

    async def _create_agent_response(
        self,
        conn: asyncpg.Connection,
        ctx: SubAgentContext,
        conversation_id: str,
    ) -> str:
        """Create agent_response for subagent."""
        return await create_agent_response(
            conn=conn,
            user_id=ctx.user_id,
            conversation_id=conversation_id,
            branch_id=None,  # Will be set later
            agent_type=AGENT_TYPE_SUBAGENT_EXPLORE,
            parent_agent_response_id=ctx.parent_agent_response_id,
        )

    async def _get_or_create_branch(
        self,
        conn: asyncpg.Connection,
        conversation_id: str,
    ) -> str:
        """Get or create default branch for subagent."""
        conversation = await get_conversation(conn, conversation_id)
        if conversation and conversation.current_branch_id:
            return conversation.current_branch_id

        raise ValueError(
            f"Subagent conversation {conversation_id} has no branch. This should not happen."
        )

    # ------------------------------------------------------------
    # Execution logic
    # ------------------------------------------------------------
    async def _iterate_agent_responses(
        self,
        run_config: RunConfig,
        branch_context: BranchContext,
        tools: List[Dict[str, Any]],
        subagent_metadata: SubAgentMetadata,
        parent_agent_response_id: str,
        max_turns: int,
    ) -> Tuple[int, int, str]:
        """
        Iterate through subagent responses until completion or max turns.

        Returns:
            Tuple of (turns_used, total_tokens, final_content)
        """
        turn = 0
        total_tokens = 0
        final_content = ""
        completed_naturally = False

        while turn < max_turns:
            # CHECK PARENT STOP 1: Before starting new iteration (safe point - no tokens consumed yet)
            if await self._should_stop_by_parent_signal(
                user_id=run_config.user_id,
                parent_conversation_id=subagent_metadata.parent_conversation_id,
                parent_agent_response_id=parent_agent_response_id,
            ):
                await self._handle_stop_by_parent_signal(
                    branch_context.agent_response_id
                )
                break

            logger.info(f"Subagent iteration {turn + 1}/{max_turns}")

            result = await self.iteration_runner.run(
                run_config=run_config,
                branch_context=branch_context,
                tools=tools,
                subagent_metadata=subagent_metadata,
                current_turn=turn,
                max_turns=max_turns,
            )

            total_tokens += result.tokens_used
            final_content = result.final_content or final_content
            turn += 1

            # CHECK PARENT STOP 2: After billing is saved (safe point - all tokens billed)
            if await self._should_stop_by_parent_signal(
                user_id=run_config.user_id,
                parent_conversation_id=subagent_metadata.parent_conversation_id,
                parent_agent_response_id=parent_agent_response_id,
            ):
                await self._handle_stop_by_parent_signal(
                    branch_context.agent_response_id
                )
                break

            if result.should_stop:
                completed_naturally = True
                break

        # If subagent hit max_turns without composing a final response,
        # prepend a truncation warning so the main agent knows and can decide to resume.
        if not completed_naturally and turn >= max_turns:
            truncation_notice = (
                "[SUBAGENT INCOMPLETE] This Explore subagent was terminated after reaching "
                f"the maximum iteration limit ({max_turns} turns) before it could compose "
                "a final report. The partial findings above (if any) may be incomplete.\n"
                "You can RESUME this subagent using the `resume` parameter with its subagent_id "
                "to let it continue and finish the report, or proceed with whatever information "
                "is available."
            )
            if final_content:
                final_content = f"{truncation_notice}\n\n---\n\n{final_content}"
            else:
                final_content = truncation_notice

        return turn, total_tokens, final_content

    async def _should_stop_by_parent_signal(
        self,
        user_id: str,
        parent_conversation_id: str,
        parent_agent_response_id: str,
    ) -> bool:
        """Check if stop signal exists for this subagent execution by parent signal."""
        return await self.socket_service.redis_agent_manager.check_agent_stop_signal(
            user_id=user_id,
            conversation_id=parent_conversation_id,
            agent_response_id=parent_agent_response_id,
        )

    async def _handle_stop_by_parent_signal(self, agent_response_id: str) -> None:
        """Handle stop signal by parent signal."""
        async with async_db_transaction() as conn:
            await stop_agent_response(conn, agent_response_id)

    async def _finalize_and_deduct(self, agent_response_id: str) -> None:
        """Finalize agent_response (status='completed') and deduct credits."""
        async with async_db_transaction() as conn:
            await finalize_agent_response(conn, agent_response_id)
            await deduct_credits_after_agent(conn, agent_response_id)
