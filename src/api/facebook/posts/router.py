"""API router for Facebook posts operations."""

from typing import Optional
from fastapi import APIRouter, Depends, HTTPException, Request

from src.database.postgres.connection import async_db_transaction
from src.database.postgres.repositories.facebook_queries.comments.comment_posts import (
    get_post_by_id,
)
from src.middleware.auth_middleware import get_current_user_id
from src.services.facebook.posts.post_read_service import PostReadService
from src.services.facebook.auth import FacebookPermissionService

from .schemas import (
    PostsListResponse,
    PostListItem,
    PostDetailResponse,
)


router = APIRouter(
    prefix="/posts",
    tags=["Facebook Posts"],
)


def get_post_read_service(request: Request) -> PostReadService:
    """Resolve PostReadService from app.state."""
    # PostReadService is stateless, create new instance
    return PostReadService()


def get_permission_service(request: Request) -> FacebookPermissionService:
    """Resolve FacebookPermissionService from app.state."""
    service = getattr(request.app.state, "facebook_permission_service", None)
    if service is None:
        raise HTTPException(
            status_code=500,
            detail="Permission service is not initialized",
        )
    return service


async def _check_page_permission(
    permission_service: FacebookPermissionService,
    user_id: str,
    page_id: str,
) -> None:
    """Check user has admin permission for page."""
    has_permission = await permission_service.check_user_page_admin_permission(
        user_id, page_id
    )
    if not has_permission:
        raise HTTPException(
            status_code=403,
            detail=f"User does not have permission to manage page {page_id}",
        )


@router.get(
    "",
    summary="List posts for a page",
    description=(
        "List posts for a page with pagination. "
        "Useful to see which posts need comment sync. "
        "Can filter by comment sync status."
    ),
    response_model=PostsListResponse,
)
async def list_posts(
    page_id: str,
    limit: int = 20,
    cursor: Optional[str] = None,
    need_comment_sync: Optional[bool] = None,
    service: PostReadService = Depends(get_post_read_service),
    user_id: str = Depends(get_current_user_id),
    permission_service: FacebookPermissionService = Depends(get_permission_service),
):
    """
    List posts for a page.

    Args:
        page_id: Facebook Page ID
        limit: Max posts to return (1-100, default: 20)
        cursor: Optional pagination cursor (JSON-encoded tuple)
        need_comment_sync: Optional filter - True = only posts needing sync,
                          False = only completed, None = all
    """
    await _check_page_permission(permission_service, user_id, page_id)

    # Parse cursor
    parsed_cursor = None
    if cursor:
        try:
            import json

            parsed_cursor = tuple(json.loads(cursor))
        except Exception:
            pass  # Invalid cursor, ignore

    async with async_db_transaction() as conn:
        posts, has_more, next_cursor = await service.list_posts(
            conn=conn,
            fan_page_id=page_id,
            limit=limit,
            cursor=parsed_cursor,
            need_comment_sync=need_comment_sync,
        )

        # Format cursor
        next_cursor_str = None
        if next_cursor:
            import json

            next_cursor_str = json.dumps(list(next_cursor))

        return PostsListResponse(
            posts=[PostListItem(**post) for post in posts],
            has_more=has_more,
            cursor=next_cursor_str,
        )


@router.get(
    "/{post_id}",
    summary="Get post detail by ID",
    description="Get detailed information about a specific post by post_id.",
    response_model=PostDetailResponse,
)
async def get_post_detail(
    post_id: str,
    permission_service: FacebookPermissionService = Depends(get_permission_service),
    user_id: str = Depends(get_current_user_id),
):
    """Get post detail by post_id."""
    async with async_db_transaction() as conn:
        post = await get_post_by_id(conn, post_id)

        if not post:
            raise HTTPException(
                status_code=404,
                detail=f"Post {post_id} not found",
            )

        # Check user has admin permission for the page that owns this post
        page_id = post.get("fan_page_id")
        if not page_id:
            raise HTTPException(
                status_code=500,
                detail="Post has no associated page",
            )

        await _check_page_permission(permission_service, user_id, page_id)

        # Remove photo_media field if present (not in schema)
        post_data = {k: v for k, v in post.items() if k != "photo_media"}

        return PostDetailResponse(**post_data)
