"""
Async MongoDB query execution utilities for high-performance operations.

This module provides async MongoDB operation functions with proper error handling
and result formatting using motor (async MongoDB driver).
"""

from typing import List, Dict, Any, Optional
from motor.motor_asyncio import (
    AsyncIOMotorCollection,
    AsyncIOMotorClientSession,
)
from pymongo.errors import PyMongoError
from src.utils.logger import get_logger

logger = get_logger()


async def insert_one_async(
    collection: AsyncIOMotorCollection,
    document: Dict[str, Any],
    session: Optional[AsyncIOMotorClientSession] = None,
) -> str:
    """
    Insert a single document into a collection.

    Args:
        collection: MongoDB collection
        document: Document to insert
        session: Optional session for transactions

    Returns:
        str: The inserted document's ID

    Example:
        result_id = await insert_one_async(
            db.users,
            {"name": "John", "email": "john@example.com"}
        )
    """
    try:
        result = await collection.insert_one(document, session=session)
        logger.debug(f"Document inserted successfully with ID: {result.inserted_id}")
        return str(result.inserted_id)

    except PyMongoError as e:
        logger.error(f"MongoDB insert error: {e}")
        logger.error(f"Document: {document}")
        raise
    except Exception as e:
        logger.error(f"Unexpected insert error: {e}")
        raise


async def insert_many_async(
    collection: AsyncIOMotorCollection,
    documents: List[Dict[str, Any]],
    session: Optional[AsyncIOMotorClientSession] = None,
    ordered: bool = False,
) -> List[str]:
    """
    Insert multiple documents into a collection.

    Args:
        collection: MongoDB collection
        documents: List of documents to insert
        session: Optional session for transactions
        ordered: Whether to maintain order (slower) or not (faster)

    Returns:
        List[str]: List of inserted document IDs

    Example:
        ids = await insert_many_async(
            db.users,
            [
                {"name": "John", "email": "john@example.com"},
                {"name": "Jane", "email": "jane@example.com"}
            ]
        )
    """
    if not documents:
        return []

    try:
        result = await collection.insert_many(
            documents, session=session, ordered=ordered
        )
        logger.debug(f"Batch insert completed: {len(result.inserted_ids)} documents")
        return [str(id) for id in result.inserted_ids]

    except PyMongoError as e:
        logger.error(f"MongoDB batch insert error: {e}")
        logger.error(f"Document count: {len(documents)}")
        raise
    except Exception as e:
        logger.error(f"Unexpected batch insert error: {e}")
        raise


async def find_one_async(
    collection: AsyncIOMotorCollection,
    filter_dict: Dict[str, Any],
    projection: Optional[Dict[str, Any]] = None,
    session: Optional[AsyncIOMotorClientSession] = None,
) -> Optional[Dict[str, Any]]:
    """
    Find a single document in a collection.

    Args:
        collection: MongoDB collection
        filter_dict: Query filter
        projection: Fields to include/exclude
        session: Optional session for transactions

    Returns:
        Dict or None: The found document or None if not found

    Example:
        user = await find_one_async(
            db.users,
            {"email": "john@example.com"},
            {"password": 0}  # Exclude password field
        )
    """
    try:
        result = await collection.find_one(filter_dict, projection, session=session)
        return dict(result) if result else None

    except PyMongoError as e:
        logger.error(f"MongoDB find_one error: {e}")
        logger.error(f"Filter: {filter_dict}")
        raise
    except Exception as e:
        logger.error(f"Unexpected find_one error: {e}")
        raise


async def find_many_async(
    collection: AsyncIOMotorCollection,
    filter_dict: Dict[str, Any] = None,
    projection: Optional[Dict[str, Any]] = None,
    sort: Optional[List[tuple]] = None,
    limit: Optional[int] = None,
    skip: Optional[int] = None,
    session: Optional[AsyncIOMotorClientSession] = None,
) -> List[Dict[str, Any]]:
    """
    Find multiple documents in a collection.

    Args:
        collection: MongoDB collection
        filter_dict: Query filter (empty dict for all documents)
        projection: Fields to include/exclude
        sort: Sort specification [(field, direction)]
        limit: Maximum number of documents to return
        skip: Number of documents to skip
        session: Optional session for transactions

    Returns:
        List[Dict]: List of found documents

    Example:
        users = await find_many_async(
            db.users,
            {"active": True},
            sort=[("created_at", -1)],
            limit=10
        )
    """
    try:
        cursor = collection.find(filter_dict or {}, projection, session=session)

        if sort:
            cursor = cursor.sort(sort)
        if skip:
            cursor = cursor.skip(skip)
        if limit:
            cursor = cursor.limit(limit)

        results = await cursor.to_list(length=limit)
        return [dict(doc) for doc in results]

    except PyMongoError as e:
        logger.error(f"MongoDB find_many error: {e}")
        logger.error(f"Filter: {filter_dict}")
        raise
    except Exception as e:
        logger.error(f"Unexpected find_many error: {e}")
        raise


async def update_one_async(
    collection: AsyncIOMotorCollection,
    filter_dict: Dict[str, Any],
    update_dict: Dict[str, Any],
    upsert: bool = False,
    session: Optional[AsyncIOMotorClientSession] = None,
) -> Dict[str, Any]:
    """
    Update a single document in a collection.

    Args:
        collection: MongoDB collection
        filter_dict: Query filter
        update_dict: Update operations (e.g., {"$set": {...}})
        upsert: Whether to insert if document doesn't exist
        session: Optional session for transactions

    Returns:
        Dict: Update result information

    Example:
        result = await update_one_async(
            db.users,
            {"email": "john@example.com"},
            {"$set": {"last_login": datetime.utcnow()}}
        )
    """
    try:
        result = await collection.update_one(
            filter_dict, update_dict, upsert=upsert, session=session
        )

        update_info = {
            "matched_count": result.matched_count,
            "modified_count": result.modified_count,
            "upserted_id": str(result.upserted_id) if result.upserted_id else None,
        }

        logger.debug(f"Update completed: {update_info}")
        return update_info

    except PyMongoError as e:
        logger.error(f"MongoDB update_one error: {e}")
        logger.error(f"Filter: {filter_dict}")
        logger.error(f"Update: {update_dict}")
        raise
    except Exception as e:
        logger.error(f"Unexpected update_one error: {e}")
        raise


async def update_many_async(
    collection: AsyncIOMotorCollection,
    filter_dict: Dict[str, Any],
    update_dict: Dict[str, Any],
    upsert: bool = False,
    session: Optional[AsyncIOMotorClientSession] = None,
) -> Dict[str, Any]:
    """
    Update multiple documents in a collection.

    Args:
        collection: MongoDB collection
        filter_dict: Query filter
        update_dict: Update operations
        upsert: Whether to insert if no documents match
        session: Optional session for transactions

    Returns:
        Dict: Update result information

    Example:
        result = await update_many_async(
            db.users,
            {"active": False},
            {"$set": {"status": "inactive"}}
        )
    """
    try:
        result = await collection.update_many(
            filter_dict, update_dict, upsert=upsert, session=session
        )

        update_info = {
            "matched_count": result.matched_count,
            "modified_count": result.modified_count,
            "upserted_id": str(result.upserted_id) if result.upserted_id else None,
        }

        logger.debug(f"Batch update completed: {update_info}")
        return update_info

    except PyMongoError as e:
        logger.error(f"MongoDB update_many error: {e}")
        logger.error(f"Filter: {filter_dict}")
        logger.error(f"Update: {update_dict}")
        raise
    except Exception as e:
        logger.error(f"Unexpected update_many error: {e}")
        raise


async def delete_one_async(
    collection: AsyncIOMotorCollection,
    filter_dict: Dict[str, Any],
    session: Optional[AsyncIOMotorClientSession] = None,
) -> bool:
    """
    Delete a single document from a collection.

    Args:
        collection: MongoDB collection
        filter_dict: Query filter
        session: Optional session for transactions

    Returns:
        bool: True if a document was deleted, False otherwise

    Example:
        deleted = await delete_one_async(
            db.users,
            {"email": "john@example.com"}
        )
    """
    try:
        result = await collection.delete_one(filter_dict, session=session)
        logger.debug(f"Delete completed: {result.deleted_count} documents deleted")
        return result.deleted_count > 0

    except PyMongoError as e:
        logger.error(f"MongoDB delete_one error: {e}")
        logger.error(f"Filter: {filter_dict}")
        raise
    except Exception as e:
        logger.error(f"Unexpected delete_one error: {e}")
        raise


async def delete_many_async(
    collection: AsyncIOMotorCollection,
    filter_dict: Dict[str, Any],
    session: Optional[AsyncIOMotorClientSession] = None,
) -> int:
    """
    Delete multiple documents from a collection.

    Args:
        collection: MongoDB collection
        filter_dict: Query filter
        session: Optional session for transactions

    Returns:
        int: Number of documents deleted

    Example:
        count = await delete_many_async(
            db.users,
            {"active": False}
        )
    """
    try:
        result = await collection.delete_many(filter_dict, session=session)
        logger.debug(
            f"Batch delete completed: {result.deleted_count} documents deleted"
        )
        return result.deleted_count

    except PyMongoError as e:
        logger.error(f"MongoDB delete_many error: {e}")
        logger.error(f"Filter: {filter_dict}")
        raise
    except Exception as e:
        logger.error(f"Unexpected delete_many error: {e}")
        raise


async def count_documents_async(
    collection: AsyncIOMotorCollection,
    filter_dict: Dict[str, Any] = None,
    session: Optional[AsyncIOMotorClientSession] = None,
) -> int:
    """
    Count documents in a collection.

    Args:
        collection: MongoDB collection
        filter_dict: Query filter (empty dict for all documents)
        session: Optional session for transactions

    Returns:
        int: Number of documents matching the filter

    Example:
        count = await count_documents_async(
            db.users,
            {"active": True}
        )
    """
    try:
        count = await collection.count_documents(filter_dict or {}, session=session)
        return count

    except PyMongoError as e:
        logger.error(f"MongoDB count error: {e}")
        logger.error(f"Filter: {filter_dict}")
        raise
    except Exception as e:
        logger.error(f"Unexpected count error: {e}")
        raise


async def aggregate_async(
    collection: AsyncIOMotorCollection,
    pipeline: List[Dict[str, Any]],
    session: Optional[AsyncIOMotorClientSession] = None,
) -> List[Dict[str, Any]]:
    """
    Run an aggregation pipeline on a collection.

    Args:
        collection: MongoDB collection
        pipeline: Aggregation pipeline stages
        session: Optional session for transactions

    Returns:
        List[Dict]: Aggregation results

    Example:
        results = await aggregate_async(
            db.users,
            [
                {"$match": {"active": True}},
                {"$group": {"_id": "$role", "count": {"$sum": 1}}}
            ]
        )
    """
    try:
        cursor = collection.aggregate(pipeline, session=session)
        results = await cursor.to_list(length=None)
        return [dict(doc) for doc in results]

    except PyMongoError as e:
        logger.error(f"MongoDB aggregation error: {e}")
        logger.error(f"Pipeline: {pipeline}")
        raise
    except Exception as e:
        logger.error(f"Unexpected aggregation error: {e}")
        raise


# Utility functions for common operations
async def find_by_id_async(
    collection: AsyncIOMotorCollection,
    document_id: str,
    session: Optional[AsyncIOMotorClientSession] = None,
) -> Optional[Dict[str, Any]]:
    """Find a document by its _id field."""
    from bson import ObjectId

    try:
        object_id = ObjectId(document_id)
        return await find_one_async(collection, {"_id": object_id}, session=session)
    except Exception:
        # If not a valid ObjectId, try as string
        return await find_one_async(collection, {"_id": document_id}, session=session)


async def upsert_async(
    collection: AsyncIOMotorCollection,
    filter_dict: Dict[str, Any],
    document: Dict[str, Any],
    session: Optional[AsyncIOMotorClientSession] = None,
) -> Dict[str, Any]:
    """Insert or update a document (upsert operation)."""
    return await update_one_async(
        collection, filter_dict, {"$set": document}, upsert=True, session=session
    )
