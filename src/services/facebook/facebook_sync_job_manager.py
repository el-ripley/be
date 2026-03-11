"""
Unified Facebook Sync Job Manager.

Centralizes lock management and job queue operations for all Facebook sync tasks.
Used by both API endpoints and Agent tools to ensure consistent behavior.

LOCK LIFECYCLE:
1. FacebookSyncJobManager.submit_sync() → acquire_lock() before enqueue
2. Job is enqueued with lock_key in payload
3. Worker processes job
4. Worker releases lock in finally block (always executed)
"""

import asyncio
from enum import Enum
from typing import Any, Dict, Optional

from src.redis_client.redis_facebook_sync_locks import RedisFacebookSyncLocks
from src.redis_client.redis_job_queue import RedisJobQueue
from src.utils.logger import get_logger

logger = get_logger()


class SyncType(str, Enum):
    """Types of sync operations."""

    FULL = "full_sync"
    POSTS = "post_sync"
    COMMENTS = "comment_sync"
    INBOX = "inbox_sync"


class SyncMode(str, Enum):
    """Execution mode for sync operations."""

    ASYNC = "async"  # Return job_id immediately (for API)
    SYNC = "sync"  # Wait for completion (for Agent tools)


class FacebookSyncJobManager:
    """
    Unified manager for Facebook sync operations.

    Responsibilities:
    1. Acquire appropriate lock before enqueuing (prevent race conditions)
    2. Enqueue job to queue with lock_key
    3. Handle both async (immediate return) and sync (wait) modes
    4. Worker is responsible for releasing locks

    Benefits:
    - Prevents race conditions via locks
    - Prevents server blocking via job queue
    - Single source of truth for sync job submission
    - Consistent behavior for API and Agent callers
    """

    def __init__(
        self,
        job_queue: RedisJobQueue,
        sync_locks: RedisFacebookSyncLocks,
        default_lock_ttl: int = 3600,  # 1 hour
    ):
        self.job_queue = job_queue
        self.sync_locks = sync_locks
        self.default_lock_ttl = default_lock_ttl

    async def submit_sync(
        self,
        sync_type: SyncType,
        payload: Dict[str, Any],
        user_id: Optional[str] = None,
        mode: SyncMode = SyncMode.ASYNC,
        timeout_seconds: int = 300,  # 5 minutes for sync mode
    ) -> Dict[str, Any]:
        """
        Submit a sync job with lock protection.

        Args:
            sync_type: Type of sync operation
            payload: Job payload (page_id, post_id, limit, etc.)
            user_id: User who initiated the sync
            mode: ASYNC (return job_id) or SYNC (wait for result)
            timeout_seconds: Max wait time for SYNC mode

        Returns:
            - ASYNC mode: {"success": True, "job_id": str, "status": "queued"}
            - SYNC mode: {"success": True, "job_id": str, "status": "completed", "result": {...}}
            - Error: {"success": False, "error": str, "message": str}
        """
        # 1. Generate lock key
        lock_key = self._get_lock_key(sync_type, payload)

        # 2. Try to acquire lock BEFORE enqueuing
        lock_acquired = await self.sync_locks.acquire_lock(
            lock_key, ttl_seconds=self.default_lock_ttl
        )

        if not lock_acquired:
            logger.warning(
                f"⚠️ {sync_type.value} already in progress for {lock_key}, rejecting duplicate"
            )
            return {
                "success": False,
                "error": "sync_already_in_progress",
                "message": f"Another {sync_type.value} is already running for this resource",
            }

        try:
            # 3. Add lock_key to payload so worker can release it
            enriched_payload = {
                **payload,
                "_lock_key": lock_key,  # Internal field for worker
            }

            # 4. Enqueue job
            job_id = await self.job_queue.enqueue(
                job_type=sync_type.value,
                payload=enriched_payload,
                user_id=user_id,
            )

            logger.info(f"📋 {sync_type.value} job {job_id} enqueued (mode: {mode})")

            # 5. Handle execution mode
            if mode == SyncMode.ASYNC:
                # Return immediately for API calls
                return {
                    "success": True,
                    "job_id": job_id,
                    "status": "queued",
                }

            else:  # SyncMode.SYNC
                # Wait for completion for Agent tools
                result = await self._wait_for_completion(
                    job_id, timeout_seconds=timeout_seconds
                )
                return result

        except Exception as e:
            # If enqueue failed, release lock immediately
            await self.sync_locks.release_lock(lock_key)
            logger.error(f"❌ Failed to submit {sync_type.value}: {e}")
            return {
                "success": False,
                "error": "enqueue_failed",
                "message": str(e),
            }

    async def _wait_for_completion(
        self,
        job_id: str,
        timeout_seconds: int,
        poll_interval: float = 1.0,
    ) -> Dict[str, Any]:
        """
        Wait for job completion (for SYNC mode).

        Polls job status until completed/failed or timeout.

        Note:
            Uses asyncio.wait_for() to properly handle timeout and ensure
            the coroutine doesn't block the event loop unnecessarily.
            The poll_interval is adaptive - starts fast, slows down over time.
        """

        async def _poll_job_status() -> Dict[str, Any]:
            """Inner coroutine that polls job status."""
            # Adaptive polling: faster at start, slower over time
            initial_poll = min(poll_interval, 0.5)  # Start with 0.5s or less
            max_poll = min(poll_interval * 2, 5.0)  # Max 5s between polls
            current_poll = initial_poll
            poll_count = 0

            while True:
                job = await self.job_queue.get_job(job_id)

                if not job:
                    return {
                        "success": False,
                        "job_id": job_id,
                        "error": "job_not_found",
                    }

                status = job.get("status")

                if status == "completed":
                    return {
                        "success": True,
                        "job_id": job_id,
                        "status": "completed",
                        "result": job.get("result", {}),
                    }

                elif status in ["failed", "cancelled"]:
                    return {
                        "success": False,
                        "job_id": job_id,
                        "status": status,
                        "error": job.get("error", "unknown_error"),
                    }

                # Still processing - adaptive polling
                # Gradually increase poll interval to reduce load
                poll_count += 1
                if poll_count > 10:  # After 10 polls, slow down
                    current_poll = min(current_poll * 1.1, max_poll)

                await asyncio.sleep(current_poll)

        try:
            # Use asyncio.wait_for to properly handle timeout
            # This ensures the coroutine is cancelled on timeout
            return await asyncio.wait_for(
                _poll_job_status(),
                timeout=timeout_seconds,
            )
        except asyncio.TimeoutError:
            logger.warning(f"⏰ Job {job_id} timed out after {timeout_seconds}s")
            return {
                "success": False,
                "job_id": job_id,
                "error": "timeout",
                "message": f"Job did not complete within {timeout_seconds} seconds",
            }

    def _get_lock_key(self, sync_type: SyncType, payload: Dict[str, Any]) -> str:
        """
        Generate Redis lock key based on sync type and payload.

        Lock key patterns:
        - sync:full:{page_id}
        - sync:posts:{page_id}
        - sync:comments:{post_id}
        - sync:inbox:{page_id}
        """
        if sync_type == SyncType.FULL:
            page_id = payload.get("page_id")
            if not page_id:
                raise ValueError("page_id required for full_sync")
            return f"sync:full:{page_id}"

        elif sync_type == SyncType.POSTS:
            page_id = payload.get("page_id")
            if not page_id:
                raise ValueError("page_id required for post_sync")
            return f"sync:posts:{page_id}"

        elif sync_type == SyncType.COMMENTS:
            post_id = payload.get("post_id")
            if not post_id:
                raise ValueError("post_id required for comment_sync")
            return f"sync:comments:{post_id}"

        elif sync_type == SyncType.INBOX:
            page_id = payload.get("page_id")
            if not page_id:
                raise ValueError("page_id required for inbox_sync")
            return f"sync:inbox:{page_id}"

        else:
            raise ValueError(f"Unknown sync type: {sync_type}")
