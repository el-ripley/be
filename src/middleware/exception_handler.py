"""
Comprehensive exception handler for the FastAPI application.

This module provides centralized error handling for all types of exceptions
that can occur in the application, ensuring consistent error responses
and proper logging.
"""

import traceback
from typing import Any, Dict, Optional

import asyncpg
import jwt
from fastapi import HTTPException, Request, status
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse

from src.utils.logger import get_logger

logger = get_logger()


class ErrorResponse:
    """Standardized error response structure."""

    @staticmethod
    def create(
        error_type: str,
        message: str,
        details: Optional[Dict[str, Any]] = None,
        status_code: int = 500,
    ) -> Dict[str, Any]:
        """Create a standardized error response."""
        response = {
            "error": {
                "type": error_type,
                "message": message,
                "status_code": status_code,
            }
        }

        if details:
            response["error"]["details"] = details

        return response


async def http_exception_handler(request: Request, exc: HTTPException) -> JSONResponse:
    """
    Handle HTTP exceptions (400, 401, 403, 404, etc.).

    Args:
        request: FastAPI request object
        exc: HTTP exception instance

    Returns:
        JSONResponse with standardized error format
    """
    error_type = "http_error"

    # Map status codes to error types for better categorization
    status_code_mapping = {
        400: "bad_request",
        401: "unauthorized",
        403: "forbidden",
        404: "not_found",
        405: "method_not_allowed",
        409: "conflict",
        422: "validation_error",
        429: "rate_limit_exceeded",
        500: "internal_server_error",
    }

    error_type = status_code_mapping.get(exc.status_code, "http_error")

    # Log based on severity
    if exc.status_code >= 500:
        logger.error(
            f"🚨 SERVER ERROR {exc.status_code}: {exc.detail} - Path: {request.url.path}"
        )
    elif exc.status_code >= 400:
        logger.warning(
            f"⚠️  CLIENT ERROR {exc.status_code}: {exc.detail} - Path: {request.url.path}"
        )

    return JSONResponse(
        status_code=exc.status_code,
        content=ErrorResponse.create(
            error_type=error_type, message=str(exc.detail), status_code=exc.status_code
        ),
    )


async def validation_exception_handler(
    request: Request, exc: RequestValidationError
) -> JSONResponse:
    """
    Handle Pydantic validation errors.

    Args:
        request: FastAPI request object
        exc: Validation error instance

    Returns:
        JSONResponse with detailed validation error information
    """
    logger.warning(f"📋 VALIDATION ERROR: {exc.errors()} - Path: {request.url.path}")

    # Format validation errors for better readability
    formatted_errors = []
    for error in exc.errors():
        formatted_error = {
            "field": " -> ".join(str(loc) for loc in error["loc"]),
            "message": error["msg"],
            "type": error["type"],
        }
        if "input" in error:
            formatted_error["input"] = error["input"]
        formatted_errors.append(formatted_error)

    return JSONResponse(
        status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
        content=ErrorResponse.create(
            error_type="validation_error",
            message="Request validation failed",
            details={
                "validation_errors": formatted_errors,
                "error_count": len(formatted_errors),
            },
            status_code=422,
        ),
    )


async def jwt_exception_handler(request: Request, exc: jwt.PyJWTError) -> JSONResponse:
    """
    Handle JWT-related errors.

    Args:
        request: FastAPI request object
        exc: JWT error instance

    Returns:
        JSONResponse with authentication error
    """
    error_message = "Invalid or expired authentication token"

    # Provide specific error messages for different JWT errors
    if isinstance(exc, jwt.ExpiredSignatureError):
        error_message = "Authentication token has expired"
        logger.warning(f"🔐 JWT EXPIRED: Token expired - Path: {request.url.path}")
    elif isinstance(exc, jwt.InvalidTokenError):
        error_message = "Invalid authentication token"
        logger.warning(f"🔐 JWT INVALID: Invalid token - Path: {request.url.path}")
    elif isinstance(exc, jwt.DecodeError):
        error_message = "Malformed authentication token"
        logger.warning(f"🔐 JWT MALFORMED: Decode error - Path: {request.url.path}")
    else:
        logger.warning(f"🔐 JWT ERROR: {str(exc)} - Path: {request.url.path}")

    return JSONResponse(
        status_code=status.HTTP_401_UNAUTHORIZED,
        content=ErrorResponse.create(
            error_type="authentication_error", message=error_message, status_code=401
        ),
        headers={"WWW-Authenticate": "Bearer"},
    )


async def database_exception_handler(request: Request, exc: Exception) -> JSONResponse:
    """
    Handle database-related errors.

    Args:
        request: FastAPI request object
        exc: Database error instance

    Returns:
        JSONResponse with database error information
    """
    error_message = "Database operation failed"
    status_code = status.HTTP_500_INTERNAL_SERVER_ERROR

    # Handle specific asyncpg errors
    if isinstance(exc, asyncpg.ConnectionDoesNotExistError):
        error_message = "Database connection unavailable"
        logger.error(
            f"🗄️  DB CONNECTION ERROR: No connection - Path: {request.url.path}"
        )
    elif isinstance(exc, asyncpg.PostgresError):
        # Get the specific PostgreSQL error code and message
        error_message = (
            f"Database error: {exc.message}" if hasattr(exc, "message") else str(exc)
        )
        logger.error(f"🗄️  POSTGRES ERROR: {error_message} - Path: {request.url.path}")
    elif "connection" in str(exc).lower():
        error_message = "Database connection error"
        logger.error(f"🗄️  DB CONNECTION ERROR: {str(exc)} - Path: {request.url.path}")
    else:
        logger.error(f"🗄️  DATABASE ERROR: {str(exc)} - Path: {request.url.path}")

    # In production, don't expose detailed database errors to clients
    return JSONResponse(
        status_code=status_code,
        content=ErrorResponse.create(
            error_type="database_error",
            message="A database error occurred. Please try again later.",
            status_code=status_code,
        ),
    )


async def general_exception_handler(request: Request, exc: Exception) -> JSONResponse:
    """
    Handle all other unexpected exceptions.

    Args:
        request: FastAPI request object
        exc: General exception instance

    Returns:
        JSONResponse with generic server error
    """
    # Log the full traceback for debugging
    error_traceback = traceback.format_exc()
    logger.error(f"💥 UNHANDLED EXCEPTION: {str(exc)} - Path: {request.url.path}")
    logger.error(f"Traceback: {error_traceback}")

    # Don't expose internal error details to clients in production
    return JSONResponse(
        status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
        content=ErrorResponse.create(
            error_type="server_error",
            message="An unexpected error occurred. Please try again later.",
            status_code=500,
        ),
    )


# Custom exception classes for application-specific errors
class BusinessLogicError(Exception):
    """Raised when business logic validation fails."""

    def __init__(self, message: str, details: Optional[Dict[str, Any]] = None):
        self.message = message
        self.details = details or {}
        super().__init__(self.message)


class ExternalServiceError(Exception):
    """Raised when external service (Facebook API, etc.) fails."""

    def __init__(self, service: str, message: str, status_code: Optional[int] = None):
        self.service = service
        self.message = message
        self.status_code = status_code
        super().__init__(f"{service}: {message}")


async def business_logic_exception_handler(
    request: Request, exc: BusinessLogicError
) -> JSONResponse:
    """Handle business logic errors."""
    logger.warning(f"📊 BUSINESS LOGIC ERROR: {exc.message} - Path: {request.url.path}")

    return JSONResponse(
        status_code=status.HTTP_400_BAD_REQUEST,
        content=ErrorResponse.create(
            error_type="business_logic_error",
            message=exc.message,
            details=exc.details,
            status_code=400,
        ),
    )


async def external_service_exception_handler(
    request: Request, exc: ExternalServiceError
) -> JSONResponse:
    """Handle external service errors."""
    logger.error(
        f"🌐 EXTERNAL SERVICE ERROR ({exc.service}): {exc.message} - Path: {request.url.path}"
    )

    status_code = exc.status_code or status.HTTP_502_BAD_GATEWAY

    return JSONResponse(
        status_code=status_code,
        content=ErrorResponse.create(
            error_type="external_service_error",
            message=f"External service ({exc.service}) is currently unavailable",
            details={"service": exc.service},
            status_code=status_code,
        ),
    )


# Helper function to check if exception is database-related
def is_database_exception(exc: Exception) -> bool:
    """Check if an exception is database-related."""
    return (
        isinstance(exc, (asyncpg.PostgresError, asyncpg.ConnectionDoesNotExistError))
        or "connection" in str(exc).lower()
        or "database" in str(exc).lower()
        or "asyncpg" in str(type(exc).__module__)
    )
