from typing import Any, Dict, Optional, List
from src.common.clients.facebook_graph_page_client import FacebookGraphPageClient
from src.services.facebook._core.helpers import execute_graph_client_with_random_tokens


def infer_comment_authorship(
    *,
    actor_id: str,
    page_id: str,
    from_id: Optional[str],
    verb: str,
) -> Dict[str, Any]:
    """
    Infer authorship for a comment using webhook/Graph fetch signals only.

    Args:
        actor_id: ID of the entity that performed the webhook action.
        page_id: Facebook page ID for the conversation.
        from_id: Author ID returned by Facebook fetch (may be None/empty).
        verb: add | edited | hide | unhide | remove.

    Returns:
        {
            "is_act_by_page": bool,
            "is_comment_from_page": bool,
            "facebook_page_scope_user_id": Optional[str],
            "case": "page" | "user_known" | "user_unknown",
        }
    """

    normalized_from_id = (from_id or "").strip()
    normalized_actor_id = (actor_id or "").strip()
    is_act_by_page = normalized_actor_id == page_id

    if normalized_from_id:
        if normalized_from_id == page_id:
            return {
                "is_act_by_page": is_act_by_page,
                "is_comment_from_page": True,
                "facebook_page_scope_user_id": None,
                "case": "page",
            }

        return {
            "is_act_by_page": is_act_by_page,
            "is_comment_from_page": False,
            "facebook_page_scope_user_id": normalized_from_id,
            "case": "user_known",
        }

    if verb in ("add", "edited"):
        if is_act_by_page:
            return {
                "is_act_by_page": True,
                "is_comment_from_page": True,
                "facebook_page_scope_user_id": None,
                "case": "page",
            }

        return {
            "is_act_by_page": False,
            "is_comment_from_page": False,
            "facebook_page_scope_user_id": normalized_actor_id,
            "case": "user_known",
        }

    if is_act_by_page:
        return {
            "is_act_by_page": True,
            "is_comment_from_page": False,
            "facebook_page_scope_user_id": None,
            "case": "user_unknown",
        }

    return {
        "is_act_by_page": False,
        "is_comment_from_page": False,
        "facebook_page_scope_user_id": normalized_actor_id,
        "case": "user_known",
    }


async def get_comment_data(
    comment_id: str,
    page_admins: List[Dict],
) -> Optional[Dict[str, Any]]:
    """Fetch comment data with parent information from Facebook."""

    async def fetch_comment_with_parent_callback(client: FacebookGraphPageClient):
        return await client.fetch_comment_with_parent(comment_id)

    return await execute_graph_client_with_random_tokens(
        page_admins,
        fetch_comment_with_parent_callback,
        f"fetch comment with parent for {comment_id}",
    )
