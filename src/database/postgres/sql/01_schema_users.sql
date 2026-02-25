-- ElRipley Database Schema - Users Domain
-- Generated from SQLAlchemy models: facebook.py and user.py
-- Database: PostgreSQL
-- ================================================================
-- USERS AND ROLES TABLES
-- ================================================================
-- Roles table for user permissions
CREATE TABLE roles (
    id VARCHAR(36) PRIMARY KEY,
    name VARCHAR(50) NOT NULL UNIQUE DEFAULT 'user'
);

-- Main users table
CREATE TABLE users (
    id VARCHAR(36) PRIMARY KEY,
    created_at INTEGER NOT NULL,
    updated_at INTEGER NOT NULL
);

-- User API keys table - REMOVED: BYOK (Bring Your Own Key) strategy has been removed
-- All users now use system API key from environment settings
-- Note: This table can be dropped in production via migration script

-- Many-to-many relationship between users and roles
CREATE TABLE user_role (
    user_id VARCHAR(36) NOT NULL,
    role_id VARCHAR(36) NOT NULL,
    PRIMARY KEY (user_id, role_id),
    FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE,
    FOREIGN KEY (role_id) REFERENCES roles(id) ON DELETE CASCADE
);

-- Refresh tokens table for token rotation and session management
CREATE TABLE refresh_tokens (
    id VARCHAR(36) PRIMARY KEY,
    user_id VARCHAR(36) NOT NULL,
    token VARCHAR(512) NOT NULL UNIQUE, -- JWT refresh token
    expires_at INTEGER NOT NULL, -- Unix timestamp when token expires
    is_revoked BOOLEAN NOT NULL DEFAULT FALSE, -- For token rotation/revocation
    created_at INTEGER NOT NULL,
    updated_at INTEGER NOT NULL,
    FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE
);

-- User conversation settings table - user-level defaults for context management
CREATE TABLE user_conversation_settings (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id VARCHAR(36) NOT NULL UNIQUE REFERENCES users(id) ON DELETE CASCADE,
    context_token_limit INTEGER, -- User's preferred context token limit
    context_buffer_percent INTEGER, -- User's preferred buffer percentage (0-100)
    summarizer_model VARCHAR(100), -- User's preferred model for summarization
    vision_model VARCHAR(100), -- User's preferred model for vision tasks
    created_at BIGINT NOT NULL DEFAULT EXTRACT(EPOCH FROM NOW())::BIGINT * 1000,
    updated_at BIGINT NOT NULL DEFAULT EXTRACT(EPOCH FROM NOW())::BIGINT * 1000
);
