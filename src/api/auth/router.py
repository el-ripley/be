"""
Authentication router with token refresh and logout endpoints.
"""

from fastapi import APIRouter, Request, HTTPException, Depends, status
from typing import Dict, Any
import asyncpg

from src.services.auth_service import AuthService
from src.database.postgres.connection import get_async_connection_pool
from src.database.postgres.repositories.user_queries import (
    revoke_all_refresh_tokens_by_user_id,
)
from src.middleware.auth_middleware import get_current_user_id, get_auth_service
from src.utils.logger import get_logger

logger = get_logger()

router = APIRouter(prefix="/auth", tags=["Authentication"])


async def get_database_connection() -> asyncpg.Connection:
    """Get database connection from pool."""
    pool = await get_async_connection_pool()
    return await pool.acquire()


@router.post("/refresh", response_model=Dict[str, Any])
async def refresh_access_token(
    request: Request,
    auth_service: AuthService = Depends(get_auth_service),
) -> Dict[str, Any]:
    """
    Refresh access token using refresh token from X-Refresh-Token header.

    This endpoint:
    1. Reads refresh token from X-Refresh-Token header
    2. Validates refresh token and checks database
    3. Rotates refresh token (revokes old, creates new)
    4. Issues new access token
    5. Returns both tokens in response

    Returns:
        New access and refresh tokens with expiration info

    Raises:
        401: If refresh token is missing, invalid, expired, or revoked
        500: If database error occurs
    """
    logger.info("🔄 AUTH ROUTER: Token refresh request received")

    # Get refresh token from X-Refresh-Token header
    refresh_token = auth_service.get_refresh_token_from_request(request)

    if not refresh_token:
        logger.warning(
            "🔄 AUTH ROUTER: No refresh token found in X-Refresh-Token header"
        )
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED, detail="Refresh token required"
        )

    # Get database connection
    conn = await get_database_connection()

    try:
        # Refresh access token with rotation
        token_result = await auth_service.refresh_access_token(conn, refresh_token)

        if not token_result:
            logger.warning("🔄 AUTH ROUTER: Refresh token validation failed")
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Invalid or expired refresh token",
            )

        new_access_token, new_refresh_token = token_result

        logger.info("✅ AUTH ROUTER: Token refresh successful")

        return {
            "message": "Tokens refreshed successfully",
            "access_token": new_access_token,
            "refresh_token": new_refresh_token,
            "token_type": "bearer",
        }

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"❌ AUTH ROUTER: Token refresh failed: {str(e)}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Token refresh failed",
        )
    finally:
        # Return connection to pool
        await conn.close()


@router.post("/logout", response_model=Dict[str, str])
async def logout_user(
    request: Request,
    current_user_id: str = Depends(get_current_user_id),
    auth_service: AuthService = Depends(get_auth_service),
) -> Dict[str, str]:
    """
    Logout user by revoking all refresh tokens.

    This endpoint:
    1. Revokes all refresh tokens for the user (logout from all devices)

    Returns:
        Success message

    Raises:
        401: If access token is missing or invalid
        500: If database error occurs
    """
    logger.info(f"🚪 AUTH ROUTER: Logout request for user: {current_user_id}")

    # Get database connection
    conn = await get_database_connection()

    try:
        # Logout user (revoke tokens)
        success = await revoke_all_refresh_tokens_by_user_id(conn, current_user_id)

        if success:
            logger.info(
                f"✅ AUTH ROUTER: User {current_user_id} logged out successfully"
            )
            return {"message": "Logged out successfully"}
        else:
            logger.warning(f"⚠️ AUTH ROUTER: Logout failed for user {current_user_id}")
            return {
                "message": "Logout completed (some tokens may have already been revoked)"
            }

    except Exception as e:
        logger.error(
            f"❌ AUTH ROUTER: Logout failed for user {current_user_id}: {str(e)}"
        )
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail="Logout failed"
        )
    finally:
        # Return connection to pool
        await conn.close()
