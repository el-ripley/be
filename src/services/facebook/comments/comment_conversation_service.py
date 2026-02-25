import json
from typing import Dict, Any, List, Optional, Tuple

from src.utils.logger import get_logger
from src.database.postgres.repositories.facebook_queries.comments.comment_conversations import (
    get_conversation_by_root_comment_id,
    create_conversation,
    update_conversation,
    upsert_conversation_entry,
    get_latest_comment_in_conversation,
    get_conversation_with_unread_count,
)
from src.database.postgres.utils import get_current_timestamp


logger = get_logger()


def _parse_participants(raw_participants: Any) -> List[Dict[str, Any]]:
    """
    Safely parse participant_scope_users which may be a list (from JSONB auto-decode)
    or a JSON string.
    """
    if isinstance(raw_participants, list):
        return raw_participants
    if isinstance(raw_participants, str):
        try:
            parsed = json.loads(raw_participants)
            return parsed if isinstance(parsed, list) else []
        except (json.JSONDecodeError, TypeError):
            return []
    return []


def _extract_participant_profile(
    comment: Dict[str, Any],
) -> Tuple[Optional[str], Optional[str]]:
    """
    Extract participant profile metadata (name, avatar) from comment payload.
    """
    raw_user_info = comment.get("user_info")
    user_info: Dict[str, Any] = {}

    if isinstance(raw_user_info, dict):
        user_info = raw_user_info
    elif isinstance(raw_user_info, str):
        try:
            parsed = json.loads(raw_user_info)
            if isinstance(parsed, dict):
                user_info = parsed
        except (json.JSONDecodeError, TypeError):
            user_info = {}

    name = comment.get("fpsu_name") or user_info.get("name")
    avatar = comment.get("fpsu_profile_pic") or user_info.get("profile_pic")

    return name, avatar


class CommentConversationService:
    """
    Keeps facebook_conversation_comments and facebook_conversation_comment_entries
    in sync with comment mutations triggered via webhook handler.
    """

    async def sync_single_comment_to_conversation(
        self,
        conn,
        *,
        fan_page_id: str,
        post_id: str,
        root_comment_id: str,
        comment: Dict[str, Any],
        verb: str,
    ) -> Optional[Dict[str, Any]]:
        """
        Update conversation aggregates and mapping entries for a comment event.
        """
        if not root_comment_id:
            root_comment_id = comment.get("id")

        if not root_comment_id:
            logger.warning("⚠️ Cannot sync conversation without root_comment_id")
            return None

        conversation = await get_conversation_by_root_comment_id(conn, root_comment_id)

        if not conversation:
            conversation = await create_conversation(
                conn=conn,
                root_comment_id=root_comment_id,
                fan_page_id=fan_page_id,
                post_id=post_id,
                latest_comment_id=None,
                latest_comment_facebook_time=None,
                latest_comment_is_from_page=None,
                has_page_reply=False,
                participant_scope_users=[],
            )

        if verb == "remove":
            await self._handle_remove(conn, conversation, comment)
            return await get_conversation_with_unread_count(conn, conversation["id"])

        await upsert_conversation_entry(
            conn,
            conversation_id=conversation["id"],
            comment_id=comment["id"],
            is_root_comment=comment["id"] == conversation["root_comment_id"],
        )

        await self._apply_metadata_updates(conn, conversation, comment, verb)
        return await get_conversation_with_unread_count(conn, conversation["id"])

    async def sync_backfill_comments_to_conversation(
        self,
        conn,
        *,
        fan_page_id: str,
        post_id: str,
        root_comment_id: str,
        comments: List[Dict[str, Any]],
    ):
        """
        Used after backfilling a tree of comments to ensure entries and aggregates exist.
        """
        if not comments:
            return

        conversation = await get_conversation_by_root_comment_id(conn, root_comment_id)

        if not conversation:
            conversation = await create_conversation(
                conn=conn,
                root_comment_id=root_comment_id,
                fan_page_id=fan_page_id,
                post_id=post_id,
                latest_comment_id=None,
                latest_comment_facebook_time=None,
                latest_comment_is_from_page=None,
                has_page_reply=False,
                participant_scope_users=[],
            )

        latest_candidate: Optional[Tuple[int, Dict[str, Any]]] = None
        participants = _parse_participants(conversation.get("participant_scope_users"))
        has_page_reply = conversation.get("has_page_reply", False)

        for comment in comments:
            await upsert_conversation_entry(
                conn,
                conversation_id=conversation["id"],
                comment_id=comment["id"],
                is_root_comment=comment["id"] == conversation["root_comment_id"],
            )

            if comment.get("deleted_at"):
                continue

            if not comment.get("is_from_page"):
                participants, _ = self._merge_participant_snapshot(
                    participants, comment
                )
            else:
                has_page_reply = True

            candidate_time = (
                comment.get("facebook_created_time") or get_current_timestamp()
            )
            if not latest_candidate or candidate_time >= latest_candidate[0]:
                latest_candidate = (candidate_time, comment)

        latest_fields = {}
        if latest_candidate:
            latest_comment = latest_candidate[1]
            latest_fields = {
                "latest_comment_id": latest_comment["id"],
                "latest_comment_facebook_time": latest_candidate[0],
                "latest_comment_is_from_page": latest_comment.get("is_from_page"),
            }

        await update_conversation(
            conn,
            conversation_id=conversation["id"],
            participant_scope_users=participants,
            has_page_reply=has_page_reply,
            **latest_fields,
        )

        # After syncing all comments, refresh the latest_comment metadata on conversation
        # This ensures latest_comment_* fields are accurate after bulk sync operations
        try:
            from src.database.postgres.repositories.facebook_queries.comments.comment_conversations import (
                refresh_conversation_latest_comment,
            )

            await refresh_conversation_latest_comment(conn, conversation["id"])
            logger.debug(
                f"✅ Refreshed latest_comment metadata for conversation {conversation['id']}"
            )
        except Exception as e:
            logger.warning(
                f"⚠️ Failed to refresh latest_comment for conversation {conversation['id']}: {e}"
            )
            # Don't fail the entire sync if this update fails

    async def _apply_metadata_updates(
        self, conn, conversation: Dict[str, Any], comment: Dict[str, Any], verb: str
    ):
        has_page_reply = conversation.get("has_page_reply", False)
        participants = _parse_participants(conversation.get("participant_scope_users"))
        latest_fields = {}

        if comment.get("deleted_at"):
            latest_fields = {}
        else:
            latest_fields = {
                "latest_comment_id": comment["id"],
                "latest_comment_facebook_time": comment.get("facebook_created_time")
                or get_current_timestamp(),
                "latest_comment_is_from_page": comment.get("is_from_page"),
            }

            if comment.get("is_from_page"):
                has_page_reply = True
            else:
                participants, _ = self._merge_participant_snapshot(
                    participants, comment
                )

        await update_conversation(
            conn,
            conversation_id=conversation["id"],
            participant_scope_users=participants,
            has_page_reply=has_page_reply,
            **latest_fields,
        )

    async def _handle_remove(
        self, conn, conversation: Dict[str, Any], comment: Dict[str, Any]
    ):
        latest_fields = {}
        latest_comment = conversation.get("latest_comment_id")
        if latest_comment == comment["id"]:
            replacement = await get_latest_comment_in_conversation(
                conn, conversation["id"]
            )
            if replacement:
                latest_fields = {
                    "latest_comment_id": replacement["id"],
                    "latest_comment_facebook_time": replacement.get(
                        "facebook_created_time"
                    ),
                    "latest_comment_is_from_page": replacement.get("is_from_page"),
                }
            else:
                latest_fields = {
                    "latest_comment_id": None,
                    "latest_comment_facebook_time": None,
                    "latest_comment_is_from_page": None,
                }

        if latest_fields:
            await update_conversation(
                conn,
                conversation_id=conversation["id"],
                **latest_fields,
            )

    def _merge_participant_snapshot(
        self, participants: List[Dict[str, Any]], comment: Dict[str, Any]
    ) -> Tuple[List[Dict[str, Any]], bool]:
        """
        Update participants JSON list with the commenter snapshot.
        Returns the updated list and a flag indicating whether it changed.
        """
        psid = comment.get("facebook_page_scope_user_id")
        if not psid:
            return participants, False

        updated = False
        timestamp = comment.get("facebook_created_time") or get_current_timestamp()
        name, avatar = _extract_participant_profile(comment)

        new_snapshot = {
            "facebook_page_scope_user_id": psid,
            "last_comment_id": comment["id"],
            "last_comment_time": timestamp,
        }

        if name:
            new_snapshot["name"] = name
        if avatar:
            new_snapshot["avatar"] = avatar

        new_participants = []
        found = False
        for entry in participants:
            if entry.get("facebook_page_scope_user_id") == psid:
                found = True
                new_participants.append({**entry, **new_snapshot})
                updated = True
            else:
                new_participants.append(entry)

        if not found:
            new_participants.append(new_snapshot)
            updated = True

        return new_participants, updated
