"""Playbook sync service — orchestrates Postgres, embedding client, Qdrant, and billing."""

from typing import Any, Dict, List, Optional

import asyncpg

from src.common.clients.openai_embedding_client import (
    EMBEDDING_MODEL,
    embed_texts,
)
from src.database.postgres.repositories.agent_queries.agent_responses import (
    insert_openai_response_with_agent,
    update_agent_response_aggregates,
)
from src.database.postgres.utils import generate_uuid, get_current_timestamp
from src.database.qdrant.playbook_repository import (
    delete_playbook as qdrant_delete_playbook,
)
from src.database.qdrant.playbook_repository import (
    search_playbooks as qdrant_search_playbooks,
)
from src.database.qdrant.playbook_repository import upsert_playbook as qdrant_upsert_playbook
from src.utils.logger import get_logger

logger = get_logger()


async def _log_embedding_usage(
    conn: asyncpg.Connection,
    user_id: str,
    agent_response_id: Optional[str],
    total_tokens: int,
    conversation_id: Optional[str] = None,
    branch_id: Optional[str] = None,
) -> None:
    """Log embedding API usage to openai_response for billing aggregation."""
    ar_id = agent_response_id if agent_response_id else None
    response_data = {
        "id": str(generate_uuid()),
        "created": get_current_timestamp(),
        "usage": {
            "input_tokens": total_tokens,
            "output_tokens": 0,
            "total_tokens": total_tokens,
        },
        "output": [],
    }
    await insert_openai_response_with_agent(
        conn=conn,
        user_id=user_id,
        conversation_id=conversation_id,
        branch_id=branch_id,
        agent_response_id=ar_id,
        response_data=response_data,
        input_messages=[],
        tools=[],
        model=EMBEDDING_MODEL,
        metadata={"type": "embedding"},
    )


async def create_playbook(
    conn: asyncpg.Connection,
    title: str,
    situation: str,
    content: str,
    tags: Optional[List[str]],
    user_id: str,
    agent_response_id: str,
    conversation_id: Optional[str] = None,
    branch_id: Optional[str] = None,
) -> str:
    """
    Create a playbook in Postgres, embed text, upsert to Qdrant, and log usage.

    Returns:
        playbook_id (UUID string)
    """
    ts = get_current_timestamp()
    row = await conn.fetchrow(
        """
        INSERT INTO page_playbooks (owner_user_id, title, situation, content, tags, created_by_type, created_at, updated_at)
        VALUES ($1, $2, $3, $4, $5, 'agent', $6, $6)
        RETURNING id
        """,
        user_id,
        title,
        situation,
        content,
        tags or [],
        ts,
    )
    playbook_id = str(row["id"])

    text_to_embed = f"{title}\n{situation}"
    result = await embed_texts([text_to_embed])
    situation_vec = result.vectors[0]

    await _log_embedding_usage(
        conn=conn,
        user_id=user_id,
        agent_response_id=agent_response_id,
        total_tokens=result.total_tokens,
        conversation_id=conversation_id,
        branch_id=branch_id,
    )
    await update_agent_response_aggregates(conn, agent_response_id)

    payload: Dict[str, Any] = {
        "title": title,
        "situation": situation,
        "content": content,
        "tags": tags or [],
        "owner_user_id": user_id,
        "embedding_model": EMBEDDING_MODEL,
    }
    try:
        await qdrant_upsert_playbook(
            playbook_id=playbook_id,
            situation_vec=situation_vec,
            payload=payload,
        )
    except Exception as e:
        logger.exception("Qdrant upsert failed for playbook %s: %s", playbook_id, e)
        await conn.execute(
            "UPDATE page_playbooks SET embedding_model = NULL, updated_at = $1 WHERE id = $2",
            ts,
            playbook_id,
        )
        raise

    await conn.execute(
        "UPDATE page_playbooks SET embedding_model = $1, updated_at = $2 WHERE id = $3",
        EMBEDDING_MODEL,
        ts,
        playbook_id,
    )

    return playbook_id


async def update_playbook(
    conn: asyncpg.Connection,
    playbook_id: str,
    user_id: str,
    agent_response_id: str,
    title: Optional[str] = None,
    situation: Optional[str] = None,
    content: Optional[str] = None,
    tags: Optional[List[str]] = None,
    conversation_id: Optional[str] = None,
    branch_id: Optional[str] = None,
) -> List[str]:
    """
    Update a playbook. Only provided (non-None) fields are changed.
    If title/situation/content change, re-embed and re-upsert to Qdrant.

    Returns:
        List of updated field names.
    """
    row = await conn.fetchrow(
        "SELECT title, situation, content, tags FROM page_playbooks WHERE id = $1 AND deleted_at IS NULL",
        playbook_id,
    )
    if not row:
        raise ValueError(f"Playbook not found: {playbook_id}")

    updated_fields: List[str] = []
    new_title = title if title is not None else row["title"]
    new_situation = situation if situation is not None else row["situation"]
    new_content = content if content is not None else row["content"]
    new_tags = tags if tags is not None else row["tags"] or []

    if title is not None:
        updated_fields.append("title")
    if situation is not None:
        updated_fields.append("situation")
    if content is not None:
        updated_fields.append("content")
    if tags is not None:
        updated_fields.append("tags")

    ts = get_current_timestamp()
    await conn.execute(
        """
        UPDATE page_playbooks
        SET title = $1, situation = $2, content = $3, tags = $4, updated_at = $5
        WHERE id = $6
        """,
        new_title,
        new_situation,
        new_content,
        new_tags,
        ts,
        playbook_id,
    )

    needs_reembed = title is not None or situation is not None or content is not None
    if needs_reembed:
        text_to_embed = f"{new_title}\n{new_situation}"
        result = await embed_texts([text_to_embed])
        situation_vec = result.vectors[0]

        await _log_embedding_usage(
            conn=conn,
            user_id=user_id,
            agent_response_id=agent_response_id,
            total_tokens=result.total_tokens,
            conversation_id=conversation_id,
            branch_id=branch_id,
        )
        await update_agent_response_aggregates(conn, agent_response_id)

        payload: Dict[str, Any] = {
            "title": new_title,
            "situation": new_situation,
            "content": new_content,
            "tags": new_tags,
            "owner_user_id": user_id,
            "embedding_model": EMBEDDING_MODEL,
        }
        try:
            await qdrant_upsert_playbook(
                playbook_id=playbook_id,
                situation_vec=situation_vec,
                payload=payload,
            )
        except Exception as e:
            logger.exception("Qdrant upsert failed for playbook %s: %s", playbook_id, e)
            await conn.execute(
                "UPDATE page_playbooks SET embedding_model = NULL WHERE id = $1",
                playbook_id,
            )
            raise
        await conn.execute(
            "UPDATE page_playbooks SET embedding_model = $1 WHERE id = $2",
            EMBEDDING_MODEL,
            playbook_id,
        )

    return updated_fields


async def delete_playbook(
    conn: asyncpg.Connection,
    playbook_id: str,
) -> None:
    """Soft-delete playbook in Postgres and remove point from Qdrant."""
    ts = get_current_timestamp()
    await conn.execute(
        "UPDATE page_playbooks SET deleted_at = $1, updated_at = $1 WHERE id = $2",
        ts,
        playbook_id,
    )
    try:
        await qdrant_delete_playbook(playbook_id)
    except Exception as e:
        logger.exception("Qdrant delete failed for playbook %s: %s", playbook_id, e)
        raise


async def search_playbooks(
    conn: asyncpg.Connection,
    query_text: str,
    user_id: str,
    agent_response_id: str,
    playbook_ids: Optional[List[str]] = None,
    limit: int = 3,
    conversation_id: Optional[str] = None,
    branch_id: Optional[str] = None,
) -> List[Dict[str, Any]]:
    """
    Embed query, search Qdrant by situation (title+situation), log usage.

    Returns:
        List of dicts with playbook_id, title, situation, content, score, tags.
    """
    result = await embed_texts([query_text])
    query_vec = result.vectors[0]

    await _log_embedding_usage(
        conn=conn,
        user_id=user_id,
        agent_response_id=agent_response_id,
        total_tokens=result.total_tokens,
        conversation_id=conversation_id,
        branch_id=branch_id,
    )
    await update_agent_response_aggregates(conn, agent_response_id)

    try:
        return await qdrant_search_playbooks(
            query_vec=query_vec,
            playbook_ids=playbook_ids,
            limit=min(limit, 10),
        )
    except Exception as e:
        logger.exception("Qdrant search failed: %s", e)
        raise
