"""
LLM call repository for agent system.

Database operations for LLM call tracking, cost calculation, and conversation management.
All functions require an asyncpg.Connection to ensure proper transaction management.
"""

from .agent_responses import (
    create_agent_response,
    finalize_agent_response,
    get_agent_response_for_user,
    get_agent_response_id_from_message_id,
    get_agent_response_with_hierarchy,
    get_latest_conversation_token_count,
    get_latest_openai_response_for_conversation,
    get_sub_agent_responses,
    insert_openai_response_with_agent,
    save_message_and_update_branch,
    set_agent_response_in_progress,
    set_agent_response_waiting,
    stop_agent_response,
    update_agent_response_aggregates,
    update_agent_response_message_ids,
    update_tool_results_function_output,
)
from .branches import (
    create_branch,
    create_branch_before_message,
    create_conversation_with_master_branch,
    get_all_branch_messages,
    get_branch_info,
    get_branch_message,
    get_branch_messages,
    get_conversation_branches,
    update_branch_name,
    update_conversation,
    upsert_message_mapping,
)
from .conversations import (
    create_subagent_conversation,
    get_conversation,
    get_conversation_settings,
    get_conversation_with_relations,
    get_user_conversation_count_for_title,
    get_user_conversations,
    get_user_conversations_count,
    update_conversation_settings,
)
from .facebook_context import find_fb_context_messages_to_hide
from .messages import get_message

# Re-export all functions from domain modules to maintain backward compatibility
from .pricing import DEFAULT_PRICING, MODEL_PRICING, calculate_cost

__all__ = [
    # Pricing
    "calculate_cost",
    "MODEL_PRICING",
    "DEFAULT_PRICING",
    # Conversations
    "get_conversation",
    "get_user_conversations",
    "get_user_conversations_count",
    "get_user_conversation_count_for_title",
    "get_conversation_with_relations",
    "get_conversation_settings",
    "update_conversation_settings",
    "create_subagent_conversation",
    # Messages
    "get_message",
    # Branches
    "create_conversation_with_master_branch",
    "create_branch",
    "create_branch_before_message",
    "update_conversation",
    "upsert_message_mapping",
    "get_conversation_branches",
    "get_branch_info",
    "update_branch_name",
    "get_branch_messages",
    "get_all_branch_messages",
    "get_branch_message",
    # Agent Responses
    "create_agent_response",
    "update_agent_response_message_ids",
    "update_agent_response_aggregates",
    "finalize_agent_response",
    "stop_agent_response",
    "set_agent_response_waiting",
    "set_agent_response_in_progress",
    "get_agent_response_id_from_message_id",
    "update_tool_results_function_output",
    "insert_openai_response_with_agent",
    "save_message_and_update_branch",
    "get_latest_conversation_token_count",
    "get_latest_openai_response_for_conversation",
    "get_agent_response_for_user",
    "get_sub_agent_responses",
    "get_agent_response_with_hierarchy",
    # Facebook Context
    "find_fb_context_messages_to_hide",
]
