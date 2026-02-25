-- ElRipley Database Schema - Polar Integration
-- Polar-specific tables for payments (top-ups)
-- Database: PostgreSQL
-- ================================================================
-- POLAR-SPECIFIC TABLES
-- ================================================================

-- ================================================================
-- POLAR PAYMENTS (top-ups only)
-- ================================================================
-- Tracks all payments via Polar. User is linked via user_id (external_customer_id at checkout).
CREATE TABLE polar_payments (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id VARCHAR(36) NOT NULL REFERENCES users(id) ON DELETE CASCADE,

    -- Polar references
    polar_order_id VARCHAR(255) NOT NULL UNIQUE,
    polar_product_id VARCHAR(255),
    polar_customer_id VARCHAR(255),

    -- Amount
    amount_usd DECIMAL(10, 2) NOT NULL,
    credits_usd DECIMAL(10, 4) NOT NULL,
    currency VARCHAR(3) NOT NULL DEFAULT 'usd',

    -- Status: 'pending', 'paid', 'refunded'
    status VARCHAR(50) NOT NULL,
    billing_reason VARCHAR(50), -- 'purchase', 'subscription_create', etc.

    -- Timestamps
    paid_at BIGINT,
    created_at BIGINT NOT NULL DEFAULT EXTRACT(EPOCH FROM NOW())::BIGINT * 1000,
    updated_at BIGINT NOT NULL DEFAULT EXTRACT(EPOCH FROM NOW())::BIGINT * 1000,

    -- Metadata
    metadata JSONB
);

-- ================================================================
-- POLAR WEBHOOK EVENTS (for idempotency and debugging)
-- ================================================================
CREATE TABLE polar_webhook_events (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),

    polar_event_id VARCHAR(255) NOT NULL UNIQUE,
    event_type VARCHAR(100) NOT NULL,

    status VARCHAR(50) NOT NULL DEFAULT 'pending', -- 'pending', 'processing', 'processed', 'failed', 'ignored'

    event_data JSONB NOT NULL,

    user_id VARCHAR(36) REFERENCES users(id) ON DELETE SET NULL,
    processed_at BIGINT,
    error_message TEXT,

    created_at BIGINT NOT NULL DEFAULT EXTRACT(EPOCH FROM NOW())::BIGINT * 1000
);

-- ================================================================
-- COMMENTS
-- ================================================================
COMMENT ON TABLE polar_payments IS 'All payments via Polar. Links to users via user_id (external_customer_id)';
COMMENT ON TABLE polar_webhook_events IS 'Polar webhook events for idempotency';
