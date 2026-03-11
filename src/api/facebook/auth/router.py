import asyncpg
from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import RedirectResponse
from starlette.status import HTTP_302_FOUND

from src.database.postgres.connection import get_async_connection_pool
from src.settings import settings
from src.utils.logger import get_logger

from .handler import FbHandler
from .utils import (
    check_and_mark_code_processed,
    extract_frontend_url_from_state,
    generate_auth_redirect_url,
    remove_code_from_cache,
)

logger = get_logger()

# Create auth router
auth_router = APIRouter()


# Get singleton handlers from app state
def get_fb_handler(request: Request) -> FbHandler:
    """Get Facebook handler singleton from app state"""
    return request.app.state.fb_handler


async def get_database_connection() -> asyncpg.Connection:
    """Get database connection from pool"""
    pool = await get_async_connection_pool()
    return await pool.acquire()


@auth_router.get("/auth/callback")
async def facebook_auth_callback(
    request: Request,
    fb_handler: FbHandler = Depends(get_fb_handler),
):
    """
    Facebook OAuth callback endpoint with header-based authentication.

    This endpoint:
    1. Delegates to FbHandler for complete Facebook authentication flow
    2. Handler manages: code exchange, user creation, token generation
    3. Redirects to frontend with tokens as URL parameters
    """
    fb_code = request.query_params.get("code")

    if not fb_code:
        logger.error("🔐 AUTH CALLBACK ERROR: Missing authorization code")
        raise HTTPException(status_code=400, detail="Missing authorization code")

    state = request.query_params.get("state")
    frontend_url_from_state = extract_frontend_url_from_state(state)

    # Check if this code was recently processed (handles cleanup and marking)
    if check_and_mark_code_processed(fb_code):
        error_redirect_url = generate_auth_redirect_url(
            error="duplicate_request", frontend_url=frontend_url_from_state
        )
        return RedirectResponse(url=error_redirect_url, status_code=HTTP_302_FOUND)

    # Get database connection
    conn = await get_database_connection()

    try:
        # Sign in with Facebook - handler manages complete flow including token creation
        (
            internal_user_id,
            user,
            user_info,
            pages_data,
            access_token,
            refresh_token,
        ) = await fb_handler.sign_in_facebook(conn, fb_code)

        # Redirect to frontend with tokens in URL parameters
        return RedirectResponse(
            url=generate_auth_redirect_url(
                success=True,
                access_token=access_token,
                refresh_token=refresh_token,
                frontend_url=frontend_url_from_state,
            ),
            status_code=HTTP_302_FOUND,
        )

    except Exception as e:
        remove_code_from_cache(fb_code)
        logger.error(f"🔐 AUTH CALLBACK ERROR: {e}")

        # Redirect to frontend with error
        try:
            error_redirect_url = generate_auth_redirect_url(
                success=False, frontend_url=frontend_url_from_state
            )
            return RedirectResponse(
                url=error_redirect_url,
                status_code=HTTP_302_FOUND,
            )
        except Exception as redirect_error:
            logger.error(f"🔐 ERROR generating redirect URL: {redirect_error}")
            # Last resort: use first allowed URL
            if settings.allowed_frontend_urls:
                fallback_url = f"{settings.allowed_frontend_urls[0].rstrip('/')}/auth/facebook?success=false"
                return RedirectResponse(url=fallback_url, status_code=HTTP_302_FOUND)
            else:
                raise
    finally:
        # Return connection to pool
        await conn.close()
