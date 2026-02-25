from typing import Dict, Any, List, Callable, Awaitable, Optional
from src.common.clients.facebook_graph_page_client import FacebookGraphPageClient
from src.utils.logger import get_logger

logger = get_logger()


async def execute_graph_client_with_random_tokens(
    page_admins: List[Dict[str, Any]],
    callback: Callable[[FacebookGraphPageClient], Awaitable[Any]],
    operation_name: str = "Facebook API request",
) -> Optional[Any]:
    """
    Execute a Facebook page graph client request with automatic token retry.

    This function tries different page access tokens from page admins in order
    until one works successfully. This is useful since not every page access token
    may be valid or have the required permissions.

    Args:
        page_admins: List of page admin records containing access tokens
        callback: Async function that takes a FacebookGraphPageClient and returns a result
        operation_name: Description of the operation for logging purposes

    Returns:
        The result from the first successful API call, or None if all tokens fail

    Example usage:
        from src.services.facebook.token_service import execute_graph_client_with_random_tokens

        page_admins = await page_service.get_facebook_page_admins_by_page_id(conn, page_id)

        async def get_user_info_callback(client: FacebookGraphPageClient):
            return await client.get_user_info(psid)

        result = await execute_graph_client_with_random_tokens(
            page_admins, get_user_info_callback, "get user info"
        )
    """
    if not page_admins:
        logger.warning(f"⚠️ No page admins provided for {operation_name}")
        return None

    for i, admin in enumerate(page_admins, 1):
        access_token = admin.get("access_token")

        if not access_token:
            continue

        try:
            # Create page client with this admin's access token
            page_client = FacebookGraphPageClient(
                page_access_token=access_token,
            )

            # Execute the callback with this client
            result = await callback(page_client)

            if result is not None:
                return result

        except Exception as e:
            # Only log if this is the last attempt
            if i == len(page_admins):
                logger.error(
                    f"❌ All {len(page_admins)} page access tokens failed for {operation_name}: {e}"
                )
            continue

    return None
