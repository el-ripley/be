import json
from typing import List

from fastapi import APIRouter, Depends, HTTPException, Query, Request

from src.middleware.auth_middleware import get_current_user_id
from src.services.facebook.auth import FacebookPageService
from src.services.facebook.users.page_scope_user_service import PageScopeUserService

from .schemas import (
    PageItem,
    PageScopeUserItem,
    PageScopeUsersResponse,
    PagesListResponse,
)

router = APIRouter(prefix="/pages", tags=["Facebook Pages"])


def get_page_service(request: Request) -> FacebookPageService:
    service = getattr(request.app.state, "facebook_page_service", None)
    if service is None:
        raise HTTPException(
            status_code=500, detail="Facebook page service is not initialized"
        )
    return service


def get_page_scope_user_service(request: Request) -> PageScopeUserService:
    service = getattr(request.app.state, "page_scope_user_service", None)
    if service is None:
        raise HTTPException(
            status_code=500, detail="Page scope user service is not initialized"
        )
    return service


@router.get(
    "/mine",
    response_model=PagesListResponse,
    summary="List pages that the current user admins",
    description="Return page metadata for all pages where the current user is an admin.",
)
async def list_my_pages(
    page_service: FacebookPageService = Depends(get_page_service),
    user_id: str = Depends(get_current_user_id),
):
    """
    Fetch all pages that the current user can manage.
    """
    admins = await page_service.get_facebook_page_admins_by_user_id(user_id)
    pages = []
    for admin in admins:
        tasks_raw = admin.get("tasks")
        tasks = tasks_raw
        if isinstance(tasks_raw, str):
            try:
                tasks = json.loads(tasks_raw)
            except Exception:
                tasks = tasks_raw

        pages.append(
            PageItem(
                page_id=admin.get("page_id"),
                name=admin.get("page_name"),
                avatar=admin.get("page_avatar"),
                category=admin.get("page_category"),
                tasks=tasks,
            )
        )

    return PagesListResponse(pages=pages)


@router.get(
    "/scope-users",
    response_model=PageScopeUsersResponse,
    summary="Get page scope users by page IDs",
    description="Return page scope users for the specified page IDs with pagination.",
)
async def get_page_scope_users_by_pages(
    page_ids: List[str] = Query(..., description="List of Facebook page IDs"),
    page: int = Query(1, ge=1, description="Page number (1-indexed)"),
    limit: int = Query(20, ge=1, le=100, description="Number of items per page"),
    page_scope_user_service: PageScopeUserService = Depends(
        get_page_scope_user_service
    ),
    user_id: str = Depends(get_current_user_id),
):
    """
    Fetch page scope users for the given page IDs with pagination.
    """
    from src.database.postgres.utils import paginate_params

    limit_val, offset = paginate_params(page=page, page_size=limit)
    users, total = await page_scope_user_service.get_page_scope_users_by_page_ids(
        page_ids, limit=limit_val, offset=offset
    )

    # Parse user_info JSON if it's a string
    user_items = []
    for user in users:
        user_info = user.get("user_info")
        if isinstance(user_info, str):
            try:
                user_info = json.loads(user_info)
            except Exception:
                pass

        user_items.append(
            PageScopeUserItem(
                id=user.get("id"),
                fan_page_id=user.get("fan_page_id"),
                user_info=user_info,
                created_at=user.get("created_at"),
                updated_at=user.get("updated_at"),
            )
        )

    has_more = offset + len(user_items) < total

    return PageScopeUsersResponse(
        users=user_items,
        total=total,
        page=page,
        limit=limit,
        has_more=has_more,
    )
