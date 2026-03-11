"""
Summarizer service for context compression.

Uses a separate LLM agent to summarize conversation history,
reducing context size while preserving essential information.
"""

import json
import time
from dataclasses import dataclass
from typing import Any, Dict, List, Optional, Tuple

import asyncpg
from pydantic import BaseModel, Field

from src.agent.common.agent_types import AGENT_TYPE_SUMMARIZATION_AGENT
from src.agent.core.llm_call import LLM_call
from src.database.postgres.repositories.agent_queries import (
    create_agent_response,
    finalize_agent_response,
    insert_openai_response_with_agent,
)
from src.utils.logger import get_logger

logger = get_logger()

# Default configuration (system-level, not user-configurable for now)
DEFAULT_SUMMARIZER_CONFIG = {
    "preserve_recent_turns": 2,  # Keep last N complete conversation turns verbatim
    "target_reduction_percent": 70,  # Target 70% reduction
}


SUMMARIZER_SYSTEM_PROMPT = """# CONTEXT SUMMARIZER AGENT

## Purpose

You are a specialized agent that summarizes conversation history to reduce context size while preserving essential information.

## Input Format

You will receive a JSON object with:
- `messages`: Array of conversation messages to summarize
- `active_tab`: Current active tab context (MUST be preserved exactly)
- `preserve_recent_turns`: Number of complete conversation turns kept verbatim (not included in messages array)

## Output Format

Return a JSON object with:
```json
{
    "summary": "Concise summary of the conversation...",
    "preserved_context": {
        "user_intent": "What the user is trying to achieve",
        "key_decisions": ["Decision 1", "Decision 2"],
        "important_ids": {"page_id": "xxx", "post_id": "yyy"},
        "current_task_state": "Description of where we are in the task"
    }
}
```

## Summarization Rules

### MUST Preserve:
1. User's original request and any clarifications
2. Key decisions made during the conversation
3. Important IDs (page_id, post_id, conversation_id, user_id)
4. Current task state and next steps
5. Any user preferences or constraints mentioned
6. Errors encountered and their resolutions

### MUST Compress/Remove:
1. Verbose tool outputs (keep only essential results)
2. Redundant information (data fetched multiple times)
3. Intermediate reasoning (keep only conclusions)
4. Superseded data (old versions of updated content)
5. Detailed API responses (summarize to key fields)

### Quality Guidelines:
- Target: Reduce to ~25-30% of original token count
- Clarity: Summary should be immediately understandable
- Completeness: Agent should be able to continue the task with only the summary
- Structure: Use bullet points and clear sections

## Example

**Input messages (simplified):**
```
User: "Help me reply to comments on my latest post"
Assistant: [calls get_pages_details]
Tool: {pages: [{id: "123", name: "My Page"}]}
Assistant: [calls list_page_posts]
Tool: {posts: [{id: "456", content: "Hello world", comments_count: 5}]}
Assistant: [calls get_post_details]
Tool: {post: {id: "456", full_content: "...", media: [...]}}
Assistant: "I found your latest post. Let me get the comments..."
```

**Output:**
```json
{
    "summary": "User requested help replying to comments on their latest post. Found Page 'My Page' (id: 123) with post (id: 456) that has 5 comments. Currently fetching comments to assist with replies.",
    "preserved_context": {
        "user_intent": "Reply to comments on latest post",
        "key_decisions": [],
        "important_ids": {"page_id": "123", "post_id": "456"},
        "current_task_state": "About to fetch and display comments for user to reply"
    }
}
```
"""


class PreservedContext(BaseModel):
    user_intent: str
    key_decisions: List[str]
    important_ids: Optional[Dict[str, str]] = Field(
        default_factory=dict
    )  # Optional with default empty dict
    current_task_state: str


class SummaryOutputSchema(BaseModel):
    summary: str
    preserved_context: PreservedContext


@dataclass
class SummarizationResult:
    summary_text: str
    preserved_context: Dict[str, Any]
    messages_to_hide: List[str]  # message_ids to mark as hidden


class SummarizerService:
    def __init__(self, model: str = "gpt-5-mini"):
        self.config = DEFAULT_SUMMARIZER_CONFIG.copy()
        self.config["model"] = model

    async def summarize(
        self,
        conn: asyncpg.Connection,
        messages: List[Dict[str, Any]],  # Messages from branch (OpenAI format)
        message_ids: List[str],  # Corresponding message IDs
        active_tab: Optional[Dict[str, Any]] = None,
        user_id: Optional[str] = None,
        api_key: Optional[str] = None,
        parent_agent_response_id: Optional[str] = None,
        conversation_id: Optional[str] = None,
        branch_id: Optional[str] = None,
    ) -> Optional[Tuple[SummarizationResult, Optional[str]]]:
        """
        Summarize conversation history with cost tracking.

        Args:
            conn: Database connection
            messages: List of OpenAI-format messages
            message_ids: List of message IDs corresponding to messages
            active_tab: Active tab context to preserve
            user_id: User ID for cost tracking
            api_key: System OpenAI API key
            parent_agent_response_id: Parent agent_response ID for hierarchical tracking
            conversation_id: Conversation ID (optional, for tracking)
            branch_id: Branch ID (optional, for tracking)

        Returns:
            Tuple of (SummarizationResult, agent_response_id) or None if nothing to summarize
        """
        # Find split point based on complete turns
        preserve_turns = self.config["preserve_recent_turns"]
        split_index = self._find_preserve_split_index(messages, preserve_turns)

        if split_index <= 1:
            # Not enough messages to summarize (only system message or nothing)
            logger.info("Not enough messages to summarize after turn-based split")
            return None

        # Split: [system + to_summarize] vs [preserved]
        messages_to_summarize = messages[1:split_index]  # Exclude system message
        ids_to_hide = message_ids[1:split_index]

        if not messages_to_summarize:
            logger.info("No messages to summarize after split")
            return None

        logger.info(
            f"Summarizing {len(messages_to_summarize)} messages, "
            f"preserving {len(messages) - split_index} messages ({preserve_turns} turns)"
        )

        # Call summarizer LLM with cost tracking
        summary_response, agent_response_id = await self._call_summarizer_llm(
            conn=conn,
            messages_to_summarize=messages_to_summarize,
            active_tab=active_tab,
            user_id=user_id,
            api_key=api_key,
            parent_agent_response_id=parent_agent_response_id,
            conversation_id=conversation_id,
            branch_id=branch_id,
        )

        if not summary_response:
            return None

        # Create summary message content
        summary_text = self._format_summary_message(summary_response)

        return (
            SummarizationResult(
                summary_text=summary_text,
                preserved_context=(
                    summary_response.preserved_context.model_dump()
                    if hasattr(summary_response, "preserved_context")
                    else {}
                ),
                messages_to_hide=ids_to_hide,
            ),
            agent_response_id,
        )

    def _find_preserve_split_index(
        self, messages: List[Dict[str, Any]], preserve_turns: int
    ) -> int:
        """
        Find the split index to preserve N complete conversation turns.

        A complete turn includes:
        - User message (role='user')
        - Assistant reasoning (type='reasoning') - optional
        - Assistant message (role='assistant')
        - All function_calls and their outputs (paired)

        Returns:
            Split index where messages[:index] should be summarized,
            and messages[index:] should be preserved.
            Returns 1 if we should preserve everything (keep system message only for summarization).
        """
        if len(messages) <= 1:
            return 1  # Only system message, nothing to summarize

        # Group messages into complete turns
        turns = self._group_messages_into_turns(messages[1:])  # Exclude system message

        if len(turns) <= preserve_turns:
            # Not enough turns to summarize, preserve all
            return 1

        # Calculate split index: summarize first (len(turns) - preserve_turns) turns
        turns_to_summarize = turns[: len(turns) - preserve_turns]
        split_index = 1 + sum(len(turn) for turn in turns_to_summarize)

        return split_index

    def _group_messages_into_turns(
        self, messages: List[Dict[str, Any]]
    ) -> List[List[Dict[str, Any]]]:
        """
        Group messages into complete conversation turns.

        A turn is defined as:
        1. Optional: User message (starts a new turn)
        2. Optional: Assistant reasoning
        3. Optional: Assistant text message
        4. Optional: Function calls + their outputs (must be paired)

        Returns:
            List of turns, where each turn is a list of messages.
        """
        if not messages:
            return []

        turns = []
        current_turn = []
        pending_function_calls = set()  # Track call_ids waiting for outputs

        for msg in messages:
            role = msg.get("role")
            msg_type = msg.get("type")

            # User message starts a new turn
            if role == "user":
                # Save previous turn if exists and complete
                if current_turn and not pending_function_calls:
                    turns.append(current_turn)
                    current_turn = []
                # Start new turn
                current_turn.append(msg)

            # Assistant messages (reasoning, message, function_call)
            elif role == "assistant":
                current_turn.append(msg)
                # Track function calls
                if msg_type == "function_call":
                    call_id = msg.get("call_id")
                    if call_id:
                        pending_function_calls.add(call_id)

            # Tool outputs (function_call_output)
            elif role == "tool":
                current_turn.append(msg)
                # Mark function call as complete
                if msg_type == "function_call_output":
                    call_id = msg.get("call_id")
                    if call_id in pending_function_calls:
                        pending_function_calls.remove(call_id)

            # Other messages (system, summary, etc.)
            else:
                current_turn.append(msg)

        # Add last turn if complete (no pending function calls)
        if current_turn:
            if pending_function_calls:
                # Turn is incomplete, keep all messages in this turn to preserve structure
                logger.warning(
                    f"Incomplete turn detected with {len(pending_function_calls)} pending function calls. "
                    f"Keeping entire turn to preserve structure."
                )
            turns.append(current_turn)

        return turns

    async def _call_summarizer_llm(
        self,
        conn: asyncpg.Connection,
        messages_to_summarize: List[Dict[str, Any]],
        active_tab: Optional[Dict[str, Any]],
        user_id: Optional[str] = None,
        api_key: Optional[str] = None,
        parent_agent_response_id: Optional[str] = None,
        conversation_id: Optional[str] = None,
        branch_id: Optional[str] = None,
    ) -> Tuple[Optional[SummaryOutputSchema], Optional[str]]:
        """
        Call LLM to generate summary with cost tracking.

        Returns:
            Tuple of (SummaryOutputSchema, agent_response_id) or (None, None) on failure
        """
        # Convert UUID objects to strings (asyncpg returns UUID objects)
        user_id = str(user_id) if user_id else None
        conversation_id = str(conversation_id) if conversation_id else None
        branch_id = str(branch_id) if branch_id else None
        parent_agent_response_id = (
            str(parent_agent_response_id) if parent_agent_response_id else None
        )

        if not api_key or not user_id:
            logger.error("Cannot track summarization cost: missing api_key or user_id")
            return None, None

        # Create agent_response record BEFORE LLM call
        agent_response_id = await create_agent_response(
            conn=conn,
            user_id=user_id,
            conversation_id=conversation_id,
            branch_id=branch_id,
            agent_type=AGENT_TYPE_SUMMARIZATION_AGENT,
            parent_agent_response_id=parent_agent_response_id,
        )
        # Convert UUID object to string (asyncpg returns UUID objects)
        agent_response_id = str(agent_response_id) if agent_response_id else None

        input_payload = {
            "messages": messages_to_summarize,
            "active_tab": active_tab,
            "preserve_recent_turns": self.config["preserve_recent_turns"],
        }

        llm_input = [
            {"role": "system", "content": SUMMARIZER_SYSTEM_PROMPT},
            {"role": "user", "content": json.dumps(input_payload, ensure_ascii=False)},
        ]

        llm_call = LLM_call(api_key=api_key)
        start_time_ms = int(time.time() * 1000)

        try:
            # Call LLM with parse (structured output) - get full response for cost tracking
            parsed_response, full_response = await llm_call.parse(
                model=self.config["model"],
                input=llm_input,
                text_format=SummaryOutputSchema,
                return_full_response=True,
            )

            end_time_ms = int(time.time() * 1000)
            latency_ms = end_time_ms - start_time_ms

            if not parsed_response:
                logger.error("Summarizer LLM parse returned None")
                await finalize_agent_response(conn, agent_response_id)
                return None, agent_response_id

            # Use full_response data which includes usage information
            response_data = full_response
            response_data["latency_ms"] = latency_ms

            # Save openai_response
            await insert_openai_response_with_agent(
                conn=conn,
                user_id=user_id,
                conversation_id=conversation_id,
                branch_id=branch_id,
                agent_response_id=agent_response_id,
                response_data=response_data,
                input_messages=llm_input,
                tools=[],
                model=self.config["model"],
            )

            # Finalize agent_response to update aggregates
            await finalize_agent_response(conn, agent_response_id)
            # Deduct credits after finalization
            from src.billing.credit_service import deduct_credits_after_agent

            await deduct_credits_after_agent(conn, agent_response_id)

            return parsed_response, agent_response_id
        except Exception as e:
            logger.error(f"Summarizer LLM call failed: {e}")
            # Finalize even on error to mark as failed
            await finalize_agent_response(conn, agent_response_id)
            # Deduct credits after finalization (even on error)
            from src.billing.credit_service import deduct_credits_after_agent

            await deduct_credits_after_agent(conn, agent_response_id)
            return None, agent_response_id

    def _format_summary_message(self, summary_response: SummaryOutputSchema) -> str:
        """Format summary as a message to insert into context."""
        summary = summary_response.summary
        preserved = summary_response.preserved_context

        formatted = f"""[CONVERSATION SUMMARY]

{summary}

**Preserved Context:**
- User Intent: {preserved.user_intent}
- Current State: {preserved.current_task_state}
- Important IDs: {json.dumps(preserved.important_ids or {})}
- Key Decisions: {', '.join(preserved.key_decisions) if preserved.key_decisions else 'None'}

[END SUMMARY - Conversation continues below]"""

        return formatted
