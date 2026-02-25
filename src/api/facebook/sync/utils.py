"""Shared utilities for Facebook sync operations."""

from fastapi import HTTPException, Request

from src.services.facebook.auth import FacebookPermissionService


def get_permission_service(request: Request) -> FacebookPermissionService:
    """Resolve FacebookPermissionService from app.state."""
    service = getattr(request.app.state, "facebook_permission_service", None)
    if service is None:
        raise HTTPException(
            status_code=500,
            detail="Permission service is not initialized",
        )
    return service


async def check_page_permission(
    permission_service: FacebookPermissionService,
    user_id: str,
    page_id: str,
) -> None:
    """Check user has admin permission for page."""
    has_permission = await permission_service.check_user_page_admin_permission(
        user_id, page_id
    )
    if not has_permission:
        raise HTTPException(
            status_code=403,
            detail=f"User does not have permission to manage page {page_id}",
        )
