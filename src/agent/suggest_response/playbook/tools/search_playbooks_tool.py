"""Search playbooks tool - BaseTool for playbook selection agent."""

import json
import time
import uuid
from typing import Any, Dict

import asyncpg

from src.agent.suggest_response.playbook.constants import MAX_SEARCHES
from src.agent.tools.base import BaseTool, ToolCallContext, ToolResult
from src.api.openai_conversations.schemas import MessageResponse
from src.services.playbook.playbook_sync_service import search_playbooks


def build_search_playbooks_definition() -> Dict[str, Any]:
    """OpenAI tool definition for search_playbooks."""
    return {
        "type": "function",
        "name": "search_playbooks",
        "description": "Search for coaching playbooks by situation. Queries are matched against playbook `title + situation` vectors. You may call this multiple times in parallel (each with a distinct query) to cover different angles. Total searches capped at 6.",
        "parameters": {
            "type": "object",
            "properties": {
                "query": {
                    "type": "string",
                    "description": "Describe the CURRENT situation (what is happening right now), not a summary of the whole conversation. Write it like the operator would have written the playbook's `situation` field. Good: 'customer confirmed order, waiting for stock check result'. Bad: 'customer asks about size and wants COD' (too broad, mixes past and present).",
                },
            },
            "required": ["query"],
            "additionalProperties": False,
        },
        "strict": True,
    }


class SearchPlaybooksTool(BaseTool):
    """Tool to search playbooks by situation query."""

    @property
    def name(self) -> str:
        return "search_playbooks"

    @property
    def definition(self) -> Dict[str, Any]:
        return self._apply_description_override(build_search_playbooks_definition())

    async def execute(
        self,
        conn: asyncpg.Connection,
        context: ToolCallContext,
        arguments: Dict[str, Any],
    ) -> Any:
        """Run search; return dict with output_for_llm, function_output, updated_cache, new_search_count (or error output_text)."""
        search_count = context.playbook_search_count or 0
        cache = dict(context.playbook_cache or {})
        assigned_ids = context.playbook_assigned_ids or []
        agent_response_id = context.playbook_agent_response_id or ""

        if search_count >= MAX_SEARCHES:
            msg = f"You have already used the maximum number of searches ({MAX_SEARCHES}). Call select_playbooks now."
            return {
                "output_text": msg,
                "updated_cache": cache,
                "new_search_count": search_count,
                "function_output": {"output": msg},
            }

        query = (arguments.get("query") or "").strip()
        if not query:
            return {
                "output_text": "Error: query is required.",
                "updated_cache": cache,
                "new_search_count": search_count,
                "function_output": {"output": "Error: query is required."},
            }

        matches = await search_playbooks(
            conn=conn,
            query_text=query,
            user_id=context.user_id,
            agent_response_id=agent_response_id,
            playbook_ids=assigned_ids,
            limit=5,
        )
        for p in matches:
            pid = p.get("playbook_id") or p.get("id")
            if pid:
                cache[str(pid)] = p

        output_for_llm = [
            {
                "type": "input_text",
                "text": json.dumps(
                    [
                        {
                            "playbook_id": p.get("playbook_id") or p.get("id"),
                            "title": p.get("title"),
                            "situation": p.get("situation"),
                            "content": (p.get("content") or "")[:500],
                            "score": p.get("score"),
                        }
                        for p in matches
                    ],
                    ensure_ascii=False,
                ),
            }
        ]
        return {
            "output_for_llm": output_for_llm,
            "function_output": {"query": query, "results_count": len(matches)},
            "updated_cache": cache,
            "new_search_count": search_count + 1,
        }

    def process_result(self, context: ToolCallContext, raw_result: Any) -> ToolResult:
        """Build function_call_output MessageResponse and metadata for handler."""
        current_time = int(time.time() * 1000)
        out_id = str(uuid.uuid4())

        if "output_text" in raw_result:
            func_output = raw_result["function_output"]
        else:
            func_output = raw_result["function_output"]

        output_message = MessageResponse(
            id=out_id,
            conversation_id=context.conv_id,
            sequence_number=0,
            type="function_call_output",
            role="tool",
            content=None,
            call_id=context.call_id,
            function_name=self.name,
            function_output=func_output,
            status="completed",
            metadata=None,
            created_at=current_time,
            updated_at=current_time,
        )
        metadata: Dict[str, Any] = {
            "updated_cache": raw_result.get("updated_cache", {}),
            "new_search_count": raw_result.get("new_search_count", 0),
        }
        if "output_for_llm" in raw_result:
            metadata["output_for_llm"] = raw_result["output_for_llm"]
        else:
            metadata["output_text"] = raw_result.get("output_text", "")
        return ToolResult(
            output_message=output_message,
            human_message=None,
            metadata=metadata,
        )
