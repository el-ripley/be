"""Qdrant repository for playbook vectors."""

from typing import Any, Dict, List, Optional

from qdrant_client import models as qdrant_models

from src.database.qdrant.connection import (
    PLAYBOOKS_COLLECTION,
    get_qdrant_client,
)


async def upsert_playbook(
    playbook_id: str,
    situation_vec: List[float],
    payload: Dict[str, Any],
) -> None:
    """Upsert a playbook point with one vector (title + situation embedding)."""

    client = await get_qdrant_client()
    point = qdrant_models.PointStruct(
        id=playbook_id,
        vector=situation_vec,
        payload=payload,
    )
    await client.upsert(collection_name=PLAYBOOKS_COLLECTION, points=[point])


async def delete_playbook(playbook_id: str) -> None:
    """Delete a playbook point by ID."""

    client = await get_qdrant_client()
    await client.delete(
        collection_name=PLAYBOOKS_COLLECTION,
        points_selector=qdrant_models.PointIdsList(points=[playbook_id]),
    )


async def search_playbooks(
    query_vec: List[float],
    playbook_ids: Optional[List[str]] = None,
    limit: int = 3,
    score_threshold: Optional[float] = 0.5,
) -> List[Dict[str, Any]]:
    """
    Search playbooks by vector similarity (situation: title + situation).

    Args:
        query_vec: Query embedding vector.
        playbook_ids: Optional list of playbook UUIDs to filter results.
        limit: Max results (default 3).
        score_threshold: Min similarity score (default 0.5). Results below are excluded.
            Pass None to disable filtering.

    Returns:
        List of dicts with playbook_id, title, situation, content, score, tags.
    """
    client = await get_qdrant_client()

    query_filter = None
    if playbook_ids:
        query_filter = qdrant_models.Filter(
            must=[qdrant_models.HasIdCondition(has_id=playbook_ids)]
        )

    # Request more when filtering by score so we can return up to limit after threshold
    fetch_limit = limit * 3 if score_threshold is not None else limit

    response = await client.query_points(
        collection_name=PLAYBOOKS_COLLECTION,
        query=query_vec,
        query_filter=query_filter,
        limit=fetch_limit,
    )

    results = []
    for p in response.points:
        if len(results) >= limit:
            break
        point_id = str(p.id) if hasattr(p.id, "uuid") else p.id
        payload = p.payload or {}
        score = getattr(p, "score", 0.0)
        if score_threshold is not None and float(score) < score_threshold:
            continue
        results.append(
            {
                "playbook_id": point_id,
                "title": payload.get("title", ""),
                "situation": payload.get("situation", ""),
                "content": payload.get("content", ""),
                "score": float(score),
                "tags": payload.get("tags") or [],
            }
        )
    return results
