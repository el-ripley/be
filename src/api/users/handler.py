"""
Handlers for user operations including user info, settings, and file uploads.
"""

from typing import Any, Dict, List, Optional

from fastapi import HTTPException, UploadFile

from src.api.users.schemas import FileUploadResponse
from src.services.auth_service import AuthService
from src.services.users.user_files_service import UserFilesService
from src.services.users.user_service import UserService
from src.services.users.user_settings_service import UserSettingsService
from src.utils.logger import get_logger

logger = get_logger()


class UserHandler:
    """Handler for user operations"""

    def __init__(
        self,
        user_service: UserService,
        auth_service: AuthService,
        user_settings_service: UserSettingsService = None,
    ):
        self.user_service = user_service
        self.auth_service = auth_service
        self.user_settings_service = user_settings_service or UserSettingsService()

    async def get_user_comprehensive_info(self, user_id: str) -> Dict[str, Any]:
        try:
            user_info = await self.user_service.get_user_comprehensive_info(user_id)

            if not user_info:
                logger.warning(f"User not found: {user_id}")
                raise HTTPException(status_code=404, detail="User not found")

            return user_info

        except HTTPException:
            # Re-raise HTTP exceptions
            raise
        except Exception as e:
            logger.error(
                f"❌ USER HANDLER: Error getting user info for {user_id}: {str(e)}"
            )
            raise HTTPException(status_code=500, detail="Internal server error")

    # ================================================================
    # USER CONVERSATION SETTINGS HANDLERS
    # ================================================================

    async def get_conversation_settings(self, user_id: str) -> Dict[str, Any]:
        """Get user conversation settings."""
        try:
            result = await self.user_settings_service.get_settings(user_id)
            return result
        except Exception as e:
            logger.error(
                f"❌ USER HANDLER: Error getting conversation settings: {str(e)}"
            )
            raise HTTPException(status_code=500, detail="Internal server error")

    async def update_conversation_settings(
        self,
        user_id: str,
        context_token_limit: int = None,
        context_buffer_percent: int = None,
        summarizer_model: str = None,
        vision_model: str = None,
    ) -> Dict[str, Any]:
        """Update user conversation settings."""
        try:
            result = await self.user_settings_service.update_settings(
                user_id=user_id,
                context_token_limit=context_token_limit,
                context_buffer_percent=context_buffer_percent,
                summarizer_model=summarizer_model,
                vision_model=vision_model,
            )
            return result
        except ValueError as e:
            logger.warning(f"⚠️ USER HANDLER: Validation error: {str(e)}")
            raise HTTPException(status_code=400, detail=str(e))
        except Exception as e:
            logger.error(
                f"❌ USER HANDLER: Error updating conversation settings: {str(e)}"
            )
            raise HTTPException(status_code=500, detail="Internal server error")


class UserFilesHandler:
    """Handler for user file upload operations"""

    def __init__(self):
        self.user_files_service = UserFilesService()

    async def upload_files(
        self,
        user_id: str,
        files: List[UploadFile],
        purpose: str,
        descriptions: Optional[List[Optional[str]]] = None,
    ) -> FileUploadResponse:
        """
        Handle file upload request.

        Args:
            user_id: ID of the authenticated user
            files: List of uploaded files
            purpose: Upload purpose - 'facebook' (1 day), 'agent' (7 days), or 'prompt' (permanent)

        Returns:
            FileUploadResponse with upload results and S3 URLs
        """
        try:
            # Convert UploadFile objects to service format
            files_data = []
            logger.info(f"Processing {len(files)} files for user {user_id}")

            for i, file in enumerate(files):
                file_content = await file.read()

                if len(file_content) == 0:
                    continue

                files_data.append(
                    {
                        "filename": file.filename or f"file_{i+1}",
                        "content": file_content,
                        "content_type": file.content_type or "application/octet-stream",
                    }
                )

            if not files_data:
                return FileUploadResponse(
                    success=False,
                    results=[],
                    successful_uploads=0,
                    total_files=len(files),
                    message="No valid files provided (all files are empty)",
                )

            result = await self.user_files_service.upload_files(
                user_id, files_data, purpose, descriptions
            )

            return FileUploadResponse(
                success=result["success"],
                results=result["results"],
                successful_uploads=result["successful_uploads"],
                total_files=result["total_files"],
                message=result.get("message", "Upload completed"),
            )

        except Exception as e:
            logger.error(f"Error in upload_files handler: {e}")
            return FileUploadResponse(
                success=False,
                results=[],
                successful_uploads=0,
                total_files=len(files),
                message=f"Upload failed: {str(e)}",
            )
