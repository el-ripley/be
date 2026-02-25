"""Context handler: get_context."""

from typing import TYPE_CHECKING, Any, Optional

import socketio

from src.database.postgres import get_async_connection
from src.database.postgres.repositories.agent_queries import (
    get_latest_conversation_token_count,
)
from src.socket_service.utils import filter_out_system_prompts
from src.utils.estimate_context_tokens_o200k_base import estimate_context_tokens
from src.utils.logger import get_logger
from src.utils.serialization import to_serializable

if TYPE_CHECKING:
    from src.redis_client.redis_user_sessions import RedisUserSessions

logger = get_logger()


class ContextHandler:
    """Handles get_context event."""

    def __init__(
        self,
        sio: socketio.AsyncServer,
        session_manager: "RedisUserSessions",
        messages_service: Optional[Any] = None,
    ):
        self.sio = sio
        self.session_manager = session_manager
        self.messages_service = messages_service  # Set via set_dependencies

    async def handle_get_context(self, sid: str, data: Any) -> None:
        """Handle client request to get conversation context and/or token count."""
        try:
            user_id = await self.session_manager.get_user_by_session_id(sid)
            if not user_id:
                logger.warning(f"No user found for session {sid}")
                await self.sio.emit(
                    "context.error",
                    {"error": "Unauthorized: No valid session"},
                    room=sid,
                )
                return

            if not data or not isinstance(data, dict):
                logger.warning(f"Invalid data for get_context from session {sid}")
                await self.sio.emit(
                    "context.error",
                    {"error": "Invalid request data"},
                    room=sid,
                )
                return

            conversation_id = data.get("conversation_id")
            include_context = data.get("include_context", True)
            include_tokens = data.get("include_tokens", True)

            if not conversation_id:
                logger.warning(
                    f"Missing conversation_id for get_context from session {sid}"
                )
                await self.sio.emit(
                    "context.error",
                    {"error": "Missing conversation_id"},
                    room=sid,
                )
                return

            context = None
            if include_context and self.messages_service:
                # Need conn and user_id to query media_assets for media_ids
                async with get_async_connection() as conn:
                    context = await self.messages_service.build_context(
                        conversation_id=conversation_id,
                        conn=conn,
                        user_id=user_id,
                    )
                context = filter_out_system_prompts(context)

            response_data: dict = {"conversation_id": conversation_id}

            if include_context and context:
                response_data["context"] = to_serializable(context)

            if include_tokens:
                tokens = None
                async with get_async_connection() as conn:
                    tokens = await get_latest_conversation_token_count(
                        conn, conversation_id
                    )

                if tokens is None:
                    if context is None and self.messages_service:
                        # Need conn and user_id to query media_assets for media_ids
                        async with get_async_connection() as conn:
                            context = await self.messages_service.build_context(
                                conversation_id=conversation_id,
                                conn=conn,
                                user_id=user_id,
                            )
                        context = filter_out_system_prompts(context)
                    tokens = estimate_context_tokens(context) if context else 0

                response_data["tokens"] = tokens

            await self.sio.emit("context.response", response_data, room=sid)

        except Exception as e:
            logger.error(
                f"Error handling get_context for session {sid}: {str(e)}"
            )
            await self.sio.emit(
                "context.error",
                {"error": f"Internal error: {str(e)}"},
                room=sid,
            )
