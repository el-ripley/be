"""Balance guard for checking and deducting credits."""

from src.database.postgres.connection import async_db_transaction
from src.database.postgres.repositories.agent_queries import finalize_agent_response
from src.socket_service import SocketService
from src.utils.logger import get_logger

logger = get_logger()


class BalanceGuard:
    """Guard for checking and deducting credits."""

    def __init__(self, socket_service: SocketService):
        self.socket_service = socket_service

    async def check_can_run(
        self, user_id: str, conversation_id: str
    ) -> bool:
        """Check if user has sufficient balance. Emits error if not.
        
        Returns:
            True if user can run agent, False otherwise.
        """
        async with async_db_transaction() as conn:
            from src.billing.credit_service import can_use_ai, get_balance
            from src.billing.repositories import billing_queries

            if not await can_use_ai(conn, user_id):
                balance = await get_balance(conn, user_id)
                min_balance_usd = await billing_queries.get_billing_setting(
                    conn, "min_balance_usd"
                )

                error_msg = (
                    f"Insufficient balance. Current balance: ${balance:.4f}. "
                    f"Minimum required: ${min_balance_usd:.4f}. "
                    f"Please top up your credits to continue using AI."
                )

                await self.socket_service.emit_agent_error(
                    user_id=user_id,
                    conv_id=conversation_id,
                    error_type="insufficient_balance",
                    code="INSUFFICIENT_BALANCE",
                    message=error_msg,
                    branch_id=None,
                    agent_response_id=None,
                )
                return False
        return True

    async def finalize_and_deduct(self, agent_response_id: str) -> None:
        """Finalize agent_response and deduct credits."""
        async with async_db_transaction() as conn:
            await finalize_agent_response(conn, agent_response_id)
            # Deduct credits after finalization
            from src.billing.credit_service import deduct_credits_after_agent

            await deduct_credits_after_agent(conn, agent_response_id)
