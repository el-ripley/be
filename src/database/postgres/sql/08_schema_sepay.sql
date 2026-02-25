-- ElRipley Database Schema - SePay Integration
-- Simple VND bank transfer payments via VietQR
-- Database: PostgreSQL
-- ================================================================

-- ================================================================
-- SEPAY CONFIGURATION
-- ================================================================
-- Bank account info and settings for VND payments
CREATE TABLE sepay_config (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    config_key VARCHAR(100) NOT NULL UNIQUE,
    config_value TEXT NOT NULL,
    description TEXT,
    created_at BIGINT NOT NULL DEFAULT EXTRACT(EPOCH FROM NOW())::BIGINT * 1000,
    updated_at BIGINT NOT NULL DEFAULT EXTRACT(EPOCH FROM NOW())::BIGINT * 1000
);

-- Default config (UPDATE with your actual bank details)
INSERT INTO sepay_config (config_key, config_value, description) VALUES
('bank_code', 'MBBank', 'Bank code for QR URL (e.g., MBBank, Vietcombank, ACB)'),
('account_number', '0932335774', 'Bank account number'),
('account_name', 'DAM QUOC DUNG', 'Account holder name for display'),
('transfer_content_prefix', 'NAPTIEN', 'Prefix for transfer content matching'),
('exchange_rate_vnd_per_usd', '27500', 'VND per 1 USD (e.g., 27500 = 27,500 VND/USD)'),
('min_amount_vnd', '100000', 'Minimum top-up amount in VND (100,000 = 100k)'),
('max_amount_vnd', '50000000', 'Maximum top-up amount in VND (50,000,000 = 50M)');

-- ================================================================
-- USER TOPUP CODES
-- ================================================================
-- Each user has a unique topup code for matching bank transfers
-- Example: User has code "ER12ABC" → transfer content: "NAPTIEN ER12ABC"
CREATE TABLE user_topup_codes (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id VARCHAR(36) NOT NULL UNIQUE REFERENCES users(id) ON DELETE CASCADE,
    topup_code VARCHAR(20) NOT NULL UNIQUE, -- "ER12ABC" - unique code for this user
    created_at BIGINT NOT NULL DEFAULT EXTRACT(EPOCH FROM NOW())::BIGINT * 1000
);


-- ================================================================
-- SEPAY TRANSACTIONS (Webhook events / Payment history)
-- ================================================================
-- Records all incoming bank transfers detected by SePay
CREATE TABLE sepay_transactions (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    
    -- SePay transaction ID (from webhook "id" field) - for idempotency
    sepay_id BIGINT NOT NULL UNIQUE,
    
    -- Matched user (NULL if unmatched)
    user_id VARCHAR(36) REFERENCES users(id) ON DELETE SET NULL,
    
    -- Transaction details from webhook
    gateway VARCHAR(50) NOT NULL, -- "MBBank", "Vietcombank", etc.
    account_number VARCHAR(50) NOT NULL, -- receiving account
    amount_vnd BIGINT NOT NULL, -- transfer amount in VND
    amount_usd DECIMAL(10, 4), -- converted to USD (NULL if unmatched)
    transfer_type VARCHAR(10) NOT NULL, -- "in" or "out"
    content TEXT, -- transfer content (for matching)
    reference_code VARCHAR(100), -- bank reference code
    transaction_date VARCHAR(50), -- original format from SePay
    
    -- Matching status: 'processed', 'unmatched', 'below_minimum', 'error'
    status VARCHAR(50) NOT NULL DEFAULT 'unmatched',
    
    -- Full webhook payload for debugging
    event_data JSONB NOT NULL,
    
    -- Processing notes
    notes TEXT,
    
    -- Timestamps
    processed_at BIGINT,
    created_at BIGINT NOT NULL DEFAULT EXTRACT(EPOCH FROM NOW())::BIGINT * 1000
);


-- ================================================================
-- COMMENTS
-- ================================================================
COMMENT ON TABLE sepay_config IS 'SePay configuration: bank account, exchange rate, limits';
COMMENT ON TABLE user_topup_codes IS 'Unique topup code per user for matching bank transfers';
COMMENT ON TABLE sepay_transactions IS 'All incoming bank transfers detected by SePay webhook';

COMMENT ON COLUMN user_topup_codes.topup_code IS 'Unique code included in transfer content (e.g., ER12ABC)';
COMMENT ON COLUMN sepay_transactions.sepay_id IS 'Transaction ID from SePay webhook - used for idempotency';
COMMENT ON COLUMN sepay_transactions.amount_usd IS 'Converted USD amount using exchange_rate_vnd_per_usd config';
