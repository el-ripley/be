"""
Service for generating image descriptions using vision model.
Uses gpt-5-nano by default (configurable like summarize agent).
"""

from typing import Optional, Dict, Any, List
import asyncio
import time

import asyncpg

from src.agent.core.llm_call import LLM_call
from src.agent.common.agent_types import AGENT_TYPE_MEDIA_DESCRIPTION_AGENT
from src.agent.common.conversation_settings import (
    DEFAULT_VISION_MODEL,
    get_effective_context_settings,
)
from src.database.postgres.repositories.agent_queries import (
    create_agent_response,
    insert_openai_response_with_agent,
    finalize_agent_response,
)
from src.settings import settings
from src.utils.logger import get_logger

logger = get_logger()


class MediaDescriptionService:
    """Generate image descriptions using vision model."""

    def __init__(self, model: Optional[str] = None):
        self.model = (
            model
            or getattr(settings, "vision_description_model", None)
            or DEFAULT_VISION_MODEL
        )

    def _is_valid_s3_url(self, url: str) -> bool:
        """Check if URL is a valid S3 URL (not Facebook CDN)."""
        if not url:
            return False
        # S3 URLs should contain 's3' and 'amazonaws.com'
        # Facebook URLs contain 'fbcdn.net' or 'facebook.com'
        is_s3 = "s3" in url and "amazonaws.com" in url
        is_facebook = "fbcdn.net" in url or "facebook.com" in url
        return is_s3 and not is_facebook

    async def describe_image(
        self,
        conn: asyncpg.Connection,
        image_url: str,
        api_key: str,
        agent_response_id: str,
        user_id: str,
        conversation_id: Optional[str] = None,
        branch_id: Optional[str] = None,
        context: Optional[str] = None,
        save_to_db: bool = True,
        vision_model: Optional[str] = None,
    ) -> Optional[str]:
        """
        Generate description for a single image with cost tracking.

        Args:
            conn: Database connection
            image_url: S3 URL of the image (MUST be S3, not Facebook CDN)
            api_key: System OpenAI API key
            agent_response_id: Agent response ID for cost tracking
            user_id: User ID for cost tracking
            conversation_id: Conversation ID (optional)
            branch_id: Branch ID (optional)
            context: Optional context about where the image appears
                     (e.g., "message attachment", "user avatar", "post image")
            save_to_db: If False, skip database save (used in batch mode)
            vision_model: Optional vision model to use (if provided, skips DB query)

        Returns:
            Description text or None if failed
            If save_to_db=False, returns tuple (description, response_data, messages)
        """
        # Convert UUID objects to strings (asyncpg returns UUID objects)
        agent_response_id = str(agent_response_id) if agent_response_id else None
        user_id = str(user_id) if user_id else None
        conversation_id = str(conversation_id) if conversation_id else None
        branch_id = str(branch_id) if branch_id else None

        if not api_key:
            logger.warning("Cannot describe image: API key not provided")
            return None

        # Validate URL is S3, not Facebook CDN
        if not self._is_valid_s3_url(image_url):
            logger.warning(
                f"Cannot describe image: URL is not S3 URL. Got: {image_url[:100]}..."
            )
            return None

        # Get effective vision model (user settings merged with defaults)
        # If vision_model is provided (e.g., from batch mode), use it to avoid concurrent DB queries
        if vision_model is None:
            effective_context_settings = await get_effective_context_settings(
                user_id, conn
            )
            vision_model = effective_context_settings["vision_model"]
        # Build prompt
        prompt = self._build_description_prompt(context)

        # Call vision model
        llm = LLM_call(api_key=api_key)

        messages = [
            {"role": "system", "content": prompt},
            {
                "role": "user",
                "content": [{"type": "input_image", "image_url": image_url}],
            },
        ]

        start_time_ms = int(time.time() * 1000)
        try:
            response = await llm.create(
                model=vision_model,
                input=messages,
            )
            end_time_ms = int(time.time() * 1000)
            latency_ms = end_time_ms - start_time_ms

            description = self._extract_description(response)

            # Prepare response data for cost tracking
            if isinstance(response, dict):
                response_data = {
                    "id": response.get(
                        "id", f"resp_{agent_response_id[:8]}_{int(time.time() * 1000)}"
                    ),
                    "created": response.get("created", start_time_ms),
                    "latency_ms": latency_ms,
                    "usage": response.get("usage", {}),
                    "output": response.get("output", []),
                }
            else:
                # Fallback if response is not a dict
                response_data = {
                    "id": f"resp_{agent_response_id[:8]}_{int(time.time() * 1000)}",
                    "created": start_time_ms,
                    "latency_ms": latency_ms,
                    "usage": {},
                    "output": [],
                }
                # Try to extract from response object
                if hasattr(response, "usage"):
                    response_data["usage"] = (
                        response.usage.model_dump()
                        if hasattr(response.usage, "model_dump")
                        else {}
                    )
                if hasattr(response, "output"):
                    response_data["output"] = (
                        [
                            item.model_dump() if hasattr(item, "model_dump") else item
                            for item in response.output
                        ]
                        if response.output
                        else []
                    )

            # Save to database if requested (sequential mode)
            if save_to_db:
                await insert_openai_response_with_agent(
                    conn=conn,
                    user_id=user_id,
                    conversation_id=conversation_id,
                    branch_id=branch_id,
                    agent_response_id=agent_response_id,
                    response_data=response_data,
                    input_messages=messages,
                    tools=[],
                    model=vision_model,
                )

            if description:
                logger.debug(f"Generated description: {description[:50]}...")
            else:
                logger.warning(
                    f"No description extracted from response for {image_url[:80]}..."
                )

            # Return tuple with extra data if not saving to DB (batch mode)
            if not save_to_db:
                return (description, response_data, messages)

            return description
        except Exception as e:
            logger.error(f"Failed to describe image {image_url[:80]}...: {e}")
            if not save_to_db:
                return (None, None, None)
            return None

    async def describe_batch(
        self,
        conn: asyncpg.Connection,
        items: List[Dict[str, Any]],
        api_key: str,
        user_id: str,
        parent_agent_response_id: Optional[str] = None,
        conversation_id: Optional[str] = None,
        branch_id: Optional[str] = None,
    ) -> Dict[str, str]:
        """
        Describe multiple images in batch with cost tracking.

        Args:
            conn: Database connection
            items: List of {"url": str, "context": str, "media_id": str}
            api_key: System OpenAI API key
            user_id: User ID for cost tracking
            parent_agent_response_id: Parent agent_response ID for hierarchical tracking
            conversation_id: Conversation ID (optional)
            branch_id: Branch ID (optional)

        Returns:
            Dict mapping media_id -> description
        """
        # Convert UUID objects to strings (asyncpg returns UUID objects)
        user_id = str(user_id) if user_id else None
        conversation_id = str(conversation_id) if conversation_id else None
        branch_id = str(branch_id) if branch_id else None
        parent_agent_response_id = (
            str(parent_agent_response_id) if parent_agent_response_id else None
        )

        if not items:
            return {}

        # Create agent_response record BEFORE batch processing
        agent_response_id = await create_agent_response(
            conn=conn,
            user_id=user_id,
            conversation_id=conversation_id,
            branch_id=branch_id,
            agent_type=AGENT_TYPE_MEDIA_DESCRIPTION_AGENT,
            parent_agent_response_id=parent_agent_response_id,
        )

        async def describe_item_llm_only(item: Dict[str, Any]) -> tuple:
            """
            Call LLM to describe image (parallel safe - no DB writes).
            Returns: (media_id, description, response_data, messages)
            """
            result = await self.describe_image(
                conn=conn,
                image_url=item["url"],
                api_key=api_key,
                agent_response_id=agent_response_id,
                user_id=user_id,
                conversation_id=conversation_id,
                branch_id=branch_id,
                context=item.get("context"),
                save_to_db=False,  # Skip DB writes during parallel phase
                vision_model=vision_model,  # Pass pre-fetched vision_model to avoid concurrent DB queries
            )
            # result is (description, response_data, messages) or (None, None, None)
            if result and result[0]:  # Has description
                return (item.get("media_id"), result[0], result[1], result[2])
            return (item.get("media_id"), None, None, None)

        # Get effective vision model (user settings merged with defaults)
        effective_context_settings = await get_effective_context_settings(user_id, conn)
        vision_model = effective_context_settings["vision_model"]

        # PHASE 1: Call all LLMs in parallel (batch of 10 at a time for rate limits)
        all_responses = []
        batch_size = 10
        for batch_start_idx in range(0, len(items), batch_size):
            batch = items[batch_start_idx : batch_start_idx + batch_size]
            batch_results = await asyncio.gather(
                *[describe_item_llm_only(item) for item in batch]
            )
            all_responses.extend(batch_results)

        # PHASE 2: Save all responses to database SEQUENTIALLY (avoid concurrent connection use)
        results = {}
        for media_id, description, response_data, messages in all_responses:
            if description and response_data and messages:
                try:
                    await insert_openai_response_with_agent(
                        conn=conn,
                        user_id=user_id,
                        conversation_id=conversation_id,
                        branch_id=branch_id,
                        agent_response_id=agent_response_id,
                        response_data=response_data,
                        input_messages=messages,
                        tools=[],
                        model=vision_model,
                    )
                    results[media_id] = description
                except Exception as e:
                    logger.error(f"Failed to save response for media {media_id}: {e}")
                    # Still include description in results even if DB save failed
                    results[media_id] = description

        # Finalize agent_response to aggregate all responses
        await finalize_agent_response(conn, agent_response_id)
        # Deduct credits after finalization
        from src.billing.credit_service import deduct_credits_after_agent

        await deduct_credits_after_agent(conn, agent_response_id)

        return results

    def _build_description_prompt(self, context: Optional[str]) -> str:
        base = (
            "Describe this image in 2-3 concise sentences. "
            "Include key visual attributes: main subject, colors, shape/form, "
            "material/texture, patterns, any visible text or labels, and distinctive features. "
            "Be specific enough that someone could identify this exact item "
            "from the description alone. "
            "Do not include metadata or technical details."
        )
        if context:
            base += f"\nContext: This image is a {context}."
        return base

    def _extract_description(self, response: Any) -> Optional[str]:
        """Extract description text from LLM response."""
        try:
            if response is None:
                return None

            # Response structure from OpenAI Response API
            if isinstance(response, dict):
                output = response.get("output")
                if output is None:
                    return None
                for item in output:
                    if isinstance(item, dict):
                        content = item.get("content")
                        if content is None:
                            continue
                        for content_item in content:
                            if isinstance(content_item, dict):
                                text = content_item.get("text")
                                if text:
                                    return text.strip()

            # Fallback: try to get text from response object
            if hasattr(response, "output") and response.output is not None:
                for item in response.output:
                    if hasattr(item, "content") and item.content is not None:
                        for content in item.content:
                            if hasattr(content, "text") and content.text:
                                return content.text.strip()
        except Exception as e:
            logger.error(f"Error extracting description from response: {e}")
        return None
