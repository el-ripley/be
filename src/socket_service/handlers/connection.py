"""Connection handlers: connect, disconnect."""

from typing import TYPE_CHECKING, Any

import socketio

from src.utils.logger import get_logger

if TYPE_CHECKING:
    from src.redis_client.redis_user_sessions import RedisUserSessions
    from src.services.auth_service import AuthService

logger = get_logger()


class ConnectionHandler:
    """Handles socket connect and disconnect events."""

    def __init__(
        self,
        sio: socketio.AsyncServer,
        auth_service: "AuthService",
        session_manager: "RedisUserSessions",
    ):
        self.sio = sio
        self.auth_service = auth_service
        self.session_manager = session_manager

    async def handle_connect(self, sid: str, environ: Any, auth: Any) -> bool:
        """Handle client connection with authentication."""
        try:
            if not auth or not isinstance(auth, dict):
                logger.warning(f"No auth data provided for connection {sid}")
                await self.sio.disconnect(sid)
                return False

            token = auth.get("token")
            if not token:
                logger.warning(f"No token provided for connection {sid}")
                await self.sio.disconnect(sid)
                return False

            user_data = self.auth_service.get_user_from_token(token)
            if not user_data:
                logger.warning(f"Invalid token for connection {sid}")
                await self.sio.disconnect(sid)
                return False

            user_id = user_data.get("id")
            if not user_id:
                logger.warning(f"No user ID in token for connection {sid}")
                await self.sio.disconnect(sid)
                return False

            await self.session_manager.remove_ttl_from_user_keys(user_id)
            await self.session_manager.add_user_session(user_id, sid)
            await self.sio.enter_room(sid, f"user_{user_id}")

            logger.info(f"User {user_id} connected with session {sid}")

            await self.sio.emit(
                "connected",
                {
                    "status": "connected",
                    "user_id": user_id,
                    "message": "Successfully connected to real-time service",
                },
                room=sid,
            )
            return True

        except Exception as e:
            logger.error(f"Error during connection: {str(e)}")
            await self.sio.disconnect(sid)
            return False

    async def handle_disconnect(self, sid: str) -> None:
        """Handle client disconnection."""
        try:
            user_id = await self.session_manager.get_user_by_session_id(sid)
            if user_id:
                logger.info(f"User {user_id} disconnected (session {sid})")
                await self.sio.leave_room(sid, f"user_{user_id}")
                await self.session_manager.remove_user_session(user_id, sid)

                has_other_sessions = (
                    await self.session_manager.has_other_active_sessions(user_id, sid)
                )

                if not has_other_sessions:
                    logger.info(
                        f"User {user_id} has no other sessions, setting TTL 15min for all keys"
                    )
                    await self.session_manager.set_ttl_for_user_keys(
                        user_id, ttl_seconds=900
                    )
                else:
                    logger.info(
                        f"User {user_id} still has other active sessions, keeping data persistent"
                    )
            else:
                logger.info(f"Unknown session {sid} disconnected")
        except Exception as e:
            logger.error(f"Error during disconnection: {str(e)}")
