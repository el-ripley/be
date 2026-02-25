"""Stop handler for managing agent stop signals."""

from src.database.postgres.connection import async_db_transaction
from src.database.postgres.repositories.agent_queries import stop_agent_response
from src.socket_service import SocketService
from src.agent.general_agent.core.run_config import RunConfig


# Forward declaration for BranchContext to avoid circular import
# BranchContext is defined in agent_runner.py
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from src.agent.general_agent.core.agent_runner import BranchContext

from src.utils.logger import get_logger

logger = get_logger()


class StopHandler:
    """Handle agent stop signals."""

    def __init__(self, socket_service: SocketService):
        self.socket_service = socket_service

    async def should_stop(
        self,
        run_config: RunConfig,
        branch_context: "BranchContext",
    ) -> bool:
        """Check if stop signal exists for this agent execution."""
        result = await self.socket_service.redis_agent_manager.check_agent_stop_signal(
            user_id=run_config.user_id,
            conversation_id=run_config.conversation_id,
            agent_response_id=branch_context.agent_response_id,
        )
        return result

    async def handle_stop(
        self,
        run_config: RunConfig,
        branch_context: "BranchContext",
    ) -> None:
        """Handle agent stop: finalize billing and emit stop event."""
        logger.info(
            f"Agent stop requested for agent_response {branch_context.agent_response_id}"
        )

        # Finalize agent_response with status='stopped'
        # This ensures all billing from saved openai_response records is aggregated
        # stop_agent_response already calls update_agent_response_aggregates and sets status='stopped'
        async with async_db_transaction() as conn:
            await stop_agent_response(conn, branch_context.agent_response_id)

        # Clear stop signal
        await self.socket_service.redis_agent_manager.clear_agent_stop_signal(
            user_id=run_config.user_id,
            conversation_id=run_config.conversation_id,
            agent_response_id=branch_context.agent_response_id,
        )

        # Emit stop event to client
        await self.socket_service.emit_agent_stopped(
            user_id=run_config.user_id,
            conv_id=run_config.conversation_id,
            branch_id=branch_context.current_branch_id,
            agent_response_id=branch_context.agent_response_id,
        )

        logger.info(
            f"Agent stopped successfully for agent_response {branch_context.agent_response_id}"
        )
