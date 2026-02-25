"""
MongoDB repository for Facebook webhook events.

This repository handles storage, retrieval, and management of webhook events
with efficient querying and analysis capabilities.
"""

from datetime import datetime, timedelta
from typing import List, Optional, Dict, Any
from motor.motor_asyncio import AsyncIOMotorDatabase, AsyncIOMotorClientSession

from src.database.mongo.executor import (
    insert_one_async,
    find_one_async,
    find_many_async,
    update_one_async,
    aggregate_async,
)
from src.database.mongo.schemas.webhook_event_schema import (
    WebhookEventSchema,
    WebhookEventData,
)
from src.utils.logger import get_logger

logger = get_logger()


class WebhookEventRepository:
    """Repository for managing Facebook webhook events in MongoDB."""

    COLLECTION_NAME = "webhook_events"

    def __init__(self, db: AsyncIOMotorDatabase):
        """Initialize repository with database connection."""
        self.db = db
        self.collection = db[self.COLLECTION_NAME]

    async def store_webhook_event(
        self,
        webhook_data: WebhookEventData,
        session: Optional[AsyncIOMotorClientSession] = None,
    ) -> str:
        """
        Store a webhook event in MongoDB.

        Args:
            webhook_data: The webhook event data to store
            session: Optional database session for transactions

        Returns:
            str: The inserted document ID
        """
        try:
            document = WebhookEventSchema.to_document(webhook_data)

            return await insert_one_async(self.collection, document, session=session)

        except Exception as e:
            logger.error(f"❌ Failed to store webhook event: {e}")
            raise

    async def get_webhook_event_by_id(
        self, event_id: str, session: Optional[AsyncIOMotorClientSession] = None
    ) -> Optional[WebhookEventData]:
        """
        Retrieve a webhook event by its ID.

        Args:
            event_id: The event ID to search for
            session: Optional database session

        Returns:
            WebhookEventData or None if not found
        """
        try:
            from bson import ObjectId

            doc = await find_one_async(
                self.collection, {"_id": ObjectId(event_id)}, session=session
            )

            if doc:
                return WebhookEventSchema.from_document(doc)
            return None

        except Exception as e:
            logger.error(f"❌ Failed to get webhook event {event_id}: {e}")
            raise

    async def get_events_by_type(
        self,
        event_type: str,
        event_subtype: Optional[str] = None,
        limit: int = 50,
        skip: int = 0,
        session: Optional[AsyncIOMotorClientSession] = None,
    ) -> List[WebhookEventData]:
        """
        Get webhook events by type and optional subtype.

        Args:
            event_type: The event type to filter by
            event_subtype: Optional event subtype to filter by
            limit: Maximum number of events to return
            skip: Number of events to skip
            session: Optional database session

        Returns:
            List of webhook event data
        """
        try:
            filter_dict = {"event_type": event_type}
            if event_subtype:
                filter_dict["event_subtype"] = event_subtype

            docs = await find_many_async(
                self.collection,
                filter_dict,
                sort=[("created_at", -1)],
                limit=limit,
                skip=skip,
                session=session,
            )

            return [WebhookEventSchema.from_document(doc) for doc in docs]

        except Exception as e:
            logger.error(f"❌ Failed to get events by type {event_type}: {e}")
            raise

    async def get_events_by_page(
        self,
        page_id: str,
        event_type: Optional[str] = None,
        hours_back: Optional[int] = 24,
        limit: int = 100,
        session: Optional[AsyncIOMotorClientSession] = None,
    ) -> List[WebhookEventData]:
        """
        Get webhook events for a specific page.

        Args:
            page_id: The Facebook page ID
            event_type: Optional event type filter
            hours_back: Number of hours to look back (None for all)
            limit: Maximum number of events to return
            session: Optional database session

        Returns:
            List of webhook event data
        """
        try:
            filter_dict = {"page_id": page_id}

            if event_type:
                filter_dict["event_type"] = event_type

            if hours_back:
                cutoff_time = datetime.utcnow() - timedelta(hours=hours_back)
                filter_dict["created_at"] = {"$gte": cutoff_time}

            docs = await find_many_async(
                self.collection,
                filter_dict,
                sort=[("created_at", -1)],
                limit=limit,
                session=session,
            )

            return [WebhookEventSchema.from_document(doc) for doc in docs]

        except Exception as e:
            logger.error(f"❌ Failed to get events for page {page_id}: {e}")
            raise

    async def get_unprocessed_events(
        self, limit: int = 50, session: Optional[AsyncIOMotorClientSession] = None
    ) -> List[WebhookEventData]:
        """
        Get webhook events that haven't been processed yet.

        Args:
            limit: Maximum number of events to return
            session: Optional database session

        Returns:
            List of unprocessed webhook event data
        """
        try:
            docs = await find_many_async(
                self.collection,
                {"processed": False},
                sort=[("created_at", 1)],  # Oldest first
                limit=limit,
                session=session,
            )

            return [WebhookEventSchema.from_document(doc) for doc in docs]

        except Exception as e:
            logger.error(f"❌ Failed to get unprocessed events: {e}")
            raise

    async def mark_event_processed(
        self,
        event_id: str,
        processing_errors: Optional[List[str]] = None,
        session: Optional[AsyncIOMotorClientSession] = None,
    ) -> bool:
        """
        Mark a webhook event as processed.

        Args:
            event_id: The event ID to mark as processed
            processing_errors: Optional list of processing errors
            session: Optional database session

        Returns:
            bool: True if updated successfully
        """
        try:
            from bson import ObjectId

            update_data = {
                "$set": {
                    "processed": True,
                    "processed_at": datetime.utcnow(),
                }
            }

            if processing_errors:
                update_data["$set"]["processing_errors"] = processing_errors

            result = await update_one_async(
                self.collection,
                {"_id": ObjectId(event_id)},
                update_data,
                session=session,
            )

            return result["modified_count"] > 0

        except Exception as e:
            logger.error(f"❌ Failed to mark event {event_id} as processed: {e}")
            raise

    async def get_event_stats(
        self, hours_back: int = 24, session: Optional[AsyncIOMotorClientSession] = None
    ) -> Dict[str, Any]:
        """
        Get statistics about webhook events.

        Args:
            hours_back: Number of hours to analyze
            session: Optional database session

        Returns:
            Dictionary with event statistics
        """
        try:
            cutoff_time = datetime.utcnow() - timedelta(hours=hours_back)

            # Aggregation pipeline for statistics
            pipeline = [
                {"$match": {"created_at": {"$gte": cutoff_time}}},
                {
                    "$group": {
                        "_id": {
                            "event_type": "$event_type",
                            "event_subtype": "$event_subtype",
                            "processed": "$processed",
                        },
                        "count": {"$sum": 1},
                        "latest": {"$max": "$created_at"},
                        "earliest": {"$min": "$created_at"},
                    }
                },
                {"$sort": {"count": -1}},
            ]

            results = await aggregate_async(self.collection, pipeline, session=session)

            # Process results
            stats = {
                "total_events": 0,
                "processed_events": 0,
                "unprocessed_events": 0,
                "by_type": {},
                "time_range": {
                    "hours_back": hours_back,
                    "cutoff_time": cutoff_time,
                },
            }

            for result in results:
                count = result["count"]
                event_info = result["_id"]

                stats["total_events"] += count

                if event_info["processed"]:
                    stats["processed_events"] += count
                else:
                    stats["unprocessed_events"] += count

                # Group by type
                event_type = event_info["event_type"]
                if event_type not in stats["by_type"]:
                    stats["by_type"][event_type] = {
                        "total": 0,
                        "processed": 0,
                        "unprocessed": 0,
                        "subtypes": {},
                    }

                stats["by_type"][event_type]["total"] += count

                if event_info["processed"]:
                    stats["by_type"][event_type]["processed"] += count
                else:
                    stats["by_type"][event_type]["unprocessed"] += count

                # Track subtypes
                subtype = event_info["event_subtype"]
                if subtype not in stats["by_type"][event_type]["subtypes"]:
                    stats["by_type"][event_type]["subtypes"][subtype] = 0
                stats["by_type"][event_type]["subtypes"][subtype] += count

            return stats

        except Exception as e:
            logger.error(f"❌ Failed to get event stats: {e}")
            raise

    async def cleanup_old_events(
        self,
        days_to_keep: int = 30,
        session: Optional[AsyncIOMotorClientSession] = None,
    ) -> int:
        """
        Clean up old webhook events to manage storage.

        Args:
            days_to_keep: Number of days of events to keep
            session: Optional database session

        Returns:
            int: Number of events deleted
        """
        try:
            from src.database.mongo.executor import delete_many_async

            cutoff_time = datetime.utcnow() - timedelta(days=days_to_keep)

            deleted_count = await delete_many_async(
                self.collection, {"created_at": {"$lt": cutoff_time}}, session=session
            )

            logger.info(
                f"🧹 Cleaned up {deleted_count} old webhook events (older than {days_to_keep} days)"
            )
            return deleted_count

        except Exception as e:
            logger.error(f"❌ Failed to cleanup old events: {e}")
            raise

    async def search_events(
        self,
        text_query: str,
        limit: int = 50,
        session: Optional[AsyncIOMotorClientSession] = None,
    ) -> List[WebhookEventData]:
        """
        Search webhook events by text in raw data.

        Args:
            text_query: Text to search for in raw data
            limit: Maximum number of events to return
            session: Optional database session

        Returns:
            List of matching webhook event data
        """
        try:
            # Use text search if available, otherwise regex
            filter_dict = {
                "$or": [
                    {"raw_data": {"$regex": text_query, "$options": "i"}},
                    {"notes": {"$regex": text_query, "$options": "i"}},
                    {"tags": {"$in": [text_query]}},
                ]
            }

            docs = await find_many_async(
                self.collection,
                filter_dict,
                sort=[("created_at", -1)],
                limit=limit,
                session=session,
            )

            return [WebhookEventSchema.from_document(doc) for doc in docs]

        except Exception as e:
            logger.error(f"❌ Failed to search events: {e}")
            raise
