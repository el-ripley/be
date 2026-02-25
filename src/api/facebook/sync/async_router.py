"""
Async Job-Based API Router for Facebook Sync Operations.

These endpoints enqueue sync jobs instead of running them synchronously,
preventing server blocking for long-running operations.
"""

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field
from typing import Optional

from src.middleware.auth_middleware import get_current_user_id
from src.redis_client.redis_job_queue import RedisJobQueue
from src.services.facebook.facebook_sync_job_manager import (
    FacebookSyncJobManager,
    SyncType,
    SyncMode,
)
from .schemas import (
    FullSyncRequest,
    PostsSyncRequest,
    CommentsSyncRequest,
    InboxSyncRequest,
)
from .utils import get_permission_service, check_page_permission


router = APIRouter(
    prefix="/sync/async",
    tags=["Facebook Sync (Async)"],
)


# ============================================================================
# RESPONSE MODELS
# ============================================================================


class JobResponse(BaseModel):
    """Response when job is enqueued."""

    job_id: str = Field(..., description="Job ID for tracking")
    status: str = Field(default="queued", description="Job status")
    message: str = Field(..., description="Human-readable message")


class JobStatusResponse(BaseModel):
    """Job status response."""

    job_id: str
    type: str
    status: str
    progress: int = Field(ge=0, le=100, description="Progress percentage")
    created_at: str
    updated_at: str
    result: Optional[dict] = None
    error: Optional[str] = None


# ============================================================================
# DEPENDENCIES
# ============================================================================


def get_sync_job_manager(request: Request) -> FacebookSyncJobManager:
    """Get sync job manager from app state."""
    sync_job_manager = getattr(request.app.state, "sync_job_manager", None)
    if sync_job_manager is None:
        raise HTTPException(
            status_code=500,
            detail="Sync job manager is not initialized",
        )
    return sync_job_manager


def get_job_queue(request: Request) -> RedisJobQueue:
    """Get job queue from app state (for status queries)."""
    job_queue = getattr(request.app.state, "job_queue", None)
    if job_queue is None:
        raise HTTPException(
            status_code=500,
            detail="Job queue is not initialized",
        )
    return job_queue


# ============================================================================
# ENDPOINTS
# ============================================================================


@router.post(
    "/full",
    summary="[Async] Trigger full sync for a page",
    description=(
        "Enqueue a full sync job for a page. "
        "Returns immediately with job ID. "
        "Use GET /sync/async/jobs/{job_id} to check status."
    ),
    response_model=JobResponse,
)
async def async_full_sync(
    payload: FullSyncRequest,
    sync_job_manager: FacebookSyncJobManager = Depends(get_sync_job_manager),
    user_id: str = Depends(get_current_user_id),
    permission_service=Depends(get_permission_service),
):
    """Enqueue full sync job with lock protection."""
    await check_page_permission(permission_service, user_id, payload.page_id)

    # Submit job via FacebookSyncJobManager (with lock protection)
    result = await sync_job_manager.submit_sync(
        sync_type=SyncType.FULL,
        payload={
            "page_id": payload.page_id,
            "posts_limit": payload.posts_limit,
            "comments_per_post": payload.comments_per_post,
        },
        user_id=user_id,
        mode=SyncMode.ASYNC,  # Return job_id immediately
    )

    if not result["success"]:
        raise HTTPException(
            status_code=409,  # Conflict
            detail=result.get("message", result.get("error")),
        )

    return JobResponse(
        job_id=result["job_id"],
        status="queued",
        message=f"Full sync job enqueued for page {payload.page_id}",
    )


@router.post(
    "/posts",
    summary="[Async] Sync posts from a Facebook page",
    description=(
        "Enqueue a post sync job for a page. "
        "Returns immediately with job ID. "
        "Use GET /sync/async/jobs/{job_id} to check status."
    ),
    response_model=JobResponse,
)
async def async_posts_sync(
    payload: PostsSyncRequest,
    sync_job_manager: FacebookSyncJobManager = Depends(get_sync_job_manager),
    user_id: str = Depends(get_current_user_id),
    permission_service=Depends(get_permission_service),
):
    """Enqueue post sync job with lock protection."""
    await check_page_permission(permission_service, user_id, payload.page_id)

    result = await sync_job_manager.submit_sync(
        sync_type=SyncType.POSTS,
        payload={
            "page_id": payload.page_id,
            "limit": payload.limit,
            "continue_from_cursor": payload.continue_from_cursor,
        },
        user_id=user_id,
        mode=SyncMode.ASYNC,
    )

    if not result["success"]:
        raise HTTPException(
            status_code=409,
            detail=result.get("message", result.get("error")),
        )

    return JobResponse(
        job_id=result["job_id"],
        status="queued",
        message=f"Post sync job enqueued for page {payload.page_id}",
    )


@router.post(
    "/comments",
    summary="[Async] Sync comment trees for a post",
    description=(
        "Enqueue a comment sync job for a post. "
        "Returns immediately with job ID. "
        "Use GET /sync/async/jobs/{job_id} to check status."
    ),
    response_model=JobResponse,
)
async def async_comments_sync(
    payload: CommentsSyncRequest,
    sync_job_manager: FacebookSyncJobManager = Depends(get_sync_job_manager),
    user_id: str = Depends(get_current_user_id),
    permission_service=Depends(get_permission_service),
):
    """Enqueue comment sync job with lock protection."""
    await check_page_permission(permission_service, user_id, payload.page_id)

    result = await sync_job_manager.submit_sync(
        sync_type=SyncType.COMMENTS,
        payload={
            "page_id": payload.page_id,
            "post_id": payload.post_id,
            "limit": payload.limit,
            "continue_from_cursor": payload.continue_from_cursor,
        },
        user_id=user_id,
        mode=SyncMode.ASYNC,
    )

    if not result["success"]:
        raise HTTPException(
            status_code=409,
            detail=result.get("message", result.get("error")),
        )

    return JobResponse(
        job_id=result["job_id"],
        status="queued",
        message=f"Comment sync job enqueued for post {payload.post_id}",
    )


@router.post(
    "/inbox",
    summary="[Async] Sync Facebook inbox conversations",
    description=(
        "Enqueue an inbox sync job for a page. "
        "Returns immediately with job ID. "
        "Use GET /sync/async/jobs/{job_id} to check status."
    ),
    response_model=JobResponse,
)
async def async_inbox_sync(
    payload: InboxSyncRequest,
    sync_job_manager: FacebookSyncJobManager = Depends(get_sync_job_manager),
    user_id: str = Depends(get_current_user_id),
    permission_service=Depends(get_permission_service),
):
    """Enqueue inbox sync job with lock protection."""
    await check_page_permission(permission_service, user_id, payload.page_id)

    result = await sync_job_manager.submit_sync(
        sync_type=SyncType.INBOX,
        payload={
            "page_id": payload.page_id,
            "limit": payload.limit,
            "messages_per_conv": payload.messages_per_conv,
            "continue_from_cursor": payload.continue_from_cursor,
        },
        user_id=user_id,
        mode=SyncMode.ASYNC,
    )

    if not result["success"]:
        raise HTTPException(
            status_code=409,
            detail=result.get("message", result.get("error")),
        )

    return JobResponse(
        job_id=result["job_id"],
        status="queued",
        message=f"Inbox sync job enqueued for page {payload.page_id}",
    )


@router.get(
    "/jobs/{job_id}",
    summary="Get job status",
    description="Check the status and progress of an async sync job.",
    response_model=JobStatusResponse,
)
async def get_job_status(
    job_id: str,
    job_queue: RedisJobQueue = Depends(get_job_queue),
    user_id: str = Depends(get_current_user_id),
):
    """Get job status by ID."""
    job = await job_queue.get_job(job_id)

    if not job:
        raise HTTPException(status_code=404, detail="Job not found")

    # Check permission (only owner or admin can see job)
    if job.get("user_id") != user_id:
        # TODO: Check if user is admin
        raise HTTPException(status_code=403, detail="Permission denied")

    # Map 'id' to 'job_id' for response
    job["job_id"] = job.pop("id", job_id)
    return JobStatusResponse(**job)


@router.delete(
    "/jobs/{job_id}",
    summary="Cancel job",
    description="Cancel a queued or processing job.",
)
async def cancel_job(
    job_id: str,
    job_queue: RedisJobQueue = Depends(get_job_queue),
    user_id: str = Depends(get_current_user_id),
):
    """Cancel a job."""
    job = await job_queue.get_job(job_id)

    if not job:
        raise HTTPException(status_code=404, detail="Job not found")

    # Check permission
    if job.get("user_id") != user_id:
        raise HTTPException(status_code=403, detail="Permission denied")

    success = await job_queue.cancel_job(job_id)

    if not success:
        raise HTTPException(
            status_code=400,
            detail="Job cannot be cancelled (already completed or failed)",
        )

    return {"message": f"Job {job_id} cancelled successfully"}


@router.get(
    "/jobs",
    summary="List user's jobs",
    description="List all jobs for the current user.",
    response_model=list[JobStatusResponse],
)
async def list_user_jobs(
    job_queue: RedisJobQueue = Depends(get_job_queue),
    user_id: str = Depends(get_current_user_id),
    limit: int = 50,
):
    """List user's jobs."""
    jobs = await job_queue.list_jobs_by_user(user_id, limit=limit)
    # Map 'id' to 'job_id' for each job
    for job in jobs:
        if "id" in job:
            job["job_id"] = job.pop("id")
    return [JobStatusResponse(**job) for job in jobs]
