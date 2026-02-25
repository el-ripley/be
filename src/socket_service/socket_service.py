"""Socket.IO service for real-time communication."""

from typing import TYPE_CHECKING, Any, Dict, Optional

import socketio

from src.redis_client.redis_agent_manager import RedisAgentManager
from src.redis_client.redis_user_sessions import RedisUserSessions
from src.services.auth_service import AuthService
from src.socket_service.emitters import SocketEmitters
from src.socket_service.handlers.agent import AgentHandler
from src.socket_service.handlers.connection import ConnectionHandler
from src.socket_service.handlers.context import ContextHandler

if TYPE_CHECKING:
    from src.agent.general_agent import AgentRunner


class SocketService:
    """Service for handling Socket.IO connections and events."""

    def __init__(
        self,
        sio: socketio.AsyncServer,
        auth_service: AuthService,
        redis_agent_manager: RedisAgentManager,
        redis_user_sessions: RedisUserSessions,
    ):
        self.sio = sio
        self.auth_service = auth_service
        self.redis_agent_manager = redis_agent_manager
        self.redis_user_sessions = redis_user_sessions

        self.emitters = SocketEmitters(sio)
        self.connection_handler = ConnectionHandler(
            sio, auth_service, redis_user_sessions
        )
        self.agent_handler = AgentHandler(
            sio,
            self.emitters,
            redis_user_sessions,
            redis_agent_manager,
            agent_runner=None,
        )
        self.context_handler = ContextHandler(
            sio, redis_user_sessions, messages_service=None
        )

        self._register_events()

    def _register_events(self) -> None:
        """Register all socket event handlers."""
        self.sio.on("connect", self.connection_handler.handle_connect)
        self.sio.on("disconnect", self.connection_handler.handle_disconnect)
        self.sio.on("agent_trigger", self.agent_handler.handle_trigger)
        self.sio.on("agent_question_answer", self.agent_handler.handle_question_answer)
        self.sio.on("agent_stop", self.agent_handler.handle_stop)
        self.sio.on(
            "edit_humes_regenerate",
            self.agent_handler.handle_edit_humes_regenerate,
        )
        self.sio.on("get_context", self.context_handler.handle_get_context)

    def set_dependencies(self, agent_runner: "AgentRunner") -> None:
        self.agent_runner = agent_runner
        self.messages_service = agent_runner.context_manager.context_builder
        self.agent_handler.agent_runner = agent_runner
        self.context_handler.messages_service = self.messages_service

    # -------------------------------------------------------------------------
    # Emit methods (delegate to emitters, keep backward compatibility)
    # -------------------------------------------------------------------------

    async def emit_agent_event(
        self,
        user_id: str,
        conv_id: str,
        branch_id: str,
        agent_response_id: Optional[str] = None,
        msg_type: Optional[str] = None,
        event_name: Optional[str] = None,
        msg_item: Optional[Dict[str, Any]] = None,
        msg_id: Optional[str] = None,
        delta: Optional[str] = None,
        subagent_metadata: Optional[Dict[str, Any]] = None,
    ) -> None:
        await self.emitters.emit_agent_event(
            user_id=user_id,
            conv_id=conv_id,
            branch_id=branch_id,
            agent_response_id=agent_response_id,
            msg_type=msg_type,
            event_name=event_name,
            msg_item=msg_item,
            msg_id=msg_id,
            delta=delta,
            subagent_metadata=subagent_metadata,
        )

    async def emit_agent_error(
        self,
        user_id: str,
        conv_id: str,
        error_type: str,
        code: str,
        message: str,
        branch_id: Optional[str] = None,
        agent_response_id: Optional[str] = None,
    ) -> None:
        await self.emitters.emit_agent_error(
            user_id=user_id,
            conv_id=conv_id,
            error_type=error_type,
            code=code,
            message=message,
            branch_id=branch_id,
            agent_response_id=agent_response_id,
        )

    async def emit_agent_warning(
        self,
        user_id: str,
        conv_id: str,
        warning_type: str,
        reason: str,
        branch_id: Optional[str] = None,
        agent_response_id: Optional[str] = None,
        has_partial_content: bool = False,
    ) -> None:
        await self.emitters.emit_agent_warning(
            user_id=user_id,
            conv_id=conv_id,
            warning_type=warning_type,
            reason=reason,
            branch_id=branch_id,
            agent_response_id=agent_response_id,
            has_partial_content=has_partial_content,
        )

    async def emit_agent_stopped(
        self,
        user_id: str,
        conv_id: str,
        branch_id: str,
        agent_response_id: str,
    ) -> None:
        await self.emitters.emit_agent_stopped(
            user_id=user_id,
            conv_id=conv_id,
            branch_id=branch_id,
            agent_response_id=agent_response_id,
        )

    async def emit_branch_created(
        self,
        user_id: str,
        conv_id: str,
        branch_data: Dict[str, Any],
    ) -> None:
        await self.emitters.emit_branch_created(
            user_id=user_id,
            conv_id=conv_id,
            branch_data=branch_data,
        )

    async def emit_suggest_response_event(
        self,
        user_id: str,
        conversation_type: str,
        conversation_id: str,
        event_name: str,
        data: Optional[Dict[str, Any]] = None,
    ) -> None:
        await self.emitters.emit_suggest_response_event(
            user_id=user_id,
            conversation_type=conversation_type,
            conversation_id=conversation_id,
            event_name=event_name,
            data=data,
        )

    async def send_webhook_event(
        self,
        user_id: str,
        event_type: str,
        event_data: Dict[str, Any],
    ) -> None:
        await self.emitters.send_webhook_event(
            user_id=user_id,
            event_type=event_type,
            event_data=event_data,
        )

    async def emit_notification(
        self, user_id: str, notification_data: Dict[str, Any]
    ) -> None:
        await self.emitters.emit_notification(
            user_id=user_id, notification_data=notification_data
        )
