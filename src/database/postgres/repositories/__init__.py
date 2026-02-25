"""
Domain-organized query functions for PostgreSQL database operations.

This package contains minimal async SQL query functions organized by business domain.
These are intentionally kept simple - more complex queries will be added as needed
based on actual business requirements discovered during development.
"""

from .user_queries import (
    create_user,
    get_user_by_id,
    assign_role_to_user_by_name,
    get_user_with_roles,
    get_comprehensive_user_info,
    get_user_conversation_settings,
    upsert_user_conversation_settings,
)

from .facebook_queries import (
    create_facebook_app_scope_user,
    get_facebook_app_scope_user_by_id,
    update_facebook_app_scope_user,
    create_fan_page,
    create_facebook_page_admin,
    get_post_by_id,
    get_facebook_page_admins_by_user_id,
)

# User files queries removed - files are now stored in ephemeral S3 storage without database tracking

from .agent_queries import (
    get_conversation,
    get_user_conversations,
    get_user_conversations_count,
    get_message,
    create_conversation_with_master_branch,
    create_branch,
    update_conversation,
    upsert_message_mapping,
    get_conversation_branches,
    update_branch_name,
    get_branch_messages,
    get_branch_message,
    create_agent_response,
    update_agent_response_message_ids,
    update_agent_response_aggregates,
    finalize_agent_response,
    insert_openai_response_with_agent,
    save_message_and_update_branch,
    find_fb_context_messages_to_hide,
)

from .agent_comm_queries import (
    get_active_block,
    is_conversation_blocked,
    upsert_block,
    get_escalations_with_filters,
    count_escalations_with_filters,
    get_escalation_by_id,
    update_escalation_status,
    get_escalation_messages,
    insert_escalation_message,
    get_open_escalations_with_messages,
    get_escalations_for_context,
    get_escalation_list_minimal,
)

from .notification_queries import (
    insert_notification,
    get_notifications,
    count_unread_notifications,
    mark_notification_read,
    mark_all_notifications_read,
)

from .playbook_queries import (
    get_assigned_playbook_ids,
    get_playbooks_by_ids,
)

from .suggest_response_queries import (
    get_agent_settings,
    upsert_agent_settings,
    get_page_admin_suggest_config,
    get_page_admin_suggest_configs_by_page,
    upsert_page_admin_suggest_config,
    create_suggest_response_message,
    get_suggest_response_messages_by_history,
    get_active_page_prompt,
    get_active_page_prompt_with_media,
    create_page_prompt,
    get_active_page_scope_user_prompt,
    get_active_page_scope_user_prompt_with_media,
    create_page_scope_user_prompt,
    create_suggest_response_history,
    get_suggest_response_history_by_id,
    get_suggest_response_history_by_conversation,
    get_suggest_response_history_by_page,
    count_suggest_response_history_by_conversation,
    count_suggest_response_history_by_page,
    update_suggest_response_history,
    get_suggest_response_history_with_filters,
    count_suggest_response_history_with_filters,
    get_active_user_memory,
    get_active_user_memory_with_blocks,
    deactivate_user_memory,
)

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
