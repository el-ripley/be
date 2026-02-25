"""
Background Worker for Facebook Sync Jobs.

Processes sync jobs from Redis queue without blocking the main web server.
"""

import asyncio
import signal
import sys
from typing import Dict, Any

from src.redis_client.redis_client import RedisClient
from src.redis_client.redis_job_queue import RedisJobQueue, JobStatus
from src.database.postgres.connection import get_async_connection
from src.utils.logger import get_logger

# Note: Services are imported lazily in _init_services() to avoid circular imports

logger = get_logger()


class SyncWorker:
    """
    Background worker for processing sync jobs.

    Listens to Redis job queue and processes Facebook sync operations
    without blocking the main API server.
    """

    def __init__(self, redis_client: RedisClient):
        self.redis_client = redis_client
        self.job_queue = RedisJobQueue(redis_client)
        self.running = False
        self.current_job_id = None

        # Services will be initialized lazily to avoid circular imports
        self._services_initialized = False
        self._post_sync_service = None
        self._comment_sync_service = None
        self._inbox_sync_service = None
        self._full_sync_service = None

    def _init_services(self):
        """Lazy initialization of services to avoid circular imports."""
        if self._services_initialized:
            return

        # Import here to avoid circular import at module level
        # (Worker doesn't need webhook handlers, only sync services)
        # noinspection PyUnresolvedReferences
        from src.services.facebook.auth import FacebookPageService

        # noinspection PyUnresolvedReferences
        from src.services.facebook.users.page_scope_user_service import (
            PageScopeUserService,
        )

        # noinspection PyUnresolvedReferences
        from src.services.facebook.posts.post_sync_service import PostSyncService

        # noinspection PyUnresolvedReferences
        from src.services.facebook.comments.sync.comment_sync_service import (
            CommentSyncService,
        )

        # noinspection PyUnresolvedReferences
        from src.services.facebook.messages.sync.inbox_sync_service import (
            InboxSyncService,
        )

        # noinspection PyUnresolvedReferences
        from src.services.facebook.full_sync_service import FullSyncService

        page_service = FacebookPageService()
        page_scope_user_service = PageScopeUserService()

        self._post_sync_service = PostSyncService(page_service)
        # CommentSyncService will create CommentConversationService and CommentWriteService internally
        # to avoid circular import issues with CommentWebhookHandler
        self._comment_sync_service = CommentSyncService(
            page_service,
            page_scope_user_service,
        )
        self._inbox_sync_service = InboxSyncService(
            page_service, page_scope_user_service
        )
        self._full_sync_service = FullSyncService(
            self._post_sync_service,
            self._comment_sync_service,
        )

        self._services_initialized = True

    async def start(self):
        """Start the worker."""
        self.running = True
        logger.info("🚀 Sync worker started, waiting for jobs...")

        # Setup signal handlers
        loop = asyncio.get_event_loop()
        for sig in (signal.SIGTERM, signal.SIGINT):
            loop.add_signal_handler(
                sig, lambda s=sig: asyncio.create_task(self.shutdown(s))
            )

        while self.running:
            try:
                # Wait for next job (blocking with timeout)
                job_id = await self.job_queue.dequeue(timeout=5)

                if job_id:
                    self.current_job_id = job_id
                    await self.process_job(job_id)
                    self.current_job_id = None

            except asyncio.CancelledError:
                logger.info("Worker cancelled, shutting down...")
                break
            except Exception as e:
                logger.error(f"❌ Worker error: {e}", exc_info=True)
                await asyncio.sleep(1)  # Prevent tight loop on persistent errors

    async def process_job(self, job_id: str):
        """Process a single job."""
        logger.info(f"⚙️ Processing job {job_id}")

        lock_key = None  # Track lock key for cleanup

        try:
            # Get job data
            job = await self.job_queue.get_job(job_id)
            if not job:
                logger.warning(f"⚠️ Job {job_id} not found")
                return

            # Check if cancelled
            if job["status"] == JobStatus.CANCELLED:
                logger.info(f"🚫 Job {job_id} was cancelled, skipping")
                # Extract lock_key even for cancelled jobs
                payload = job.get("payload", {})
                lock_key = payload.get("_lock_key")
                return

            # Mark as processing
            await self.job_queue.update_job(job_id, status=JobStatus.PROCESSING)

            # Route to appropriate handler
            job_type = job["type"]
            payload = job["payload"]

            # Extract lock_key from payload (set by FacebookSyncJobManager)
            lock_key = payload.get("_lock_key")

            if job_type == "full_sync":
                result = await self._handle_full_sync(job_id, payload)
            elif job_type == "post_sync":
                result = await self._handle_post_sync(job_id, payload)
            elif job_type == "comment_sync":
                result = await self._handle_comment_sync(job_id, payload)
            elif job_type == "inbox_sync":
                result = await self._handle_inbox_sync(job_id, payload)
            else:
                raise ValueError(f"Unknown job type: {job_type}")

            # Mark completed
            await self.job_queue.mark_completed(job_id, result=result)

        except Exception as e:
            logger.error(f"❌ Job {job_id} failed: {e}", exc_info=True)
            await self.job_queue.mark_failed(job_id, error=str(e))

        finally:
            # ALWAYS release lock (success, failure, or cancellation)
            if lock_key:
                try:
                    from src.redis_client.redis_facebook_sync_locks import (
                        RedisFacebookSyncLocks,
                    )

                    sync_locks = RedisFacebookSyncLocks(redis_client=self.redis_client)
                    await sync_locks.release_lock(lock_key)
                    logger.info(f"🔓 Released lock for job {job_id}: {lock_key}")
                except Exception as e:
                    logger.error(
                        f"❌ Failed to release lock {lock_key} for job {job_id}: {e}"
                    )

    async def _handle_full_sync(
        self, job_id: str, payload: Dict[str, Any]
    ) -> Dict[str, Any]:
        """Handle full sync job."""
        self._init_services()  # Ensure services are initialized
        page_id = payload["page_id"]
        posts_limit = payload.get("posts_limit", 25)
        comments_per_post = payload.get("comments_per_post", 10)

        logger.info(f"🔄 Full sync started for page {page_id} (job: {job_id})")

        async with get_async_connection() as conn:
            result = await self._full_sync_service.full_sync(
                conn=conn,
                page_id=page_id,
                posts_limit=posts_limit,
                comments_per_post=comments_per_post,
            )

        logger.info(f"✅ Full sync completed for page {page_id} (job: {job_id})")
        return result

    async def _handle_post_sync(
        self, job_id: str, payload: Dict[str, Any]
    ) -> Dict[str, Any]:
        """Handle post sync job."""
        self._init_services()  # Ensure services are initialized
        page_id = payload["page_id"]
        limit = payload.get("limit", 25)
        continue_from_cursor = payload.get("continue_from_cursor", True)

        async with get_async_connection() as conn:
            result = await self._post_sync_service.sync_posts(
                conn=conn,
                page_id=page_id,
                limit=limit,
                continue_from_cursor=continue_from_cursor,
            )

        return result

    async def _handle_comment_sync(
        self, job_id: str, payload: Dict[str, Any]
    ) -> Dict[str, Any]:
        """Handle comment sync job."""
        self._init_services()  # Ensure services are initialized
        page_id = payload["page_id"]
        post_id = payload["post_id"]
        limit = payload.get("limit", 10)
        continue_from_cursor = payload.get("continue_from_cursor", True)

        async with get_async_connection() as conn:
            result = await self._comment_sync_service.sync_comments(
                conn=conn,
                page_id=page_id,
                post_id=post_id,
                limit=limit,
                continue_from_cursor=continue_from_cursor,
            )

        return result

    async def _handle_inbox_sync(
        self, job_id: str, payload: Dict[str, Any]
    ) -> Dict[str, Any]:
        """Handle inbox sync job."""
        self._init_services()  # Ensure services are initialized
        page_id = payload["page_id"]
        limit = payload.get("limit", 50)
        messages_per_conv = payload.get("messages_per_conv", 250)
        continue_from_cursor = payload.get("continue_from_cursor", True)

        async with get_async_connection() as conn:
            result = await self._inbox_sync_service.sync_inbox(
                conn=conn,
                page_id=page_id,
                limit=limit,
                messages_per_conv=messages_per_conv,
                continue_from_cursor=continue_from_cursor,
            )

        return result

    async def shutdown(self, sig):
        """Graceful shutdown."""
        logger.info(f"📴 Received exit signal {sig.name}, shutting down...")
        self.running = False

        # Wait for current job to complete (with timeout)
        if self.current_job_id:
            logger.info(
                f"⏳ Waiting for current job {self.current_job_id} to complete..."
            )
            await asyncio.sleep(2)  # Give it a moment to finish

        logger.info("👋 Worker shut down gracefully")
        sys.exit(0)


async def main():
    """Main entry point for worker."""
    logger.info("Starting Sync Worker...")

    # Initialize Redis
    redis_client = RedisClient()
    await redis_client.connect()

    # Create and start worker
    worker = SyncWorker(redis_client)

    try:
        await worker.start()
    finally:
        await redis_client.disconnect()


if __name__ == "__main__":
    asyncio.run(main())
