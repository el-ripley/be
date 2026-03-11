"""
Domain-organized query functions for PostgreSQL database operations.

This package contains minimal async SQL query functions organized by business domain.
These are intentionally kept simple - more complex queries will be added as needed
based on actual business requirements discovered during development.
"""

from .agent_comm_queries import (
    count_escalations_with_filters,
    get_active_block,
    get_escalation_by_id,
    get_escalation_list_minimal,
    get_escalation_messages,
    get_escalations_for_context,
    get_escalations_with_filters,
    get_open_escalations_with_messages,
    insert_escalation_message,
    is_conversation_blocked,
    update_escalation_status,
    upsert_block,
)
from .agent_queries import (
    create_agent_response,
    create_branch,
    create_conversation_with_master_branch,
    finalize_agent_response,
    find_fb_context_messages_to_hide,
    get_branch_message,
    get_branch_messages,
    get_conversation,
    get_conversation_branches,
    get_message,
    get_user_conversations,
    get_user_conversations_count,
    insert_openai_response_with_agent,
    save_message_and_update_branch,
    update_agent_response_aggregates,
    update_agent_response_message_ids,
    update_branch_name,
    update_conversation,
    upsert_message_mapping,
)
from .facebook_queries import (
    create_facebook_app_scope_user,
    create_facebook_page_admin,
    create_fan_page,
    get_facebook_app_scope_user_by_id,
    get_facebook_page_admins_by_user_id,
    get_post_by_id,
    update_facebook_app_scope_user,
)
from .notification_queries import (
    count_unread_notifications,
    get_notifications,
    insert_notification,
    mark_all_notifications_read,
    mark_notification_read,
)
from .playbook_queries import get_assigned_playbook_ids, get_playbooks_by_ids
from .suggest_response_queries import (
    count_suggest_response_history_by_conversation,
    count_suggest_response_history_by_page,
    count_suggest_response_history_with_filters,
    create_page_prompt,
    create_page_scope_user_prompt,
    create_suggest_response_history,
    create_suggest_response_message,
    deactivate_user_memory,
    get_active_page_prompt,
    get_active_page_prompt_with_media,
    get_active_page_scope_user_prompt,
    get_active_page_scope_user_prompt_with_media,
    get_active_user_memory,
    get_active_user_memory_with_blocks,
    get_agent_settings,
    get_page_admin_suggest_config,
    get_page_admin_suggest_configs_by_page,
    get_suggest_response_history_by_conversation,
    get_suggest_response_history_by_id,
    get_suggest_response_history_by_page,
    get_suggest_response_history_with_filters,
    get_suggest_response_messages_by_history,
    update_suggest_response_history,
    upsert_agent_settings,
    upsert_page_admin_suggest_config,
)
from .user_queries import (
    assign_role_to_user_by_name,
    create_user,
    get_comprehensive_user_info,
    get_user_by_id,
    get_user_conversation_settings,
    get_user_with_roles,
    upsert_user_conversation_settings,
)

# User files queries removed - files are now stored in ephemeral S3 storage without database tracking






# Note: comments_queries and messages_queries modules removed as they contained no used functions

__all__ = [
    # User domain
    "create_user",
    "get_user_by_id",
    "assign_role_to_user_by_name",
    "get_user_with_roles",
    "get_comprehensive_user_info",
    "get_user_conversation_settings",
    "upsert_user_conversation_settings",
    # Facebook domain
    "create_facebook_app_scope_user",
    "get_facebook_app_scope_user_by_id",
    "update_facebook_app_scope_user",
    "create_fan_page",
    "create_facebook_page_admin",
    "get_post_by_id",
    "get_facebook_page_admins_by_user_id",
    # User files domain - removed (files stored in ephemeral S3 storage)
    # Agent domain
    "get_conversation",
    "get_user_conversations",
    "get_user_conversations_count",
    "get_message",
    "create_conversation_with_master_branch",
    "create_branch",
    "update_conversation",
    "upsert_message_mapping",
    "get_conversation_branches",
    "update_branch_name",
    "get_branch_messages",
    "get_branch_message",
    "create_agent_response",
    "update_agent_response_message_ids",
    "update_agent_response_aggregates",
    "finalize_agent_response",
    "insert_openai_response_with_agent",
    "save_message_and_update_branch",
    "find_fb_context_messages_to_hide",
    # Suggest response domain
    "get_agent_settings",
    "upsert_agent_settings",
    "get_page_admin_suggest_config",
    "get_page_admin_suggest_configs_by_page",
    "upsert_page_admin_suggest_config",
    "create_suggest_response_message",
    "get_suggest_response_messages_by_history",
    "get_active_page_prompt",
    "get_active_page_prompt_with_media",
    "create_page_prompt",
    "get_active_page_scope_user_prompt",
    "get_active_page_scope_user_prompt_with_media",
    "create_page_scope_user_prompt",
    "create_suggest_response_history",
    "get_suggest_response_history_by_id",
    "get_suggest_response_history_by_conversation",
    "get_suggest_response_history_by_page",
    "count_suggest_response_history_by_conversation",
    "count_suggest_response_history_by_page",
    "update_suggest_response_history",
    "get_suggest_response_history_with_filters",
    "count_suggest_response_history_with_filters",
    "get_active_user_memory",
    "get_active_user_memory_with_blocks",
    "deactivate_user_memory",
    # Playbook domain
    "get_assigned_playbook_ids",
    "get_playbooks_by_ids",
    # Agent comm domain
    "get_active_block",
    "is_conversation_blocked",
    "upsert_block",
    "get_escalations_with_filters",
    "count_escalations_with_filters",
    "get_escalation_by_id",
    "update_escalation_status",
    "get_escalation_messages",
    "insert_escalation_message",
    "get_open_escalations_with_messages",
    "get_escalations_for_context",
    "get_escalation_list_minimal",
    # Notifications domain
    "insert_notification",
    "get_notifications",
    "count_unread_notifications",
    "mark_notification_read",
    "mark_all_notifications_read",
]
