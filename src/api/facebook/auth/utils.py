import base64
import hashlib
import hmac
import time
from typing import Optional
from urllib.parse import urlparse, unquote

from src.settings import settings
from src.utils.logger import get_logger

logger = get_logger()


# Simple in-memory cache to prevent duplicate Facebook code processing
_processed_codes_cache = {}
_CACHE_TTL = 60  # 60 seconds TTL


def cleanup_expired_codes():
    """Remove expired codes from cache"""
    current_time = time.time()
    expired_codes = [
        code
        for code, timestamp in _processed_codes_cache.items()
        if current_time - timestamp > _CACHE_TTL
    ]
    for code in expired_codes:
        _processed_codes_cache.pop(code, None)


def check_and_mark_code_processed(fb_code: str) -> bool:
    """
    Check if a Facebook authorization code was recently processed.
    If not, mark it as processed.

    Returns:
        True if code is duplicate (already processed)
        False if code is new and has been marked as processed
    """
    cleanup_expired_codes()

    current_time = time.time()
    if fb_code in _processed_codes_cache:
        logger.warning(
            f"🔐 AUTH CALLBACK WARNING: Duplicate request for code {fb_code[:10]}..."
        )
        return True

    # Mark this code as being processed
    _processed_codes_cache[fb_code] = current_time
    return False


def remove_code_from_cache(fb_code: str):
    """Remove a code from the processed cache (used when processing fails)"""
    _processed_codes_cache.pop(fb_code, None)


def extract_frontend_url_from_state(state: Optional[str]) -> Optional[str]:
    """
    Extract and validate frontend URL from base64-encoded state parameter.

    Args:
        state: Base64-encoded state parameter containing frontend URL (may be URL-encoded)

    Returns:
        Validated frontend URL (normalized) or None if invalid/missing
    """
    if not state:
        return None

    try:
        # URL decode first (in case state is URL-encoded in query string)
        # FastAPI usually does this automatically, but handle it explicitly for safety
        url_decoded = unquote(state)

        # Then base64 decode
        decoded = base64.b64decode(url_decoded).decode("utf-8")

        # Validate that the decoded state is an allowed frontend URL
        if is_allowed_frontend_url(decoded):
            frontend_url = decoded.rstrip("/")
            return frontend_url
        else:
            logger.warning(f"⚠️ Frontend URL from state not in allowed list: {decoded}")
            return None
    except Exception as e:
        logger.error(f"❌ Error decoding state: {e}")
        return None


def is_allowed_frontend_url(url: str) -> bool:
    """
    Validate if a frontend URL is in the allowed list.

    Args:
        url: The frontend URL to validate

    Returns:
        True if URL is allowed, False otherwise
    """
    if not url:
        return False

    # Normalize URL (remove trailing slash)
    normalized_url = url.rstrip("/")

    try:
        parsed = urlparse(normalized_url)
        if not parsed.scheme or not parsed.netloc:
            return False

        # Check exact match first
        for allowed_url in settings.allowed_frontend_urls:
            normalized_allowed = allowed_url.rstrip("/")
            if normalized_url == normalized_allowed:
                return True

        # For localhost URLs, allow any port if localhost is in allowed list
        # This provides flexibility for development
        if parsed.hostname in ("localhost", "127.0.0.1") and parsed.scheme == "http":
            for allowed_url in settings.allowed_frontend_urls:
                allowed_parsed = urlparse(allowed_url.rstrip("/"))
                if (
                    allowed_parsed.hostname in ("localhost", "127.0.0.1")
                    and allowed_parsed.scheme == "http"
                ):
                    return True

        # For other URLs, check if scheme and netloc match exactly
        for allowed_url in settings.allowed_frontend_urls:
            allowed_parsed = urlparse(allowed_url.rstrip("/"))
            if (
                parsed.scheme == allowed_parsed.scheme
                and parsed.netloc == allowed_parsed.netloc
            ):
                return True

    except Exception as e:
        logger.warning(f"Error parsing URL {url}: {e}")
        return False

    return False


def generate_auth_redirect_url(
    success: bool = True,
    error: str = None,
    access_token: str = None,
    refresh_token: str = None,
    frontend_url: str = None,
) -> str:
    """
    Generate redirect URL for Facebook authentication callback.

    Args:
        success: Whether authentication was successful
        error: Error message if authentication failed
        access_token: JWT access token
        refresh_token: JWT refresh token
        frontend_url: Frontend URL to redirect to (from state parameter).
                     If not provided, falls back to first URL in allowed_frontend_urls
    """
    # Normalize: strip whitespace and check if empty
    if frontend_url:
        frontend_url = frontend_url.strip()

    # Use provided frontend_url or fall back to first allowed URL
    if not frontend_url:
        if settings.allowed_frontend_urls:
            frontend_url = settings.allowed_frontend_urls[0]
        else:
            logger.error("❌ No frontend URL available: allowed_frontend_urls is empty")
            raise ValueError(
                "No frontend URL available for redirect. "
                "Please configure ALLOWED_FRONTEND_URLS environment variable."
            )

    if not frontend_url or not frontend_url.strip():
        raise ValueError("No frontend URL available for redirect")

    base_url = f"{frontend_url.rstrip('/')}/auth/facebook"

    if error:
        return f"{base_url}?success=false&error={error}"

    params = [f"success={success}"]

    if success and access_token and refresh_token:
        params.append(f"access_token={access_token}")
        params.append(f"refresh_token={refresh_token}")

    return f"{base_url}?{'&'.join(params)}"


def verify_fb_signature(payload: bytes, signature_header: str, app_secret: str) -> bool:
    if not signature_header or not signature_header.startswith("sha1="):
        return False

    sent_signature = signature_header.split("sha1=")[-1]
    expected_signature = hmac.new(
        key=app_secret.encode("utf-8"), msg=payload, digestmod=hashlib.sha1
    ).hexdigest()

    return hmac.compare_digest(sent_signature, expected_signature)
