import json
from typing import Any, Dict, List, Optional

from fastapi import (
    APIRouter,
    Body,
    Depends,
    File,
    Form,
    HTTPException,
    Query,
    Request,
    Response,
    UploadFile,
)

from src.api.users.handler import UserFilesHandler, UserHandler
from src.api.users.schemas import (
    DeleteMediaResponse,
    FileUploadResponse,
    ListMediaResponse,
    MediaItemResponse,
    MemoryBlockItem,
    PromptReference,
    UserConversationSettingsResponse,
    UserConversationSettingsUpdate,
    UserMemoryResponse,
)
from src.middleware.auth_middleware import get_current_user_id, verify_token
from src.utils.logger import get_logger

logger = get_logger()


def get_user_handler(request: Request) -> UserHandler:
    return request.app.state.user_handler


def get_user_files_handler(request: Request) -> UserFilesHandler:
    """Get user files handler from app state."""
    if not hasattr(request.app.state, "user_files_handler"):
        request.app.state.user_files_handler = UserFilesHandler()
    return request.app.state.user_files_handler


def get_user_memory_service(request: Request):
    """Get user memory service from app state."""
    return getattr(request.app.state, "user_memory_service", None)


router = APIRouter(prefix="/users", tags=["Users"])


# ================================================================
# USER INFO ENDPOINTS
# ================================================================


@router.get("/me", response_model=Dict[str, Any])
async def get_current_user_comprehensive_info(
    response: Response,
    current_user_id: str = Depends(get_current_user_id),
    handler: UserHandler = Depends(get_user_handler),
) -> Dict[str, Any]:
    """
    Get comprehensive information for the currently authenticated user.

    This endpoint returns complete user profile including:
    - Basic user information
    - User roles and permissions
    - Facebook profile information
    - Facebook page admin relationships with fan page details

    Authentication:
        Requires valid JWT token in Authorization header as Bearer token

    Returns:
        Comprehensive user information structured as:
        {
            "user": {...},
            "roles": [...],
            "facebook_user": {...},
            "page_admins": [...]
        }

    Raises:
        401: If authorization token is missing, invalid, or expired
        404: If authenticated user is not found in database
        500: If server error occurs during data retrieval
    """
    logger.info(
        f"🌐 USER ROUTER: GET /me - Getting comprehensive info for authenticated user: {current_user_id}"
    )
    return await handler.get_user_comprehensive_info(current_user_id)


# ================================================================
# USER MEMORY ENDPOINTS (view and delete only)
# ================================================================


@router.get("/memory", response_model=UserMemoryResponse)
async def get_user_memory(
    current_user_id: str = Depends(get_current_user_id),
    user_memory_service=Depends(get_user_memory_service),
):
    """Get current user's active memory with blocks (read-only)."""
    if not user_memory_service:
        raise HTTPException(
            status_code=500,
            detail="User memory service not available",
        )
    data = await user_memory_service.get_user_memory(current_user_id)
    if not data:
        return UserMemoryResponse(
            id=None,
            is_active=False,
            created_at=None,
            created_by_type=None,
            blocks=[],
        )
    blocks = []
    for b in data.get("blocks") or []:
        blocks.append(
            MemoryBlockItem(
                id=str(b.get("id", "")),
                block_key=b.get("block_key", ""),
                title=b.get("title", ""),
                content=b.get("content", ""),
                display_order=int(b.get("display_order", 0)),
                created_at=int(b.get("created_at", 0)),
                created_by_type=b.get("created_by_type", ""),
            )
        )
    return UserMemoryResponse(
        id=data.get("id"),
        is_active=data.get("is_active", True),
        created_at=data.get("created_at"),
        created_by_type=data.get("created_by_type"),
        blocks=blocks,
    )


@router.delete("/memory")
async def delete_user_memory(
    current_user_id: str = Depends(get_current_user_id),
    user_memory_service=Depends(get_user_memory_service),
):
    """Soft delete current user's active memory (set is_active=FALSE)."""
    if not user_memory_service:
        raise HTTPException(
            status_code=500,
            detail="User memory service not available",
        )
    deleted = await user_memory_service.delete_user_memory(current_user_id)
    return {"deleted": deleted}


# ================================================================
# USER CONVERSATION SETTINGS ENDPOINTS
# ================================================================


@router.get(
    "/settings",
    response_model=UserConversationSettingsResponse,
    summary="Get user conversation settings",
)
async def get_conversation_settings(
    current_user_id: str = Depends(get_current_user_id),
    handler: UserHandler = Depends(get_user_handler),
) -> Dict[str, Any]:
    """
    Get user's conversation settings for context management.

    Returns user's configured values or system defaults if not set.
    """
    return await handler.get_conversation_settings(user_id=current_user_id)


@router.put(
    "/settings",
    response_model=UserConversationSettingsResponse,
    summary="Update user conversation settings",
)
async def update_conversation_settings(
    settings_data: UserConversationSettingsUpdate,
    current_user_id: str = Depends(get_current_user_id),
    handler: UserHandler = Depends(get_user_handler),
) -> Dict[str, Any]:
    """
    Update user's conversation settings for context management.

    Only provided fields will be updated. Other fields remain unchanged.
    Use system defaults by omitting fields or setting them to None.
    """
    return await handler.update_conversation_settings(
        user_id=current_user_id,
        context_token_limit=settings_data.context_token_limit,
        context_buffer_percent=settings_data.context_buffer_percent,
        summarizer_model=settings_data.summarizer_model,
        vision_model=settings_data.vision_model,
    )


# ================================================================
# USER FILES ENDPOINTS
# ================================================================


@router.post("/files/upload", response_model=FileUploadResponse)
async def upload_files(
    files: List[UploadFile] = File(..., description="Files to upload"),
    purpose: str = Query(
        ...,
        description="Upload purpose: 'facebook' (1 day retention), 'agent' (7 days retention), or 'prompt' (permanent, counts toward quota)",
    ),
    descriptions: Optional[str] = Form(
        None,
        description="JSON array of descriptions matching files order (e.g., ['desc1', 'desc2', null])",
    ),
    handler: UserFilesHandler = Depends(get_user_files_handler),
    token: dict = Depends(verify_token),
):
    """
    Upload files to ephemeral S3 storage.

    Upload images and videos to S3. Files are automatically deleted after the specified duration.
    No storage quota - upload unlimited files.

    **File Limits:**
    - **Images**: 5MB max (JPEG, PNG, GIF, WebP)
    - **Videos**: 25MB max (MP4, MOV, AVI, WebM)
    - **Batch**: 10 files max per request

    **Storage:**
    - Files stored in `ephemeral/one_day/` (facebook), `ephemeral/one_week/` (agent), or `permanent/` (prompt) prefix
    - Ephemeral files auto-deleted after specified duration via S3 lifecycle policy
    - Permanent files (prompt) count toward storage quota and never expire
    - Returns S3 URLs and file_id for immediate use

    **Parameters:**
    - **purpose**:
      - 'facebook' (1 day retention, ephemeral)
      - 'agent' (7 days retention, ephemeral)
      - 'prompt' (permanent, counts toward quota) - Use for media attached to prompts

    **Authentication**: Required (Bearer token)
    """
    try:
        user_id = token.get("user_id")
        if not user_id:
            raise HTTPException(
                status_code=401, detail="Invalid token: user_id missing"
            )

        if not files:
            raise HTTPException(status_code=400, detail="No files provided")

        if len(files) > 10:
            raise HTTPException(
                status_code=400, detail="Maximum 10 files allowed per batch"
            )

        # Validate purpose
        if purpose not in ["facebook", "agent", "prompt"]:
            raise HTTPException(
                status_code=400,
                detail="purpose must be 'facebook', 'agent', or 'prompt'",
            )

        # Parse descriptions JSON array
        descriptions_list = None
        if descriptions:
            try:
                descriptions_list = json.loads(descriptions)
                if not isinstance(descriptions_list, list):
                    raise HTTPException(
                        status_code=400,
                        detail="descriptions must be a JSON array",
                    )
            except json.JSONDecodeError:
                raise HTTPException(
                    status_code=400,
                    detail="Invalid JSON format for descriptions",
                )

        response = await handler.upload_files(
            user_id, files, purpose, descriptions_list
        )

        if not response.success and response.successful_uploads == 0:
            raise HTTPException(status_code=400, detail=response.message)

        return response

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error during file upload: {str(e)}")
        raise HTTPException(status_code=500, detail=f"Internal server error: {str(e)}")


@router.patch("/files/media/{media_id}/description")
async def update_media_description(
    media_id: str,
    description: Optional[str] = Body(
        None, description="New description (null to clear)"
    ),
    handler: UserFilesHandler = Depends(get_user_files_handler),
    token: dict = Depends(verify_token),
):
    """
    Update description for uploaded media.
    When user updates description, description_model is set to NULL.
    """
    try:
        user_id = token.get("user_id")
        if not user_id:
            raise HTTPException(
                status_code=401, detail="Invalid token: user_id missing"
            )

        from src.services.users.user_media_service import UserMediaService

        media_service = UserMediaService()
        result = await media_service.update_media_description(
            user_id, media_id, description
        )

        return result

    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error updating media description: {str(e)}")
        raise HTTPException(status_code=500, detail=f"Internal server error: {str(e)}")


@router.get("/files/media", response_model=ListMediaResponse)
async def list_user_media(
    dangling: Optional[bool] = Query(
        None,
        description="Filter for orphaned media: True=only orphaned, False=only with prompts, None=both (default)",
    ),
    page: int = Query(1, ge=1, description="Page number"),
    limit: int = Query(20, ge=1, le=100, description="Items per page"),
    handler: UserFilesHandler = Depends(get_user_files_handler),
    token: dict = Depends(verify_token),
) -> ListMediaResponse:
    """
    List user's permanent media with pagination.
    Default: Shows all permanent media (retention_policy='permanent').
    Includes information about which prompts use each media.

    Note: Automatically excludes system-managed avatars (fan_page avatars, page_scope_user profile pics)
    as these are fetched by the agent and shouldn't be managed/deleted by users.

    **Filters:**
    - **dangling**: Filter for orphaned media
      - `None` (default): Include both orphaned and non-orphaned media
      - `True`: Only show orphaned media (not attached to any prompt)
      - `False`: Only show media attached to prompts
    """
    try:
        user_id = token.get("user_id")
        if not user_id:
            raise HTTPException(
                status_code=401, detail="Invalid token: user_id missing"
            )

        from src.services.users.user_media_service import UserMediaService

        media_service = UserMediaService()
        offset = (page - 1) * limit
        results = await media_service.list_user_media(
            user_id, limit, offset, purpose=None, dangling=dangling
        )

        # Format results
        media_items = []
        for m in results:
            # Parse prompts from JSON
            prompts_data = m.get("prompts", [])
            if isinstance(prompts_data, str):
                import json

                try:
                    prompts_data = json.loads(prompts_data)
                except (json.JSONDecodeError, TypeError):
                    prompts_data = []

            prompts = [
                PromptReference(
                    prompt_type=p.get("prompt_type"),
                    prompt_id=p.get("prompt_id"),
                    display_order=p.get("display_order", 0),
                )
                for p in prompts_data
                if p.get("prompt_type") and p.get("prompt_id")
            ]

            media_items.append(
                MediaItemResponse(
                    id=str(m.get("id")),
                    s3_url=m.get("s3_url"),
                    description=m.get("description"),
                    media_type=m.get("media_type"),
                    mime_type=m.get("mime_type"),
                    file_size_bytes=m.get("file_size_bytes", 0),
                    retention_policy=m.get("retention_policy"),
                    expires_at=m.get("expires_at"),
                    created_at=m.get("created_at"),
                    updated_at=m.get("updated_at"),
                    prompts=prompts,
                )
            )

        # Get total count
        total_results = await media_service.list_user_media(
            user_id, 10000, 0, purpose=None, dangling=dangling
        )  # Get all for count
        total = len(total_results)

        return ListMediaResponse(
            media=media_items, total=total, limit=limit, offset=offset
        )

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error listing media: {str(e)}")
        raise HTTPException(status_code=500, detail=f"Internal server error: {str(e)}")


@router.delete("/files/media", response_model=DeleteMediaResponse)
async def delete_user_media(
    ids: str = Query(..., description="Comma-separated list of media IDs to delete"),
    handler: UserFilesHandler = Depends(get_user_files_handler),
    token: dict = Depends(verify_token),
) -> DeleteMediaResponse:
    """
    Delete user's media assets by IDs.
    Only deletes media owned by the user.
    For permanent media, decreases quota accordingly.
    """
    try:
        user_id = token.get("user_id")
        if not user_id:
            raise HTTPException(
                status_code=401, detail="Invalid token: user_id missing"
            )

        # Parse comma-separated IDs
        media_ids = [mid.strip() for mid in ids.split(",") if mid.strip()]
        if not media_ids:
            raise HTTPException(status_code=400, detail="No media IDs provided")

        from src.services.users.user_media_service import UserMediaService

        media_service = UserMediaService()
        result = await media_service.delete_user_media(user_id, media_ids)

        return DeleteMediaResponse(**result)

    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error deleting media: {str(e)}")
        raise HTTPException(status_code=500, detail=f"Internal server error: {str(e)}")
