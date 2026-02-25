"""Common helpers for socket handlers: auth check, lock helpers."""

from contextlib import asynccontextmanager
from typing import TYPE_CHECKING, Optional

import socketio

from src.utils.logger import get_logger

if TYPE_CHECKING:
    from src.redis_client.redis_agent_manager import RedisAgentManager
    from src.redis_client.redis_user_sessions import RedisUserSessions

logger = get_logger()

UNAUTHORIZED_PAYLOAD = {
    "error_type": "unauthorized",
    "code": "UNAUTHORIZED",
    "message": "Unauthorized: No valid session",
    "conv_id": None,
}


async def get_user_id_or_emit_error(
    sid: str,
    sio: socketio.AsyncServer,
    session_manager: "RedisUserSessions",
) -> Optional[str]:
    """
    Resolve user_id from session. If none, emit agent.error (unauthorized) to sid and return None.
    """
    user_id = await session_manager.get_user_by_session_id(sid)
    if not user_id:
        await emit_unauthorized_error(sio, sid)
        return None
    return user_id


async def emit_unauthorized_error(sio: socketio.AsyncServer, sid: str) -> None:
    """Emit agent.error (unauthorized) to the given session."""
    await sio.emit("agent.error", UNAUTHORIZED_PAYLOAD, room=sid)


async def emit_internal_error_to_sid(
    sio: socketio.AsyncServer,
    sid: str,
    message: str,
    conv_id: Optional[str] = None,
) -> None:
    """Emit agent.error (internal_error) to the given session (e.g. when user_id unknown)."""
    payload = {
        "error_type": "internal_error",
        "code": "INTERNAL_ERROR",
        "message": message,
        "conv_id": conv_id,
    }
    await sio.emit("agent.error", payload, room=sid)


@asynccontextmanager
async def with_conversation_lock(
    state_manager: "RedisAgentManager",
    user_id: str,
    conversation_id: str,
    ttl_seconds: int = 180,
):
    """
    Async context manager for conversation lock.
    Yields True if lock acquired, False otherwise. Releases on exit when acquired.
    """
    acquired = await state_manager.acquire_conversation_lock(
        user_id=user_id,
        conversation_id=conversation_id,
        ttl_seconds=ttl_seconds,
    )
    try:
        yield acquired
    finally:
        if acquired:
            released = await state_manager.release_conversation_lock(
                user_id=user_id,
                conversation_id=conversation_id,
            )
            if not released:
                logger.warning(
                    "Failed to release conversation lock for user %s, conversation %s",
                    user_id,
                    conversation_id,
                )


def filter_out_system_prompts(context):
    """Filter out system prompts from context."""
    if not context:
        return context
    filtered_context = []
    for item in context:
        if isinstance(item, tuple):
            message_id, message = item
            if message.get("role") != "system":
                filtered_context.append(item)
        else:
            if item.get("role") != "system":
                filtered_context.append(item)
    return filtered_context
