"""Agent event handlers: agent_trigger, agent_question_answer, agent_stop, edit_humes_regenerate."""

from typing import TYPE_CHECKING, Any, Dict, Optional, Tuple

import socketio
from pydantic import ValidationError

from src.database.postgres import get_async_connection
from src.database.postgres.connection import async_db_transaction
from src.database.postgres.repositories.agent_queries import (
    create_branch_before_message,
    get_agent_response_for_user,
    get_agent_response_id_from_message_id,
    get_conversation,
    get_conversation_branches,
    get_message,
)
from src.socket_service.emitters import SocketEmitters
from src.socket_service.schemas import CustomerTabData
from src.socket_service.utils import (
    emit_internal_error_to_sid,
    get_user_id_or_emit_error,
    with_conversation_lock,
)
from src.utils.logger import get_logger

if TYPE_CHECKING:
    from src.agent.general_agent import AgentRunner
    from src.redis_client.redis_agent_manager import RedisAgentManager
    from src.redis_client.redis_user_sessions import RedisUserSessions

logger = get_logger()


class AgentHandler:
    """Handles agent_trigger, agent_question_answer, agent_stop, edit_humes_regenerate events."""

    def __init__(
        self,
        sio: socketio.AsyncServer,
        emitters: SocketEmitters,
        session_manager: "RedisUserSessions",
        state_manager: "RedisAgentManager",
        agent_runner: Optional["AgentRunner"] = None,
    ):
        self.sio = sio
        self.emitters = emitters
        self.session_manager = session_manager
        self.state_manager = state_manager
        self.agent_runner = agent_runner  # Set via set_dependencies(agent_runner)

    async def _validate_conversation_id(
        self, user_id: str, conversation_id: Optional[str]
    ) -> bool:
        """Validate conversation_id. Returns True if valid, False otherwise."""
        if not conversation_id:
            await self.emitters.emit_agent_error(
                user_id=user_id,
                conv_id="",
                error_type="validation_error",
                code="MISSING_CONVERSATION_ID",
                message="Missing conversation_id",
            )
            return False
        return True

    async def _validate_and_process_image_urls(
        self,
        user_id: str,
        conversation_id: str,
        image_urls_raw: Any,
        sid: str,
    ) -> Tuple[bool, Optional[list]]:
        """Validate and process image_urls. Returns (success, image_urls)."""
        if image_urls_raw is None:
            return (True, None)

        if not isinstance(image_urls_raw, list):
            await self.emitters.emit_agent_error(
                user_id=user_id,
                conv_id=conversation_id,
                error_type="validation_error",
                code="INVALID_IMAGE_URLS",
                message="image_urls must be an array",
            )
            return (False, None)

        image_urls = []
        for url in image_urls_raw:
            if not isinstance(url, str):
                await self.emitters.emit_agent_error(
                    user_id=user_id,
                    conv_id=conversation_id,
                    error_type="validation_error",
                    code="INVALID_IMAGE_URL",
                    message="All image_urls must be strings",
                )
                return (False, None)
            url = url.strip()
            if not url:
                continue
            if not url.startswith("https://") or ".s3." not in url:
                logger.warning(f"Invalid image URL format from session {sid}: {url}")
            image_urls.append(url)

        return (True, image_urls if image_urls else None)

    async def _validate_and_process_active_tab(
        self, active_tab_raw: Any, sid: str
    ) -> Optional[Dict[str, str]]:
        """Validate and process active_tab. Returns None if not provided or invalid, dict if valid."""
        if active_tab_raw is None:
            return None

        try:
            if isinstance(active_tab_raw, dict):
                tab = CustomerTabData(**active_tab_raw)
                return {"type": tab.type, "id": tab.id}
            else:
                logger.warning(
                    f"Invalid active_tab format from session {sid}: expected dict"
                )
        except ValidationError as e:
            logger.warning(
                f"Invalid active_tab payload from session {sid}: {e.errors()}"
            )

        return None

    async def _emit_conversation_busy_error(
        self, user_id: str, conversation_id: str
    ) -> None:
        """Emit conversation busy error."""
        await self.emitters.emit_agent_error(
            user_id=user_id,
            conv_id=conversation_id,
            error_type="conversation_busy",
            code="CONVERSATION_BUSY",
            message="Conversation is busy. Please wait for the current run to finish.",
        )

    async def _handle_error(
        self,
        sid: str,
        error: Exception,
        user_id: Optional[str],
        conversation_id: Optional[str] = None,
        error_context: str = "",
    ) -> None:
        """Handle errors consistently across all handlers."""
        logger.error(f"Error {error_context}for session {sid}: {str(error)}")
        uid = user_id or await self.session_manager.get_user_by_session_id(sid)
        conv_id = conversation_id or ""
        if conv_id is None and hasattr(error, "conversation_id"):
            conv_id = getattr(error, "conversation_id", None) or ""

        if uid:
            await self.emitters.emit_agent_error(
                user_id=uid,
                conv_id=conv_id,
                error_type="internal_error",
                code="INTERNAL_ERROR",
                message=f"Internal error: {str(error)}",
            )
        else:
            await emit_internal_error_to_sid(
                self.sio, sid, f"Internal error: {str(error)}", conv_id or None
            )

    async def handle_trigger(self, sid: str, data: Any) -> None:
        """Handle agent_trigger event."""
        user_id: Optional[str] = None
        conversation_id: Optional[str] = None
        try:
            user_id = await get_user_id_or_emit_error(
                sid, self.sio, self.session_manager
            )
            if not user_id:
                return

            conversation_id = data.get("conversation_id")
            new_human_mes = data.get("new_human_mes")
            image_urls_raw = data.get("image_urls")
            active_tab_raw = data.get("active_tab")

            if not await self._validate_conversation_id(user_id, conversation_id):
                return

            if not new_human_mes or not isinstance(new_human_mes, str):
                await self.emitters.emit_agent_error(
                    user_id=user_id,
                    conv_id=conversation_id or "",
                    error_type="validation_error",
                    code="INVALID_MESSAGE",
                    message="Missing or invalid message",
                )
                return

            success, image_urls = await self._validate_and_process_image_urls(
                user_id, conversation_id, image_urls_raw, sid
            )
            if not success:
                return  # Error already emitted

            active_tab = await self._validate_and_process_active_tab(
                active_tab_raw, sid
            )

            async with with_conversation_lock(
                self.state_manager,
                user_id,
                conversation_id,
                ttl_seconds=180,
            ) as acquired:
                if not acquired:
                    await self._emit_conversation_busy_error(user_id, conversation_id)
                    return

                await self.agent_runner.run(
                    user_id=user_id,
                    conversation_id=conversation_id,
                    new_human_mes=new_human_mes,
                    image_urls=image_urls,
                    active_tab=active_tab,
                )

        except Exception as e:
            await self._handle_error(
                sid, e, user_id, conversation_id, "handling agent_trigger "
            )

    async def handle_edit_humes_regenerate(self, sid: str, data: Any) -> None:
        """Handle edit HuMes and regenerate event."""
        try:
            user_id = await get_user_id_or_emit_error(
                sid, self.sio, self.session_manager
            )
            if not user_id:
                return

            conversation_id = data.get("conversation_id")
            branch_id = data.get("branch_id")
            message_id = data.get("message_id")
            edited_content = data.get("edited_content")
            image_urls_raw = data.get("image_urls")
            active_tab_raw = data.get("active_tab")

            if not await self._validate_conversation_id(user_id, conversation_id):
                return

            if not branch_id:
                await self.emitters.emit_agent_error(
                    user_id=user_id,
                    conv_id=conversation_id,
                    error_type="validation_error",
                    code="MISSING_BRANCH_ID",
                    message="Missing branch_id",
                )
                return

            if not message_id:
                await self.emitters.emit_agent_error(
                    user_id=user_id,
                    conv_id=conversation_id,
                    error_type="validation_error",
                    code="MISSING_MESSAGE_ID",
                    message="Missing message_id",
                )
                return

            if not edited_content or not isinstance(edited_content, str):
                await self.emitters.emit_agent_error(
                    user_id=user_id,
                    conv_id=conversation_id,
                    error_type="validation_error",
                    code="INVALID_EDITED_CONTENT",
                    message="Missing or invalid edited_content",
                )
                return

            success, image_urls = await self._validate_and_process_image_urls(
                user_id, conversation_id, image_urls_raw, sid
            )
            if not success:
                return  # Error already emitted

            active_tab = await self._validate_and_process_active_tab(
                active_tab_raw, sid
            )

            async with get_async_connection() as conn:
                conversation = await get_conversation(conn, conversation_id)
                if not conversation:
                    await self.emitters.emit_agent_error(
                        user_id=user_id,
                        conv_id=conversation_id,
                        error_type="not_found",
                        code="CONVERSATION_NOT_FOUND",
                        message="Conversation not found",
                    )
                    return

                if conversation.user_id != user_id:
                    await self.emitters.emit_agent_error(
                        user_id=user_id,
                        conv_id=conversation_id,
                        error_type="access_denied",
                        code="ACCESS_DENIED",
                        message="Access denied to this conversation",
                    )
                    return

                message = await get_message(conn, message_id)
                if not message:
                    await self.emitters.emit_agent_error(
                        user_id=user_id,
                        conv_id=conversation_id,
                        error_type="not_found",
                        code="MESSAGE_NOT_FOUND",
                        message="Message not found",
                    )
                    return

                if message.role != "user":
                    await self.emitters.emit_agent_error(
                        user_id=user_id,
                        conv_id=conversation_id,
                        error_type="validation_error",
                        code="INVALID_MESSAGE_ROLE",
                        message="Message must be a user message (HuMes)",
                    )
                    return

                if message.type not in ("message", "user_input"):
                    await self.emitters.emit_agent_error(
                        user_id=user_id,
                        conv_id=conversation_id,
                        error_type="validation_error",
                        code="INVALID_MESSAGE_TYPE",
                        message="Message type must be 'message' or 'user_input'",
                    )
                    return

                # Skip has_text validation if edited_content is provided
                # because edited_content already contains text (validated above)
                # This allows editing image-only messages by adding text
                if not edited_content or not edited_content.strip():
                    has_text = False
                    if isinstance(message.content, str):
                        has_text = bool(message.content.strip())
                    elif isinstance(message.content, dict):
                        if message.content.get("text"):
                            has_text = bool(str(message.content.get("text")).strip())
                        elif message.content.get("items"):
                            for item in message.content.get("items", []):
                                if (
                                    isinstance(item, dict)
                                    and item.get("type") == "text"
                                ):
                                    if item.get("text"):
                                        has_text = bool(str(item.get("text")).strip())
                                        break

                    if not has_text:
                        await self.emitters.emit_agent_error(
                            user_id=user_id,
                            conv_id=conversation_id,
                            error_type="validation_error",
                            code="MESSAGE_NO_TEXT",
                            message="Message must have text content (cannot be image-only)",
                        )
                        return

            async with with_conversation_lock(
                self.state_manager,
                user_id,
                conversation_id,
                ttl_seconds=180,
            ) as acquired:
                if not acquired:
                    await self._emit_conversation_busy_error(user_id, conversation_id)
                    return

                await self.sio.emit(
                    "edit_humes_regenerate.acknowledged",
                    {
                        "conversation_id": conversation_id,
                        "branch_id": branch_id,
                        "message_id": message_id,
                        "message": "Request received. Creating branch and starting regeneration...",
                    },
                    room=sid,
                )

                async with async_db_transaction() as conn:
                    new_branch_id = await create_branch_before_message(
                        conn=conn,
                        conversation_id=conversation_id,
                        target_message_id=message_id,
                        source_branch_id=branch_id,
                        branch_name=None,
                    )
                    branches = await get_conversation_branches(conn, conversation_id)
                    branch_data = next(
                        (b for b in branches if b["id"] == new_branch_id), None
                    )

                if branch_data:
                    await self.emitters.emit_branch_created(
                        user_id=user_id,
                        conv_id=conversation_id,
                        branch_data=branch_data,
                    )

                await self.agent_runner.run(
                    user_id=user_id,
                    conversation_id=conversation_id,
                    new_human_mes=edited_content,
                    image_urls=image_urls,
                    active_tab=active_tab,
                )

        except Exception as e:
            await self._handle_error(
                sid, e, user_id, conversation_id, "handling edit_humes_regenerate "
            )

    async def handle_question_answer(self, sid: str, data: Any) -> None:
        """Handle agent_question_answer event."""
        user_id: Optional[str] = None
        conversation_id: Optional[str] = None
        try:
            user_id = await get_user_id_or_emit_error(
                sid, self.sio, self.session_manager
            )
            if not user_id:
                return

            conversation_id = data.get("conversation_id")
            message_id = data.get("message_id")
            call_id = data.get("call_id")
            answers = data.get("answers", {})
            text = data.get("text", "")
            image_urls_raw = data.get("image_urls")
            active_tab_raw = data.get("active_tab")

            if not await self._validate_conversation_id(user_id, conversation_id):
                return

            if not message_id:
                await self.emitters.emit_agent_error(
                    user_id=user_id,
                    conv_id=conversation_id or "",
                    error_type="validation_error",
                    code="MISSING_MESSAGE_ID",
                    message="Missing message_id. Both message_id and call_id are required.",
                )
                return

            if not call_id:
                await self.emitters.emit_agent_error(
                    user_id=user_id,
                    conv_id=conversation_id or "",
                    error_type="validation_error",
                    code="MISSING_CALL_ID",
                    message="Missing call_id. Both message_id and call_id are required.",
                )
                return

            agent_response_id: Optional[str] = None
            async with async_db_transaction() as conn:
                message = await get_message(conn, message_id)
                if not message:
                    await self.emitters.emit_agent_error(
                        user_id=user_id,
                        conv_id=conversation_id,
                        error_type="validation_error",
                        code="MESSAGE_NOT_FOUND",
                        message="Message not found",
                    )
                    return

                if message.type != "function_call":
                    await self.emitters.emit_agent_error(
                        user_id=user_id,
                        conv_id=conversation_id,
                        error_type="validation_error",
                        code="INVALID_MESSAGE_TYPE",
                        message="Message is not a function_call",
                    )
                    return

                if not message.call_id:
                    await self.emitters.emit_agent_error(
                        user_id=user_id,
                        conv_id=conversation_id,
                        error_type="validation_error",
                        code="MESSAGE_MISSING_CALL_ID",
                        message="Message does not have call_id",
                    )
                    return

                if message.call_id != call_id:
                    await self.emitters.emit_agent_error(
                        user_id=user_id,
                        conv_id=conversation_id,
                        error_type="validation_error",
                        code="CALL_ID_MISMATCH",
                        message="call_id does not match the message's call_id",
                    )
                    return

                agent_response_id = await get_agent_response_id_from_message_id(
                    conn, message_id, conversation_id, user_id
                )

            if not agent_response_id:
                await self.emitters.emit_agent_error(
                    user_id=user_id,
                    conv_id=conversation_id,
                    error_type="validation_error",
                    code="AGENT_RESPONSE_NOT_FOUND",
                    message="No waiting agent response found for the provided message_id",
                )
                return

            if answers is not None and not isinstance(answers, dict):
                await self.emitters.emit_agent_error(
                    user_id=user_id,
                    conv_id=conversation_id,
                    error_type="validation_error",
                    code="INVALID_ANSWERS",
                    message="Answers must be a dictionary",
                )
                return

            if text is not None and not isinstance(text, str):
                await self.emitters.emit_agent_error(
                    user_id=user_id,
                    conv_id=conversation_id,
                    error_type="validation_error",
                    code="INVALID_TEXT",
                    message="Text must be a string",
                )
                return

            success, image_urls = await self._validate_and_process_image_urls(
                user_id, conversation_id, image_urls_raw, sid
            )
            if not success:
                return  # Error already emitted

            active_tab = await self._validate_and_process_active_tab(
                active_tab_raw, sid
            )

            async with with_conversation_lock(
                self.state_manager,
                user_id,
                conversation_id,
                ttl_seconds=180,
            ) as acquired:
                if not acquired:
                    await self._emit_conversation_busy_error(user_id, conversation_id)
                    return

                await self.agent_runner.resume_with_answer(
                    user_id=user_id,
                    conversation_id=conversation_id,
                    agent_response_id=agent_response_id,
                    answers=answers,
                    text=text,
                    call_id=call_id,
                    image_urls=image_urls,
                    active_tab=active_tab,
                )

        except Exception as e:
            await self._handle_error(
                sid, e, user_id, conversation_id, "handling agent_question_answer "
            )

    async def handle_stop(self, sid: str, data: Any) -> None:
        """Handle agent_stop event."""
        try:
            user_id = await get_user_id_or_emit_error(
                sid, self.sio, self.session_manager
            )
            if not user_id:
                return

            if not data or not isinstance(data, dict):
                await self.emitters.emit_agent_error(
                    user_id=user_id,
                    conv_id="",
                    error_type="validation_error",
                    code="INVALID_REQUEST_DATA",
                    message="Invalid request data",
                )
                return

            conversation_id = data.get("conversation_id")
            agent_response_id = data.get("agent_response_id")

            if not await self._validate_conversation_id(user_id, conversation_id):
                return

            if not agent_response_id:
                await self.emitters.emit_agent_error(
                    user_id=user_id,
                    conv_id=conversation_id or "",
                    error_type="validation_error",
                    code="MISSING_AGENT_RESPONSE_ID",
                    message="Missing agent_response_id",
                )
                return

            async with get_async_connection() as conn:
                agent_response = await get_agent_response_for_user(
                    conn, agent_response_id, user_id
                )
                if not agent_response:
                    await self.emitters.emit_agent_error(
                        user_id=user_id,
                        conv_id=conversation_id,
                        error_type="not_found",
                        code="AGENT_RESPONSE_NOT_FOUND",
                        message="Agent response not found or you don't own it",
                        agent_response_id=agent_response_id,
                    )
                    return

                status = agent_response["status"]
                if status not in ("in_progress",):
                    await self.emitters.emit_agent_error(
                        user_id=user_id,
                        conv_id=conversation_id,
                        error_type="invalid_state",
                        code="AGENT_ALREADY_STOPPED",
                        message=f"Agent is already {status}. Cannot stop a completed/failed agent.",
                        agent_response_id=agent_response_id,
                    )
                    return

            stop_set = await self.state_manager.set_agent_stop_signal(
                user_id=user_id,
                conversation_id=conversation_id,
                agent_response_id=agent_response_id,
                ttl_seconds=300,
            )

            if not stop_set:
                await self.emitters.emit_agent_error(
                    user_id=user_id,
                    conv_id=conversation_id,
                    error_type="internal_error",
                    code="FAILED_TO_SET_STOP_SIGNAL",
                    message="Failed to set stop signal",
                    agent_response_id=agent_response_id,
                )
                return

            await self.sio.emit(
                "agent.stop.acknowledged",
                {
                    "conversation_id": conversation_id,
                    "agent_response_id": agent_response_id,
                    "message": "Stop signal set. Agent will stop at next safe checkpoint.",
                },
                room=sid,
            )

        except Exception as e:
            await self._handle_error(
                sid, e, user_id, conversation_id, "handling agent_stop "
            )
