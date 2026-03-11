"""Test script to verify SQL Query Tool RLS security.

This script simulates an agent executing dangerous queries through the SQL Query Tool
to verify that RLS policies and permissions are working correctly.
"""

import asyncio
import sys
from pathlib import Path

from src.agent.tools.base import ToolCallContext
from src.agent.tools.sql_query import SqlQueryTool
from src.database.postgres.connection import (
    get_async_connection,
    shutdown_async_database,
    startup_async_database,
)
from src.utils.logger import get_logger

# Add project root to path
project_root = Path(__file__).parent.parent
sys.path.insert(0, str(project_root))

logger = get_logger()


# Test queries organized by category
# Note: Some queries will use placeholders that need to be replaced with actual IDs
DANGEROUS_QUERIES = [
    # Group 1: DDL Commands - Expect DENIED
    ("DROP TABLE fan_pages", "DDL: DROP TABLE"),
    ("TRUNCATE TABLE posts", "DDL: TRUNCATE"),
    ("ALTER TABLE fan_pages ADD COLUMN hack VARCHAR(255)", "DDL: ALTER TABLE"),
    ("CREATE TABLE malicious (id INT)", "DDL: CREATE TABLE"),
    ("DROP DATABASE test", "DDL: DROP DATABASE"),
    ("CREATE INDEX idx_test ON fan_pages(id)", "DDL: CREATE INDEX"),
    # Group 2: DML Write Commands on Non-Memory Tables - Expect DENIED
    ("DELETE FROM fan_pages", "DML: DELETE from fan_pages"),
    ("DELETE FROM posts WHERE 1=1", "DML: DELETE from posts"),
    ("DELETE FROM comments WHERE id = 'test'", "DML: DELETE from comments"),
    ("DELETE FROM messages WHERE id = 'test'", "DML: DELETE from messages"),
    ("DELETE FROM media_assets WHERE id = 'test'", "DML: DELETE from media_assets"),
    ("UPDATE fan_pages SET name = 'hacked'", "DML: UPDATE fan_pages"),
    (
        "UPDATE posts SET message = 'hacked' WHERE id = 'test'",
        "DML: UPDATE posts",
    ),
    ("UPDATE comments SET message = 'hacked'", "DML: UPDATE comments"),
    (
        "INSERT INTO fan_pages (id, name) VALUES ('x', 'y')",
        "DML: INSERT into fan_pages",
    ),
    (
        "INSERT INTO posts (id, fan_page_id, message) VALUES ('x', 'y', 'z')",
        "DML: INSERT into posts",
    ),
    # Group 3: DELETE on Memory Container Tables - Expect DENIED (only memory_blocks allows DELETE)
    (
        "DELETE FROM page_memory WHERE id = '00000000-0000-0000-0000-000000000000'",
        "MEMORY: DELETE from page_memory",
    ),
    (
        "DELETE FROM page_scope_user_memory WHERE id = '00000000-0000-0000-0000-000000000000'",
        "MEMORY: DELETE from page_scope_user_memory",
    ),
    (
        "DELETE FROM user_memory WHERE id = '00000000-0000-0000-0000-000000000000'",
        "MEMORY: DELETE from user_memory",
    ),
    # Group 4: Sensitive Tables - Expect DENIED
    ("SELECT * FROM users", "SENSITIVE: users table"),
    ("SELECT * FROM facebook_page_admins", "SENSITIVE: access tokens"),
    ("SELECT * FROM refresh_tokens", "SENSITIVE: JWT tokens"),
    ("SELECT id, email FROM users LIMIT 1", "SENSITIVE: users columns"),
    ("SELECT * FROM facebook_app_scope_users", "SENSITIVE: app scope users"),
    # Group 5: SQL Injection Attempts - Expect DENIED or filtered
    ("SELECT * FROM fan_pages; DROP TABLE users;", "INJECTION: multi-statement"),
    (
        "SELECT * FROM fan_pages UNION SELECT * FROM users",
        "INJECTION: UNION with sensitive table",
    ),
    (
        "SELECT * FROM fan_pages WHERE id IN (SELECT user_id FROM users)",
        "INJECTION: subquery to sensitive",
    ),
]

# Valid queries that should succeed (will be populated with real IDs during test)
VALID_QUERIES = [
    # Group 1: Valid SELECT Queries - Expect SUCCESS (with RLS filter)
    ("SELECT COUNT(*) as count FROM fan_pages", "VALID: count pages"),
    ("SELECT id, name FROM fan_pages LIMIT 5", "VALID: list pages"),
    ("SELECT * FROM posts LIMIT 3", "VALID: list posts"),
    (
        "SELECT p.id, p.message, COUNT(c.id) as comment_count FROM posts p LEFT JOIN comments c ON c.post_id = p.id GROUP BY p.id, p.message LIMIT 5",
        "VALID: JOIN query",
    ),
    ("SELECT * FROM suggest_response_agent", "VALID: suggest response agent"),
    ("SELECT * FROM media_assets LIMIT 5", "VALID: media assets"),
    ("SELECT * FROM memory_blocks LIMIT 5", "VALID: memory blocks"),
    ("SELECT * FROM memory_block_media LIMIT 5", "VALID: memory block media"),
    # Group 2: Valid INSERT on Memory Tables - Expect SUCCESS
    # These will be populated with real IDs during test execution
    # Group 3: Valid UPDATE on Memory Tables - Expect SUCCESS
    # These will be populated with real IDs during test execution
    # Group 4: Valid DELETE on memory_blocks - Expect SUCCESS
    # These will be populated with real IDs during test execution
    # Group 5: Valid INSERT/DELETE on memory_block_media - Expect SUCCESS
    # These will be populated with real IDs during test execution
]


async def get_test_user_id() -> str:
    """Get a real user_id from database for testing."""
    async with get_async_connection() as conn:
        # Get first user_id that has pages
        result = await conn.fetchrow(
            """
            SELECT DISTINCT u.id
            FROM users u
            JOIN facebook_app_scope_users fasu ON fasu.user_id = u.id
            JOIN facebook_page_admins fpa ON fpa.facebook_user_id = fasu.id
            LIMIT 1
        """
        )

        if result:
            return result["id"]

        # Fallback: get any user_id
        result = await conn.fetchrow("SELECT id FROM users LIMIT 1")
        if result:
            return result["id"]

        raise ValueError("No users found in database for testing")


async def get_test_fan_page_id(user_id: str) -> str:
    """Get a fan_page_id that the user has access to."""
    async with get_async_connection() as conn:
        result = await conn.fetchrow(
            """
            SELECT DISTINCT fpa.page_id
            FROM facebook_page_admins fpa
            JOIN facebook_app_scope_users fasu ON fpa.facebook_user_id = fasu.id
            WHERE fasu.user_id = $1
            LIMIT 1
        """,
            user_id,
        )
        if result:
            return result["page_id"]
        raise ValueError(f"No accessible pages found for user {user_id}")


async def create_test_memory_data(user_id: str, fan_page_id: str) -> dict:
    """Create test memory data and return IDs for testing."""
    async with get_async_connection() as conn:
        # Check if user_memory already exists and is active
        existing_user_memory_id = await conn.fetchval(
            """
            SELECT id FROM user_memory 
            WHERE owner_user_id = $1 AND is_active = TRUE
            LIMIT 1
        """,
            user_id,
        )

        if existing_user_memory_id:
            # Deactivate existing one
            await conn.execute(
                """
                UPDATE user_memory 
                SET is_active = FALSE 
                WHERE id = $1
            """,
                existing_user_memory_id,
            )

        # Create new user_memory
        user_memory_id = await conn.fetchval(
            """
            INSERT INTO user_memory (owner_user_id, created_by_type, is_active)
            VALUES ($1, 'agent', TRUE)
            RETURNING id
        """,
            user_id,
        )

        # Check if page_memory already exists and is active for this fan_page + prompt_type
        existing_page_memory_id = await conn.fetchval(
            """
            SELECT id FROM page_memory 
            WHERE fan_page_id = $1 AND prompt_type = 'messages' AND is_active = TRUE
            LIMIT 1
        """,
            fan_page_id,
        )

        if existing_page_memory_id:
            # Deactivate existing one
            await conn.execute(
                """
                UPDATE page_memory 
                SET is_active = FALSE 
                WHERE id = $1
            """,
                existing_page_memory_id,
            )

        # Create new page_memory
        page_memory_id = await conn.fetchval(
            """
            INSERT INTO page_memory (fan_page_id, owner_user_id, prompt_type, created_by_type, is_active)
            VALUES ($1, $2, 'messages', 'agent', TRUE)
            RETURNING id
        """,
            fan_page_id,
            user_id,
        )

        # Create memory_blocks
        user_memory_block_id = await conn.fetchval(
            """
            INSERT INTO memory_blocks (prompt_type, prompt_id, block_key, title, content, display_order, created_by_type)
            VALUES ('user_memory', $1, 'test_block', 'Test Block', 'Test content', 1, 'agent')
            RETURNING id
        """,
            user_memory_id,
        )

        page_memory_block_id = await conn.fetchval(
            """
            INSERT INTO memory_blocks (prompt_type, prompt_id, block_key, title, content, display_order, created_by_type)
            VALUES ('page_prompt', $1, 'test_block', 'Test Block', 'Test content', 1, 'agent')
            RETURNING id
        """,
            page_memory_id,
        )

        # Get or create a media_asset for testing
        media_id = await conn.fetchval(
            """
            SELECT id FROM media_assets WHERE user_id = $1 LIMIT 1
        """,
            user_id,
        )

        if not media_id:
            # Create a dummy media_asset if none exists
            # s3_key and s3_url are required fields
            test_s3_key = f"test/{user_id}/test-image.jpg"
            media_id = await conn.fetchval(
                """
                INSERT INTO media_assets (user_id, source_type, media_type, status, s3_key, s3_url, retention_policy)
                VALUES ($1, 'user_upload', 'image', 'ready', $2, 'https://test.com/image.jpg', 'permanent')
                RETURNING id
            """,
                user_id,
                test_s3_key,
            )

        # Create memory_block_media link
        memory_block_media_id = await conn.fetchval(
            """
            INSERT INTO memory_block_media (block_id, media_id, display_order)
            VALUES ($1, $2, 1)
            RETURNING id
        """,
            user_memory_block_id,
            media_id,
        )

        return {
            "user_memory_id": str(user_memory_id),
            "page_memory_id": str(page_memory_id),
            "user_memory_block_id": str(user_memory_block_id),
            "page_memory_block_id": str(page_memory_block_id),
            "media_id": str(media_id),
            "memory_block_media_id": str(memory_block_media_id),
        }


async def cleanup_test_memory_data(user_id: str, test_data: dict):
    """Clean up test memory data using admin connection (bypasses RLS)."""
    async with get_async_connection() as conn:
        # Delete in reverse order of dependencies
        # Use admin connection (not agent connection) to bypass RLS

        # Clean up any memory_block_media links for test blocks
        if test_data.get("user_memory_id"):
            try:
                await conn.execute(
                    """
                    DELETE FROM memory_block_media 
                    WHERE block_id IN (
                        SELECT id FROM memory_blocks 
                        WHERE prompt_id = $1 
                        AND block_key IN ('test_block', 'test_block_for_media')
                    )
                    """,
                    test_data["user_memory_id"],
                )
            except Exception:
                pass

        # Clean up memory blocks
        if test_data.get("user_memory_id"):
            try:
                await conn.execute(
                    """
                    DELETE FROM memory_blocks 
                    WHERE prompt_id = $1 
                    AND block_key IN ('test_block', 'test_block_for_media', 'test_insert')
                    """,
                    test_data["user_memory_id"],
                )
            except Exception:
                pass

        if test_data.get("page_memory_id"):
            try:
                await conn.execute(
                    "DELETE FROM memory_blocks WHERE prompt_id = $1 AND block_key = 'test_block'",
                    test_data["page_memory_id"],
                )
            except Exception:
                pass

        if test_data.get("memory_block_media_id"):
            try:
                await conn.execute(
                    "DELETE FROM memory_block_media WHERE id = $1",
                    test_data["memory_block_media_id"],
                )
            except Exception:
                pass

        if test_data.get("user_memory_block_id"):
            try:
                await conn.execute(
                    "DELETE FROM memory_blocks WHERE id = $1",
                    test_data["user_memory_block_id"],
                )
            except Exception:
                pass

        if test_data.get("page_memory_block_id"):
            try:
                await conn.execute(
                    "DELETE FROM memory_blocks WHERE id = $1",
                    test_data["page_memory_block_id"],
                )
            except Exception:
                pass

        if test_data.get("user_memory_id"):
            try:
                await conn.execute(
                    "DELETE FROM user_memory WHERE id = $1",
                    test_data["user_memory_id"],
                )
            except Exception:
                pass

        if test_data.get("page_memory_id"):
            try:
                await conn.execute(
                    "DELETE FROM page_memory WHERE id = $1",
                    test_data["page_memory_id"],
                )
            except Exception:
                pass


async def test_query(
    tool: SqlQueryTool,
    context: ToolCallContext,
    sql: str,
    description: str,
    mode: str = "read",
) -> dict:
    """Test a single query and return result summary."""
    return await test_query_multiple(tool, context, [sql], description, mode)


async def test_query_multiple(
    tool: SqlQueryTool,
    context: ToolCallContext,
    sqls: list,
    description: str,
    mode: str = "read",
) -> dict:
    """Test multiple queries in a transaction (for write mode) or single query (for read mode)."""
    print(f"\n[{description}]")
    if len(sqls) == 1:
        print(f"  Query: {sqls[0][:80]}{'...' if len(sqls[0]) > 80 else ''}")
    else:
        print(f"  Queries ({len(sqls)} statements):")
        for i, sql in enumerate(sqls, 1):
            print(f"    {i}. {sql[:70]}{'...' if len(sql) > 70 else ''}")

    # Create arguments with mode, sqls array, and description (required by tool)
    arguments = {
        "mode": mode,
        "sqls": sqls,
        "description": f"Test query: {description}",
    }

    # Execute through tool (conn is ignored, tool uses agent connection)
    fake_conn = None  # Will be ignored by tool
    try:
        raw_result = await tool.execute(fake_conn, context, arguments)
        result = tool.process_result(context, raw_result)

        function_output = result.output_message.function_output

        if function_output.get("success"):
            # Handle read mode (has row_count) vs write mode (has results array)
            if "row_count" in function_output:
                row_count = function_output.get("row_count", 0)
                print(f"  Result: ✅ SUCCESS - {row_count} rows returned")
                return {
                    "status": "success",
                    "row_count": row_count,
                    "description": description,
                }
            elif "results" in function_output:
                results = function_output.get("results", [])
                total_affected = sum(r.get("affected", 0) for r in results)
                print(
                    f"  Result: ✅ SUCCESS - {total_affected} rows affected across {len(results)} statements"
                )
                return {
                    "status": "success",
                    "affected_rows": total_affected,
                    "statement_count": len(results),
                    "description": description,
                }
            else:
                print(f"  Result: ✅ SUCCESS - (unknown format)")
                return {
                    "status": "success",
                    "description": description,
                }
        else:
            error = function_output.get("error", "Unknown error")
            error_type = function_output.get("error_type", "Unknown")
            print(f"  Result: ❌ DENIED - {error_type}: {error[:100]}")
            return {
                "status": "denied",
                "error": error,
                "error_type": error_type,
                "description": description,
            }
    except Exception as e:
        print(f"  Result: ❌ EXCEPTION - {type(e).__name__}: {str(e)[:100]}")
        return {
            "status": "exception",
            "error": str(e),
            "error_type": type(e).__name__,
            "description": description,
        }


async def main():
    """Main test execution."""
    print("=" * 60)
    print("SQL Query Tool RLS Security Test")
    print("=" * 60)

    # Initialize database connection
    await startup_async_database()

    test_data = None
    try:
        # Get test user_id and fan_page_id
        print("\nGetting test user_id and fan_page_id from database...")
        user_id = await get_test_user_id()
        fan_page_id = await get_test_fan_page_id(user_id)
        print(f"User ID: {user_id}")
        print(f"Fan Page ID: {fan_page_id}")

        # Create test memory data
        print("\nCreating test memory data...")
        test_data = await create_test_memory_data(user_id, fan_page_id)
        print(f"Test data created: {len(test_data)} items")

        # Create tool and context
        tool = SqlQueryTool()
        context = ToolCallContext(
            user_id=user_id,
            conv_id="test-conv-id",
            branch_id="test-branch-id",
            agent_response_id="test-agent-resp-id",
            call_id="test-call-id",
            tool_name="sql_query",
            arguments={},
        )

        print("\n" + "=" * 60)
        print("Executing Dangerous Queries (should be blocked)")
        print("=" * 60)

        # Execute dangerous queries
        results = []
        for i, (sql, description) in enumerate(DANGEROUS_QUERIES, 1):
            display_desc = f"[{i}/{len(DANGEROUS_QUERIES)}] {description}"
            # Determine mode based on SQL statement
            sql_upper = sql.strip().upper()
            mode = (
                "write"
                if sql_upper.startswith(
                    (
                        "INSERT",
                        "UPDATE",
                        "DELETE",
                        "DROP",
                        "TRUNCATE",
                        "ALTER",
                        "CREATE",
                    )
                )
                else "read"
            )
            result = await test_query(tool, context, sql, display_desc, mode=mode)
            # Store original description for grouping
            result["category"] = description
            results.append(result)

        print("\n" + "=" * 60)
        print("Executing Valid Queries (should succeed)")
        print("=" * 60)

        # Prepare valid queries with real IDs
        valid_queries_with_ids = [
            # Valid SELECT queries
            ("SELECT COUNT(*) as count FROM fan_pages", "VALID: count pages"),
            ("SELECT id, name FROM fan_pages LIMIT 5", "VALID: list pages"),
            ("SELECT * FROM posts LIMIT 3", "VALID: list posts"),
            (
                "SELECT p.id, p.message, COUNT(c.id) as comment_count FROM posts p LEFT JOIN comments c ON c.post_id = p.id GROUP BY p.id, p.message LIMIT 5",
                "VALID: JOIN query",
            ),
            ("SELECT * FROM suggest_response_agent", "VALID: suggest response agent"),
            ("SELECT * FROM media_assets LIMIT 5", "VALID: media assets"),
            ("SELECT * FROM memory_blocks LIMIT 5", "VALID: memory blocks"),
            ("SELECT * FROM memory_block_media LIMIT 5", "VALID: memory block media"),
            # Valid INSERT on memory tables
            # First deactivate existing active ones, then insert new
            (
                f"UPDATE user_memory SET is_active = FALSE WHERE owner_user_id = '{user_id}' AND is_active = TRUE",
                "VALID: Deactivate existing user_memory before INSERT",
            ),
            (
                f"INSERT INTO user_memory (owner_user_id, created_by_type, is_active) VALUES ('{user_id}', 'agent', TRUE) RETURNING id",
                "VALID: INSERT into user_memory",
            ),
            (
                f"UPDATE page_memory SET is_active = FALSE WHERE fan_page_id = '{fan_page_id}' AND prompt_type = 'messages' AND is_active = TRUE",
                "VALID: Deactivate existing page_memory before INSERT",
            ),
            (
                f"INSERT INTO page_memory (fan_page_id, owner_user_id, prompt_type, created_by_type, is_active) VALUES ('{fan_page_id}', '{user_id}', 'messages', 'agent', TRUE) RETURNING id",
                "VALID: INSERT into page_memory",
            ),
            (
                f"INSERT INTO memory_blocks (prompt_type, prompt_id, block_key, title, content, display_order, created_by_type) VALUES ('user_memory', '{test_data['user_memory_id']}', 'test_insert', 'Test Insert', 'Test content', 1, 'agent') RETURNING id",
                "VALID: INSERT into memory_blocks",
            ),
            # Note: Skip INSERT into memory_block_media here to avoid UNIQUE constraint conflict
            # We'll test INSERT separately with a new block
            # Valid UPDATE on memory tables
            (
                f"UPDATE user_memory SET is_active = FALSE WHERE id = '{test_data['user_memory_id']}'",
                "VALID: UPDATE user_memory",
            ),
            (
                f"UPDATE page_memory SET is_active = FALSE WHERE id = '{test_data['page_memory_id']}'",
                "VALID: UPDATE page_memory",
            ),
            (
                f"UPDATE memory_blocks SET title = 'Updated Title' WHERE id = '{test_data['user_memory_block_id']}'",
                "VALID: UPDATE memory_blocks",
            ),
            # Valid DELETE on memory_blocks
            (
                f"DELETE FROM memory_blocks WHERE id = '{test_data['page_memory_block_id']}'",
                "VALID: DELETE from memory_blocks",
            ),
            # Valid INSERT/DELETE on memory_block_media
            # First create a new memory block to avoid UNIQUE constraint
            (
                f"INSERT INTO memory_blocks (prompt_type, prompt_id, block_key, title, content, display_order, created_by_type) VALUES ('user_memory', '{test_data['user_memory_id']}', 'test_block_for_media', 'Test Block for Media', 'Test content', 1, 'agent') RETURNING id",
                "VALID: INSERT memory_block for media test",
            ),
            # Then insert media link (using the new block)
            # Note: We'll use a subquery to get the block_id we just created
            (
                f"INSERT INTO memory_block_media (block_id, media_id, display_order) SELECT id, '{test_data['media_id']}', 4 FROM memory_blocks WHERE block_key = 'test_block_for_media' AND prompt_id = '{test_data['user_memory_id']}' RETURNING id",
                "VALID: INSERT into memory_block_media",
            ),
            # Then delete the media link
            (
                f"DELETE FROM memory_block_media WHERE block_id IN (SELECT id FROM memory_blocks WHERE block_key = 'test_block_for_media' AND prompt_id = '{test_data['user_memory_id']}') AND display_order = 4",
                "VALID: DELETE from memory_block_media",
            ),
        ]

        # Execute valid queries
        for i, (sql, description) in enumerate(valid_queries_with_ids, 1):
            display_desc = f"[{i}/{len(valid_queries_with_ids)}] {description}"
            # Determine mode based on SQL statement
            sql_upper = sql.strip().upper()
            mode = (
                "write"
                if sql_upper.startswith(("INSERT", "UPDATE", "DELETE"))
                else "read"
            )
            result = await test_query(tool, context, sql, display_desc, mode=mode)
            result["category"] = description
            results.append(result)

        # ============================================================
        # EDGE CASES: Multiple Statements & Transaction Tests
        # ============================================================
        print("\n" + "=" * 60)
        print("Executing Edge Case Tests")
        print("=" * 60)

        # Edge Case 1: Multiple statements in transaction (all succeed)
        if test_data.get("user_memory_id"):
            edge_case_1_sqls = [
                f"UPDATE user_memory SET is_active = FALSE WHERE id = '{test_data['user_memory_id']}'",
                f"INSERT INTO memory_blocks (prompt_type, prompt_id, block_key, title, content, display_order, created_by_type) VALUES ('user_memory', '{test_data['user_memory_id']}', 'test_multi_stmt', 'Multi Stmt Test', 'Test content', 1, 'agent') RETURNING id",
                f"UPDATE memory_blocks SET title = 'Updated by Multi Stmt' WHERE block_key = 'test_multi_stmt' AND prompt_id = '{test_data['user_memory_id']}'",
            ]
            result = await test_query_multiple(
                tool,
                context,
                edge_case_1_sqls,
                "EDGE: Multiple statements in transaction (all succeed)",
                mode="write",
            )
            result["category"] = "EDGE: Multiple statements success"
            results.append(result)

        # Edge Case 2: Transaction rollback (second statement fails)
        if test_data.get("user_memory_id"):
            # First, check initial count (may have leftover from previous test)
            initial_count_result = await test_query(
                tool,
                context,
                f"SELECT COUNT(*) as count FROM memory_blocks WHERE block_key = 'test_rollback' AND prompt_id = '{test_data['user_memory_id']}'",
                "EDGE: Initial count before rollback test",
                mode="read",
            )
            initial_count = initial_count_result.get("row_count", 0)
            if initial_count > 0 and "rows" in initial_count_result.get(
                "function_output", {}
            ):
                initial_count = (
                    initial_count_result["function_output"]["rows"][0].get("count", 0)
                    if initial_count_result["function_output"]["rows"]
                    else 0
                )

            # First statement succeeds, second fails due to CHECK constraint violation
            # This should cause entire transaction to rollback
            edge_case_2_sqls = [
                f"INSERT INTO memory_blocks (prompt_type, prompt_id, block_key, title, content, display_order, created_by_type) VALUES ('user_memory', '{test_data['user_memory_id']}', 'test_rollback', 'Rollback Test', 'Test content', 1, 'agent')",
                f"INSERT INTO memory_blocks (prompt_type, prompt_id, block_key, title, content, display_order, created_by_type) VALUES ('user_memory', '{test_data['user_memory_id']}', 'test_rollback_fail', 'Should Fail', 'Test content', 1, 'invalid_type')",  # CHECK constraint violation: created_by_type must be 'user' or 'agent'
            ]
            result = await test_query_multiple(
                tool,
                context,
                edge_case_2_sqls,
                "EDGE: Transaction rollback (CHECK constraint violation)",
                mode="write",
            )
            result["category"] = "EDGE: Transaction rollback"
            results.append(result)
            # Verify first statement was rolled back by checking count is same as initial
            verify_result = await test_query(
                tool,
                context,
                f"SELECT COUNT(*) as count FROM memory_blocks WHERE block_key = 'test_rollback' AND prompt_id = '{test_data['user_memory_id']}'",
                f"EDGE: Verify rollback (should be {initial_count}, same as initial)",
                mode="read",
            )
            verify_result["category"] = "EDGE: Rollback verification"
            # Check if count matches initial (rollback successful)
            if verify_result.get("status") == "success" and "rows" in verify_result.get(
                "function_output", {}
            ):
                final_count = (
                    verify_result["function_output"]["rows"][0].get("count", -1)
                    if verify_result["function_output"]["rows"]
                    else -1
                )
                if final_count == initial_count:
                    verify_result["rollback_verified"] = True
                else:
                    verify_result["rollback_verified"] = False
                    verify_result[
                        "note"
                    ] = f"Count changed from {initial_count} to {final_count} - rollback may not have worked"
            results.append(verify_result)

        # Edge Case 3: Mixed RETURNING and non-RETURNING statements
        if test_data.get("user_memory_id"):
            edge_case_3_sqls = [
                f"UPDATE memory_blocks SET title = 'Mixed Test' WHERE block_key = 'test_block' AND prompt_id = '{test_data['user_memory_id']}'",  # No RETURNING
                f"INSERT INTO memory_blocks (prompt_type, prompt_id, block_key, title, content, display_order, created_by_type) VALUES ('user_memory', '{test_data['user_memory_id']}', 'test_mixed', 'Mixed Test', 'Test content', 1, 'agent') RETURNING id",  # With RETURNING
                f"DELETE FROM memory_blocks WHERE block_key = 'test_mixed' AND prompt_id = '{test_data['user_memory_id']}'",  # No RETURNING
            ]
            result = await test_query_multiple(
                tool,
                context,
                edge_case_3_sqls,
                "EDGE: Mixed RETURNING and non-RETURNING",
                mode="write",
            )
            result["category"] = "EDGE: Mixed RETURNING"
            results.append(result)

        # Edge Case 4: SQL syntax errors
        syntax_error_tests = [
            (
                "SELECT * FROM WHERE id = 'test'",
                "EDGE: Syntax error - missing table name",
                "read",
            ),
            (
                "SELECT nonexistent_column FROM fan_pages",
                "EDGE: Syntax error - missing column",
                "read",
            ),
            (
                "INSERT INTO memory_blocks (invalid_column) VALUES ('test')",
                "EDGE: Syntax error - invalid column in INSERT",
                "write",
            ),
            (
                "UPDATE memory_blocks SET invalid_column = 'test'",
                "EDGE: Syntax error - invalid column in UPDATE",
                "write",
            ),
        ]
        for sql, desc, mode in syntax_error_tests:
            result = await test_query(tool, context, sql, desc, mode=mode)
            result["category"] = "EDGE: Syntax error"
            results.append(result)

        # Edge Case 5: Empty result sets
        empty_result_tests = [
            (
                "SELECT * FROM fan_pages WHERE id = 'nonexistent_page_id'",
                "EDGE: Empty SELECT result",
                "read",
            ),
            (
                "UPDATE memory_blocks SET title = 'No Match' WHERE id = '00000000-0000-0000-0000-000000000000'",
                "EDGE: UPDATE with no matching rows",
                "write",
            ),
            (
                "DELETE FROM memory_blocks WHERE id = '00000000-0000-0000-0000-000000000000'",
                "EDGE: DELETE with no matching rows",
                "write",
            ),
        ]
        for sql, desc, mode in empty_result_tests:
            result = await test_query(tool, context, sql, desc, mode=mode)
            result["category"] = "EDGE: Empty results"
            results.append(result)

        # Summary
        print("\n" + "=" * 60)
        print("SUMMARY")
        print("=" * 60)

        # Group results by category
        ddl_results = [r for r in results if r.get("category", "").startswith("DDL:")]
        dml_results = [r for r in results if r.get("category", "").startswith("DML:")]
        memory_delete_results = [
            r for r in results if r.get("category", "").startswith("MEMORY: DELETE")
        ]
        sensitive_results = [
            r for r in results if r.get("category", "").startswith("SENSITIVE:")
        ]
        valid_results = [
            r for r in results if r.get("category", "").startswith("VALID:")
        ]
        injection_results = [
            r for r in results if r.get("category", "").startswith("INJECTION:")
        ]
        edge_results = [r for r in results if r.get("category", "").startswith("EDGE:")]

        # Count successes and denials
        def count_blocked(results_list):
            return sum(
                1 for r in results_list if r["status"] in ("denied", "exception")
            )

        def count_success(results_list):
            return sum(1 for r in results_list if r["status"] == "success")

        total = len(results)
        ddl_blocked = count_blocked(ddl_results)
        dml_blocked = count_blocked(dml_results)
        memory_delete_blocked = count_blocked(memory_delete_results)
        sensitive_blocked = count_blocked(sensitive_results)
        valid_success = count_success(valid_results)
        injection_blocked = count_blocked(injection_results)
        edge_success = count_success(
            [r for r in edge_results if "error" not in r.get("category", "").lower()]
        )
        edge_total = len(edge_results)

        print(f"\nTotal queries: {total}")
        print("\nDDL Commands (should be blocked):")
        print(
            f"  Blocked: {ddl_blocked}/{len(ddl_results)} ✅"
            if ddl_blocked == len(ddl_results)
            else f"  Blocked: {ddl_blocked}/{len(ddl_results)} ❌"
        )

        print("\nDML Write Commands on Non-Memory Tables (should be blocked):")
        print(
            f"  Blocked: {dml_blocked}/{len(dml_results)} ✅"
            if dml_blocked == len(dml_results)
            else f"  Blocked: {dml_blocked}/{len(dml_results)} ❌"
        )

        print("\nDELETE on Memory Container Tables (should be blocked):")
        print(
            f"  Blocked: {memory_delete_blocked}/{len(memory_delete_results)} ✅"
            if memory_delete_blocked == len(memory_delete_results)
            else f"  Blocked: {memory_delete_blocked}/{len(memory_delete_results)} ❌"
        )

        print("\nSensitive Tables (should be blocked):")
        print(
            f"  Blocked: {sensitive_blocked}/{len(sensitive_results)} ✅"
            if sensitive_blocked == len(sensitive_results)
            else f"  Blocked: {sensitive_blocked}/{len(sensitive_results)} ❌"
        )

        print("\nValid Queries (should succeed):")
        print(
            f"  Success: {valid_success}/{len(valid_results)} ✅"
            if valid_success == len(valid_results)
            else f"  Success: {valid_success}/{len(valid_results)} ❌"
        )

        print("\nSQL Injection Attempts (should be blocked):")
        print(
            f"  Blocked: {injection_blocked}/{len(injection_results)} ✅"
            if injection_blocked == len(injection_results)
            else f"  Blocked: {injection_blocked}/{len(injection_results)} ❌"
        )

        print("\nEdge Cases:")
        print(f"  Tests: {edge_total}")
        # Count expected behaviors:
        # - Success cases: multiple statements, mixed RETURNING, empty results (should succeed)
        # - Error cases: syntax errors, rollback (should fail/deny)
        edge_success_cases = [
            r
            for r in edge_results
            if r.get("category", "").startswith("EDGE:")
            and "error" not in r.get("category", "").lower()
            and "rollback" not in r.get("category", "").lower()
            and r.get("status") == "success"
        ]
        edge_error_cases = [
            r
            for r in edge_results
            if r.get("category", "").startswith("EDGE:")
            and (
                "error" in r.get("category", "").lower()
                or r.get("status") in ("denied", "exception")
            )
        ]
        edge_rollback_cases = [
            r
            for r in edge_results
            if r.get("category", "").startswith("EDGE:")
            and "rollback" in r.get("category", "").lower()
        ]
        # Rollback cases: transaction should fail (denied) and verification should show rollback worked
        edge_rollback_verified = sum(
            1
            for r in edge_rollback_cases
            if r.get("rollback_verified") is True
            or (
                r.get("status") in ("denied", "exception")
                and "Transaction rollback" in r.get("category", "")
            )
        )
        edge_expected = (
            len(edge_success_cases) + len(edge_error_cases) + edge_rollback_verified
        )
        print(
            f"  Expected behavior verified: {edge_expected}/{edge_total} ✅"
            if edge_expected == edge_total
            else f"  Expected behavior verified: {edge_expected}/{edge_total} ⚠️"
        )

        # Overall result
        all_blocked = (
            ddl_blocked == len(ddl_results)
            and dml_blocked == len(dml_results)
            and memory_delete_blocked == len(memory_delete_results)
            and sensitive_blocked == len(sensitive_results)
            and injection_blocked == len(injection_results)
        )
        all_valid_work = valid_success == len(valid_results)

        if all_blocked and all_valid_work:
            print("\n🎉 ALL TESTS PASSED! RLS security is working correctly.")
        else:
            print("\n⚠️  SOME TESTS FAILED. Review results above.")
            # Print failed valid queries
            failed_valid = [r for r in valid_results if r["status"] != "success"]
            if failed_valid:
                print("\nFailed valid queries:")
                for r in failed_valid:
                    print(
                        f"  - {r.get('category', 'Unknown')}: {r.get('error', 'Unknown error')[:100]}"
                    )

    finally:
        # Cleanup test data
        if test_data and "user_id" in locals():
            print("\nCleaning up test memory data...")
            try:
                await cleanup_test_memory_data(user_id, test_data)
                print("Test data cleaned up successfully")
            except Exception as e:
                print(f"Warning: Failed to cleanup test data: {e}")

        # Cleanup database connection
        await shutdown_async_database()


if __name__ == "__main__":
    asyncio.run(main())
