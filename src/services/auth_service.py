from datetime import datetime, timedelta, timezone
from typing import Dict, Optional, Tuple
import jwt
import asyncpg
from fastapi import Request

from src.database.postgres.entities.user_entities import User, Role
from src.database.postgres.repositories.user_queries import (
    get_user_with_roles,
    create_refresh_token,
    get_refresh_token_by_token,
    revoke_refresh_token,
)
from src.database.postgres.utils import get_current_timestamp
from src.settings import settings


class AuthService:
    """Service for authentication and JWT token handling with access/refresh tokens."""

    def __init__(self):
        self.secret_key = settings.jwt_secret_key
        self.algorithm = settings.jwt_algorithm

        # Token expiration settings
        self.access_token_expire_minutes = settings.access_token_expire_minutes
        self.refresh_token_expire_days = settings.refresh_token_expire_days

    def create_access_token(self, user: User) -> str:
        """Create short-lived access token for authenticated user."""
        expire = datetime.now(timezone.utc) + timedelta(
            minutes=self.access_token_expire_minutes
        )

        # Extract role names from user.roles if they exist
        role_names = []
        if user.roles:
            role_names = [role.name for role in user.roles]

        payload = {
            "sub": user.id,  # subject (user_id)
            "exp": expire,  # expiration time
            "roles": role_names,
            "iat": datetime.now(timezone.utc),  # issued at time
            "type": "access",  # token type
        }

        # Create and return the JWT token
        token = jwt.encode(payload, self.secret_key, algorithm=self.algorithm)
        return token

    def create_refresh_token_jwt(self, user: User) -> str:
        """Create long-lived refresh token for authenticated user."""
        expire = datetime.now(timezone.utc) + timedelta(
            days=self.refresh_token_expire_days
        )

        payload = {
            "sub": user.id,  # subject (user_id)
            "exp": expire,  # expiration time
            "iat": datetime.now(timezone.utc),  # issued at time
            "type": "refresh",  # token type
        }

        # Create and return the JWT token
        token = jwt.encode(payload, self.secret_key, algorithm=self.algorithm)
        return token

    def get_user_from_token(self, token: str) -> Optional[Dict]:
        """Extract user data from any valid JWT token."""
        try:
            payload = jwt.decode(token, self.secret_key, algorithms=[self.algorithm])
            return {"id": payload.get("sub"), "roles": payload.get("roles", [])}
        except jwt.PyJWTError:
            return None

    # ================================================================
    # TOKEN EXTRACTION FROM HEADERS
    # ================================================================

    def get_access_token_from_request(self, request: Request) -> Optional[str]:
        """Extract access token from Authorization header."""
        auth_header = request.headers.get("Authorization")
        if auth_header and auth_header.startswith("Bearer "):
            return auth_header.split(" ")[1]
        return None

    def get_refresh_token_from_request(self, request: Request) -> Optional[str]:
        """Extract refresh token from X-Refresh-Token header."""
        return request.headers.get("X-Refresh-Token")

    # ================================================================
    # TOKEN VALIDATION METHODS
    # ================================================================

    def validate_token(self, token: str, expected_type: str) -> Optional[Dict]:
        """Validate JWT token and return payload if valid for expected type."""
        try:
            payload = jwt.decode(token, self.secret_key, algorithms=[self.algorithm])
            if payload.get("type") == expected_type:
                return payload
            return None
        except jwt.PyJWTError:
            return None

    # ================================================================
    # AUTHENTICATION FLOW METHODS
    # ================================================================

    async def create_token_pair_for_user(
        self, conn: asyncpg.Connection, user: User
    ) -> Tuple[str, str]:
        """Create access and refresh token pair for user and store refresh token in DB."""
        # Create access token
        access_token = self.create_access_token(user)

        # Create refresh token JWT
        refresh_token = self.create_refresh_token_jwt(user)

        # Calculate refresh token expiry timestamp
        expires_at = int(
            (
                datetime.now(timezone.utc)
                + timedelta(days=self.refresh_token_expire_days)
            ).timestamp()
        )

        # Store refresh token in database
        await create_refresh_token(
            conn=conn,
            user_id=user.id,
            token=refresh_token,
            expires_at=expires_at,
        )

        return access_token, refresh_token

    async def refresh_access_token(
        self, conn: asyncpg.Connection, refresh_token: str
    ) -> Optional[Tuple[str, str]]:
        """Refresh access token using refresh token with rotation."""
        # Validate refresh token JWT
        refresh_payload = self.validate_token(refresh_token, "refresh")
        if not refresh_payload:
            return None

        # Check if refresh token exists in database and is active
        refresh_token_data = await get_refresh_token_by_token(conn, refresh_token)
        if not refresh_token_data:
            return None

        # Check if token is expired
        current_timestamp = get_current_timestamp()
        if refresh_token_data["expires_at"] <= current_timestamp:
            return None

        # Get user with roles
        user_data = await get_user_with_roles(conn, refresh_token_data["user_id"])
        if not user_data:
            return None

        # Convert to User object
        roles = []
        if user_data.get("roles"):
            roles = [Role(**role_data) for role_data in user_data["roles"]]

        user = User(
            id=user_data["id"],
            created_at=user_data["created_at"],
            updated_at=user_data["updated_at"],
            roles=roles,
        )

        # Revoke old refresh token (token rotation)
        await revoke_refresh_token(conn, refresh_token)

        # Create new token pair
        new_access_token, new_refresh_token = await self.create_token_pair_for_user(
            conn, user
        )

        return new_access_token, new_refresh_token
