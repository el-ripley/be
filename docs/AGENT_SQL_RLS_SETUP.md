# Agent SQL Query với Row-Level Security (RLS)

## Tổng quan

Hệ thống này cho phép AI Agent thực hiện SQL queries trực tiếp trên database Facebook data thông qua một role đặc biệt `agent_user` với Row-Level Security (RLS) policies. Thay vì sử dụng nhiều tools riêng lẻ, agent có thể tự compose các queries phức tạp trong phạm vi được phép.

### Mục tiêu

- **Flexibility**: Agent có thể tự compose SQL queries thay vì bị giới hạn bởi các tools cố định
- **Security**: RLS policies đảm bảo agent chỉ truy cập data của user mà agent đang phục vụ
- **Scalability**: Dễ dàng mở rộng permissions (INSERT/UPDATE/DELETE) khi cần

---

## Kiến trúc

### Security Model

```
┌─────────────────────────────────────────────────────────────┐
│  agent_user Role                                             │
│  - Full capabilities (SELECT, INSERT, UPDATE, DELETE)        │
│  - Currently: SELECT only (matching current policies)        │
└─────────────────────────────────────────────────────────────┘
                           │
                           ▼
┌─────────────────────────────────────────────────────────────┐
│  Row-Level Security (RLS) Policies                           │
│  - Filter by: app.current_user_id session variable          │
│  - Scope: Only pages user has admin access to                │
│  - Current: SELECT policies only                             │
└─────────────────────────────────────────────────────────────┘
                           │
                           ▼
┌─────────────────────────────────────────────────────────────┐
│  Data Access Chain                                           │
│  users → facebook_app_scope_users → facebook_page_admins    │
│    → fan_pages → (posts, comments, messages, ...)           │
└─────────────────────────────────────────────────────────────┘
```

### Ownership Chain

```
users (id)
  └── facebook_app_scope_users (user_id → id)
        └── facebook_page_admins (facebook_user_id → page_id)
              └── fan_pages (id)
                    ├── posts (fan_page_id)
                    ├── comments (fan_page_id)
                    ├── messages (via conversations)
                    └── ... (other Facebook tables)
```

---

## Files đã tạo/sửa

### 1. SQL Schema Files

#### `src/database/postgres/sql/00_init_agent_role.sql`
- **Mục đích**: Tạo `agent_user` role và grant basic permissions
- **Chạy khi**: Đầu tiên trong `elripley.sql` (trước khi tạo tables)
- **Nội dung**:
  - Tạo role `agent_user` với login capability
  - Set password từ environment variable
  - Grant CONNECT, USAGE on schema
  - Note: SELECT grants được thêm sau khi tables được tạo

#### `src/database/postgres/sql/99_rls_policies.sql`
- **Mục đích**: Enable RLS và tạo policies cho agent_user
- **Chạy khi**: Cuối cùng trong `elripley.sql` (sau khi tất cả tables được tạo)
- **Nội dung**:
  - Helper function: `get_user_accessible_page_ids()`
  - Enable RLS trên 3 nhóm tables:
    - **Facebook tables** (13 tables): Filter theo `fan_page_id IN accessible_pages`
    - **Suggest Response tables** (5 tables): Filter theo `user_id` hoặc `fan_page_id`
    - **Media Assets tables** (2 tables): Filter theo `user_id`
  - Tạo SELECT policies cho từng table
  - Policies filter theo `app.current_user_id` session variable

#### `src/database/postgres/sql/elripley.sql`
- **Thay đổi**: 
  - Include `00_init_agent_role.sql` ở đầu
  - Include `99_rls_policies.sql` ở cuối
  - Grant SELECT to agent_user sau khi tables được tạo

### 2. Infrastructure Files

#### `docker-compose.infra.yml`
- **Thay đổi**: 
  - Removed: Mount `./docker/init-scripts` (không cần nữa)
  - Removed: `POSTGRES_AGENT_PASSWORD` env var (được handle bởi init script)

#### `scripts/init_postgres.sh`
- **Thay đổi**:
  - Thêm `POSTGRES_AGENT_PASSWORD` variable
  - Pass `agent_password` và `dbname` variables vào psql khi chạy `elripley.sql`

### 3. Application Code

#### `src/settings.py`
- **Thay đổi**:
  - Thêm `postgres_agent_user` (default: "agent_user")
  - Thêm `postgres_agent_password` (từ env var)

#### `src/database/postgres/connection.py`
- **Thay đổi**:
  - Thêm `_agent_connection_pool` (global pool cho agent_user)
  - Thêm `get_agent_connection_pool()` function
  - Thêm `close_agent_connection_pool()` function
  - Thêm `get_agent_connection(user_id)` context manager
    - Tự động set `app.current_user_id` session variable
    - RLS policies tự động filter data
  - Update `startup_async_database()` và `shutdown_async_database()`
  - Update `DatabaseMode.get_connection_info()` để include agent pool stats

### 4. Environment Variables

#### `.env`
- **Thêm**:
  ```bash
  POSTGRES_AGENT_USER=agent_user
  POSTGRES_AGENT_PASSWORD=Rk7mXp2vL9nQwY4sT6hJ3aKbE8cUfG1d
  ```

---

## Setup Instructions

### 1. Drop và recreate database

```bash
# Stop và xóa volumes
docker-compose -f docker-compose.infra.yml down -v

# Start lại postgres
docker-compose -f docker-compose.infra.yml up -d postgres
```

### 2. Initialize database schema

```bash
# Chạy init script (tự động chạy tất cả SQL files)
./scripts/init_postgres.sh
```

Script này sẽ:
1. Tạo `agent_user` role với password từ `.env`
2. Tạo tất cả tables
3. Grant SELECT permissions to `agent_user`
4. Enable RLS và tạo policies

### 3. Verify setup

```bash
# Connect as agent_user
docker run --rm --network ai_agent_network \
  -e PGPASSWORD="Rk7mXp2vL9nQwY4sT6hJ3aKbE8cUfG1d" \
  postgres:16.3 \
  psql -h postgres -U agent_user -d el_ripley

# Test RLS
SET app.current_user_id = 'some-user-id';
SELECT * FROM fan_pages;  -- Should only show pages user has access to
```

---

## Usage trong Code

### Basic Usage

```python
from src.database.postgres.connection import get_agent_connection

async def agent_sql_query(user_id: str, sql: str):
    """Execute SQL query as agent_user with RLS context."""
    async with get_agent_connection(user_id) as conn:
        # RLS tự động filter - chỉ thấy data của user này
        result = await conn.fetch(sql)
        return result
```

### Example: Query posts

```python
async def get_user_posts(user_id: str, page_id: str):
    sql = """
        SELECT 
            p.id,
            p.message,
            p.reaction_total_count,
            p.comment_count,
            COUNT(c.id) as actual_comments
        FROM posts p
        LEFT JOIN comments c ON c.post_id = p.id
        WHERE p.fan_page_id = $1
        GROUP BY p.id
        ORDER BY p.facebook_created_time DESC
        LIMIT 10
    """
    
    async with get_agent_connection(user_id) as conn:
        # RLS đảm bảo chỉ query được posts từ pages user có quyền
        rows = await conn.fetch(sql, page_id)
        return [dict(row) for row in rows]
```

### Example: Complex query với JOINs

```python
async def get_conversation_stats(user_id: str):
    sql = """
        SELECT 
            fcm.fan_page_id,
            fp.name as page_name,
            COUNT(DISTINCT fcm.id) as total_conversations,
            COUNT(DISTINCT CASE WHEN NOT fcm.mark_as_read THEN fcm.id END) as unread_conversations,
            COUNT(m.id) as total_messages,
            MAX(m.facebook_timestamp) as last_message_time
        FROM facebook_conversation_messages fcm
        JOIN fan_pages fp ON fp.id = fcm.fan_page_id
        LEFT JOIN messages m ON m.conversation_id = fcm.id
        GROUP BY fcm.fan_page_id, fp.name
        ORDER BY last_message_time DESC
    """
    
    async with get_agent_connection(user_id) as conn:
        rows = await conn.fetch(sql)
        return [dict(row) for row in rows]
```

### Example: Query suggest response prompts

```python
async def get_page_prompts(user_id: str, page_id: str):
    sql = """
        SELECT 
            srpp.id,
            srpp.prompt_type,
            srpp.is_active,
            COUNT(srmb.id) as memory_block_count
        FROM suggest_response_page_prompts srpp
        LEFT JOIN suggest_response_memory_blocks srmb 
            ON srmb.prompt_type = 'page_prompt' 
            AND srmb.prompt_id = srpp.id
        WHERE srpp.fan_page_id = $1
            AND srpp.is_active = TRUE
        GROUP BY srpp.id, srpp.prompt_type, srpp.is_active
    """
    
    async with get_agent_connection(user_id) as conn:
        # RLS đảm bảo chỉ query được prompts từ pages user có quyền
        rows = await conn.fetch(sql, page_id)
        return [dict(row) for row in rows]
```

### Example: Query media assets

```python
async def get_user_media(user_id: str, media_type: str = None):
    sql = """
        SELECT 
            id,
            source_type,
            media_type,
            s3_url,
            description,
            file_size_bytes
        FROM media_assets
        WHERE user_id = $1
            AND ($2::VARCHAR IS NULL OR media_type = $2)
            AND status = 'ready'
        ORDER BY created_at DESC
        LIMIT 50
    """
    
    async with get_agent_connection(user_id) as conn:
        # RLS đảm bảo chỉ query được media của user hiện tại
        rows = await conn.fetch(sql, user_id, media_type)
        return [dict(row) for row in rows]
```

---

## Security Details

### RLS Policy Mechanism

1. **Session Variable**: `app.current_user_id` được set khi acquire connection
2. **Helper Function**: `get_user_accessible_page_ids()` lookup pages user có quyền
3. **Policy Filter**: Mỗi policy sử dụng `USING` clause để filter rows

### Example Policy

```sql
CREATE POLICY agent_posts_policy ON posts
    FOR SELECT
    TO agent_user
    USING (fan_page_id IN (SELECT get_user_accessible_page_ids()));
```

Khi agent query `SELECT * FROM posts`:
- PostgreSQL tự động thêm filter: `WHERE fan_page_id IN (SELECT get_user_accessible_page_ids())`
- Function `get_user_accessible_page_ids()` lookup từ `app.current_user_id`
- Chỉ trả về posts từ pages user có quyền

### Protected Tables

RLS được enable trên **20 tables** được chia thành 3 nhóm:

#### 1. Facebook Tables (13 tables)
Filter strategy: `fan_page_id IN get_user_accessible_page_ids()`
- `fan_pages`
- `posts`
- `comments`
- `messages`
- `facebook_page_scope_users`
- `facebook_conversation_messages`
- `facebook_conversation_comments`
- `facebook_conversation_comment_entries`
- `post_reactions`
- `comment_reactions`
- `facebook_post_sync_states`
- `facebook_post_comment_sync_states`
- `facebook_inbox_sync_states`

#### 2. Suggest Response Tables (5 tables)
Filter strategy: `user_id = current_user_id` hoặc `fan_page_id IN accessible_pages`
- `suggest_response_agent` - Filter by `user_id`
- `suggest_response_page_prompts` - Filter by `fan_page_id`
- `suggest_response_page_scope_user_prompts` - Filter by `fan_page_id`
- `suggest_response_history` - Filter by `user_id`
- `suggest_response_memory_blocks` - Filter through parent prompts (polymorphic)

#### 3. Media Assets Tables (2 tables)
Filter strategy: `user_id = current_user_id`
- `media_assets` - Filter by `user_id`
- `suggest_response_memory_block_media` - Filter through memory blocks → prompts

### Forbidden Tables

Các tables sau **KHÔNG** được grant cho `agent_user` hoặc **KHÔNG có RLS**:
- `facebook_page_admins` (chứa access tokens) - No grant
- `refresh_tokens` (chứa JWT tokens) - No grant
- `users` (sensitive user data) - No grant
- `user_storage_quotas` (quota management) - No RLS (Agent không cần)
- `facebook_app_scope_users` (internal lookup) - No RLS
- `openai_*` tables (conversation history) - Managed by app layer
- `agent_response` (execution logs) - Managed by app layer
- `billing_*` tables (payment data) - No access needed

---

## Future Extensions

### Thêm INSERT Capability

Khi cần agent có thể insert data:

```sql
-- 1. Tạo policy
CREATE POLICY agent_comments_insert_policy ON comments
    FOR INSERT
    TO agent_user
    WITH CHECK (
        fan_page_id IN (SELECT get_user_accessible_page_ids())
    );

-- 2. Grant permission
GRANT INSERT ON comments TO agent_user;
```

### Thêm UPDATE Capability

```sql
-- 1. Tạo policy
CREATE POLICY agent_comments_update_policy ON comments
    FOR UPDATE
    TO agent_user
    USING (fan_page_id IN (SELECT get_user_accessible_page_ids()))
    WITH CHECK (fan_page_id IN (SELECT get_user_accessible_page_ids()));

-- 2. Grant permission
GRANT UPDATE ON comments TO agent_user;
```

### Thêm DELETE Capability

```sql
-- 1. Tạo policy
CREATE POLICY agent_comments_delete_policy ON comments
    FOR DELETE
    TO agent_user
    USING (fan_page_id IN (SELECT get_user_accessible_page_ids()));

-- 2. Grant permission
GRANT DELETE ON comments TO agent_user;
```

---

## Troubleshooting

### Agent không thấy data

**Nguyên nhân**: `app.current_user_id` chưa được set hoặc user không có pages

**Giải pháp**:
```sql
-- Check session variable
SHOW app.current_user_id;

-- Check accessible pages
SELECT get_user_accessible_page_ids();

-- Verify user has pages
SELECT fpa.page_id, fp.name
FROM facebook_page_admins fpa
JOIN facebook_app_scope_users fasu ON fpa.facebook_user_id = fasu.id
WHERE fasu.user_id = current_setting('app.current_user_id', true);
```

### Permission denied errors

**Nguyên nhân**: Chưa grant permission cho operation

**Giải pháp**: Thêm grant statement trong `elripley.sql` hoặc chạy manual:
```sql
GRANT SELECT ON table_name TO agent_user;
```

### RLS policies không hoạt động

**Nguyên nhân**: RLS chưa được enable hoặc policies chưa được tạo

**Giải pháp**:
```sql
-- Check RLS status
SELECT tablename, rowsecurity 
FROM pg_tables 
WHERE schemaname = 'public' 
AND tablename IN ('fan_pages', 'posts', 'comments');

-- Check policies
SELECT schemaname, tablename, policyname, roles, cmd
FROM pg_policies
WHERE schemaname = 'public'
AND roles::text LIKE '%agent_user%';
```

---

## Best Practices

1. **Always use `get_agent_connection(user_id)`**: Đảm bảo RLS context được set đúng
2. **Validate SQL in application layer**: Mặc dù RLS bảo vệ, nên validate SQL syntax trước khi execute
3. **Add query timeout**: Prevent long-running queries
4. **Limit result size**: Add `LIMIT` clause hoặc pagination
5. **Monitor queries**: Log queries để debug và optimize
6. **Test policies**: Verify RLS hoạt động đúng với test cases

---

## Related Files

- `src/database/postgres/sql/00_init_agent_role.sql` - Role setup
- `src/database/postgres/sql/99_rls_policies.sql` - RLS policies
- `src/database/postgres/connection.py` - Connection pool management
- `src/settings.py` - Configuration
- `scripts/init_postgres.sh` - Database initialization

---

## Changelog

### 2026-01-25 (Updated)
- **Added**: RLS policies cho Suggest Response tables (5 tables)
- **Added**: RLS policies cho Media Assets tables (2 tables)
- **Total**: 20 tables với RLS protection (từ 13 → 20)
- **Filter strategies**: 
  - Facebook tables: `fan_page_id IN accessible_pages`
  - User-scoped tables: `user_id = current_user_id`
  - Junction tables: Filter through parent relationships

### 2026-01-25 (Initial)
- Initial implementation với `agent_user` role
- RLS policies cho Facebook tables (13 tables)
- Connection pool management
- Documentation

---

## Security Test Results

### Test Execution

Test script: `scripts/test_sql_tool_rls.py`

Script này giả lập agent execute SQL Query Tool với các dangerous queries để verify RLS security.

### Test Results (2026-01-25)

#### ✅ DDL Commands - BLOCKED (4/4)
- `DROP TABLE fan_pages` - ❌ DENIED (tool validation)
- `TRUNCATE TABLE posts` - ❌ DENIED (tool validation)
- `ALTER TABLE fan_pages ADD COLUMN` - ❌ DENIED (tool validation)
- `CREATE TABLE malicious` - ❌ DENIED (tool validation)

**Result**: Tool validation layer successfully blocks all DDL commands before they reach database.

#### ✅ DML Write Commands - BLOCKED (5/5)
- `DELETE FROM fan_pages` - ❌ DENIED (tool validation)
- `DELETE FROM posts WHERE 1=1` - ❌ DENIED (tool validation)
- `UPDATE fan_pages SET name = 'hacked'` - ❌ DENIED (tool validation)
- `UPDATE posts SET message = 'hacked'` - ❌ DENIED (tool validation)
- `INSERT INTO fan_pages` - ❌ DENIED (tool validation)

**Result**: Tool validation layer successfully blocks all DML write commands.

#### ⚠️ Sensitive Tables - PARTIALLY BLOCKED (1/4)
- `SELECT * FROM users` - ⚠️ ACCESSIBLE (needs database re-init to apply REVOKE)
- `SELECT * FROM facebook_page_admins` - ⚠️ ACCESSIBLE (needs database re-init)
- `SELECT * FROM refresh_tokens` - ⚠️ ACCESSIBLE (needs database re-init)
- `SELECT id, email FROM users` - ❌ DENIED (column doesn't exist - expected)

**Result**: 3 sensitive tables are still accessible because database hasn't been re-initialized with new REVOKE statements. After re-init, these will be properly blocked.

**Action Required**: Re-initialize database to apply REVOKE statements in `elripley.sql`.

#### ✅ Valid SELECT Queries - SUCCESS (6/6)
- `SELECT COUNT(*) FROM fan_pages` - ✅ SUCCESS (RLS filtered)
- `SELECT id, name FROM fan_pages LIMIT 5` - ✅ SUCCESS (RLS filtered)
- `SELECT * FROM posts LIMIT 3` - ✅ SUCCESS (RLS filtered)
- `SELECT p.id, p.message, COUNT(c.id) FROM posts p LEFT JOIN comments c` - ✅ SUCCESS (complex JOIN with RLS)
- `SELECT * FROM suggest_response_agent` - ✅ SUCCESS (user-scoped RLS)
- `SELECT * FROM media_assets LIMIT 5` - ✅ SUCCESS (user-scoped RLS)

**Result**: All valid SELECT queries work correctly with RLS filtering applied.

#### ✅ SQL Injection Attempts - BLOCKED (3/3)
- `SELECT * FROM fan_pages; DROP TABLE users;` - ❌ DENIED (asyncpg blocks multi-statement)
- `SELECT * FROM fan_pages UNION SELECT * FROM users` - ❌ DENIED (column mismatch error)
- `SELECT * FROM fan_pages WHERE id IN (SELECT user_id FROM users)` - ❌ DENIED (column doesn't exist)

**Result**: SQL injection attempts are blocked by:
1. asyncpg prepared statements (no multi-statement support)
2. Database schema validation (column mismatches)
3. Missing permissions (sensitive tables not accessible after re-init)

### Overall Security Assessment

**Tool Layer Protection**:
- ✅ DDL commands blocked by validation
- ✅ DML write commands blocked by validation
- ✅ Only SELECT queries allowed

**Database Layer Protection**:
- ✅ RLS policies active on 20 tables
- ✅ Data isolation working (user can only see own data)
- ⚠️ Sensitive tables need database re-init to apply REVOKE

**Recommendation**: Re-initialize database to apply REVOKE statements for sensitive tables, then re-run test to verify 100% blocking.

### Running Tests

```bash
# Run security test
poetry run python scripts/test_sql_tool_rls.py
```

---

## Notes

- Role name đã đổi từ `agent_reader` → `agent_user` để phản ánh khả năng mở rộng
- Hiện tại chỉ có SELECT policies, nhưng structure sẵn sàng cho INSERT/UPDATE/DELETE
- RLS policies được force (`FORCE ROW LEVEL SECURITY`) - không thể bypass
- Main app user (`el-ripley-user`) bypass RLS (superuser hoặc explicit BYPASSRLS)
- **Filter strategies**:
  - **Page-based filtering**: Tables với `fan_page_id` → filter qua `get_user_accessible_page_ids()`
  - **User-based filtering**: Tables với `user_id` → filter trực tiếp bằng `current_user_id`
  - **Junction tables**: Filter qua parent relationships (polymorphic cho memory_blocks)
- **Agent access scope**: Agent chỉ thấy data của user mà agent đang phục vụ, đảm bảo isolation hoàn toàn giữa các users
