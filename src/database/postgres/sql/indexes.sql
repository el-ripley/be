-- ElRipley Database Schema - Indexes
-- Generated from SQLAlchemy models: facebook.py and user.py
-- Database: PostgreSQL
-- ================================================================
-- ALL INDEXES (Created after all tables)
-- ================================================================
-- Index for Facebook app scope user lookups
CREATE INDEX ix_facebook_app_scope_users_user_id ON facebook_app_scope_users(user_id);

-- Index for page admin lookups
CREATE INDEX ix_facebook_page_admins_facebook_user_id ON facebook_page_admins(facebook_user_id);

CREATE INDEX ix_facebook_page_admins_page_id ON facebook_page_admins(page_id);

-- Index for Facebook page scope user lookups
CREATE INDEX ix_facebook_page_scope_users_fan_page_id ON facebook_page_scope_users(fan_page_id);

-- Index for user role queries
CREATE INDEX ix_user_role_user_id ON user_role(user_id);

CREATE INDEX ix_user_role_role_id ON user_role(role_id);

-- Index for refresh tokens queries
CREATE INDEX ix_refresh_tokens_user_id ON refresh_tokens(user_id);

CREATE INDEX ix_refresh_tokens_token ON refresh_tokens(token);

CREATE INDEX ix_refresh_tokens_expires_at ON refresh_tokens(expires_at);

CREATE INDEX ix_refresh_tokens_active ON refresh_tokens(user_id, is_revoked, expires_at);

-- for active tokens lookup
-- Index for posts queries
CREATE INDEX ix_posts_fan_page_id ON posts(fan_page_id);

CREATE INDEX ix_posts_created_time ON posts(facebook_created_time);

-- Index for facebook_conversation_messages queries
CREATE INDEX ix_facebook_conversation_messages_fan_page_id ON facebook_conversation_messages(fan_page_id);

CREATE INDEX ix_facebook_conversation_messages_facebook_page_scope_user_id ON facebook_conversation_messages(facebook_page_scope_user_id);

CREATE INDEX ix_facebook_conversation_messages_deleted_at ON facebook_conversation_messages(deleted_at);

CREATE INDEX ix_facebook_conversation_messages_active ON facebook_conversation_messages(fan_page_id, deleted_at);

CREATE INDEX ix_facebook_conversation_messages_mark_as_read ON facebook_conversation_messages(mark_as_read);

CREATE INDEX ix_facebook_conversation_messages_user_seen_at ON facebook_conversation_messages(user_seen_at);

CREATE INDEX ix_facebook_conversation_messages_unread ON facebook_conversation_messages(fan_page_id, mark_as_read, deleted_at);

-- for active conversations lookup
-- Index for comments queries
CREATE INDEX ix_comments_post_id ON comments(post_id);

CREATE INDEX ix_comments_fan_page_id ON comments(fan_page_id);

CREATE INDEX ix_comments_parent_comment_id ON comments(parent_comment_id);

CREATE INDEX ix_comments_from_user ON comments(facebook_page_scope_user_id);

CREATE INDEX ix_comments_is_from_page ON comments(is_from_page);

CREATE INDEX ix_comments_created_time ON comments(facebook_created_time);

CREATE INDEX ix_comments_is_hidden ON comments(is_hidden);

CREATE INDEX ix_comments_deleted_at ON comments(deleted_at);

CREATE INDEX ix_comments_page_seen_at ON comments(page_seen_at);

CREATE INDEX ix_comments_active ON comments(post_id, is_hidden, deleted_at);

-- for active comments lookup
CREATE INDEX ix_comments_active_by_page ON comments(fan_page_id, is_hidden, deleted_at);

-- for active comments lookup by page
-- Index for facebook_conversation_comments queries
CREATE INDEX ix_fb_conv_comments_fan_page_id ON facebook_conversation_comments(fan_page_id);

CREATE INDEX ix_fb_conv_comments_post_id ON facebook_conversation_comments(post_id);

CREATE INDEX ix_fb_conv_comments_latest_time ON facebook_conversation_comments(latest_comment_facebook_time);

CREATE INDEX ix_fb_conv_comments_root_comment_id ON facebook_conversation_comments(root_comment_id);

CREATE INDEX ix_fb_conv_comments_mark_as_read ON facebook_conversation_comments(mark_as_read);

CREATE INDEX ix_fb_conv_comments_unread ON facebook_conversation_comments(fan_page_id, mark_as_read);

-- Index for facebook_conversation_comment_entries queries
CREATE INDEX ix_fb_conv_comment_entries_conversation ON facebook_conversation_comment_entries(conversation_id);

CREATE INDEX ix_fb_conv_comment_entries_comment_id ON facebook_conversation_comment_entries(comment_id);

-- ================================================================
-- MEDIA ASSETS INDEXES (Unified media_assets table)
-- ================================================================

-- Partial unique index for Facebook mirror deduplication
-- Only one media per (fb_owner_type, fb_owner_id, fb_field_name) for FB mirrors
CREATE UNIQUE INDEX idx_media_assets_fb_unique 
ON media_assets (fb_owner_type, fb_owner_id, fb_field_name) 
WHERE source_type = 'facebook_mirror' 
  AND fb_owner_type IS NOT NULL 
  AND fb_owner_id IS NOT NULL 
  AND fb_field_name IS NOT NULL;

-- Index for user's permanent storage calculation (quota tracking)
CREATE INDEX idx_media_assets_user_permanent 
ON media_assets (user_id, file_size_bytes) 
WHERE retention_policy = 'permanent';

-- Index for user's all media lookup
CREATE INDEX idx_media_assets_user_id ON media_assets (user_id);

-- Index for expired media cleanup jobs
CREATE INDEX idx_media_assets_expires_at 
ON media_assets (expires_at) 
WHERE expires_at IS NOT NULL AND retention_policy != 'permanent';

-- Index for s3_key lookup (for S3 operations)
CREATE INDEX idx_media_assets_s3_key ON media_assets (s3_key);

-- Index for source hash deduplication
CREATE INDEX idx_media_assets_source_hash ON media_assets(source_hash) WHERE source_hash IS NOT NULL;

-- Index for status lookup
CREATE INDEX idx_media_assets_status ON media_assets(status);

-- Index for retention policy lookup
CREATE INDEX idx_media_assets_retention ON media_assets(retention_policy);

-- Index for source type filtering
CREATE INDEX idx_media_assets_source_type ON media_assets(source_type);

-- Index for created_at ordering
CREATE INDEX idx_media_assets_created_at ON media_assets(created_at);

-- NOTE: Indexes for suggest_response_prompt_media removed (table deprecated)
-- Media is now linked to memory blocks via memory_block_media

-- Index for post_reactions queries
CREATE INDEX idx_post_reactions_post_id ON post_reactions(post_id);
CREATE INDEX idx_post_reactions_type ON post_reactions(reaction_type);
CREATE INDEX idx_post_reactions_reactor ON post_reactions(reactor_id);

-- Index for comment_reactions queries
CREATE INDEX idx_comment_reactions_comment_id ON comment_reactions(comment_id);
CREATE INDEX idx_comment_reactions_type ON comment_reactions(reaction_type);

-- Index for messages queries
CREATE INDEX ix_messages_conversation_id ON messages(conversation_id);

CREATE INDEX ix_messages_is_echo ON messages(is_echo);

CREATE INDEX ix_messages_timestamp ON messages(facebook_timestamp);

CREATE INDEX ix_messages_page_seen ON messages(conversation_id, page_seen_at);

CREATE INDEX ix_messages_page_seen_at ON messages(page_seen_at);

-- Partial indexes for efficient computed unread counts
CREATE INDEX ix_messages_unread_user_messages ON messages(conversation_id) 
WHERE is_echo = FALSE AND page_seen_at IS NULL;

CREATE INDEX ix_messages_deleted_at ON messages(deleted_at);

CREATE INDEX ix_messages_conversation_thread ON messages(conversation_id, facebook_timestamp);

-- for conversation threads
CREATE INDEX ix_messages_active ON messages(conversation_id, deleted_at);

-- for active messages lookup
CREATE INDEX ix_messages_template_data ON messages USING GIN (template_data);

-- for JSONB queries
-- Index for user API keys queries - REMOVED: BYOK support has been removed
-- Note: These indexes can be dropped via migration script

-- Index for user conversation settings queries
CREATE INDEX ix_user_conversation_settings_user_id ON user_conversation_settings(user_id);

-- Index for user storage quotas queries
CREATE INDEX ix_user_storage_quotas_updated_at ON user_storage_quotas(updated_at);

-- Index for OpenAI response queries
CREATE INDEX ix_openai_response_user_id ON openai_response(user_id);

CREATE INDEX ix_openai_response_response_id ON openai_response(response_id);

CREATE INDEX ix_openai_response_conversation_id ON openai_response(conversation_id);

CREATE INDEX ix_openai_response_branch_id ON openai_response(branch_id);

CREATE INDEX ix_openai_response_created_at ON openai_response(created_at);

CREATE INDEX ix_openai_response_user_created ON openai_response(user_id, created_at);

CREATE INDEX ix_openai_response_conversation_created ON openai_response(conversation_id, created_at);

-- Index for API key tracking - REMOVED: BYOK support has been removed
-- Note: These indexes can be dropped via migration script
-- CREATE INDEX ix_openai_response_api_key_type ON openai_response(api_key_type);
-- CREATE INDEX ix_openai_response_user_api_key_id ON openai_response(user_api_key_id);
-- CREATE INDEX ix_openai_response_user_cost_tracking ON openai_response(user_id, api_key_type, created_at);

-- Index for OpenAI conversation queries
CREATE INDEX ix_openai_conversation_user_id ON openai_conversation(user_id);

CREATE INDEX ix_openai_conversation_created_at ON openai_conversation(created_at);

CREATE INDEX ix_openai_conversation_updated_at ON openai_conversation(updated_at);

-- Index for OpenAI message queries
CREATE INDEX ix_openai_message_conversation_id ON openai_message(conversation_id);

CREATE INDEX ix_openai_message_role ON openai_message(role);

CREATE INDEX ix_openai_message_type ON openai_message(type);

CREATE INDEX ix_openai_message_sequence ON openai_message(conversation_id, sequence_number);

CREATE INDEX ix_openai_message_call_id ON openai_message(call_id) WHERE call_id IS NOT NULL;

CREATE INDEX ix_openai_message_created_at ON openai_message(created_at);

CREATE INDEX ix_openai_message_conversation_created ON openai_message(conversation_id, created_at);

-- Index for OpenAI conversation branching system
CREATE INDEX ix_openai_conversation_branch_conversation_id ON openai_conversation_branch(conversation_id);
CREATE INDEX ix_openai_conversation_branch_is_active ON openai_conversation_branch(is_active);
CREATE INDEX ix_openai_conversation_branch_conversation_active ON openai_conversation_branch(conversation_id, is_active);
CREATE INDEX ix_openai_conversation_branch_created_from_branch ON openai_conversation_branch(created_from_branch_id);
CREATE INDEX ix_openai_conversation_branch_message_ids ON openai_conversation_branch USING GIN (message_ids);

CREATE INDEX ix_openai_branch_message_mapping_message_id ON openai_branch_message_mapping(message_id);
CREATE INDEX ix_openai_branch_message_mapping_branch_id ON openai_branch_message_mapping(branch_id);
CREATE INDEX ix_openai_branch_message_mapping_branch_message ON openai_branch_message_mapping(branch_id, message_id);
CREATE INDEX ix_openai_branch_message_mapping_is_modified ON openai_branch_message_mapping(is_modified);
CREATE INDEX ix_openai_branch_message_mapping_is_hidden ON openai_branch_message_mapping(is_hidden);

-- Index for OpenAI conversation current branch
CREATE INDEX ix_openai_conversation_current_branch_id ON openai_conversation(current_branch_id);

-- Index for OpenAI conversation subagent support
CREATE INDEX idx_openai_conversation_parent ON openai_conversation(parent_conversation_id) 
WHERE parent_conversation_id IS NOT NULL;

CREATE INDEX idx_openai_conversation_subagent ON openai_conversation(is_subagent) 
WHERE is_subagent = TRUE;

CREATE INDEX idx_openai_conversation_parent_agent_response ON openai_conversation(parent_agent_response_id)
WHERE parent_agent_response_id IS NOT NULL;

CREATE INDEX idx_openai_conversation_task_call_id ON openai_conversation(task_call_id)
WHERE task_call_id IS NOT NULL;

-- Index for agent response queries
CREATE INDEX ix_agent_response_conversation_id ON agent_response(conversation_id);
CREATE INDEX ix_agent_response_branch_id ON agent_response(branch_id);
CREATE INDEX ix_agent_response_conversation_branch ON agent_response(conversation_id, branch_id);
CREATE INDEX ix_agent_response_created_at ON agent_response(created_at);
CREATE INDEX ix_agent_response_message_ids ON agent_response USING GIN (message_ids);
-- Index for API key tracking - REMOVED: BYOK support has been removed
-- Note: These indexes can be dropped via migration script
-- CREATE INDEX ix_agent_response_api_key_type ON agent_response(api_key_type);
-- CREATE INDEX ix_agent_response_user_api_key_id ON agent_response(user_api_key_id);
CREATE INDEX ix_agent_response_user_id ON agent_response(user_id);
CREATE INDEX idx_agent_response_parent_id ON agent_response(parent_agent_response_id);

-- Index for openai_response agent_response_id
CREATE INDEX ix_openai_response_agent_response_id ON openai_response(agent_response_id);

-- ================================================================
-- SUGGEST RESPONSE INDEXES
-- ================================================================

-- Indexes for page_memory
CREATE UNIQUE INDEX uq_active_page_memory 
ON page_memory (fan_page_id, prompt_type) 
WHERE is_active = TRUE;

CREATE INDEX idx_page_memory_active 
ON page_memory (fan_page_id, prompt_type, is_active);

CREATE INDEX idx_page_memory_owner 
ON page_memory (owner_user_id, is_active);

-- Indexes for page_scope_user_memory
CREATE UNIQUE INDEX uq_active_page_scope_user_memory 
ON page_scope_user_memory (fan_page_id, facebook_page_scope_user_id) 
WHERE is_active = TRUE;

CREATE INDEX idx_page_scope_user_memory_active 
ON page_scope_user_memory (fan_page_id, facebook_page_scope_user_id, is_active);

CREATE INDEX idx_page_scope_user_memory_owner 
ON page_scope_user_memory (owner_user_id, is_active);

-- Indexes for user_memory
CREATE UNIQUE INDEX uq_active_user_memory 
ON user_memory (owner_user_id) 
WHERE is_active = TRUE;

CREATE INDEX idx_user_memory_owner 
ON user_memory (owner_user_id, is_active);

-- Indexes for suggest_response_history
CREATE INDEX idx_suggest_history_user_id_fan_page_id_created_at 
ON suggest_response_history (user_id, fan_page_id, created_at DESC);

CREATE INDEX idx_suggest_history_messages_conv 
ON suggest_response_history (facebook_conversation_messages_id) 
WHERE facebook_conversation_messages_id IS NOT NULL;

CREATE INDEX idx_suggest_history_comments_conv 
ON suggest_response_history (facebook_conversation_comments_id) 
WHERE facebook_conversation_comments_id IS NOT NULL;

CREATE INDEX idx_suggest_history_page_prompt 
ON suggest_response_history (page_prompt_id) 
WHERE page_prompt_id IS NOT NULL;

CREATE INDEX idx_suggest_history_page_scope_user_prompt 
ON suggest_response_history (page_scope_user_prompt_id) 
WHERE page_scope_user_prompt_id IS NOT NULL;

CREATE INDEX idx_suggest_history_agent_response 
ON suggest_response_history (agent_response_id);

CREATE INDEX idx_suggest_history_trigger_type 
ON suggest_response_history (trigger_type, created_at DESC);

-- Indexes for memory_blocks
CREATE INDEX idx_memory_blocks_latest 
ON memory_blocks(prompt_type, prompt_id, block_key, created_at DESC);

CREATE INDEX idx_memory_blocks_prompt 
ON memory_blocks(prompt_type, prompt_id);

-- Indexes for memory_block_media
CREATE INDEX idx_memory_block_media_block 
ON memory_block_media(block_id, display_order);

-- Indexes for page_admin_suggest_config
CREATE INDEX idx_page_admin_suggest_config_page_admin 
ON page_admin_suggest_config(page_admin_id);

-- Index for finding admins with webhook automation enabled (for webhook processing)
CREATE INDEX idx_page_admin_suggest_config_webhook_enabled 
ON page_admin_suggest_config(page_admin_id) 
WHERE auto_webhook_suggest = TRUE OR auto_webhook_graph_api = TRUE;

-- Indexes for suggest_response_message
CREATE INDEX idx_suggest_response_message_history 
ON suggest_response_message(history_id);

CREATE INDEX idx_suggest_response_message_history_seq 
ON suggest_response_message(history_id, sequence_number);

CREATE INDEX idx_suggest_response_message_type 
ON suggest_response_message(type);

CREATE INDEX idx_suggest_response_message_call_id 
ON suggest_response_message(call_id) WHERE call_id IS NOT NULL;

-- ================================================================
-- PLAYBOOK INDEXES
-- ================================================================

-- page_playbooks: owner lookup (general_agent queries by owner)
CREATE INDEX idx_page_playbooks_owner
ON page_playbooks (owner_user_id)
WHERE deleted_at IS NULL;

-- page_playbooks: tag-based filtering (GIN for array containment @>)
CREATE INDEX idx_page_playbooks_tags
ON page_playbooks USING GIN (tags)
WHERE deleted_at IS NULL;

-- page_playbooks: soft-delete filter (frequently used WHERE deleted_at IS NULL)
CREATE INDEX idx_page_playbooks_active
ON page_playbooks (created_at DESC)
WHERE deleted_at IS NULL;

-- page_playbook_assignments: lookup by page_admin + conversation_type (primary access pattern)
CREATE INDEX idx_playbook_assignments_admin_type
ON page_playbook_assignments (page_admin_id, conversation_type)
WHERE deleted_at IS NULL;

-- page_playbook_assignments: reverse lookup - which pages use a playbook
CREATE INDEX idx_playbook_assignments_playbook
ON page_playbook_assignments (playbook_id)
WHERE deleted_at IS NULL;

-- ================================================================
-- CONVERSATION AGENT BLOCKS INDEXES
-- ================================================================

-- Index for checking if conversation is blocked (webhook condition check)
CREATE INDEX idx_conv_agent_blocks_messages ON conversation_agent_blocks(facebook_conversation_messages_id, is_active) 
WHERE facebook_conversation_messages_id IS NOT NULL;

CREATE INDEX idx_conv_agent_blocks_comments ON conversation_agent_blocks(facebook_conversation_comments_id, is_active) 
WHERE facebook_conversation_comments_id IS NOT NULL;

-- Index for page-level block queries
CREATE INDEX idx_conv_agent_blocks_fan_page ON conversation_agent_blocks(fan_page_id, is_active);

-- ================================================================
-- AGENT ESCALATIONS INDEXES
-- ================================================================

-- Index for agent reading open escalations for current conversation (context loading)
CREATE INDEX idx_agent_escalations_messages ON agent_escalations(facebook_conversation_messages_id, status) 
WHERE facebook_conversation_messages_id IS NOT NULL;

CREATE INDEX idx_agent_escalations_comments ON agent_escalations(facebook_conversation_comments_id, status) 
WHERE facebook_conversation_comments_id IS NOT NULL;

-- Index for user/general_agent dashboard (open escalations)
CREATE INDEX idx_agent_escalations_owner_status ON agent_escalations(owner_user_id, status, created_at DESC);

-- Index for page-level escalation queries
CREATE INDEX idx_agent_escalations_fan_page ON agent_escalations(fan_page_id, status, created_at DESC);

-- Index for priority filtering
CREATE INDEX idx_agent_escalations_priority ON agent_escalations(priority, status, created_at DESC);

-- ================================================================
-- AGENT ESCALATION MESSAGES INDEXES
-- ================================================================

-- Index for loading messages within an escalation thread (primary access pattern)
CREATE INDEX idx_escalation_messages_thread ON agent_escalation_messages(escalation_id, created_at);

-- Index for filtering by sender type within a thread
CREATE INDEX idx_escalation_messages_sender ON agent_escalation_messages(escalation_id, sender_type);


-- ================================================================
-- NOTIFICATIONS INDEXES
-- ================================================================
-- Main listing and unread count by user
CREATE INDEX ix_notifications_owner_read_created ON notifications(owner_user_id, is_read, created_at DESC);
-- Lookup by source entity
CREATE INDEX ix_notifications_reference ON notifications(reference_type, reference_id);


-- ================================================================
-- CORE BILLING INDEXES (Provider-agnostic)
-- ================================================================

-- billing_settings indexes
CREATE INDEX idx_billing_settings_key ON billing_settings(setting_key);

-- stripe_products indexes (Stripe product catalog)
CREATE INDEX idx_stripe_products_is_active ON stripe_products(is_active) WHERE is_active = TRUE;
CREATE INDEX idx_stripe_products_product_code ON stripe_products(product_code);

-- user_credit_balance indexes
CREATE INDEX idx_user_credit_balance_user_id ON user_credit_balance(user_id);
CREATE INDEX idx_user_credit_balance_balance ON user_credit_balance(balance_usd);

-- credit_transactions indexes (provider-agnostic ledger)
CREATE INDEX idx_credit_transactions_user_id ON credit_transactions(user_id);
CREATE INDEX idx_credit_transactions_type ON credit_transactions(transaction_type);
CREATE INDEX idx_credit_transactions_created_at ON credit_transactions(created_at DESC);
CREATE INDEX idx_credit_transactions_source ON credit_transactions(source_type, source_id) WHERE source_id IS NOT NULL;
CREATE INDEX idx_credit_transactions_user_created ON credit_transactions(user_id, created_at DESC);

-- ================================================================
-- STRIPE-SPECIFIC INDEXES
-- ================================================================
-- NOTE: stripe_plan_config and stripe_topup_config removed
-- Stripe product/price IDs are configured via environment variables

-- stripe_customers indexes
CREATE INDEX idx_stripe_customers_user_id ON stripe_customers(user_id);
CREATE INDEX idx_stripe_customers_stripe_id ON stripe_customers(stripe_customer_id);

-- stripe_payments indexes
CREATE INDEX idx_stripe_payments_user_id ON stripe_payments(user_id);
CREATE INDEX idx_stripe_payments_status ON stripe_payments(status);
CREATE INDEX idx_stripe_payments_payment_intent ON stripe_payments(stripe_payment_intent_id);
CREATE INDEX idx_stripe_payments_stripe_product_id ON stripe_payments(stripe_product_id) WHERE stripe_product_id IS NOT NULL;

-- stripe_webhook_events indexes
CREATE INDEX idx_stripe_webhook_events_event_id ON stripe_webhook_events(stripe_event_id);
CREATE INDEX idx_stripe_webhook_events_status ON stripe_webhook_events(status);
CREATE INDEX idx_stripe_webhook_events_type ON stripe_webhook_events(event_type);
CREATE INDEX idx_stripe_webhook_events_user_id ON stripe_webhook_events(user_id);
CREATE INDEX idx_stripe_webhook_events_pending ON stripe_webhook_events(status, received_at) WHERE status = 'pending';

-- ================================================================
-- SEPAY-SPECIFIC INDEXES
-- ================================================================

-- user_topup_codes indexes
CREATE INDEX idx_user_topup_codes_code ON user_topup_codes(topup_code);

-- sepay_transactions indexes
CREATE INDEX idx_sepay_transactions_user ON sepay_transactions(user_id, created_at DESC);
CREATE INDEX idx_sepay_transactions_status ON sepay_transactions(status);

-- ================================================================
-- POLAR-SPECIFIC INDEXES
-- ================================================================

-- polar_payments indexes
CREATE INDEX idx_polar_payments_user_id ON polar_payments(user_id);
CREATE INDEX idx_polar_payments_polar_order_id ON polar_payments(polar_order_id);
CREATE INDEX idx_polar_payments_status ON polar_payments(status);
CREATE INDEX idx_polar_payments_created_at ON polar_payments(created_at DESC);
CREATE INDEX idx_polar_payments_user_created ON polar_payments(user_id, created_at DESC);

-- polar_webhook_events indexes
CREATE INDEX idx_polar_webhook_events_event_id ON polar_webhook_events(polar_event_id);
CREATE INDEX idx_polar_webhook_events_status ON polar_webhook_events(status);
CREATE INDEX idx_polar_webhook_events_type ON polar_webhook_events(event_type);
CREATE INDEX idx_polar_webhook_events_user_id ON polar_webhook_events(user_id) WHERE user_id IS NOT NULL;

