"""
Memory Blocks Service.

Handles business logic for suggest_response memory blocks operations.
"""

from typing import Any, Dict, List, Optional

from src.database.postgres.connection import async_db_transaction
from src.database.postgres.repositories import memory_blocks_queries
from src.database.postgres.repositories.suggest_response_queries import (
    create_page_prompt,
    create_page_scope_user_prompt,
    get_active_page_prompt,
    get_active_page_scope_user_prompt,
)
from src.utils.logger import get_logger

logger = get_logger()


class MemoryBlocksService:
    """Service for managing memory blocks."""

    def _normalize_uuid(self, value: Any) -> str:
        """Convert UUID to string if needed."""
        if value is None:
            return None
        if isinstance(value, str):
            return value
        return str(value)

    # ================================================================
    # BLOCK OPERATIONS
    # ================================================================

    async def list_blocks(
        self,
        memory_type: str,  # 'page_prompt' or 'user_prompt'
        prompt_id: str,
    ) -> List[Dict[str, Any]]:
        """List all active blocks for a prompt."""
        async with async_db_transaction() as conn:
            blocks = await memory_blocks_queries.get_latest_blocks(
                conn, memory_type, prompt_id
            )

            # Get media for all blocks
            block_ids = [str(b["id"]) for b in blocks]
            media_map = await memory_blocks_queries.get_block_media(conn, block_ids)

            # Format blocks with media
            result = []
            for block in blocks:
                block_id = str(block["id"])
                formatted_block = {
                    "id": block_id,
                    "block_key": block["block_key"],
                    "title": block["title"],
                    "content": block["content"],
                    "display_order": block["display_order"],
                    "created_at": block["created_at"],
                    "media": media_map.get(block_id, []),
                }
                result.append(formatted_block)

            return result

    async def get_block(
        self,
        memory_type: str,
        prompt_id: str,
        block_key: str,
    ) -> Optional[Dict[str, Any]]:
        """Get a specific block by block_key."""
        async with async_db_transaction() as conn:
            block = await memory_blocks_queries.get_block_by_key(
                conn, memory_type, prompt_id, block_key
            )

            if not block:
                return None

            # Get media for this block
            block_id = str(block["id"])
            media_map = await memory_blocks_queries.get_block_media(conn, [block_id])

            return {
                "id": block_id,
                "block_key": block["block_key"],
                "title": block["title"],
                "content": block["content"],
                "display_order": block["display_order"],
                "created_at": block["created_at"],
                "media": media_map.get(block_id, []),
            }

    async def add_block(
        self,
        memory_type: str,
        prompt_id: str,
        block_key: str,
        title: str,
        content: str,
        display_order: Optional[int],
        created_by_type: str,
        owner_user_id: Optional[str] = None,
        media: Optional[List[Dict[str, Any]]] = None,
    ) -> Dict[str, Any]:
        """Add a new block. Fails if block_key already exists."""
        async with async_db_transaction() as conn:
            # Check if block_key already exists
            existing = await memory_blocks_queries.get_block_by_key(
                conn, memory_type, prompt_id, block_key
            )
            if existing:
                raise ValueError(
                    f"Block with key '{block_key}' already exists. Use update_block to modify it."
                )

            # Determine display_order
            if display_order is None:
                # Get max display_order and add 1
                blocks = await memory_blocks_queries.get_latest_blocks(
                    conn, memory_type, prompt_id
                )
                max_order = max([b["display_order"] for b in blocks], default=-1)
                display_order = max_order + 1

            # Persist media if needed
            persisted_media_items = []
            if media:
                from src.services.users.user_media_service import UserMediaService

                user_media_service = UserMediaService()
                for item in media:
                    media_id = item.get("media_id")
                    if not media_id:
                        continue

                    # Persist ephemeral media
                    if not owner_user_id:
                        raise ValueError(
                            "owner_user_id is required when media is provided"
                        )
                    persisted_media = await user_media_service.persist_media_for_prompt(
                        user_id=owner_user_id,
                        media_id=str(media_id),
                    )
                    persisted_media_id = str(persisted_media["id"])
                    display_order_media = item.get("display_order", 1)

                    persisted_media_items.append(
                        {
                            "media_id": persisted_media_id,
                            "display_order": display_order_media,
                        }
                    )

            # Insert block
            block = await memory_blocks_queries.insert_block(
                conn,
                memory_type,
                prompt_id,
                block_key,
                title,
                content,
                display_order,
                created_by_type,
            )

            # Link media
            if persisted_media_items:
                await memory_blocks_queries.link_media_to_block(
                    conn, str(block["id"]), persisted_media_items
                )

            # Return formatted result with media
            block_id = str(block["id"])
            media_map = await memory_blocks_queries.get_block_media(conn, [block_id])

            return {
                "id": block_id,
                "block_key": block["block_key"],
                "title": block["title"],
                "content": block["content"],
                "display_order": block["display_order"],
                "created_at": block["created_at"],
                "media": media_map.get(block_id, []),
            }

    async def update_block(
        self,
        memory_type: str,
        prompt_id: str,
        block_key: str,
        title: Optional[str] = None,
        content: Optional[str] = None,
        display_order: Optional[int] = None,
        created_by_type: str = "agent",
        owner_user_id: Optional[str] = None,
        media: Optional[List[Dict[str, Any]]] = None,
    ) -> Dict[str, Any]:
        """Update an existing block. Creates new version (append-only)."""
        async with async_db_transaction() as conn:
            # Get latest block
            latest_block = await memory_blocks_queries.get_block_by_key(
                conn, memory_type, prompt_id, block_key
            )
            if not latest_block:
                raise ValueError(f"Block with key '{block_key}' not found")

            # Use existing values if not provided
            new_title = title if title is not None else latest_block["title"]
            new_content = content if content is not None else latest_block["content"]
            new_display_order = (
                display_order
                if display_order is not None
                else latest_block["display_order"]
            )

            # Persist media if needed
            persisted_media_items = []
            if media is not None:
                from src.services.users.user_media_service import UserMediaService

                user_media_service = UserMediaService()
                for item in media:
                    media_id = item.get("media_id")
                    if not media_id:
                        continue

                    # Persist ephemeral media
                    if not owner_user_id:
                        raise ValueError(
                            "owner_user_id is required when media is provided"
                        )
                    persisted_media = await user_media_service.persist_media_for_prompt(
                        user_id=owner_user_id,
                        media_id=str(media_id),
                    )
                    persisted_media_id = str(persisted_media["id"])
                    display_order_media = item.get("display_order", 1)

                    persisted_media_items.append(
                        {
                            "media_id": persisted_media_id,
                            "display_order": display_order_media,
                        }
                    )

            # Insert new version
            block = await memory_blocks_queries.insert_block(
                conn,
                memory_type,
                prompt_id,
                block_key,
                new_title,
                new_content,
                new_display_order,
                created_by_type,
            )

            # Link media (if provided, replaces existing)
            if media is not None:
                await memory_blocks_queries.link_media_to_block(
                    conn, str(block["id"]), persisted_media_items
                )

            # Return formatted result with media
            block_id = str(block["id"])
            media_map = await memory_blocks_queries.get_block_media(conn, [block_id])

            return {
                "id": block_id,
                "block_key": block["block_key"],
                "title": block["title"],
                "content": block["content"],
                "display_order": block["display_order"],
                "created_at": block["created_at"],
                "media": media_map.get(block_id, []),
            }

    async def remove_block(
        self,
        memory_type: str,
        prompt_id: str,
        block_key: str,
        created_by_type: str,
    ) -> bool:
        """Remove a block by inserting tombstone record."""
        async with async_db_transaction() as conn:
            # Check if block exists
            existing = await memory_blocks_queries.get_block_by_key(
                conn, memory_type, prompt_id, block_key
            )
            if not existing:
                return False

            # Insert tombstone
            await memory_blocks_queries.remove_block(
                conn, memory_type, prompt_id, block_key, created_by_type
            )

            return True

    async def reorder_blocks(
        self,
        memory_type: str,
        prompt_id: str,
        block_order: List[str],
        created_by_type: str,
    ) -> List[Dict[str, Any]]:
        """Reorder blocks by updating display_order for each block."""
        async with async_db_transaction() as conn:
            # Update display_order for each block
            for idx, block_key in enumerate(block_order):
                await memory_blocks_queries.update_block_display_order(
                    conn, memory_type, prompt_id, block_key, idx, created_by_type
                )

            # Return updated blocks list
            return await self.list_blocks(memory_type, prompt_id)

    # ================================================================
    # PROMPT CONTAINER OPERATIONS
    # ================================================================

    async def get_or_create_prompt_container(
        self,
        memory_type: str,  # 'page_memory' or 'user_memory'
        fan_page_id: str,
        prompt_type: Optional[str] = None,  # 'messages' or 'comments' (for page_memory)
        psid: Optional[str] = None,  # For user_memory
        owner_user_id: str = None,
        created_by_type: str = "agent",
    ) -> Dict[str, Any]:
        """
        Get or create a prompt container.
        Returns dict with prompt_id and prompt_type_for_blocks.
        """
        async with async_db_transaction() as conn:
            if memory_type == "page_memory":
                if not prompt_type:
                    raise ValueError("prompt_type is required for page_memory")
                if not owner_user_id:
                    raise ValueError("owner_user_id is required")

                # Get or create page prompt
                prompt = await get_active_page_prompt(
                    conn, fan_page_id, prompt_type, owner_user_id
                )
                if not prompt:
                    # Create new empty prompt (without content field now)
                    # TODO: Update create_page_prompt to not require content
                    prompt = await create_page_prompt(
                        conn,
                        fan_page_id,
                        prompt_type,
                        "",  # Empty content - blocks will store actual content
                        owner_user_id,
                        created_by_type,
                    )

                return {
                    "prompt_id": str(prompt["id"]),
                    "prompt_type_for_blocks": "page_prompt",
                }

            elif memory_type == "user_memory":
                if not psid:
                    raise ValueError("psid is required for user_memory")
                if not owner_user_id:
                    raise ValueError("owner_user_id is required")

                # Get or create user prompt
                prompt = await get_active_page_scope_user_prompt(
                    conn, fan_page_id, psid, owner_user_id
                )
                if not prompt:
                    # Create new empty prompt
                    # TODO: Update create_page_scope_user_prompt to not require content
                    prompt = await create_page_scope_user_prompt(
                        conn,
                        fan_page_id,
                        psid,
                        "",  # Empty content
                        owner_user_id,
                        created_by_type,
                    )

                return {
                    "prompt_id": str(prompt["id"]),
                    "prompt_type_for_blocks": "user_prompt",
                }

            else:
                raise ValueError(f"Invalid memory_type: {memory_type}")

    async def create_fresh_prompt(
        self,
        memory_type: str,
        fan_page_id: str,
        prompt_type: Optional[str],
        psid: Optional[str],
        owner_user_id: str,
        created_by_type: str,
    ) -> Dict[str, Any]:
        """Create a new empty prompt container, deactivate old one."""
        async with async_db_transaction() as conn:
            if memory_type == "page_memory":
                if not prompt_type:
                    raise ValueError("prompt_type is required for page_memory")

                # Create new prompt (deactivates old one automatically)
                prompt = await create_page_prompt(
                    conn,
                    fan_page_id,
                    prompt_type,
                    "",  # Empty content
                    owner_user_id,
                    created_by_type,
                )

                return {
                    "prompt_id": str(prompt["id"]),
                    "prompt_type_for_blocks": "page_prompt",
                    "blocks": [],
                }

            elif memory_type == "user_memory":
                if not psid:
                    raise ValueError("psid is required for user_memory")

                prompt = await create_page_scope_user_prompt(
                    conn,
                    fan_page_id,
                    psid,
                    "",  # Empty content
                    owner_user_id,
                    created_by_type,
                )

                return {
                    "prompt_id": str(prompt["id"]),
                    "prompt_type_for_blocks": "user_prompt",
                    "blocks": [],
                }

            else:
                raise ValueError(f"Invalid memory_type: {memory_type}")

    async def migrate_prompt(
        self,
        memory_type: str,
        fan_page_id: str,
        prompt_type: Optional[str],
        psid: Optional[str],
        owner_user_id: str,
        created_by_type: str,
    ) -> Dict[str, Any]:
        """Create new prompt container and copy all blocks from old one."""
        async with async_db_transaction() as conn:
            # Get old prompt
            if memory_type == "page_memory":
                if not prompt_type:
                    raise ValueError("prompt_type is required for page_memory")

                old_prompt = await get_active_page_prompt(
                    conn, fan_page_id, prompt_type, owner_user_id
                )
                if not old_prompt:
                    raise ValueError("No existing prompt to migrate")

                old_prompt_id = str(old_prompt["id"])
                old_prompt_type_for_blocks = "page_prompt"

                # Create new prompt
                new_prompt = await create_page_prompt(
                    conn,
                    fan_page_id,
                    prompt_type,
                    "",  # Empty content
                    owner_user_id,
                    created_by_type,
                )
                new_prompt_id = str(new_prompt["id"])

            elif memory_type == "user_memory":
                if not psid:
                    raise ValueError("psid is required for user_memory")

                old_prompt = await get_active_page_scope_user_prompt(
                    conn, fan_page_id, psid, owner_user_id
                )
                if not old_prompt:
                    raise ValueError("No existing prompt to migrate")

                old_prompt_id = str(old_prompt["id"])
                old_prompt_type_for_blocks = "user_prompt"

                # Create new prompt
                new_prompt = await create_page_scope_user_prompt(
                    conn,
                    fan_page_id,
                    psid,
                    "",  # Empty content
                    owner_user_id,
                    created_by_type,
                )
                new_prompt_id = str(new_prompt["id"])

            else:
                raise ValueError(f"Invalid memory_type: {memory_type}")

            # Copy blocks
            copied_blocks = await memory_blocks_queries.copy_blocks_to_prompt(
                conn,
                old_prompt_type_for_blocks,
                old_prompt_id,
                old_prompt_type_for_blocks,
                new_prompt_id,
                created_by_type,
            )

            return {
                "prompt_id": new_prompt_id,
                "prompt_type_for_blocks": old_prompt_type_for_blocks,
                "blocks_copied": len(copied_blocks),
            }

    # ================================================================
    # RENDERING
    # ================================================================

    async def render_memory(
        self,
        memory_type: str,
        prompt_id: str,
    ) -> str:
        """
        Render memory blocks into formatted text as it appears in prompt.

        Output format:
        <memory_block key="block_key_1" title="Title 1">
        Content 1...
        <images>
        <image index="1" media_id="...">description</image>
        </images>
        </memory_block>

        <memory_block key="block_key_2" title="Title 2">
        Content 2...
        </memory_block>
        """
        async with async_db_transaction() as conn:
            blocks = await memory_blocks_queries.get_latest_blocks(
                conn, memory_type, prompt_id
            )

            # Get media for all blocks
            block_ids = [str(b["id"]) for b in blocks]
            media_map = await memory_blocks_queries.get_block_media(conn, block_ids)

            # Render
            output = []
            for block in sorted(blocks, key=lambda b: b["display_order"]):
                block_key = block["block_key"]
                block_title = block["title"]
                block_id = str(block["id"])
                block_index = block["display_order"]
                media_list = media_map.get(block_id, [])

                # Open memory_block tag with block_id, index, key and title attributes
                output.append(
                    f'<memory_block block_id="{block_id}" index="{block_index}" key="{block_key}" title="{block_title}">'
                )

                # Content
                output.append(block["content"])

                # Media for this block - grouped in <images> wrapper
                if media_list:
                    output.append("<images>")
                    for media in sorted(media_list, key=lambda m: m["display_order"]):
                        desc = media.get("description", "")
                        media_id = media.get("media_id", "")
                        output.append(
                            f'<image index="{media["display_order"]}" media_id="{media_id}">{desc}</image>'
                        )
                    output.append("</images>")

                # Close memory_block tag
                output.append("</memory_block>")
                output.append("")  # Blank line between blocks

            return "\n".join(output).strip()
