import asyncpg
import jwt
from fastapi import Depends, HTTPException, Request, Response, status

from src.database.postgres.connection import get_async_connection_pool
from src.services.auth_service import AuthService
from src.utils.logger import get_logger

logger = get_logger()


def get_auth_service(request: Request) -> AuthService:
    return request.app.state.auth_service


async def get_database_connection() -> asyncpg.Connection:
    """Get database connection from pool."""
    pool = await get_async_connection_pool()
    return await pool.acquire()


async def _attempt_automatic_refresh(
    request: Request,
    response: Response,
    auth_service: AuthService,
) -> str:
    """
    Helper function to attempt automatic token refresh.

    Returns:
        str: Authenticated user ID after successful refresh

    Raises:
        HTTPException: 401 if refresh fails
    """
    refresh_token = auth_service.get_refresh_token_from_request(request)
    if not refresh_token:
        logger.warning(
            "🔒 AUTH MIDDLEWARE: No refresh token found in X-Refresh-Token header for automatic refresh"
        )
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Authentication required",
        )

    # Get database connection for refresh operation
    conn = await get_database_connection()

    try:
        # Attempt to refresh access token
        token_result = await auth_service.refresh_access_token(conn, refresh_token)

        if not token_result:
            logger.warning("🔄 AUTH MIDDLEWARE: Automatic token refresh failed")
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Session expired - please login again",
            )

        new_access_token, new_refresh_token = token_result

        # Set new tokens in response headers for client to update
        response.headers["X-New-Access-Token"] = new_access_token
        response.headers["X-New-Refresh-Token"] = new_refresh_token

        # Validate new access token to get user_id
        new_token_payload = auth_service.validate_token(new_access_token, "access")
        if not new_token_payload or not new_token_payload.get("sub"):
            logger.error("🔄 AUTH MIDDLEWARE: New access token is invalid after refresh")
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Authentication failed",
            )

        user_id = new_token_payload["sub"]
        return user_id

    finally:
        # Always return connection to pool
        await conn.close()


async def get_current_user_id(
    request: Request,
    response: Response,
    auth_service: AuthService = Depends(get_auth_service),
) -> str:
    """
    Extract and verify access token from Authorization header, return authenticated user ID.
    Automatically refreshes tokens if access token is expired but refresh token is valid.

    Args:
        request: FastAPI request object
        response: FastAPI response object (for setting new token headers)
        auth_service: Authentication service instance

    Returns:
        str: Authenticated user ID

    Raises:
        HTTPException: 401 if both access and refresh tokens are missing, invalid, or expired
    """
    # Get access token from Authorization header
    access_token = auth_service.get_access_token_from_request(request)

    if not access_token:
        logger.warning(
            "🔒 AUTH MIDDLEWARE: No access token found in Authorization header"
        )
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Access token required",
        )

    try:
        # Try to validate access token first
        token_payload = auth_service.validate_token(access_token, "access")

        if token_payload and token_payload.get("sub"):
            user_id = token_payload["sub"]
            return user_id

        # Access token is invalid/expired, try automatic refresh
        return await _attempt_automatic_refresh(request, response, auth_service)

    except HTTPException:
        # Re-raise HTTP exceptions
        raise
    except jwt.ExpiredSignatureError:
        # Handle JWT expiration specifically for better logging
        return await _attempt_automatic_refresh(request, response, auth_service)

    except Exception as e:
        logger.error(f"❌ AUTH MIDDLEWARE: Authentication failed: {str(e)}")
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Authentication failed",
        )


# ================================================================
# LEGACY COMPATIBILITY FUNCTIONS
# ================================================================


async def verify_token(
    request: Request,
    response: Response,
    auth_service: AuthService = Depends(get_auth_service),
) -> dict:
    """
    Legacy verify_token function for backward compatibility.
    Returns user information dictionary from access token in Authorization header.
    """
    user_id = await get_current_user_id(request, response, auth_service)
    return {"user_id": user_id}
