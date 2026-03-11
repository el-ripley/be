from fastapi import APIRouter

from src.utils.logger import get_logger

# Import sub-routers
from .auth import auth_router
from .comments import comments_router
from .fanpages import router as pages_router
from .messages import messages_router
from .posts import router as posts_router
from .sync import router as sync_router
from .sync.async_router import router as async_sync_router
from .webhook import webhook_router

logger = get_logger()

# Create main Facebook router
router = APIRouter(prefix="/facebook", tags=["Facebook"])

# Include sub-routers
router.include_router(auth_router)
router.include_router(webhook_router)
router.include_router(comments_router)
router.include_router(posts_router)
router.include_router(messages_router)
router.include_router(pages_router)
router.include_router(sync_router)
router.include_router(async_sync_router)  # Async job-based sync endpoints
