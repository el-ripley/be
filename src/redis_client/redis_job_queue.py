"""
Simple Job Queue System using Redis.

Provides async job queue for long-running tasks like Facebook sync operations.
"""

import json
import uuid
from datetime import datetime
from enum import Enum
from typing import Any, Dict, List, Optional

from src.redis_client.redis_client import RedisClient
from src.utils.logger import get_logger

logger = get_logger()


class JobStatus(str, Enum):
    """Job execution status."""

    QUEUED = "queued"
    PROCESSING = "processing"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"


class RedisJobQueue:
    """
    Simple async job queue using Redis.

    Features:
    - Enqueue jobs with metadata
    - Process jobs in background
    - Track job status and progress
    - Get job results
    """

    def __init__(self, redis_client: RedisClient):
        self.redis = redis_client
        self.queue_key = "job_queue"
        self.job_prefix = "job:"
        self.processing_set = "job_queue:processing"

    async def enqueue(
        self,
        job_type: str,
        payload: Dict[str, Any],
        user_id: Optional[str] = None,
    ) -> str:
        """
        Enqueue a new job.

        Args:
            job_type: Type of job (e.g., "full_sync", "post_sync")
            payload: Job payload/parameters
            user_id: Optional user ID who initiated the job

        Returns:
            Job ID
        """
        job_id = str(uuid.uuid4())
        job_data = {
            "id": job_id,
            "type": job_type,
            "status": JobStatus.QUEUED,
            "payload": payload,
            "user_id": user_id,
            "created_at": datetime.utcnow().isoformat(),
            "updated_at": datetime.utcnow().isoformat(),
            "progress": 0,
            "result": None,
            "error": None,
        }

        # Store job data
        await self.redis.set(
            f"{self.job_prefix}{job_id}",
            json.dumps(job_data),
            expire=86400,  # Expire after 24 hours
        )

        # Add to queue
        await self.redis.lpush(self.queue_key, job_id)

        logger.info(f"📋 Enqueued job {job_id} (type: {job_type})")
        return job_id

    async def dequeue(self, timeout: int = 5) -> Optional[str]:
        """
        Dequeue next job from queue (blocking).

        Args:
            timeout: Timeout in seconds

        Returns:
            Job ID or None if timeout
        """
        result = await self.redis.brpop(self.queue_key, timeout=timeout)
        if result:
            _, job_id = result
            # Mark as processing
            await self.redis.sadd(self.processing_set, job_id)
            return job_id
        return None

    async def get_job(self, job_id: str) -> Optional[Dict[str, Any]]:
        """Get job data by ID."""
        data = await self.redis.get(f"{self.job_prefix}{job_id}")
        if data:
            return json.loads(data)
        return None

    async def update_job(
        self,
        job_id: str,
        status: Optional[JobStatus] = None,
        progress: Optional[int] = None,
        result: Optional[Dict[str, Any]] = None,
        error: Optional[str] = None,
    ) -> None:
        """Update job status and data."""
        job = await self.get_job(job_id)
        if not job:
            logger.warning(f"⚠️ Job {job_id} not found for update")
            return

        if status:
            job["status"] = status
        if progress is not None:
            job["progress"] = progress
        if result is not None:
            job["result"] = result
        if error is not None:
            job["error"] = error

        job["updated_at"] = datetime.utcnow().isoformat()

        await self.redis.set(
            f"{self.job_prefix}{job_id}",
            json.dumps(job),
            expire=86400,
        )

        logger.debug(f"📝 Updated job {job_id}: status={status}, progress={progress}")

    async def mark_completed(
        self, job_id: str, result: Optional[Dict[str, Any]] = None
    ) -> None:
        """Mark job as completed."""
        await self.update_job(
            job_id, status=JobStatus.COMPLETED, progress=100, result=result
        )
        await self.redis.srem(self.processing_set, job_id)
        logger.info(f"✅ Job {job_id} completed")

    async def mark_failed(self, job_id: str, error: str) -> None:
        """Mark job as failed."""
        await self.update_job(job_id, status=JobStatus.FAILED, error=error)
        await self.redis.srem(self.processing_set, job_id)
        logger.error(f"❌ Job {job_id} failed: {error}")

    async def cancel_job(self, job_id: str) -> bool:
        """
        Cancel a queued or processing job.

        Returns:
            True if cancelled, False if job not found or already completed
        """
        job = await self.get_job(job_id)
        if not job:
            return False

        if job["status"] in [
            JobStatus.COMPLETED,
            JobStatus.FAILED,
            JobStatus.CANCELLED,
        ]:
            return False

        await self.update_job(job_id, status=JobStatus.CANCELLED)
        await self.redis.srem(self.processing_set, job_id)
        logger.info(f"🚫 Job {job_id} cancelled")
        return True

    async def get_queue_size(self) -> int:
        """Get number of jobs in queue."""
        return await self.redis.llen(self.queue_key)

    async def get_processing_count(self) -> int:
        """Get number of jobs currently processing."""
        return await self.redis.scard(self.processing_set)

    async def list_jobs_by_user(
        self, user_id: str, limit: int = 50
    ) -> List[Dict[str, Any]]:
        """
        List jobs for a specific user.

        Note: This is a simple implementation. For production, consider using
        Redis sorted sets for better performance.
        """
        # Get all job keys
        keys = await self.redis.keys(f"{self.job_prefix}*")
        jobs = []

        for key in keys[:limit]:  # Limit to prevent memory issues
            data = await self.redis.get(key)
            if data:
                job = json.loads(data)
                if job.get("user_id") == user_id:
                    jobs.append(job)

        # Sort by created_at descending
        jobs.sort(key=lambda x: x.get("created_at", ""), reverse=True)
        return jobs[:limit]
