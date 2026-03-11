"""Unified API router for Facebook sync operations."""

from fastapi import APIRouter, Depends, HTTPException, Request

from src.database.postgres.connection import async_db_transaction
from src.middleware.auth_middleware import get_current_user_id
from src.services.facebook.comments.sync.comment_sync_service import CommentSyncService
from src.services.facebook.full_sync_service import FullSyncService
from src.services.facebook.messages.sync.inbox_sync_service import InboxSyncService
from src.services.facebook.posts.post_sync_service import PostSyncService

from .schemas import (
    CommentsSyncStatus,
    CommentSyncStatusResponse,
    FullSyncStatusResponse,
    InboxSyncStatusResponse,
    PostsSyncStatus,
    SyncStatusResponse,
)
from .utils import check_page_permission, get_permission_service

router = APIRouter(
    prefix="/sync",
    tags=["Facebook Sync"],
)


# ============================================================================
# SERVICE DEPENDENCIES
# ============================================================================


def get_comment_sync_service(request: Request) -> CommentSyncService:
    """Resolve CommentSyncService from app.state."""
    service = getattr(request.app.state, "comment_sync_service", None)
    if service is None:
        raise HTTPException(
            status_code=500,
            detail="Comment sync service is not initialized",
        )
    return service


def get_inbox_sync_service(request: Request) -> InboxSyncService:
    """Resolve InboxSyncService from app.state."""
    service = getattr(request.app.state, "inbox_sync_service", None)
    if service is None:
        raise HTTPException(
            status_code=500,
            detail="Inbox sync service is not initialized",
        )
    return service


def get_full_sync_service(request: Request) -> FullSyncService:
    """Resolve FullSyncService from app.state."""
    service = getattr(request.app.state, "full_sync_service", None)
    if service is None:
        raise HTTPException(
            status_code=500,
            detail="Full sync service is not initialized",
        )
    return service


def get_post_sync_service(request: Request) -> PostSyncService:
    """Resolve PostSyncService from app.state."""
    service = getattr(request.app.state, "post_sync_service", None)
    if service is None:
        raise HTTPException(
            status_code=500,
            detail="Post sync service is not initialized",
        )
    return service


# ============================================================================
# POSTS SYNC STATUS
# ============================================================================


@router.get(
    "/posts/status",
    summary="Get posts sync status for a page",
    description="Get current posts sync progress for a page.",
    response_model=SyncStatusResponse,
)
async def get_posts_sync_status(
    page_id: str,
    service: PostSyncService = Depends(get_post_sync_service),
    user_id: str = Depends(get_current_user_id),
    permission_service=Depends(get_permission_service),
):
    """Get posts sync status for a page."""
    await check_page_permission(permission_service, user_id, page_id)

    async with async_db_transaction() as conn:
        return await service.get_sync_status(conn=conn, page_id=page_id)


# ============================================================================
# COMMENTS SYNC STATUS
# ============================================================================


@router.get(
    "/comments/status/{post_id}",
    summary="Get comment sync status for a post",
    description="Get current comments sync progress for a specific post.",
    response_model=CommentSyncStatusResponse,
)
async def get_post_comment_sync_status(
    post_id: str,
    page_id: str,
    service: CommentSyncService = Depends(get_comment_sync_service),
    user_id: str = Depends(get_current_user_id),
    permission_service=Depends(get_permission_service),
):
    """Get comment sync status for a specific post."""
    await check_page_permission(permission_service, user_id, page_id)

    async with async_db_transaction() as conn:
        return await service.get_post_comment_sync_status(conn=conn, post_id=post_id)


# ============================================================================
# MESSAGES SYNC STATUS
# ============================================================================


@router.get(
    "/messages/status",
    summary="Get inbox sync status for a page",
    description=(
        "Get current progress and cursor state for Facebook inbox sync of a page. "
        "Requires admin permission on the page."
    ),
    response_model=InboxSyncStatusResponse,
)
async def get_inbox_sync_status(
    page_id: str,
    service: InboxSyncService = Depends(get_inbox_sync_service),
    user_id: str = Depends(get_current_user_id),
    permission_service=Depends(get_permission_service),
):
    """Get current sync state for a page's inbox sync."""
    await check_page_permission(permission_service, user_id, page_id)

    async with async_db_transaction() as conn:
        return await service.get_sync_status(conn=conn, page_id=page_id)


# ============================================================================
# FULL SYNC STATUS
# ============================================================================


@router.get(
    "/full/status/{page_id}",
    summary="Get full sync status for a page",
    description="Get current sync status for posts and comments of a page.",
    response_model=FullSyncStatusResponse,
)
async def get_full_sync_status(
    page_id: str,
    service: FullSyncService = Depends(get_full_sync_service),
    user_id: str = Depends(get_current_user_id),
    permission_service=Depends(get_permission_service),
):
    """Get full sync status for a page."""
    await check_page_permission(permission_service, user_id, page_id)

    async with async_db_transaction() as conn:
        status = await service.get_sync_status(conn=conn, page_id=page_id)

        return FullSyncStatusResponse(
            fan_page_id=status["fan_page_id"],
            posts_sync=PostsSyncStatus(**status["posts_sync"]),
            comments_sync=CommentsSyncStatus(**status["comments_sync"]),
            overall_status=status["overall_status"],
            needs_initial_sync=status.get("needs_initial_sync", False),
        )
