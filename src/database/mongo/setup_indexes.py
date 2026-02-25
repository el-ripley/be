"""
MongoDB index setup for webhook events collection.

This script sets up recommended indexes for efficient querying of webhook events.
"""

from src.database.mongo import get_async_mongo_connection, AsyncCollectionHelper
from src.database.mongo.schemas.webhook_event_schema import WebhookEventSchema
from src.utils.logger import get_logger

logger = get_logger()


async def setup_webhook_event_indexes():
    """Set up indexes for webhook events collection."""
    try:
        async with get_async_mongo_connection() as db:
            collection_name = "webhook_events"
            indexes = WebhookEventSchema.get_collection_indexes()

            logger.info(
                f"Setting up {len(indexes)} indexes for {collection_name} collection..."
            )

            await AsyncCollectionHelper.ensure_indexes(db, collection_name, indexes)

            logger.info(f"✅ Successfully set up indexes for {collection_name}")

            # Get collection stats
            stats = await AsyncCollectionHelper.get_collection_stats(
                db, collection_name
            )
            logger.info(f"📊 Collection stats: {stats}")

    except Exception as e:
        logger.error(f"❌ Failed to set up webhook event indexes: {e}")
        raise


if __name__ == "__main__":
    import asyncio

    asyncio.run(setup_webhook_event_indexes())
