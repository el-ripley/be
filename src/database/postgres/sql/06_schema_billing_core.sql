-- ElRipley Database Schema - Core Billing
-- Provider-agnostic billing: credits, plans, products, transactions
-- Database: PostgreSQL
-- ================================================================
-- CORE BILLING TABLES (100% provider-agnostic)
-- ================================================================

-- Configuration for billing (charge multiplier, limits, etc.)
CREATE TABLE billing_settings (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    setting_key VARCHAR(100) NOT NULL UNIQUE,
    setting_value DECIMAL(10, 4) NOT NULL,
    description TEXT,
    created_at BIGINT NOT NULL DEFAULT EXTRACT(EPOCH FROM NOW())::BIGINT * 1000,
    updated_at BIGINT NOT NULL DEFAULT EXTRACT(EPOCH FROM NOW())::BIGINT * 1000
);

-- Insert default settings
INSERT INTO billing_settings (setting_key, setting_value, description) VALUES
('charge_multiplier', 1.5, 'Multiplier applied to AI costs (1.5 = charge 1.5x of actual cost)'),
('min_balance_usd', 0, 'Minimum balance required to use AI services (0 = allow negative)'),
('max_negative_balance_usd', -1.0, 'Maximum allowed negative balance before blocking');

-- ================================================================
-- USER CREDIT BALANCE (Simple - just track total balance)
-- ================================================================
CREATE TABLE user_credit_balance (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id VARCHAR(36) NOT NULL UNIQUE REFERENCES users(id) ON DELETE CASCADE,
    
    -- Balance (USD) - single source of truth
    balance_usd DECIMAL(10, 4) NOT NULL DEFAULT 0,
    
    -- Lifetime stats (for analytics only, not for balance calc)
    lifetime_earned_usd DECIMAL(10, 4) DEFAULT 0,
    lifetime_spent_usd DECIMAL(10, 4) DEFAULT 0,
    
    -- Last activity
    last_credited_at BIGINT,
    last_spent_at BIGINT,
    
    -- Timestamps
    created_at BIGINT NOT NULL DEFAULT EXTRACT(EPOCH FROM NOW())::BIGINT * 1000,
    updated_at BIGINT NOT NULL DEFAULT EXTRACT(EPOCH FROM NOW())::BIGINT * 1000
);

-- ================================================================
-- CREDIT TRANSACTIONS (History - provider-agnostic)
-- ================================================================
-- This is the immutable ledger of all credit changes
-- Provider-specific details are linked via source_type + source_id
CREATE TABLE credit_transactions (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id VARCHAR(36) NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    
    -- Transaction type
    transaction_type VARCHAR(50) NOT NULL, -- 'welcome_bonus', 'topup', 'ai_usage', 'adjustment'
    
    -- Amount (positive = credit, negative = debit)
    amount_usd DECIMAL(10, 4) NOT NULL,
    balance_before_usd DECIMAL(10, 4) NOT NULL,
    balance_after_usd DECIMAL(10, 4) NOT NULL,
    
    -- Generic source reference (provider-agnostic)
    -- Examples: 
    --   source_type='stripe_payment', source_id=<stripe_payments.id>
    --   source_type='payos_payment', source_id=<payos_payments.id> (future)
    --   source_type='agent_response', source_id=<agent_response.id>
    --   source_type='manual_adjustment', source_id=NULL
    source_type VARCHAR(50),
    source_id UUID,
    
    -- Description & metadata
    description TEXT,
    metadata JSONB, -- { raw_cost, multiplier, model, etc. }
    
    -- Timestamp
    created_at BIGINT NOT NULL DEFAULT EXTRACT(EPOCH FROM NOW())::BIGINT * 1000
);

-- ================================================================
-- COMMENTS FOR DOCUMENTATION
-- ================================================================
COMMENT ON TABLE billing_settings IS 'Configurable billing settings (multiplier, limits)';
COMMENT ON TABLE user_credit_balance IS 'User credit balance - single source of truth';
COMMENT ON TABLE credit_transactions IS 'Immutable ledger of all credit changes';

COMMENT ON COLUMN user_credit_balance.balance_usd IS 'Current available credits (can be negative)';
COMMENT ON COLUMN credit_transactions.amount_usd IS 'Positive = credit added, Negative = credit spent';
COMMENT ON COLUMN credit_transactions.source_type IS 'Type of source: stripe_payment, payos_payment, agent_response, manual_adjustment';
COMMENT ON COLUMN credit_transactions.source_id IS 'UUID reference to the source record (nullable for manual adjustments)';
