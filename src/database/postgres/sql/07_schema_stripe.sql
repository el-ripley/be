-- ElRipley Database Schema - Stripe Integration
-- Stripe-specific tables for payments and subscriptions
-- Database: PostgreSQL
-- ================================================================
-- STRIPE-SPECIFIC TABLES
-- ================================================================

-- Links users to Stripe customers
CREATE TABLE stripe_customers (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id VARCHAR(36) NOT NULL UNIQUE REFERENCES users(id) ON DELETE CASCADE,
    stripe_customer_id VARCHAR(255) NOT NULL UNIQUE, -- cus_xxx
    email VARCHAR(255),
    name VARCHAR(255),
    default_payment_method_id VARCHAR(255), -- pm_xxx
    metadata JSONB,
    created_at BIGINT NOT NULL DEFAULT EXTRACT(EPOCH FROM NOW())::BIGINT * 1000,
    updated_at BIGINT NOT NULL DEFAULT EXTRACT(EPOCH FROM NOW())::BIGINT * 1000
);


-- ================================================================
-- STRIPE PRODUCTS (Catalog - Stripe-specific)
-- ================================================================
-- Catalog of Stripe products: topups, subscriptions, etc.
CREATE TABLE stripe_products (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    product_code VARCHAR(50) NOT NULL UNIQUE, -- 'topup_custom', future: 'subscription_pro', etc.
    product_name VARCHAR(100) NOT NULL,
    product_type VARCHAR(50) NOT NULL DEFAULT 'topup', -- 'topup', 'subscription', etc.
    amount_usd DECIMAL(10, 2), -- NULL for subscriptions or custom amounts
    credits_usd DECIMAL(10, 4), -- NULL for subscriptions
    stripe_product_id VARCHAR(255) NOT NULL, -- Stripe product ID (prod_xxx)
    stripe_price_id VARCHAR(255), -- Stripe price ID (price_xxx) - can be NULL if using price_data
    is_active BOOLEAN DEFAULT TRUE,
    description TEXT,
    metadata JSONB,
    created_at BIGINT NOT NULL DEFAULT EXTRACT(EPOCH FROM NOW())::BIGINT * 1000,
    updated_at BIGINT NOT NULL DEFAULT EXTRACT(EPOCH FROM NOW())::BIGINT * 1000
);

-- Insert default top-up product (custom amount only)
-- Note: Uses dynamic price_data in checkout session, so amount_usd is 0
INSERT INTO stripe_products (product_code, product_name, product_type, amount_usd, credits_usd, stripe_product_id, stripe_price_id, description) VALUES
('topup_custom', 'Custom Amount', 'topup', 0.00, 0.00, 'prod_TkLxNOl4WQNl15', 'price_1SmrFiCsVvr1GPItayKoyZ3b', 'Custom credit top-up amount (dynamic)');

-- ================================================================
-- STRIPE PAYMENTS (top-ups only)
-- ================================================================
-- Tracks all payments via Stripe
-- Also serves as "user_topup" history - no separate n-n table needed
-- Query topup history: WHERE user_id = ?
CREATE TABLE stripe_payments (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id VARCHAR(36) NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    stripe_customer_id UUID NOT NULL REFERENCES stripe_customers(id) ON DELETE CASCADE,
    
    -- Stripe references
    stripe_payment_intent_id VARCHAR(255), -- pi_xxx
    stripe_charge_id VARCHAR(255), -- ch_xxx
    stripe_invoice_id VARCHAR(255), -- in_xxx
    
    -- ============================================================
    -- Links to Stripe product catalog (what did user pay for)
    -- ============================================================
    -- Reference to stripe_products table
    stripe_product_id UUID REFERENCES stripe_products(id) ON DELETE SET NULL,
    
    -- Amount
    amount_usd DECIMAL(10, 2) NOT NULL,
    credits_usd DECIMAL(10, 4) NOT NULL, -- Credits given to user
    currency VARCHAR(3) NOT NULL DEFAULT 'usd',
    
    -- Payment method info
    payment_method_type VARCHAR(50), -- 'card'
    payment_method_last4 VARCHAR(4),
    payment_method_brand VARCHAR(50), -- 'visa', 'mastercard', etc.
    
    -- Status: 'pending', 'processing', 'succeeded', 'failed', 'refunded', 'canceled'
    status VARCHAR(50) NOT NULL,
    failure_code VARCHAR(100),
    failure_message TEXT,
    
    -- Refund tracking
    refunded_amount_usd DECIMAL(10, 2) DEFAULT 0,
    refunded_at BIGINT,
    
    -- Metadata
    metadata JSONB,
    
    -- Timestamps
    paid_at BIGINT,
    created_at BIGINT NOT NULL DEFAULT EXTRACT(EPOCH FROM NOW())::BIGINT * 1000,
    updated_at BIGINT NOT NULL DEFAULT EXTRACT(EPOCH FROM NOW())::BIGINT * 1000
);

-- ================================================================
-- STRIPE WEBHOOK EVENTS (for idempotency and debugging)
-- ================================================================
CREATE TABLE stripe_webhook_events (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    
    -- Stripe event details
    stripe_event_id VARCHAR(255) NOT NULL UNIQUE, -- evt_xxx
    event_type VARCHAR(100) NOT NULL, -- 'invoice.paid', 'customer.subscription.updated', etc.
    api_version VARCHAR(50),
    
    -- Processing status: 'pending', 'processing', 'processed', 'failed', 'ignored'
    status VARCHAR(50) NOT NULL DEFAULT 'pending',
    
    -- Event data (full payload)
    event_data JSONB NOT NULL,
    
    -- Related records (extracted for quick lookup)
    stripe_customer_id VARCHAR(255),
    stripe_payment_intent_id VARCHAR(255),
    stripe_invoice_id VARCHAR(255),
    user_id VARCHAR(36) REFERENCES users(id) ON DELETE SET NULL,
    
    -- Processing tracking
    processed_at BIGINT,
    error_message TEXT,
    retry_count INTEGER DEFAULT 0,
    last_retry_at BIGINT,
    
    -- Timestamps
    event_created_at BIGINT NOT NULL, -- Stripe event creation time
    received_at BIGINT NOT NULL DEFAULT EXTRACT(EPOCH FROM NOW())::BIGINT * 1000
);

-- ================================================================
-- COMMENTS FOR DOCUMENTATION
-- ================================================================
COMMENT ON TABLE stripe_products IS 'Stripe product catalog - topups, subscriptions, etc.';
COMMENT ON TABLE stripe_customers IS 'Maps users to Stripe customers (cus_xxx)';
COMMENT ON TABLE stripe_payments IS 'All payments via Stripe. Also serves as topup history per user';
COMMENT ON TABLE stripe_webhook_events IS 'Stripe webhook events for idempotency';

COMMENT ON COLUMN stripe_products.product_type IS 'Product type: topup, subscription, etc.';
COMMENT ON COLUMN stripe_payments.stripe_product_id IS 'Link to stripe_products - what Stripe product user purchased';
