"""Qdrant client connection and collection setup."""

import asyncio
from typing import Optional

from qdrant_client import AsyncQdrantClient
from qdrant_client import models as qdrant_models

from src.settings import settings
from src.utils.logger import get_logger

logger = get_logger()

PLAYBOOKS_COLLECTION = "page_playbooks"
VECTOR_DIM = 3072  # text-embedding-3-large

_qdrant_client: Optional[AsyncQdrantClient] = None
_client_lock = asyncio.Lock()


async def get_qdrant_client() -> AsyncQdrantClient:
    """Get or create the singleton async Qdrant client."""
    global _qdrant_client

    if _qdrant_client is None:
        async with _client_lock:
            if _qdrant_client is None:
                _qdrant_client = AsyncQdrantClient(
                    host=settings.qdrant_host,
                    port=settings.qdrant_port_rest,
                )
                logger.info("Qdrant client created successfully")

    return _qdrant_client


async def ensure_playbooks_collection() -> None:
    """Idempotently create the page_playbooks collection with a single vector (title + situation)."""
    client = await get_qdrant_client()

    if await client.collection_exists(PLAYBOOKS_COLLECTION):
        return
    await client.create_collection(
        collection_name=PLAYBOOKS_COLLECTION,
        vectors_config=qdrant_models.VectorParams(
            size=VECTOR_DIM,
            distance=qdrant_models.Distance.COSINE,
        ),
    )
    logger.info(f"Created Qdrant collection {PLAYBOOKS_COLLECTION}")
