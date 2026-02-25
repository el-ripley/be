-- ElRipley Database Schema - Facebook Integration Domain
-- Generated from SQLAlchemy models: facebook.py and user.py
-- Database: PostgreSQL
-- ================================================================
-- FACEBOOK INTEGRATION TABLES
-- ================================================================
-- Facebook app scope users table (ASID)
CREATE TABLE facebook_app_scope_users (
    id VARCHAR(255) PRIMARY KEY, -- ASID
    user_id VARCHAR(36) NOT NULL,
    name VARCHAR(255),
    gender VARCHAR(255),
    email VARCHAR(255),
    picture VARCHAR(255),
    created_at INTEGER NOT NULL,
    updated_at INTEGER NOT NULL,
    FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE
);

-- Facebook fan pages table
CREATE TABLE fan_pages (
    id VARCHAR(255) PRIMARY KEY, -- page_id
    name VARCHAR(1024),
    avatar VARCHAR(1024),
    category VARCHAR,
    -- Engagement & Stats
    fan_count INTEGER,
    followers_count INTEGER,
    rating_count INTEGER,
    overall_star_rating DECIMAL(3, 2),
    -- Content & Description
    about TEXT,
    description TEXT,
    -- Contact & Location
    link TEXT,
    website TEXT,
    phone VARCHAR(255),
    emails JSONB,
    location JSONB,
    -- Media
    cover TEXT,
    -- Business Info
    hours JSONB,
    is_verified BOOLEAN,
    created_at INTEGER NOT NULL,
    updated_at INTEGER NOT NULL
);

-- Facebook page admins table
CREATE TABLE facebook_page_admins (
    id VARCHAR(36) PRIMARY KEY,
    facebook_user_id VARCHAR(255) NOT NULL,
    page_id VARCHAR(255) NOT NULL,
    access_token TEXT NOT NULL, -- page token
    tasks JSONB, -- e.g., ["MANAGE", "MESSAGING", "CREATE_CONTENT"]
    created_at INTEGER NOT NULL,
    updated_at INTEGER NOT NULL,
    FOREIGN KEY (facebook_user_id) REFERENCES facebook_app_scope_users(id) ON DELETE CASCADE,
    FOREIGN KEY (page_id) REFERENCES fan_pages(id) ON DELETE CASCADE,
    CONSTRAINT uq_user_page UNIQUE (facebook_user_id, page_id)
);

-- Facebook page scope users table (PSID)
CREATE TABLE facebook_page_scope_users (
    id VARCHAR(255) PRIMARY KEY, -- PSID
    fan_page_id VARCHAR(255) NOT NULL,
    user_info JSONB,
    created_at INTEGER NOT NULL,
    updated_at INTEGER NOT NULL,
    FOREIGN KEY (fan_page_id) REFERENCES fan_pages(id) ON DELETE CASCADE
);

-- Facebook posts table - created when comments are received
CREATE TABLE posts (
    id VARCHAR(255) PRIMARY KEY, -- Facebook post_id (e.g., "109058017539645_758750086916049")
    fan_page_id VARCHAR(255) NOT NULL,
    message TEXT, -- post text content
    video_link TEXT, -- video URL if post contains video
    photo_link TEXT, -- photo URL if post contains photo
    facebook_created_time INTEGER, -- created_time from webhook
    -- Engagement aggregate counts (populated on initial fetch, updated by Agent refetch)
    reaction_total_count INTEGER DEFAULT 0,
    reaction_like_count INTEGER DEFAULT 0,
    reaction_love_count INTEGER DEFAULT 0,
    reaction_haha_count INTEGER DEFAULT 0,
    reaction_wow_count INTEGER DEFAULT 0,
    reaction_sad_count INTEGER DEFAULT 0,
    reaction_angry_count INTEGER DEFAULT 0,
    reaction_care_count INTEGER DEFAULT 0,
    share_count INTEGER DEFAULT 0,
    comment_count INTEGER DEFAULT 0,
    -- Additional metadata (populated on initial fetch)
    full_picture TEXT,              -- High-res image URL
    permalink_url TEXT,             -- Direct link to post
    status_type VARCHAR(100),       -- mobile_status_update, added_photos, etc.
    is_published BOOLEAN DEFAULT TRUE,
    -- Tracking timestamps (for Agent to know data age)
    reactions_fetched_at INTEGER,   -- When reactions were last fetched
    engagement_fetched_at INTEGER,  -- When full engagement was last fetched
    created_at INTEGER NOT NULL,
    updated_at INTEGER NOT NULL,
    FOREIGN KEY (fan_page_id) REFERENCES fan_pages(id) ON DELETE CASCADE
);

-- Facebook comments table
CREATE TABLE comments (
    id VARCHAR(255) PRIMARY KEY, -- Facebook comment_id (e.g., "758750086916049_794400426340115")
    post_id VARCHAR(255) NOT NULL,
    parent_comment_id VARCHAR(255), -- for reply comments, references another comment, if null, it is a top-level comment
    is_from_page BOOLEAN NOT NULL DEFAULT FALSE, -- TRUE if comment is from page itself
    fan_page_id VARCHAR(255) NOT NULL, -- direct reference to fan page for performance
    facebook_page_scope_user_id VARCHAR(255), -- PSID if from user, NULL if from page
    message TEXT, -- comment text content
    photo_url TEXT, -- photo URL if comment contains photo
    video_url TEXT, -- video URL if comment contains video
    facebook_created_time INTEGER, -- created_time from webhook
    -- Engagement counts (populated on initial fetch from list_comments, updated by Agent)
    like_count INTEGER DEFAULT 0,
    reply_count INTEGER DEFAULT 0,
    -- Tracking timestamp
    reactions_fetched_at INTEGER,
    is_hidden BOOLEAN NOT NULL DEFAULT FALSE, -- for hiding comments without deleting
    page_seen_at INTEGER, -- when page/admin viewed this comment (for per-comment tracking)
    deleted_at INTEGER, -- soft delete timestamp, NULL if not deleted
    metadata JSONB, -- e.g. {"sent_by": "ai_agent"|"admin", "history_id": "uuid"}
    created_at INTEGER NOT NULL,
    updated_at INTEGER NOT NULL,
    FOREIGN KEY (post_id) REFERENCES posts(id) ON DELETE CASCADE,
    FOREIGN KEY (fan_page_id) REFERENCES fan_pages(id) ON DELETE CASCADE,
    FOREIGN KEY (parent_comment_id) REFERENCES comments(id) ON DELETE CASCADE,
    FOREIGN KEY (facebook_page_scope_user_id) REFERENCES facebook_page_scope_users(id) ON DELETE SET NULL
);

-- Facebook post reactions table - detailed reactions on posts
CREATE TABLE post_reactions (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    post_id VARCHAR(255) NOT NULL,
    fan_page_id VARCHAR(255) NOT NULL,
    -- Reactor info
    -- reactor_id: Page-scoped user ID (PSID) if user reaction, NULL if page reaction
    -- If NULL, reactor is the page itself (use fan_page_id to get page info)
    reactor_id VARCHAR(255),                -- Page-scoped user ID (PSID), NULL for page reactions
    reactor_name VARCHAR(500),              -- User/Page display name, NULL for page reactions
    reactor_profile_pic TEXT,               -- Profile picture URL (optional)
    -- Reaction details
    reaction_type VARCHAR(20) NOT NULL,     -- LIKE, LOVE, HAHA, WOW, SAD, ANGRY, CARE
    -- Timestamps
    created_at INTEGER NOT NULL,
    updated_at INTEGER NOT NULL,
    -- Constraints
    FOREIGN KEY (post_id) REFERENCES posts(id) ON DELETE CASCADE,
    FOREIGN KEY (fan_page_id) REFERENCES fan_pages(id) ON DELETE CASCADE,
    -- Unique constraint: post + reactor (NULL allowed for page reactions)
    CONSTRAINT uq_post_reactor UNIQUE (post_id, reactor_id)
);

-- Facebook comment reactions table - detailed reactions on comments
CREATE TABLE comment_reactions (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    comment_id VARCHAR(255) NOT NULL,
    post_id VARCHAR(255) NOT NULL,
    fan_page_id VARCHAR(255) NOT NULL,
    -- Reactor info
    -- reactor_id: Page-scoped user ID (PSID) if user reaction, NULL if page reaction
    -- If NULL, reactor is the page itself (use fan_page_id to get page info)
    reactor_id VARCHAR(255),                -- Page-scoped user ID (PSID), NULL for page reactions
    reactor_name VARCHAR(500),              -- User/Page display name, NULL for page reactions
    -- Reaction details
    reaction_type VARCHAR(20) NOT NULL,
    -- Timestamps
    created_at INTEGER NOT NULL,
    updated_at INTEGER NOT NULL,
    -- Constraints
    FOREIGN KEY (comment_id) REFERENCES comments(id) ON DELETE CASCADE,
    FOREIGN KEY (post_id) REFERENCES posts(id) ON DELETE CASCADE,
    FOREIGN KEY (fan_page_id) REFERENCES fan_pages(id) ON DELETE CASCADE,
    -- Unique constraint: comment + reactor (NULL allowed for page reactions)
    CONSTRAINT uq_comment_reactor UNIQUE (comment_id, reactor_id)
);

-- Facebook comment conversations table - aggregates metadata per root comment thread
CREATE TABLE facebook_conversation_comments (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    root_comment_id VARCHAR(255) NOT NULL, -- anchor comment id
    fan_page_id VARCHAR(255) NOT NULL,
    post_id VARCHAR(255) NOT NULL,
    participant_scope_users JSONB NOT NULL DEFAULT '[]'::jsonb, -- list of participant snapshots keyed by facebook_page_scope_user_id
    has_page_reply BOOLEAN NOT NULL DEFAULT FALSE,
    latest_comment_is_from_page BOOLEAN,
    latest_comment_id VARCHAR(255), -- most recent comment in the thread
    latest_comment_facebook_time INTEGER,
    page_last_seen_comment_id VARCHAR(255), -- cursor: last comment page has seen
    page_last_seen_at INTEGER, -- timestamp when page last viewed this thread
    mark_as_read BOOLEAN NOT NULL DEFAULT FALSE, -- user manually toggle read/unread state (UX feature)
    created_at INTEGER NOT NULL,
    updated_at INTEGER NOT NULL,
    FOREIGN KEY (root_comment_id) REFERENCES comments(id) ON DELETE CASCADE,
    FOREIGN KEY (fan_page_id) REFERENCES fan_pages(id) ON DELETE CASCADE,
    FOREIGN KEY (post_id) REFERENCES posts(id) ON DELETE CASCADE,
    FOREIGN KEY (latest_comment_id) REFERENCES comments(id) ON DELETE SET NULL,
    FOREIGN KEY (page_last_seen_comment_id) REFERENCES comments(id) ON DELETE SET NULL,
    CONSTRAINT uq_facebook_conversation_root UNIQUE (root_comment_id)
);

-- Mapping table to list every comment that belongs to a conversation thread
CREATE TABLE facebook_conversation_comment_entries (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    conversation_id UUID NOT NULL,
    comment_id VARCHAR(255) NOT NULL,
    is_root_comment BOOLEAN NOT NULL DEFAULT FALSE,
    created_at INTEGER NOT NULL,
    updated_at INTEGER NOT NULL,
    FOREIGN KEY (conversation_id) REFERENCES facebook_conversation_comments(id) ON DELETE CASCADE,
    FOREIGN KEY (comment_id) REFERENCES comments(id) ON DELETE CASCADE,
    CONSTRAINT uq_conversation_comment_entry UNIQUE (conversation_id, comment_id)
);

-- Track posts sync progress per page
CREATE TABLE facebook_post_sync_states (
    id SERIAL PRIMARY KEY,
    fan_page_id VARCHAR(255) NOT NULL UNIQUE,
    posts_cursor TEXT,                              -- Facebook paging cursor (after)
    total_synced_posts INTEGER NOT NULL DEFAULT 0,
    status VARCHAR(50) NOT NULL DEFAULT 'idle',     -- idle | in_progress | completed
    last_sync_at INTEGER,
    created_at INTEGER NOT NULL,
    updated_at INTEGER NOT NULL,
    FOREIGN KEY (fan_page_id) REFERENCES fan_pages(id) ON DELETE CASCADE
);

-- Track comments sync progress per post
CREATE TABLE facebook_post_comment_sync_states (
    id SERIAL PRIMARY KEY,
    post_id VARCHAR(255) NOT NULL UNIQUE,
    fan_page_id VARCHAR(255) NOT NULL,
    comments_cursor TEXT,                           -- Facebook paging cursor for root comments
    total_synced_root_comments INTEGER NOT NULL DEFAULT 0,
    total_synced_comments INTEGER NOT NULL DEFAULT 0,  -- Including replies
    status VARCHAR(50) NOT NULL DEFAULT 'idle',     -- idle | in_progress | completed
    last_sync_at INTEGER,
    created_at INTEGER NOT NULL,
    updated_at INTEGER NOT NULL,
    FOREIGN KEY (post_id) REFERENCES posts(id) ON DELETE CASCADE,
    FOREIGN KEY (fan_page_id) REFERENCES fan_pages(id) ON DELETE CASCADE
);

-- Facebook conversation messages table - represents a conversation thread between a page and a user
CREATE TABLE facebook_conversation_messages (
    id VARCHAR(255) PRIMARY KEY, -- native Graph conversation id (e.g., t_*)
    fan_page_id VARCHAR(255) NOT NULL,
    facebook_page_scope_user_id VARCHAR(255) NOT NULL,
    participants_snapshot JSONB NOT NULL DEFAULT '[]'::jsonb,
    latest_message_is_from_page BOOLEAN,
    latest_message_id VARCHAR(255),
    latest_message_facebook_time BIGINT,
    page_last_seen_message_id VARCHAR(255), -- cursor: last message page has seen (app controls this)
    page_last_seen_at BIGINT,
    user_seen_at BIGINT, -- timestamp when user saw conversation (from FB webhook, no specific message)
    mark_as_read BOOLEAN NOT NULL DEFAULT FALSE, -- user manually toggle read/unread state (UX feature)
    ad_context JSONB, -- ad context from webhook referral: {ad_id, source, type, ad_title, photo_url, video_url, post_id, product_id}
    deleted_at INTEGER, -- soft delete timestamp, NULL if not deleted
    created_at INTEGER NOT NULL,
    updated_at INTEGER NOT NULL,
    FOREIGN KEY (fan_page_id) REFERENCES fan_pages(id) ON DELETE CASCADE,
    FOREIGN KEY (facebook_page_scope_user_id) REFERENCES facebook_page_scope_users(id) ON DELETE CASCADE,
    CONSTRAINT uq_page_user_facebook_conversation UNIQUE (fan_page_id, facebook_page_scope_user_id)
);

-- Facebook messages table
CREATE TABLE messages (
    id VARCHAR(255) PRIMARY KEY, -- Facebook message_id (mid)
    conversation_id VARCHAR(255) NOT NULL, -- references facebook_conversation_messages table
    is_echo BOOLEAN NOT NULL DEFAULT FALSE, -- TRUE if message from page, FALSE if from user
    text TEXT, -- text content
    photo_url TEXT, -- photo/image/gif/sticker URL
    video_url TEXT, -- video URL
    audio_url TEXT, -- audio URL
    template_data JSONB, -- for templates and interactive elements
    facebook_timestamp BIGINT, -- timestamp from webhook (milliseconds)
    page_seen_at BIGINT, -- when page saw this message (app controls, for per-message tracking)
    metadata JSONB, -- e.g. {"sent_by": "ai_agent", "history_id": "..."} for AI-sent messages
    reply_to_message_id VARCHAR(255), -- mid of message this one replies to (from webhook reply_to.mid)
    deleted_at INTEGER, -- soft delete timestamp, NULL if not deleted
    created_at INTEGER NOT NULL,
    updated_at INTEGER NOT NULL,
    FOREIGN KEY (conversation_id) REFERENCES facebook_conversation_messages(id) ON DELETE CASCADE
);

-- Facebook inbox sync state table - tracks progress of inbox synchronization per page
CREATE TABLE facebook_inbox_sync_states (
    id SERIAL PRIMARY KEY,
    fan_page_id VARCHAR(255) NOT NULL UNIQUE,
    fb_cursor TEXT,                    -- Facebook paging cursor (after cursor)
    total_synced_conversations INTEGER NOT NULL DEFAULT 0,
    total_synced_messages INTEGER NOT NULL DEFAULT 0,
    status VARCHAR(50) NOT NULL DEFAULT 'idle',  -- idle | in_progress | completed
    last_sync_at INTEGER,
    created_at INTEGER NOT NULL,
    updated_at INTEGER NOT NULL,
    FOREIGN KEY (fan_page_id) REFERENCES fan_pages(id) ON DELETE CASCADE
);
