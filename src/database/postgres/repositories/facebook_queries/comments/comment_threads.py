import json
from typing import Optional, Dict, Any, List

import asyncpg

from src.database.postgres.executor import (
    execute_async_single,
    execute_async_query,
)
from src.database.postgres.utils import get_current_timestamp


def _media_is_active(media: Dict[str, Any]) -> bool:
    if not media:
        return False
    status = media.get("status")
    expires_at = media.get("expires_at")
    if status != "ready":
        return False
    if expires_at is None:
        return True
    try:
        return int(expires_at) > get_current_timestamp()
    except (TypeError, ValueError):
        return False


async def get_comments_by_ids(
    conn: asyncpg.Connection, comment_ids: List[str]
) -> Dict[str, Dict[str, Any]]:
    """Fetch multiple comments with author metadata."""
    if not comment_ids:
        return {}

    query = """
        SELECT
            c.*,
            CASE WHEN c.is_from_page THEN 'page' ELSE 'user' END AS author_kind,
            fpsu.id AS fpsu_id,
            fpsu.user_info->>'name' AS fpsu_name,
            fpsu.user_info->>'profile_pic' AS fpsu_profile_pic,
            fp.name as page_name,
            fp.avatar as page_avatar,
            fp.category as page_category,
            p.message as post_message
        FROM comments c
        LEFT JOIN facebook_page_scope_users fpsu ON c.facebook_page_scope_user_id = fpsu.id
        JOIN fan_pages fp ON c.fan_page_id = fp.id
        JOIN posts p ON c.post_id = p.id
        WHERE c.id = ANY($1)
    """

    rows = await execute_async_query(conn, query, comment_ids)
    return {row["id"]: row for row in rows}


async def get_comment_tree_structure(
    conn: asyncpg.Connection, root_comment_id: str
) -> Dict[str, Any]:
    """Get comment tree structure starting from a root comment."""
    root_comment = await execute_async_single(
        conn, "SELECT * FROM comments WHERE id = $1", root_comment_id
    )

    if not root_comment:
        return {"root_comment": None, "comment_tree": []}

    tree_query = """
        WITH RECURSIVE comment_tree AS (
            SELECT c.*, 0 as depth, ARRAY[c.id]::text[] as path
            FROM comments c
            WHERE c.id = $1 AND c.deleted_at IS NULL
            UNION ALL
            SELECT child.*, ct.depth + 1 as depth, ct.path || child.id::text as path
            FROM comments child
            JOIN comment_tree ct ON child.parent_comment_id = ct.id
            WHERE child.deleted_at IS NULL
              AND child.id::text <> ALL(ct.path)
              AND ct.depth < 50
        )
        SELECT
            ct.*,
            CASE WHEN ct.is_from_page THEN 'page' ELSE 'user' END AS author_kind,
            fpsu.id AS fpsu_id,
            fpsu.user_info->>'name' AS fpsu_name,
            fpsu.user_info->>'profile_pic' AS fpsu_profile_pic,
            fp.name as page_name,
            fp.avatar as page_avatar,
            fp.category as page_category
        FROM comment_tree ct
        LEFT JOIN facebook_page_scope_users fpsu ON ct.facebook_page_scope_user_id = fpsu.id
        JOIN fan_pages fp ON ct.fan_page_id = fp.id
        ORDER BY ct.depth, ct.facebook_created_time ASC
    """

    tree_comments = await execute_async_query(conn, tree_query, root_comment_id)
    comment_tree = []
    actual_root_comment = None

    for comment in tree_comments:
        if comment["depth"] == 0:
            actual_root_comment = comment
        else:
            comment_tree.append(comment)

    return {
        "root_comment": actual_root_comment or root_comment,
        "comment_tree": comment_tree,
    }


async def get_root_comments_with_latest_replies(
    conn: asyncpg.Connection, page_ids: List[str], page: int = 1, page_size: int = 20
) -> Dict[str, Any]:
    """
    Get root comments with latest replies using facebook_conversation_comments table.
    Root comments are identified via the conversation entries table.
    """
    if not page_ids:
        return {"items": [], "total": 0}

    count_query = """
        SELECT COUNT(*) as total
        FROM facebook_conversation_comments fcc
        WHERE fcc.fan_page_id = ANY($1)
    """
    count_result = await execute_async_single(conn, count_query, page_ids)
    total_count = count_result["total"] if count_result else 0
    offset = (page - 1) * page_size

    query = """
        WITH conversations AS (
            SELECT
                fcc.id as conversation_id,
                fcc.root_comment_id,
                fcc.latest_comment_id,
                fcc.latest_comment_facebook_time,
                fcc.latest_comment_is_from_page,
                fcc.participant_scope_users,
                fcc.has_page_reply,
                fcc.mark_as_read,
                fcc.page_last_seen_comment_id,
                fcc.page_last_seen_at,
                fcc.updated_at as conv_updated_at
            FROM facebook_conversation_comments fcc
            WHERE fcc.fan_page_id = ANY($1)
        ),
        root_comments AS (
            SELECT
                c.id,
                c.post_id,
                c.fan_page_id,
                c.parent_comment_id,
                c.is_from_page,
                c.facebook_page_scope_user_id,
                c.message,
                c.photo_url,
                c.video_url,
                c.facebook_created_time,
                c.like_count,
                c.reply_count,
                c.reactions_fetched_at,
                c.is_hidden,
                c.page_seen_at,
                c.deleted_at,
                c.created_at,
                c.updated_at,
                CASE WHEN c.is_from_page THEN 'page' ELSE 'user' END AS author_kind,
                fpsu.id AS fpsu_id,
                fpsu.user_info->>'name' AS fpsu_name,
                fpsu.user_info->>'profile_pic' AS fpsu_profile_pic,
                fp.name as page_name,
                fp.avatar as page_avatar,
                fp.category as page_category,
                p.message as post_message,
                p.reaction_total_count as post_reaction_total_count,
                p.reaction_like_count as post_reaction_like_count,
                p.reaction_love_count as post_reaction_love_count,
                p.reaction_haha_count as post_reaction_haha_count,
                p.reaction_wow_count as post_reaction_wow_count,
                p.reaction_sad_count as post_reaction_sad_count,
                p.reaction_angry_count as post_reaction_angry_count,
                p.reaction_care_count as post_reaction_care_count,
                p.share_count as post_share_count,
                p.comment_count as post_comment_count,
                p.full_picture as post_full_picture,
                p.permalink_url as post_permalink_url,
                p.status_type as post_status_type,
                p.is_published as post_is_published,
                p.reactions_fetched_at as post_reactions_fetched_at,
                p.engagement_fetched_at as post_engagement_fetched_at,
                conv.conversation_id,
                conv.latest_comment_id as conv_latest_comment_id,
                conv.participant_scope_users,
                conv.conv_updated_at
            FROM conversations conv
            JOIN comments c ON c.id = conv.root_comment_id
            LEFT JOIN facebook_page_scope_users fpsu ON c.facebook_page_scope_user_id = fpsu.id
            JOIN fan_pages fp ON c.fan_page_id = fp.id
            JOIN posts p ON c.post_id = p.id
        ),
        latest_replies AS (
            SELECT
                c.id,
                c.post_id,
                c.fan_page_id,
                c.parent_comment_id,
                c.is_from_page,
                c.facebook_page_scope_user_id,
                c.message,
                c.photo_url,
                c.video_url,
                c.facebook_created_time,
                c.like_count,
                c.reply_count,
                c.reactions_fetched_at,
                c.is_hidden,
                c.page_seen_at,
                c.deleted_at,
                c.created_at,
                c.updated_at,
                CASE WHEN c.is_from_page THEN 'page' ELSE 'user' END AS author_kind,
                fpsu.id AS fpsu_id,
                fpsu.user_info->>'name' AS fpsu_name,
                fpsu.user_info->>'profile_pic' AS fpsu_profile_pic,
                fp.name as page_name,
                fp.avatar as page_avatar,
                fp.category as page_category,
                fcc.root_comment_id
            FROM facebook_conversation_comments fcc
            JOIN comments c ON c.id = fcc.latest_comment_id
            LEFT JOIN facebook_page_scope_users fpsu ON c.facebook_page_scope_user_id = fpsu.id
            JOIN fan_pages fp ON c.fan_page_id = fp.id
            WHERE fcc.fan_page_id = ANY($1)
            AND fcc.latest_comment_id IS NOT NULL
            AND fcc.latest_comment_id != fcc.root_comment_id
        ),
        thread_commenters AS (
            SELECT
                fcc.root_comment_id as thread_root_id,
                c.is_from_page,
                c.facebook_page_scope_user_id,
                fpsu.id AS fpsu_id,
                fpsu.user_info->>'name' AS fpsu_name,
                fpsu.user_info->>'profile_pic' AS fpsu_profile_pic,
                CASE WHEN c.is_from_page THEN 'page' ELSE 'user' END AS author_kind
            FROM facebook_conversation_comments fcc
            JOIN facebook_conversation_comment_entries fcce ON fcce.conversation_id = fcc.id
            JOIN comments c ON c.id = fcce.comment_id
            LEFT JOIN facebook_page_scope_users fpsu ON c.facebook_page_scope_user_id = fpsu.id
            WHERE fcc.fan_page_id = ANY($1)
            GROUP BY
                fcc.root_comment_id,
                c.is_from_page,
                c.facebook_page_scope_user_id,
                fpsu.id,
                fpsu.user_info->>'name',
                fpsu.user_info->>'profile_pic'
        )
        SELECT
            rc.*,
            lr.id as latest_comment_id,
            lr.post_id as latest_comment_post_id,
            lr.fan_page_id as latest_comment_fan_page_id,
            lr.parent_comment_id as latest_comment_parent_comment_id,
            lr.is_from_page as latest_comment_is_from_page,
            lr.facebook_page_scope_user_id as latest_comment_facebook_page_scope_user_id,
            lr.message as latest_comment_message,
            lr.photo_url as latest_comment_photo_url,
            lr.video_url as latest_comment_video_url,
            lr.facebook_created_time as latest_comment_facebook_created_time,
            lr.like_count as latest_comment_like_count,
            lr.reply_count as latest_comment_reply_count,
            lr.reactions_fetched_at as latest_comment_reactions_fetched_at,
            lr.is_hidden as latest_comment_is_hidden,
            lr.page_seen_at as latest_comment_page_seen_at,
            lr.deleted_at as latest_comment_deleted_at,
            lr.created_at as latest_comment_created_at,
            lr.updated_at as latest_comment_updated_at,
            lr.author_kind as latest_comment_author_kind,
            lr.fpsu_id as latest_comment_fpsu_id,
            lr.fpsu_name as latest_comment_fpsu_name,
            lr.fpsu_profile_pic as latest_comment_fpsu_profile_pic,
            lr.page_name as latest_comment_page_name,
            lr.page_avatar as latest_comment_page_avatar,
            lr.page_category as latest_comment_page_category,
            COALESCE(
                JSON_AGG(
                    JSON_BUILD_OBJECT(
                        'fpsu_id', tc.fpsu_id,
                        'fpsu_name', tc.fpsu_name,
                        'fpsu_profile_pic', tc.fpsu_profile_pic,
                        'author_kind', tc.author_kind
                    )
                    ORDER BY tc.author_kind, tc.fpsu_name
                ) FILTER (WHERE tc.thread_root_id IS NOT NULL),
                '[]'::json
            ) as thread_commenters
        FROM root_comments rc
        LEFT JOIN latest_replies lr ON rc.id = lr.root_comment_id
        LEFT JOIN thread_commenters tc ON rc.id = tc.thread_root_id
        GROUP BY
            rc.id, rc.post_id, rc.fan_page_id, rc.parent_comment_id,
            rc.is_from_page, rc.facebook_page_scope_user_id, rc.message, rc.photo_url,
            rc.video_url, rc.facebook_created_time, rc.like_count, rc.reply_count,
            rc.reactions_fetched_at, rc.is_hidden, rc.page_seen_at,
            rc.deleted_at, rc.created_at, rc.updated_at, rc.author_kind, rc.fpsu_id,
            rc.fpsu_name, rc.fpsu_profile_pic, rc.page_name, rc.page_avatar,
            rc.page_category, rc.post_message, rc.post_reaction_total_count,
            rc.post_reaction_like_count, rc.post_reaction_love_count,
            rc.post_reaction_haha_count, rc.post_reaction_wow_count,
            rc.post_reaction_sad_count, rc.post_reaction_angry_count,
            rc.post_reaction_care_count, rc.post_share_count, rc.post_comment_count,
            rc.post_full_picture, rc.post_permalink_url, rc.post_status_type,
            rc.post_is_published, rc.post_reactions_fetched_at,
            rc.post_engagement_fetched_at, rc.conversation_id, rc.conv_latest_comment_id,
            rc.participant_scope_users, rc.conv_updated_at,
            lr.id, lr.post_id, lr.fan_page_id, lr.parent_comment_id, lr.is_from_page,
            lr.facebook_page_scope_user_id, lr.message, lr.photo_url, lr.video_url,
            lr.facebook_created_time, lr.like_count, lr.reply_count,
            lr.reactions_fetched_at, lr.is_hidden, lr.page_seen_at, lr.deleted_at,
            lr.created_at, lr.updated_at, lr.author_kind, lr.fpsu_id, lr.fpsu_name,
            lr.fpsu_profile_pic, lr.page_name, lr.page_avatar, lr.page_category
        ORDER BY
            rc.conv_updated_at DESC
        LIMIT $2 OFFSET $3
    """

    results = await execute_async_query(conn, query, page_ids, page_size, offset)
    formatted_results = []
    for row in results:
        root_comment = {
            "id": row["id"],
            "post_id": row["post_id"],
            "fan_page_id": row["fan_page_id"],
            "parent_comment_id": row["parent_comment_id"],
            "is_from_page": row["is_from_page"],
            "facebook_page_scope_user_id": row["facebook_page_scope_user_id"],
            "message": row["message"],
            "photo_url": row["photo_url"],
            "video_url": row["video_url"],
            "facebook_created_time": row["facebook_created_time"],
            "like_count": row.get("like_count", 0),
            "reply_count": row.get("reply_count", 0),
            "reactions_fetched_at": row.get("reactions_fetched_at"),
            "is_hidden": row["is_hidden"],
            "page_seen_at": row["page_seen_at"],
            "deleted_at": row["deleted_at"],
            "created_at": row["created_at"],
            "updated_at": row["updated_at"],
            "author_kind": row["author_kind"],
            "fpsu_id": row["fpsu_id"],
            "fpsu_name": row["fpsu_name"],
            "fpsu_profile_pic": row["fpsu_profile_pic"],
            "page_name": row["page_name"],
            "page_avatar": row["page_avatar"],
            "page_category": row["page_category"],
            "post_message": row["post_message"],
            "post_reaction_total_count": row.get("post_reaction_total_count", 0),
            "post_reaction_like_count": row.get("post_reaction_like_count", 0),
            "post_reaction_love_count": row.get("post_reaction_love_count", 0),
            "post_reaction_haha_count": row.get("post_reaction_haha_count", 0),
            "post_reaction_wow_count": row.get("post_reaction_wow_count", 0),
            "post_reaction_sad_count": row.get("post_reaction_sad_count", 0),
            "post_reaction_angry_count": row.get("post_reaction_angry_count", 0),
            "post_reaction_care_count": row.get("post_reaction_care_count", 0),
            "post_share_count": row.get("post_share_count", 0),
            "post_comment_count": row.get("post_comment_count", 0),
            "post_full_picture": row.get("post_full_picture"),
            "post_permalink_url": row.get("post_permalink_url"),
            "post_status_type": row.get("post_status_type"),
            "post_is_published": row.get("post_is_published", True),
            "post_reactions_fetched_at": row.get("post_reactions_fetched_at"),
            "post_engagement_fetched_at": row.get("post_engagement_fetched_at"),
        }

        latest_comment = None
        if row["latest_comment_id"]:
            latest_comment = {
                "id": row["latest_comment_id"],
                "post_id": row["latest_comment_post_id"],
                "fan_page_id": row["latest_comment_fan_page_id"],
                "parent_comment_id": row["latest_comment_parent_comment_id"],
                "is_from_page": row["latest_comment_is_from_page"],
                "facebook_page_scope_user_id": row[
                    "latest_comment_facebook_page_scope_user_id"
                ],
                "message": row["latest_comment_message"],
                "photo_url": row["latest_comment_photo_url"],
                "video_url": row["latest_comment_video_url"],
                "facebook_created_time": row["latest_comment_facebook_created_time"],
                "like_count": row.get("latest_comment_like_count", 0),
                "reply_count": row.get("latest_comment_reply_count", 0),
                "reactions_fetched_at": row.get("latest_comment_reactions_fetched_at"),
                "is_hidden": row["latest_comment_is_hidden"],
                "page_seen_at": row["latest_comment_page_seen_at"],
                "deleted_at": row["latest_comment_deleted_at"],
                "created_at": row["latest_comment_created_at"],
                "updated_at": row["latest_comment_updated_at"],
                "author_kind": row["latest_comment_author_kind"],
                "fpsu_id": row["latest_comment_fpsu_id"],
                "fpsu_name": row["latest_comment_fpsu_name"],
                "fpsu_profile_pic": row["latest_comment_fpsu_profile_pic"],
                "page_name": row["latest_comment_page_name"],
                "page_avatar": row["latest_comment_page_avatar"],
                "page_category": row["latest_comment_page_category"],
            }

        commenters = []
        if row["thread_commenters"]:
            if isinstance(row["thread_commenters"], str):
                commenters = json.loads(row["thread_commenters"])
            else:
                commenters = row["thread_commenters"]

        formatted_results.append(
            {
                "root_comment": root_comment,
                "latest_comment": latest_comment,
                "commenters": commenters,
            }
        )

    return {"items": formatted_results, "total": total_count}


async def get_comments_by_root_comment_id(
    conn: asyncpg.Connection, root_comment_id: str
) -> List[Dict[str, Any]]:
    """Get all comments in a thread by root comment ID (with media info).
    Uses facebook_conversation_comment_entries to find all comments in the conversation.
    """
    query = """
        SELECT
            c.id,
            c.post_id,
            c.fan_page_id,
            c.parent_comment_id,
            c.is_from_page,
            c.facebook_page_scope_user_id,
            c.message,
            c.photo_url,
            c.video_url,
            c.facebook_created_time,
            c.like_count,
            c.reply_count,
            c.reactions_fetched_at,
            c.is_hidden,
            c.page_seen_at,
            c.deleted_at,
            c.metadata,
            c.created_at,
            c.updated_at,
            CASE WHEN c.is_from_page THEN 'page' ELSE 'user' END AS author_kind,
            fpsu.id AS fpsu_id,
            fpsu.user_info->>'name' AS fpsu_name,
            fpsu.user_info->>'profile_pic' AS fpsu_profile_pic,
            comment_asset.id AS comment_photo_media_id,
            comment_asset.s3_url AS comment_photo_s3_url,
            comment_asset.status AS comment_photo_s3_status,
            comment_asset.retention_policy AS comment_photo_retention_policy,
            comment_asset.expires_at AS comment_photo_expires_at,
            fpsu_avatar_asset.id AS fpsu_avatar_media_id,
            fpsu_avatar_asset.s3_url AS fpsu_avatar_s3_url,
            fpsu_avatar_asset.status AS fpsu_avatar_s3_status,
            fpsu_avatar_asset.retention_policy AS fpsu_avatar_retention_policy,
            fpsu_avatar_asset.expires_at AS fpsu_avatar_expires_at
        FROM facebook_conversation_comments fcc
        JOIN facebook_conversation_comment_entries fcce ON fcce.conversation_id = fcc.id
        JOIN comments c ON c.id = fcce.comment_id
        LEFT JOIN facebook_page_scope_users fpsu ON c.facebook_page_scope_user_id = fpsu.id
        LEFT JOIN media_assets comment_asset
            ON comment_asset.source_type = 'facebook_mirror'
           AND comment_asset.fb_owner_type = 'comment'
           AND comment_asset.fb_owner_id::text = c.id::text
           AND comment_asset.fb_field_name = 'photo_url'
        LEFT JOIN media_assets fpsu_avatar_asset
            ON fpsu_avatar_asset.source_type = 'facebook_mirror'
           AND fpsu_avatar_asset.fb_owner_type = 'page_scope_user'
           AND fpsu_avatar_asset.fb_owner_id::text = fpsu.id::text
           AND fpsu_avatar_asset.fb_field_name = 'profile_pic'
        WHERE fcc.root_comment_id = $1
        ORDER BY c.facebook_created_time ASC, c.created_at ASC
    """

    results = await execute_async_query(conn, query, root_comment_id)
    for comment in results:
        # Convert UUIDs to strings
        photo_media_id_raw = comment.get("comment_photo_media_id")
        photo_media_id = None
        if photo_media_id_raw is not None:
            photo_media_id = (
                str(photo_media_id_raw)
                if hasattr(photo_media_id_raw, "__str__")
                else photo_media_id_raw
            )

        avatar_media_id_raw = comment.get("fpsu_avatar_media_id")
        avatar_media_id = None
        if avatar_media_id_raw is not None:
            avatar_media_id = (
                str(avatar_media_id_raw)
                if hasattr(avatar_media_id_raw, "__str__")
                else avatar_media_id_raw
            )

        photo_media = {
            "id": photo_media_id,
            "s3_url": comment.get("comment_photo_s3_url"),
            "status": comment.get("comment_photo_s3_status"),
            "retention_policy": comment.get("comment_photo_retention_policy"),
            "expires_at": comment.get("comment_photo_expires_at"),
            "original_url": comment.get("photo_url"),
        }
        avatar_media = {
            "id": avatar_media_id,
            "s3_url": comment.get("fpsu_avatar_s3_url"),
            "status": comment.get("fpsu_avatar_s3_status"),
            "retention_policy": comment.get("fpsu_avatar_retention_policy"),
            "expires_at": comment.get("fpsu_avatar_expires_at"),
            "original_url": comment.get("fpsu_profile_pic"),
        }
        comment["photo_media"] = photo_media
        comment["fpsu_avatar_media"] = avatar_media
        if photo_media["s3_url"] and _media_is_active(photo_media):
            comment["photo_url"] = photo_media["s3_url"]
        for extra_key in (
            "comment_photo_media_id",
            "comment_photo_s3_url",
            "comment_photo_s3_status",
            "comment_photo_retention_policy",
            "comment_photo_expires_at",
            "fpsu_avatar_media_id",
            "fpsu_avatar_s3_url",
            "fpsu_avatar_s3_status",
            "fpsu_avatar_retention_policy",
            "fpsu_avatar_expires_at",
        ):
            comment.pop(extra_key, None)
    return results


async def get_page_info_by_root_comment_id(
    conn: asyncpg.Connection, root_comment_id: str
) -> Optional[Dict[str, Any]]:
    """Get page information for a root comment thread.
    Uses facebook_conversation_comments to find the page.
    """
    query = """
        SELECT DISTINCT
            fp.id,
            fp.name,
            fp.avatar,
            fp.category,
            fp.fan_count,
            fp.followers_count,
            fp.rating_count,
            fp.overall_star_rating,
            fp.about,
            fp.description,
            fp.link,
            fp.website,
            fp.phone,
            fp.emails,
            fp.location,
            fp.cover,
            fp.hours,
            fp.is_verified,
            fp.created_at,
            fp.updated_at,
            page_avatar_asset.id AS page_avatar_media_id,
            page_avatar_asset.s3_url AS page_avatar_s3_url,
            page_avatar_asset.status AS page_avatar_s3_status,
            page_avatar_asset.retention_policy AS page_avatar_retention_policy,
            page_avatar_asset.expires_at AS page_avatar_expires_at
        FROM facebook_conversation_comments fcc
        JOIN fan_pages fp ON fcc.fan_page_id = fp.id
        LEFT JOIN media_assets page_avatar_asset
            ON page_avatar_asset.source_type = 'facebook_mirror'
           AND page_avatar_asset.fb_owner_type = 'fan_page'
           AND page_avatar_asset.fb_owner_id::text = fp.id::text
           AND page_avatar_asset.fb_field_name = 'avatar'
        WHERE fcc.root_comment_id = $1
        LIMIT 1
    """

    result = await execute_async_single(conn, query, root_comment_id)
    if not result:
        return None

    # Convert UUID to string
    avatar_media_id_raw = result.get("page_avatar_media_id")
    avatar_media_id = None
    if avatar_media_id_raw is not None:
        avatar_media_id = (
            str(avatar_media_id_raw)
            if hasattr(avatar_media_id_raw, "__str__")
            else avatar_media_id_raw
        )

    result["avatar_media"] = {
        "id": avatar_media_id,
        "s3_url": result.get("page_avatar_s3_url"),
        "status": result.get("page_avatar_s3_status"),
        "retention_policy": result.get("page_avatar_retention_policy"),
        "expires_at": result.get("page_avatar_expires_at"),
        "original_url": result.get("avatar"),
    }
    if result["avatar_media"]["s3_url"] and _media_is_active(result["avatar_media"]):
        result["avatar"] = result["avatar_media"]["s3_url"]
    for extra_key in (
        "page_avatar_media_id",
        "page_avatar_s3_url",
        "page_avatar_s3_status",
        "page_avatar_retention_policy",
        "page_avatar_expires_at",
    ):
        result.pop(extra_key, None)
    return result


async def get_post_info_by_root_comment_id(
    conn: asyncpg.Connection, root_comment_id: str
) -> Optional[Dict[str, Any]]:
    """Get post information for a root comment thread.
    Uses facebook_conversation_comments to find the post.
    """
    query = """
        SELECT DISTINCT
            p.id,
            p.fan_page_id,
            p.message,
            p.video_link,
            p.photo_link,
            p.full_picture,
            p.facebook_created_time,
            p.created_at,
            p.updated_at,
            post_photo_asset.id AS post_photo_media_id,
            post_photo_asset.s3_url AS post_photo_s3_url,
            post_photo_asset.status AS post_photo_s3_status,
            post_photo_asset.retention_policy AS post_photo_retention_policy,
            post_photo_asset.expires_at AS post_photo_expires_at,
            post_photo_asset.description AS post_photo_description
        FROM facebook_conversation_comments fcc
        JOIN posts p ON fcc.post_id = p.id
        LEFT JOIN media_assets post_photo_asset
            ON post_photo_asset.source_type = 'facebook_mirror'
           AND post_photo_asset.fb_owner_type = 'post'
           AND post_photo_asset.fb_owner_id::text = p.id::text
           AND post_photo_asset.fb_field_name = 'photo_link'
        WHERE fcc.root_comment_id = $1
        LIMIT 1
    """

    result = await execute_async_single(conn, query, root_comment_id)
    if not result:
        return None

    # Convert UUID to string
    photo_media_id_raw = result.get("post_photo_media_id")
    photo_media_id = None
    if photo_media_id_raw is not None:
        photo_media_id = (
            str(photo_media_id_raw)
            if hasattr(photo_media_id_raw, "__str__")
            else photo_media_id_raw
        )

    # Use full_picture as fallback if photo_link is null
    photo_url = result.get("photo_link") or result.get("full_picture")

    result["photo_media"] = {
        "id": photo_media_id,
        "s3_url": result.get("post_photo_s3_url"),
        "status": result.get("post_photo_s3_status"),
        "retention_policy": result.get("post_photo_retention_policy"),
        "expires_at": result.get("post_photo_expires_at"),
        "description": result.get("post_photo_description"),
        "original_url": photo_url,
    }
    if result["photo_media"]["s3_url"] and _media_is_active(result["photo_media"]):
        result["photo_link"] = result["photo_media"]["s3_url"]
    for extra_key in (
        "post_photo_media_id",
        "post_photo_s3_url",
        "post_photo_s3_status",
        "post_photo_retention_policy",
        "post_photo_expires_at",
        "post_photo_description",
    ):
        result.pop(extra_key, None)
    return result
