# Billing System Documentation

## Tổng quan

Hệ thống billing đơn giản: **Top-up → Credits → Use AI → Deduct credits**

- **Credit Balance Management**: Quản lý số dư credits của users
- **AI Usage Billing**: Tự động trừ credits khi users sử dụng AI services
- **Stripe Integration**: Tích hợp thanh toán qua Stripe (USD, top-up only)
- **Polar Integration**: Tích hợp thanh toán qua Polar (USD, pay-what-you-want, min $10 – max $10,000)
- **SePay Integration**: Tích hợp thanh toán qua chuyển khoản ngân hàng VND (VietQR)
- **Transaction History**: Lưu trữ lịch sử giao dịch credits

## Architecture

### Folder Structure

```
src/
  billing/                          # Isolated billing module
    __init__.py
    credit_service.py               # Credit operations (check, deduct, add)
    stripe/
      __init__.py
      service.py                    # Stripe customer, checkout sessions
      webhook_handler.py            # Handle Stripe webhook events
    sepay/
      __init__.py
      service.py                    # SePay config, topup code management
      webhook_handler.py            # Handle SePay webhook events
    polar/
      __init__.py
      service.py                    # Polar checkout session creation
      webhook_handler.py            # Handle Polar webhook events (order.created, order.paid)
    repositories/
      __init__.py
      billing_queries.py            # user_credit_balance, credit_transactions
      stripe_queries.py             # stripe_* tables
      sepay_queries.py              # sepay_* tables
      polar_queries.py              # polar_* tables
  api/
    billing/                        # Billing API routes
      __init__.py
      router.py
      handler.py
      schemas.py
```

**Design Principle**: Tất cả billing logic được cô lập trong `src/billing/` để dễ maintain và refactor.

## Credit Deduction Strategy

### Core Concept: 1 Agent = 1 Transaction

**Key Design Decision**: Mỗi `agent_response` tạo ra **1 credit_transaction** duy nhất, không phải mỗi `openai_response`.

**Lý do**:
- `agent_response` đã aggregate `total_cost` từ tất cả `openai_response`
- Tránh spam transaction records
- Dễ audit và theo dõi cost per agent run

### Flow Diagram

```
Agent Start → create_agent_response (1 record)
   ↓
Iteration 1 → insert_openai_response → update_agent_response_aggregates
Iteration 2 → insert_openai_response → update_agent_response_aggregates
Iteration 3 → insert_openai_response → update_agent_response_aggregates
   ↓
Agent End → finalize_agent_response (update status)
   ↓
Deduct Credits → deduct_credits_after_agent (1 transaction)
```

### Implementation Points

**1. Repository Layer (Clean)**
- `finalize_agent_response()` chỉ lo database operations
- Không import service layer (clean architecture)

**2. Service Layer (Business Logic)**
- `deduct_credits_after_agent()` được gọi sau `finalize_agent_response()`
- Query `agent_response` data và deduct nếu `total_cost > 0`
- **Note**: Tất cả requests giờ dùng system API key (BYOK đã bị loại bỏ)

**3. Integration Points**
Tất cả agents đều gọi deduct sau finalize:
- `agent_runner.py` (main conversation agent)
- `suggest_response_runner.py` (suggest response agent)
- `summarizer_service.py` (summarization agent)
- `media_description_service.py` (media description agent)

## Credit Service API

### Core Functions

#### `initialize_user_credits(conn, user_id, amount=3.0)`
Khởi tạo credit balance cho user mới với $3 free credits.

**Called from**: `user_service.py` khi tạo user mới

#### `get_balance(conn, user_id) -> Decimal`
Lấy current credit balance.

#### `can_use_ai(conn, user_id) -> bool`
Check xem user có đủ balance để dùng AI (above `min_balance_usd`).

#### `deduct_credits_after_agent(conn, agent_response_id)`
Deduct credits sau khi agent hoàn thành.

**Logic**:
1. Query `agent_response` data
2. Deduct nếu `total_cost > 0` (tất cả requests giờ dùng system API key)
3. Apply `charge_multiplier` từ `billing_settings`
4. Update balance và tạo transaction

#### `add_credits(conn, user_id, amount, source_type, source_id, description)`
Add credits từ top-up.

**Called from**: Stripe webhook handlers, Polar webhook handlers, SePay webhook handlers

#### `admin_adjust_credits(conn, user_id, amount, admin_id, reason) -> transaction_id`
Admin-only credit adjustment. **Không expose qua API** - chỉ gọi trực tiếp từ admin scripts.

**Logic**:
1. Get current balance
2. Calculate new balance (balance_before + amount)
3. Update balance và lifetime stats
4. Create `credit_transaction` với:
   - `transaction_type = "adjustment"`
   - `source_type = "admin_adjustment"`
   - `metadata = {"admin_id": admin_id, "reason": reason}`

**Usage Example**:
```python
from src.billing.credit_service import admin_adjust_credits
from src.database.postgres.connection import async_db_transaction

async with async_db_transaction() as conn:
    transaction_id = await admin_adjust_credits(
        conn=conn,
        user_id="user-123",
        amount=Decimal("10.00"),  # Add $10
        admin_id="admin-456",
        reason="Customer service refund"
    )
```

## Stripe Integration

### Stripe Service

**File**: `src/billing/stripe/service.py`

#### `get_or_create_customer(conn, user_id, email) -> stripe_customer_id`
Get hoặc tạo Stripe customer cho user.

#### `create_topup_checkout(conn, user_id, amount_usd, success_url, cancel_url) -> checkout_url`
Tạo Stripe Checkout session cho top-up.

**Note**: Stripe price IDs được lưu trong database (`stripe_products.stripe_price_id`), không cần environment variables.

### Stripe Webhook Handler

**File**: `src/billing/stripe/webhook_handler.py`

#### Supported Events

**1. `checkout.session.completed`**
- **Payment mode only**: Tạo payment record + add top-up credits
- Verify `mode = 'payment'` (subscription mode không được support)

#### Webhook Security

Webhook endpoint verify Stripe signature:
```python
event = stripe.Webhook.construct_event(body, signature, webhook_secret)
```

**Environment Variable Required**: `STRIPE_WEBHOOK_SECRET`

## SePay Integration

### SePay Service

**File**: `src/billing/sepay/service.py`

#### `get_topup_info(conn, user_id) -> Dict`
Get top-up info for user: config + unique topup_code.

**Returns**:
- `topup_code`: Unique code for user (format: `ER` + 6 alphanumeric)
- `bank_code`, `account_number`, `account_name`: Bank account info
- `transfer_content`: Content user must include in transfer (e.g., "NAPTIEN ER12ABC")
- `exchange_rate_vnd_per_usd`: Exchange rate (default: 27500)
- `min_amount_vnd`, `max_amount_vnd`: Transfer limits

### SePay Webhook Handler

**File**: `src/billing/sepay/webhook_handler.py`

#### Webhook Processing Flow

1. **Idempotency Check**: Verify `sepay_id` chưa được xử lý
2. **Filter Incoming Only**: Chỉ xử lý `transferType == "in"`
3. **Parse Content**: Extract `topup_code` từ transfer content (regex pattern)
4. **Match User**: Tìm user bằng `topup_code` trong `user_topup_codes`
5. **Validate Amount**: Check `amount_vnd >= min_amount_vnd`
6. **Convert Currency**: `amount_usd = amount_vnd / exchange_rate_vnd_per_usd`
7. **Add Credits**: Gọi `add_credits()` với `source_type="sepay_payment"`
8. **Record Transaction**: Insert vào `sepay_transactions` với status

#### Content Matching

Transfer content format: `"NAPTIEN {topup_code}"` (case-insensitive)

Example: `"DAM QUOC DUNG chuyen tien NAPTIEN ER12ABC Ma giao dich..."`
→ Extracts: `"ER12ABC"`

#### Transaction Status

- `processed`: Successfully matched và credits added
- `unmatched`: Không tìm thấy topup_code trong content
- `below_minimum`: Amount < min_amount_vnd
- `error`: Processing error

#### Webhook Security

Webhook endpoint verify API Key authentication:
```python
Authorization: Apikey {SEPAY_WEBHOOK_API_KEY}
```

**Environment Variable Required**: `SEPAY_WEBHOOK_API_KEY`

## Polar Integration

### Polar Service

**File**: `src/billing/polar/service.py`

#### `create_topup_checkout(conn, user_id, amount_usd, success_url, cancel_url) -> checkout_url`
Tạo Polar Checkout session cho top-up.

- Dùng product "pay what you want" (min $10, max $10,000) từ `POLAR_PRODUCT_ID`
- Truyền `external_customer_id=user_id` để webhook có thể map order về user
- `amount_usd` được clamp trong khoảng 10–10000 và chuyển sang cents cho Polar API

**Environment Variables**: `POLAR_ACCESS_TOKEN`, `POLAR_PRODUCT_ID`

### Polar Webhook Handler

**File**: `src/billing/polar/webhook_handler.py`

#### Supported Events

**1. `order.created`**
- Tạo record `polar_payments` với status `pending` (tracking + idempotency)
- Lấy `user_id` từ `customer.external_id` (đã set khi tạo checkout)

**2. `order.paid`**
- Cập nhật `polar_payments` sang status `paid`
- Gọi `add_credits()` với `source_type="polar_payment"`
- Tạo `credit_transaction` type `topup`

#### Webhook Security

Verify signature qua Polar SDK:
```python
from polar_sdk.webhooks import validate_event
event = validate_event(body=body, headers=headers, secret=webhook_secret)
```

**Environment Variable Required**: `POLAR_WEBHOOK_SECRET`

#### Idempotency

- Bảng `polar_webhook_events` lưu `polar_event_id`, `event_type`, `status` (pending/processing/processed/failed/ignored)
- Event đã `processed` sẽ bị bỏ qua khi retry

## API Endpoints

### Base Path: `/billing`

#### `GET /billing/balance`
Get current credit balance cho authenticated user.

**Response**:
```json
{
  "balance_usd": "10.50",
  "lifetime_earned_usd": "50.00",
  "lifetime_spent_usd": "39.50"
}
```

#### `GET /billing/transactions`
Get credit transaction history.

**Query Parameters**:
- `limit` (default: 20)
- `offset` (default: 0)

**Response**:
```json
{
  "transactions": [
    {
      "id": "uuid",
      "transaction_type": "ai_usage",
      "amount_usd": "-0.015",
      "balance_before_usd": "10.50",
      "balance_after_usd": "10.485",
      "source_type": "agent_response",
      "source_id": "uuid",
      "description": "AI usage - gpt-5-mini",
      "created_at": 1234567890
    }
  ],
  "total": 100
}
```

#### `POST /billing/checkout/topup`
Create Stripe Checkout session cho top-up.

**Request**:
```json
{
  "amount_usd": 10.00,
  "success_url": "https://app.example.com/success",
  "cancel_url": "https://app.example.com/cancel"
}
```

**Response**:
```json
{
  "checkout_url": "https://checkout.stripe.com/..."
}
```

#### `POST /billing/polar/checkout`
Create Polar Checkout session cho top-up (pay-what-you-want, min $10, max $10,000).

**Request**:
```json
{
  "amount_usd": 25.00,
  "success_url": "https://app.example.com/billing/success",
  "cancel_url": "https://app.example.com/billing/cancel"
}
```

**Response**:
```json
{
  "checkout_url": "https://checkout.polar.sh/..."
}
```

**Note**: Frontend redirect user tới `checkout_url`; sau khi thanh toán xong Polar redirect về `success_url` hoặc `cancel_url`. Chi tiết cho FE: `docs/BILLING_API_FRONTEND.md`.

#### `POST /billing/polar/webhook`
Polar webhook endpoint (public, verify signature với `POLAR_WEBHOOK_SECRET`). Nhận events `order.created`, `order.paid`. Frontend không gọi endpoint này.

#### `GET /billing/sepay/topup-info`
Get SePay top-up info for authenticated user.

**Response**:
```json
{
  "topup_code": "ER12AB3C",
  "bank_code": "MBBank",
  "account_number": "0932335774",
  "account_name": "ELRIPLEY",
  "transfer_content": "NAPTIEN ER12AB3C",
  "exchange_rate_vnd_per_usd": 27500,
  "min_amount_vnd": 100000,
  "max_amount_vnd": 50000000
}
```

**Note**: Frontend uses this info to generate QR code via `qr.sepay.vn` API.

#### `POST /billing/sepay/webhook`
SePay webhook endpoint (public, no auth required).

**Headers Required**:
- `Authorization`: `Apikey {SEPAY_WEBHOOK_API_KEY}`

**Request Body**: SePay webhook payload (see `tests/example_sepay_webhook_data.json`)

**Response**:
```json
{
  "success": true,
  "status": "processed",
  "message": "Added 3.64 USD credits"
}
```

#### `POST /billing/stripe/webhook`
Stripe webhook endpoint (public, no auth required).

**Headers Required**:
- `stripe-signature`: Stripe webhook signature

## Database Schema

### Core Tables

#### `user_credit_balance`
Single source of truth cho user credit balance.

**Key Fields**:
- `user_id` (FK to users)
- `balance_usd` (current balance, can be negative)
- `lifetime_earned_usd` (analytics only)
- `lifetime_spent_usd` (analytics only)

#### `credit_transactions`
Immutable ledger của tất cả credit changes.

**Key Fields**:
- `user_id` (FK to users)
- `transaction_type`: `ai_usage`, `topup`, `adjustment`
- `amount_usd` (positive = credit, negative = debit)
- `balance_before_usd`, `balance_after_usd`
- `source_type`, `source_id` (generic reference)
- `metadata` (JSONB for extra context)

#### `billing_settings`
Configurable billing settings.

**Default Settings**:
- `charge_multiplier`: 1.5 (charge 1.5x of actual cost)
- `min_balance_usd`: 0
- `max_negative_balance_usd`: -1.0

### Stripe Tables

#### `stripe_products`
Stripe-specific product catalog (topups, subscriptions, etc.).

**Key Fields**:
- `product_code` (e.g., 'topup_custom', future: 'subscription_pro')
- `product_type` ('topup', 'subscription', etc.)
- `amount_usd`, `credits_usd` (NULL for subscriptions)
- `stripe_product_id`, `stripe_price_id`
- `is_active`

**Note**: Currently only contains one product (`topup_custom`) for dynamic amount top-ups. Future products (subscriptions, etc.) can be added here.

#### `stripe_customers`
Maps users to Stripe customers.

#### `stripe_payments`
All payments via Stripe (top-ups only).

**Key Fields**:
- `user_id`, `stripe_customer_id`
- `stripe_product_id` (FK to stripe_products)
- `amount_usd`, `credits_usd`
- `status` (succeeded, failed, etc.)

#### `stripe_webhook_events`
Webhook events for idempotency.

### Polar Tables

#### `polar_payments`
All payments via Polar (top-ups). User được map qua `user_id` (tương ứng `external_customer_id` khi tạo checkout).

**Key Fields**:
- `user_id` (FK to users)
- `polar_order_id` (UNIQUE, từ Polar order id)
- `polar_product_id`, `polar_customer_id`
- `amount_usd`, `credits_usd`, `currency`
- `status`: `pending`, `paid`, `refunded`
- `billing_reason` (purchase, subscription_create, etc.)
- `paid_at`, `created_at`, `updated_at`

#### `polar_webhook_events`
Webhook events cho idempotency và debug.

**Key Fields**:
- `polar_event_id` (UNIQUE)
- `event_type` (order.created, order.paid, …)
- `status`: pending, processing, processed, failed, ignored
- `event_data` (JSONB)

**Reference**: `src/database/postgres/sql/10_schema_polar.sql`

### SePay Tables

#### `sepay_config`
Configuration for SePay integration.

**Key Fields**:
- `config_key`, `config_value`: Key-value config
- Default configs:
  - `bank_code`: Bank code for QR (e.g., "MBBank")
  - `account_number`: Bank account number
  - `account_name`: Account holder name
  - `transfer_content_prefix`: Prefix for matching (default: "NAPTIEN")
  - `exchange_rate_vnd_per_usd`: Exchange rate (default: 27500)
  - `min_amount_vnd`: Minimum transfer (default: 100000)
  - `max_amount_vnd`: Maximum transfer (default: 50000000)

#### `user_topup_codes`
Unique topup code per user for matching bank transfers.

**Key Fields**:
- `user_id` (FK to users, UNIQUE)
- `topup_code` (UNIQUE, format: "ER" + 6 alphanumeric)
- Created once on user registration

#### `sepay_transactions`
All incoming bank transfers detected by SePay webhook.

**Key Fields**:
- `sepay_id` (UNIQUE, from webhook "id" field - for idempotency)
- `user_id` (FK to users, NULL if unmatched)
- `amount_vnd`, `amount_usd` (converted using exchange_rate)
- `content`: Transfer content (for matching)
- `status`: `processed`, `unmatched`, `below_minimum`, `error`
- `event_data`: Full webhook payload (JSONB)

**Reference**: See `src/database/postgres/sql/06_schema_billing_core.sql`, `07_schema_stripe.sql`, `08_schema_sepay.sql`, and `10_schema_polar.sql`

**Note**: `stripe_products` table is in `07_schema_stripe.sql` (Stripe-specific), not in core billing schema. This reflects the architecture decision: Stripe products are Stripe-specific, while SePay uses a different mechanism (topup codes).

## User Registration Flow

Khi user mới đăng ký:

1. `user_service.py` tạo user
2. Gọi `initialize_user_credits(conn, user_id, 3.0)`
3. Tạo `user_credit_balance` với balance = $3.00
4. Tạo `credit_transaction` với type = `adjustment`, description = "Initial free credits"
5. **Generate topup_code**: Gọi `sepay_queries.get_or_create_topup_code()` để tạo unique code cho SePay

## AI Usage Flow

Khi user sử dụng AI:

1. Agent starts → `create_agent_response()`
2. Multiple LLM calls → `insert_openai_response()` → `update_agent_response_aggregates()`
3. Agent completes → `finalize_agent_response()` (update status)
4. **Deduct credits** → `deduct_credits_after_agent()`:
   - Query `agent_response.total_cost`
   - Apply `charge_multiplier` từ `billing_settings`
   - Update `user_credit_balance`
   - Create `credit_transaction` với type = `ai_usage`
   - **Note**: Tất cả requests giờ dùng system API key, không cần check `api_key_type`

## Stripe Payment Flow

### Top-up Flow

1. User calls `POST /billing/checkout/topup`
2. Backend tạo Stripe Checkout session (mode = 'payment')
3. User redirect to Stripe hosted page
4. User completes payment
5. Stripe sends `checkout.session.completed` webhook
6. Backend:
   - Verify `mode = 'payment'`
   - Tạo `stripe_payment` record
   - Add credits via `add_credits()`
   - Tạo `credit_transaction` với type = `topup`

## SePay Payment Flow

### Top-up Flow

1. User calls `GET /billing/sepay/topup-info`
2. Backend returns: `topup_code`, bank info, exchange_rate, limits
3. Frontend generates QR code URL: `https://qr.sepay.vn/img?acc=...&bank=...&amount=...&des=NAPTIEN%20{topup_code}`
4. User scans QR code hoặc chuyển khoản thủ công với nội dung: `"NAPTIEN {topup_code}"`
5. SePay detects transfer và gửi webhook đến backend
6. Backend:
   - Check idempotency (`sepay_id` chưa được xử lý)
   - Parse content → extract `topup_code`
   - Find user by `topup_code`
   - Validate `amount_vnd >= min_amount_vnd`
   - Convert: `amount_usd = amount_vnd / exchange_rate_vnd_per_usd`
   - Add credits via `add_credits(source_type="sepay_payment")`
   - Create `sepay_transactions` record với status = `processed`
   - Create `credit_transaction` với type = `topup`

### Key Points

- **Unique Topup Code**: Mỗi user có 1 `topup_code` duy nhất, tạo khi đăng ký
- **Content Matching**: Backend parse transfer content để tìm `topup_code` (case-insensitive)
- **Flexible Amount**: User có thể nạp bất kỳ số tiền nào (≥ min_amount_vnd)
- **Automatic Processing**: Credits được thêm tự động sau khi webhook được xử lý

## Polar Payment Flow

### Top-up Flow

1. User chọn số tiền (FE validate: 10 ≤ amount_usd ≤ 10000)
2. User gọi `POST /billing/polar/checkout` với `amount_usd`, `success_url`, `cancel_url`
3. Backend gọi Polar API tạo checkout (product pay-what-you-want, `external_customer_id=user_id`)
4. Backend trả về `checkout_url` → FE redirect user tới Polar hosted page
5. User thanh toán trên Polar
6. Polar gửi `order.created` (backend tạo `polar_payments` pending), sau đó `order.paid`
7. Backend xử lý `order.paid`: update payment → `paid`, gọi `add_credits(source_type="polar_payment")`
8. Polar redirect user về `success_url` hoặc `cancel_url`; FE có thể gọi lại `GET /billing/balance` để cập nhật UI

### Key Points

- **No polar_customers table**: User map qua `external_customer_id` (our `user_id`) khi tạo checkout; webhook lấy `customer.external_id` để biết user
- **Idempotency**: `polar_webhook_events` + check `polar_payments.status` trước khi add credits
- **Amount**: Backend clamp amount 10–10000 USD; số tiền thực tế lấy từ webhook order

## Environment Variables

### Required

```bash
# OpenAI API Key (System-wide - all users use this)
OPENAI_API_KEY=sk_...

# Stripe
STRIPE_SECRET_KEY=sk_...
STRIPE_WEBHOOK_SECRET=whsec_...

# Polar
POLAR_ACCESS_TOKEN=...       # Production: token từ Polar dashboard (Settings → Access tokens). Sandbox: token sandbox.
POLAR_WEBHOOK_SECRET=...     # Production: secret từ Polar dashboard (Webhooks). Sandbox: secret của webhook sandbox.
POLAR_PRODUCT_ID=...         # UUID của product "pay what you want" (min $10, max $10000). Production dùng product ID production, sandbox dùng product ID sandbox.
# POLAR_SERVER=sandbox       # Chỉ set khi chạy sandbox. Production: bỏ qua hoặc để trống → dùng API production.

# SePay
SEPAY_WEBHOOK_API_KEY=your_sepay_api_key_here
```

**Note**: 
- BYOK (Bring Your Own Key) đã bị loại bỏ. Tất cả users giờ dùng system API key từ `OPENAI_API_KEY`.
- Stripe price IDs được lưu trong database (`topup_products.stripe_price_id`), không cần environment variables.
- Polar product là "pay what you want" (min $10, max $10,000); product ID lấy từ Polar dashboard, set trong `POLAR_PRODUCT_ID`.
- SePay config (bank account, exchange rate, limits) được lưu trong database (`sepay_config`), không cần environment variables ngoài `SEPAY_WEBHOOK_API_KEY`.

## Architecture Decisions

### 1. Clean Architecture

**Decision**: Repository layer không import service layer.

**Implementation**: 
- `finalize_agent_response()` chỉ lo database
- `deduct_credits_after_agent()` được gọi ở service/agent layer sau finalize

**Benefit**: Clean separation of concerns, no circular dependencies.

### 2. Single Transaction per Agent

**Decision**: 1 `agent_response` = 1 `credit_transaction`.

**Benefit**: 
- Clean transaction history
- Easy to audit cost per agent run
- No spam records

### 3. Isolated Billing Module

**Decision**: Tất cả billing logic trong `src/billing/`.

**Benefit**: 
- Easy to find and maintain
- Can refactor independently
- Clear boundaries

### 4. Top-up Only (Subscription Removed)

**Decision**: Loại bỏ hoàn toàn subscription system, chỉ giữ top-up mechanism.

**Benefit**:
- Đơn giản hóa codebase (~425 lines removed)
- Dễ maintain và test
- User experience vẫn tốt (nạp bao nhiêu dùng bấy nhiêu)

### 5. System API Key Only (BYOK Removed)

**Decision**: Loại bỏ hoàn toàn BYOK (Bring Your Own Key). Tất cả users dùng system API key từ environment.

**Implementation**:
- `ApiKeyResolverService` luôn trả về `settings.openai_api_key`
- `api_key_type` luôn là `"system"`, `user_api_key_id` luôn là `None`
- Database columns `api_key_type` và `user_api_key_id` vẫn tồn tại (deprecated) để backward compatibility

**Benefit**:
- Đơn giản hóa codebase
- Dễ quản lý và monitor costs
- Tất cả usage đều được bill qua credits system

### 6. SePay VND Integration

**Decision**: Tích hợp SePay để hỗ trợ thanh toán VND qua chuyển khoản ngân hàng (VietQR).

**Implementation**:
- Mỗi user có 1 `topup_code` duy nhất (format: `ER` + 6 alphanumeric)
- Frontend tự generate QR code sử dụng SePay public API (`qr.sepay.vn`)
- Backend parse transfer content để match `topup_code` và add credits tự động
- Exchange rate configurable trong database (default: 27,500 VND/USD)

**Benefit**:
- Hỗ trợ thanh toán VND cho users Việt Nam
- Flexible amount (user nạp bất kỳ số tiền nào ≥ min)
- Automatic processing qua webhook
- Simple architecture (không cần tạo request mỗi lần nạp)

### 7. Polar USD Integration

**Decision**: Tích hợp Polar làm cổng thanh toán USD (pay-what-you-want, min $10 – max $10,000).

**Implementation**:
- Product trên Polar: "pay what you want", min 1000 cents, max 1000000 cents
- Checkout tạo qua `polar.checkouts.create()` với `external_customer_id=user_id` để webhook map order về user
- Webhook: `order.created` → tạo `polar_payments` pending; `order.paid` → add credits, `source_type="polar_payment"`
- Không có bảng `polar_customers`; dùng `external_customer_id` thay cho customer mapping

**Benefit**:
- Thêm lựa chọn thanh toán USD ngoài Stripe
- Pay-what-you-want phù hợp top-up linh hoạt
- Kiến trúc thống nhất với Stripe/SePay (service + webhook handler + provider tables)

## Future Enhancements

### Potential Improvements
1. ~~**Admin Credit Adjustment**: Script để admin adjust credits~~ ✅ **Đã implement** - dùng `admin_adjust_credits()`
2. **Credit Expiration**: Credits expire sau X days
3. **Usage Limits**: Limit số lượng requests per day/month
4. **Refund Handling**: Handle Stripe refunds automatically
5. **Multi-currency Support**: Support currencies khác USD
6. **Database Migration**: Drop deprecated columns `api_key_type`, `user_api_key_id` từ `openai_response` và `agent_response`

## Troubleshooting

### Credits không được deduct
- Check `agent_response.total_cost` > 0
- Check `finalize_agent_response()` được gọi
- Check `deduct_credits_after_agent()` được gọi sau finalize
- **Note**: Không cần check `api_key_type` nữa vì tất cả requests dùng system key

### Stripe webhook không work
- Check `STRIPE_WEBHOOK_SECRET` đúng
- Check webhook signature verification
- Check webhook events được register trong Stripe dashboard
- Check `checkout.session.completed` event được add
- Verify checkout session `mode = 'payment'` (không phải 'subscription')

### SePay webhook không work
- Check `SEPAY_WEBHOOK_API_KEY` đúng
- Check Authorization header format: `Apikey {API_KEY}`
- Check webhook URL được configure trong SePay dashboard
- Verify transfer content includes `"NAPTIEN {topup_code}"` (case-insensitive)
- Check `sepay_transactions` table để xem status (`unmatched`, `below_minimum`, etc.)
- Verify `amount_vnd >= min_amount_vnd` từ `sepay_config`

### SePay credits không được add
- Check transfer content có đúng format: `"NAPTIEN {topup_code}"`
- Check `topup_code` exists trong `user_topup_codes` table
- Check `amount_vnd >= min_amount_vnd`
- Check `sepay_transactions.status` = `processed` (not `unmatched` or `below_minimum`)
- Check webhook được gửi và processed (check `sepay_transactions` table)

### Polar webhook không work
- Check `POLAR_WEBHOOK_SECRET` đúng (Polar dashboard → Webhooks)
- Check webhook URL trỏ tới `POST /billing/polar/webhook`
- Check events `order.created` và `order.paid` được chọn khi tạo webhook
- Verify `polar_sdk.webhooks.validate_event()` không ném (signature đúng)

### Polar credits không được add
- Check `order.paid` đã được gửi (sau `order.created`)
- Check `polar_payments` có record với `polar_order_id` tương ứng và `status` chuyển sang `paid`
- Check `customer.external_id` trong webhook payload = `user_id` (đã set khi tạo checkout)
- Check `polar_webhook_events`: event có status `processed` hay `failed`; nếu failed xem `error_message`
- Verify `credit_transactions` có bản ghi với `source_type="polar_payment"`

### Balance không đúng
- Check `credit_transactions` table
- Check `user_credit_balance.balance_usd`
- Verify transactions được tạo đúng

## References

- Database Schema:
  - Core: `src/database/postgres/sql/06_schema_billing_core.sql`
  - Stripe: `src/database/postgres/sql/07_schema_stripe.sql`
  - SePay: `src/database/postgres/sql/08_schema_sepay.sql`
  - Polar: `src/database/postgres/sql/10_schema_polar.sql`
- Credit Service: `src/billing/credit_service.py`
- Stripe Service: `src/billing/stripe/service.py`
- SePay Service: `src/billing/sepay/service.py`
- Polar Service: `src/billing/polar/service.py`
- API Routes: `src/api/billing/router.py`
- Billing API cho Frontend: `docs/BILLING_API_FRONTEND.md`
- Testing Guide: `docs/STRIPE_TESTING_GUIDE.md`
- Frontend Guide: `docs/SEPAY_FRONTEND_GUIDE.md`
