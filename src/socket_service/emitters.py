"""Socket emit methods: agent events, errors, warnings, webhooks."""

from typing import Any, Dict, Optional

import socketio

from src.utils.logger import get_logger
from src.utils.serialization import to_serializable

logger = get_logger()


class SocketEmitters:
    """Handles all socket event emissions (agent, branch, suggest_response, webhook)."""

    def __init__(self, sio: socketio.AsyncServer):
        self.sio = sio

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
        try:
            room = f"user_{user_id}"
            event_data = {
                "conv_id": conv_id,
                "branch_id": branch_id,
                "agent_response_id": agent_response_id,
                "type": msg_type,
                "event_name": event_name,
                "msg_item": msg_item,
            }
            if msg_id:
                event_data["msg_id"] = msg_id
            if delta:
                event_data["delta"] = delta
            # Add subagent metadata if present
            if subagent_metadata:
                event_data.update(subagent_metadata)
            event_data = to_serializable(event_data)
            await self.sio.emit("agent.event", event_data, room=room)
        except Exception as e:
            logger.error(f"Error emitting agent event to user {user_id}: {str(e)}")

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
        """Emit fatal error event to client (stops everything)."""
        try:
            room = f"user_{user_id}"
            event_data = {
                "error_type": error_type,
                "code": code,
                "message": message,
                "conv_id": conv_id,
            }
            if branch_id:
                event_data["branch_id"] = branch_id
            if agent_response_id:
                event_data["agent_response_id"] = agent_response_id
            event_data = to_serializable(event_data)
            await self.sio.emit("agent.error", event_data, room=room)
        except Exception as e:
            logger.error(f"Error emitting agent.error to user {user_id}: {str(e)}")

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
        """Emit warning event to client (recoverable - can continue)."""
        try:
            room = f"user_{user_id}"
            event_data = {
                "warning_type": warning_type,
                "reason": reason,
                "has_partial_content": has_partial_content,
                "conv_id": conv_id,
            }
            if branch_id:
                event_data["branch_id"] = branch_id
            if agent_response_id:
                event_data["agent_response_id"] = agent_response_id
            event_data = to_serializable(event_data)
            await self.sio.emit("agent.warning", event_data, room=room)
        except Exception as e:
            logger.error(f"Error emitting agent.warning to user {user_id}: {str(e)}")

    async def emit_agent_stopped(
        self,
        user_id: str,
        conv_id: str,
        branch_id: str,
        agent_response_id: str,
    ) -> None:
        """Emit agent stopped event to client."""
        try:
            room = f"user_{user_id}"
            event_data = {
                "conv_id": conv_id,
                "branch_id": branch_id,
                "agent_response_id": agent_response_id,
                "message": "Agent stopped successfully",
            }
            event_data = to_serializable(event_data)
            await self.sio.emit("agent.run.stopped", event_data, room=room)
        except Exception as e:
            logger.error(
                f"Error emitting agent.run.stopped to user {user_id}: {str(e)}"
            )

    async def emit_branch_created(
        self,
        user_id: str,
        conv_id: str,
        branch_data: Dict[str, Any],
    ) -> None:
        """Emit branch.created event when new branch is created."""
        try:
            room = f"user_{user_id}"
            event_data = {
                "conv_id": conv_id,
                "branch": branch_data,
            }
            event_data = to_serializable(event_data)
            await self.sio.emit("branch.created", event_data, room=room)
        except Exception as e:
            logger.error(f"Error emitting branch.created to user {user_id}: {str(e)}")

    async def emit_suggest_response_event(
        self,
        user_id: str,
        conversation_type: str,
        conversation_id: str,
        event_name: str,
        data: Optional[Dict[str, Any]] = None,
    ) -> None:
        """Emit suggest_response event to client.

        Same event name and payload shape for both API-triggered and webhook-triggered
        suggest response. FE should subscribe to 'suggest_response.event' and update
        UI by conversation_id for any trigger source (not only when user clicked Gợi ý).
        """
        try:
            room = f"user_{user_id}"
            event_data = {
                "conversation_type": conversation_type,
                "conversation_id": conversation_id,
                "event_name": event_name,
            }
            if data:
                event_data.update(data)
            event_data = to_serializable(event_data)
            await self.sio.emit("suggest_response.event", event_data, room=room)
        except Exception as e:
            logger.error(
                f"Error emitting suggest_response.event to user {user_id}: {str(e)}"
            )

    async def send_webhook_event(
        self,
        user_id: str,
        event_type: str,
        event_data: Dict[str, Any],
    ) -> None:
        try:
            room = f"user_{user_id}"
            formatted_event = {
                "type": event_type,
                "event": event_data,
                "source": "facebook",
            }
            await self.sio.emit("webhook_event", formatted_event, room=room)
        except Exception as e:
            logger.error(f"Error sending webhook event to user {user_id}: {str(e)}")

    async def emit_notification(
        self, user_id: str, notification_data: Dict[str, Any]
    ) -> None:
        """Emit notification.new event when a new notification is created."""
        try:
            room = f"user_{user_id}"
            payload = to_serializable(notification_data)
            await self.sio.emit("notification.new", payload, room=room)
        except Exception as e:
            logger.error(f"Error emitting notification.new to user {user_id}: {str(e)}")
