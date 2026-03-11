from typing import Any, Dict, List, Optional, Tuple

import asyncpg

from src.agent.common.agent_types import AGENT_TYPE_GENERAL_AGENT
from src.api.openai_conversations.schemas import MessageResponse
from src.database.postgres.entities.agent_entities import OpenAIMessage
from src.database.postgres.repositories.agent_queries import (
    create_agent_response,
    get_message,
    save_message_and_update_branch,
    set_agent_response_in_progress,
)
from src.redis_client.redis_agent_manager import RedisAgentManager
from src.utils.logger import get_logger

from .function_output_normalizer import normalize_function_output_to_api_format
from .iteration_persistence import IterationPersistence
from .iteration_warning import IterationWarningInjector
from .messages.context_builder import ContextBuilder, OpenAIMessageItem
from .temp_context_service import TempContextService

logger = get_logger()


class AgentContextManager:
    def __init__(self, redis_agent_manager: RedisAgentManager):
        self.redis_agent_manager = redis_agent_manager
        self.context_builder = ContextBuilder()
        self.temp_context_service = TempContextService(redis_agent_manager)
        self.iteration_persistence = IterationPersistence()

    @staticmethod
    def _message_entity_to_response(message: OpenAIMessage) -> MessageResponse:
        data = message.model_dump(mode="json")
        data.setdefault("is_modified", False)
        data.setdefault("modified_content", None)
        data.setdefault("modified_reasoning_summary", None)
        data.setdefault("modified_function_arguments", None)
        data.setdefault("modified_function_output", None)
        data.setdefault("is_hidden", False)
        data.setdefault("metadata", None)
        return MessageResponse(**data)

    @staticmethod
    def _convert_to_openai_entries(
        temp_messages: List[MessageResponse],
    ) -> List[Tuple[str, Dict[str, Any]]]:
        """Return (message_id, payload) pairs for Redis hash, keeping payload OpenAI clean.

        Mirrors AgentContextManager._convert_temp_messages_to_openai_entries.
        """
        import json

        entries: List[Tuple[str, Dict[str, Any]]] = []

        for msg in temp_messages:
            message_dict: Dict[str, Any] = {"type": msg.type}

            if msg.type == "reasoning":
                message_dict["summary"] = msg.reasoning_summary or []

            elif msg.type == "function_call":
                message_dict["call_id"] = msg.call_id
                message_dict["name"] = msg.function_name
                message_dict["arguments"] = (
                    json.dumps(msg.function_arguments)
                    if msg.function_arguments
                    else "{}"
                )

            elif msg.type == "function_call_output":
                message_dict["call_id"] = msg.call_id
                if isinstance(msg.function_output, list):
                    message_dict["output"] = normalize_function_output_to_api_format(
                        msg.function_output
                    )
                else:
                    message_dict["output"] = (
                        json.dumps(msg.function_output) if msg.function_output else "{}"
                    )

            elif msg.type == "web_search_call":
                message_dict["action"] = msg.web_search_action or {}

            elif msg.type == "message":
                message_dict["role"] = msg.role
                message_dict["content"] = msg.content or ""

            entries.append((msg.id, message_dict))

        return entries

    async def create_temp_context_for_current_branch(
        self,
        conn: asyncpg.Connection,
        user_id: str,
        conversation_id: str,
        new_human_mes: str,
        agent_type: str = AGENT_TYPE_GENERAL_AGENT,
        existing_agent_response_id: Optional[str] = None,
        active_tab: Optional[Dict[str, Any]] = None,
        max_iteration: int = 20,
        image_urls: Optional[List[str]] = None,
    ) -> Tuple[Optional[str], Optional[MessageResponse]]:
        try:
            current_branch_id = (
                await self.context_builder.get_current_branch_id_for_conversation(
                    conversation_id, conn=conn
                )
            )

            if current_branch_id is None:
                raise ValueError(
                    f"No current_branch_id found for conversation {conversation_id}"
                )

            user_message_model: Optional[MessageResponse] = None
            if new_human_mes or image_urls:
                # Build content array with system-reminder, text, and images
                content_items: List[Dict[str, Any]] = []

                # Add text content if provided
                if new_human_mes and new_human_mes.strip():
                    content_items.append(
                        {"type": "input_text", "text": new_human_mes.strip()}
                    )

                # Add image content if provided
                if image_urls:
                    for image_url in image_urls:
                        if image_url and image_url.strip():
                            content_items.append(
                                {"type": "input_image", "image_url": image_url.strip()}
                            )
                    # NO metadata.image_expirations - will be queried when building context

                # Prepend system-reminder if active_tab provided
                if active_tab:
                    from src.agent.general_agent.context.messages.active_tab_helper import (
                        build_active_tab_system_reminder_block,
                        prepend_system_reminder_to_content,
                    )

                    system_reminder = await build_active_tab_system_reminder_block(
                        conn=conn, active_tab=active_tab
                    )
                    content_items = prepend_system_reminder_to_content(
                        content_items, system_reminder
                    )

                # Always save as array to keep structure explicit
                content_to_save = content_items

                from src.agent.common.metadata_types import MessageMetadata

                user_metadata: MessageMetadata = {"source": "user"}
                message_id = await save_message_and_update_branch(
                    conn,
                    conversation_id,
                    current_branch_id,
                    "user",
                    content_to_save,
                    "user_input",
                    metadata=user_metadata,
                )

                message_entity = await get_message(conn, message_id)
                user_message_model = self._message_entity_to_response(message_entity)

            agent_response_id = existing_agent_response_id
            if not agent_response_id:
                agent_response_id = await create_agent_response(
                    conn=conn,
                    user_id=user_id,
                    conversation_id=conversation_id,
                    branch_id=current_branch_id,
                    agent_type=agent_type,
                )

            messages: List[
                Tuple[str, OpenAIMessageItem]
            ] = await self.context_builder.build_context(
                conversation_id=conversation_id,
                conn=conn,
                with_ids=True,
                user_id=user_id,
                current_iteration=0,
                max_iteration=max_iteration,
            )

            temp_context_created = await self.redis_agent_manager.set_temp_context(
                user_id, conversation_id, agent_response_id, messages
            )

            if temp_context_created:
                return agent_response_id, user_message_model
            return None, user_message_model

        except Exception as exc:
            logger.error("Error creating temp context: %s", exc)
            raise

    async def prepare_temp_context_for_resume(
        self,
        conn: asyncpg.Connection,
        user_id: str,
        conversation_id: str,
        branch_id: str,
        agent_response_id: str,
        tool_output_message: MessageResponse,
        text: str = "",
        image_urls: Optional[List[str]] = None,
        active_tab: Optional[Dict[str, Any]] = None,
        max_iteration: int = 20,
    ) -> Optional[MessageResponse]:
        """Save ask_user_question tool result, optional user message, and rebuild temp context.

        This mirrors the normal agent flow:
        - Persist function_call_output and user_input (if any) to Postgres
        - Update agent_response status back to 'in_progress'
        - Rebuild OpenAI context from DB and store in Redis
        """
        try:
            # Handle system-reminder injection based on whether user content exists
            has_text = text is not None and text.strip()
            has_images = bool(image_urls)
            has_user_content = has_text or has_images

            # Build system-reminder once if active_tab is provided
            system_reminder = None
            if active_tab:
                from src.agent.general_agent.context.messages.active_tab_helper import (
                    build_active_tab_system_reminder_block,
                    prepend_system_reminder_to_content,
                )

                system_reminder = await build_active_tab_system_reminder_block(
                    conn=conn, active_tab=active_tab
                )

                if not has_user_content:
                    # Inject system-reminder into tool_output when no user content
                    function_output = tool_output_message.function_output
                    content_items: List[Dict[str, Any]]
                    if isinstance(function_output, list):
                        content_items = function_output
                    elif isinstance(function_output, str) and function_output.strip():
                        content_items = [
                            {"type": "input_text", "text": function_output.strip()}
                        ]
                    else:
                        content_items = []

                    content_items = prepend_system_reminder_to_content(
                        content_items, system_reminder
                    )
                    tool_output_message.function_output = content_items

            # Save tool result (function_call_output)
            await save_message_and_update_branch(
                conn=conn,
                conversation_id=conversation_id,
                branch_id=branch_id,
                role="tool",
                content=None,
                message_type="function_call_output",
                function_output=tool_output_message.function_output,
                call_id=tool_output_message.call_id,
                status=tool_output_message.status or "completed",
                message_id=tool_output_message.id,
                metadata=tool_output_message.metadata,
            )

            # Optionally save user message with text/images/active_tab
            user_message_model: Optional[MessageResponse] = None

            if has_user_content:
                from src.agent.common.metadata_types import MessageMetadata

                content_items: List[Dict[str, Any]] = []

                if has_text:
                    content_items.append({"type": "input_text", "text": text.strip()})

                if has_images:
                    for image_url in image_urls or []:
                        if image_url and image_url.strip():
                            content_items.append(
                                {
                                    "type": "input_image",
                                    "image_url": image_url.strip(),
                                }
                            )

                # Prepend system-reminder if active_tab provided
                if system_reminder:
                    from src.agent.general_agent.context.messages.active_tab_helper import (
                        prepend_system_reminder_to_content,
                    )

                    content_items = prepend_system_reminder_to_content(
                        content_items, system_reminder
                    )

                content_to_save = content_items
                user_metadata: "MessageMetadata" = {"source": "user"}

                message_id = await save_message_and_update_branch(
                    conn=conn,
                    conversation_id=conversation_id,
                    branch_id=branch_id,
                    role="user",
                    content=content_to_save,
                    message_type="user_input",
                    metadata=user_metadata,
                    status="completed",
                )

                message_entity = await get_message(conn, message_id)
                user_message_model = self._message_entity_to_response(message_entity)

            # Set agent_response back to in_progress
            await set_agent_response_in_progress(conn, agent_response_id)

            # Rebuild temp_context from database to include the new messages
            messages: List[
                Tuple[str, OpenAIMessageItem]
            ] = await self.context_builder.build_context(
                conversation_id=conversation_id,
                conn=conn,
                with_ids=True,
                user_id=user_id,
                current_iteration=0,
                max_iteration=max_iteration,
            )

            await self.redis_agent_manager.set_temp_context(
                user_id, conversation_id, agent_response_id, messages
            )

            return user_message_model

        except Exception as exc:
            logger.error("Error preparing temp context for resume: %s", exc)
            raise

    async def create_temp_context_for_subagent(
        self,
        user_id: str,
        conversation_id: str,
        agent_response_id: str,
        system_prompt: str,
        user_prompt: str,
    ) -> bool:
        return await self.temp_context_service.create_for_subagent(
            user_id=user_id,
            conversation_id=conversation_id,
            agent_response_id=agent_response_id,
            system_prompt=system_prompt,
            user_prompt=user_prompt,
        )

    async def get_temp_context_for_current_branch(
        self,
        user_id: str,
        conversation_id: str,
        agent_resp_id: str,
    ) -> Optional[list]:
        return await self.temp_context_service.get(
            user_id, conversation_id, agent_resp_id
        )

    # ================================================================
    # Update Agent Context each iteration
    # ================================================================

    async def process_agent_iteration(
        self,
        conn: asyncpg.Connection,
        user_id: str,
        conversation_id: str,
        branch_id: str,
        agent_resp_id: str,
        temp_messages: List[MessageResponse],
        openai_response_data: Dict[str, Any],
        is_final: bool,
        model: str,
        tools: List[Dict[str, Any]],
        input_messages: List[Dict[str, Any]],
        current_iteration: int = 0,
        max_iteration: int = 20,
    ) -> bool:
        try:
            # Inject iteration warning into last tool result before saving (DB + context stay in sync)
            if not is_final:
                IterationWarningInjector.inject_warning(
                    temp_messages=temp_messages,
                    current_iteration=current_iteration,
                    max_iteration=max_iteration,
                )

            await self.iteration_persistence.save_iteration(
                conn=conn,
                user_id=user_id,
                conversation_id=conversation_id,
                branch_id=branch_id,
                agent_response_id=agent_resp_id,
                temp_messages=temp_messages,
                openai_response_data=openai_response_data,
                is_final=is_final,
                model=model,
                tools=tools,
                input_messages=input_messages,
            )

            if is_final:
                # Only delete temp context, don't finalize status
                # Status will be finalized when agent run completes or is stopped
                await self.temp_context_service.delete(
                    user_id, conversation_id, agent_resp_id
                )
            else:
                messages_for_temp_context = self._convert_to_openai_entries(
                    temp_messages=temp_messages
                )
                await self.temp_context_service.append_messages(
                    user_id, conversation_id, agent_resp_id, messages_for_temp_context
                )

            return True

        except Exception as exc:
            logger.error("Error processing agent iteration: %s", exc)
            raise


__all__ = ["AgentContextManager"]
