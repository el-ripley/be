from typing import TYPE_CHECKING, Any, Dict, Tuple

import asyncpg

if TYPE_CHECKING:
    from src.database.postgres.entities.user_entities import User

from src.database.postgres.connection import async_db_transaction
from src.database.postgres.repositories.facebook_queries import (
    create_facebook_app_scope_user,
    get_facebook_app_scope_user_by_id,
    update_facebook_app_scope_user,
)
from src.database.postgres.repositories.user_queries import (
    assign_role_to_user_by_name,
    create_user,
    get_comprehensive_user_info,
    get_user_with_roles,
)
from src.services.auth_service import AuthService
from src.utils.logger import get_logger

logger = get_logger()


class UserService:
    def __init__(self, auth_service: AuthService):
        self.auth_service = auth_service

    async def ensure_user_with_facebook_profile(
        self, facebook_user_id: str, user_info: Dict[str, Any]
    ) -> Tuple[str, "User"]:
        """
        Get or create user by Facebook ASID.
        Returns: (internal_user_id, User object with roles)
        """
        from src.database.postgres.entities.user_entities import Role, User

        async with async_db_transaction() as conn:
            # Check if FacebookAppScopeUser exists
            fb_user = await get_facebook_app_scope_user_by_id(conn, facebook_user_id)

            if fb_user:
                # Existing user - always update profile data (no change checking)
                await self._update_facebook_profile(conn, facebook_user_id, user_info)

                # Get user with roles from database
                user_data = await get_user_with_roles(conn, fb_user["user_id"])
                if not user_data:
                    raise ValueError(f"User not found: {fb_user['user_id']}")

                # Convert to User object with roles
                roles = []
                if user_data.get("roles"):
                    roles = [Role(**role_data) for role_data in user_data["roles"]]

                user = User(
                    id=user_data["id"],
                    created_at=user_data["created_at"],
                    updated_at=user_data["updated_at"],
                    roles=roles,
                )

                return fb_user["user_id"], user
            else:
                logger.info(f"🆕 USER SERVICE: Creating new user: {facebook_user_id}")

                # New user - create everything
                internal_user_id = await self._create_new_user_with_facebook_profile(
                    conn, facebook_user_id, user_info
                )

                # Get user with roles from database
                user_data = await get_user_with_roles(conn, internal_user_id)
                if not user_data:
                    raise ValueError(
                        f"User not found after creation: {internal_user_id}"
                    )

                # Convert to User object with roles
                roles = []
                if user_data.get("roles"):
                    roles = [Role(**role_data) for role_data in user_data["roles"]]

                user = User(
                    id=user_data["id"],
                    created_at=user_data["created_at"],
                    updated_at=user_data["updated_at"],
                    roles=roles,
                )

                logger.info(f"✅ USER SERVICE: New user created: {internal_user_id}")
                return internal_user_id, user

    async def _update_facebook_profile(
        self, conn: asyncpg.Connection, facebook_user_id: str, user_info: Dict[str, Any]
    ):
        """Always update FacebookAppScopeUser profile data."""
        await update_facebook_app_scope_user(
            conn,
            facebook_user_id,
            name=user_info.get("name"),
            email=user_info.get("email"),
            picture=user_info.get("picture", {}).get("data", {}).get("url"),
            gender=user_info.get("gender"),
        )

    async def _create_new_user_with_facebook_profile(
        self, conn: asyncpg.Connection, facebook_user_id: str, user_info: Dict[str, Any]
    ) -> str:
        """Create User + FacebookAppScopeUser + Role + Credit Balance."""
        # 1. Create internal user
        internal_user_id = await create_user(conn)

        # 2. Create Facebook app scope user
        await create_facebook_app_scope_user(
            conn,
            facebook_user_id,
            internal_user_id,
            name=user_info.get("name"),
            email=user_info.get("email"),
            picture=user_info.get("picture", {}).get("data", {}).get("url"),
            gender=user_info.get("gender"),
        )

        # 3. Assign default "user" role
        await assign_role_to_user_by_name(conn, internal_user_id, "user")

        # 4. Initialize credit balance with $3 free credits
        from src.billing.credit_service import initialize_user_credits

        await initialize_user_credits(conn, internal_user_id)

        # Create balance record with $0 to prevent auto-creation with $3 default
        from decimal import Decimal

        from src.billing.repositories import billing_queries

        await billing_queries.get_or_create_user_credit_balance(
            conn, internal_user_id, Decimal("0")
        )

        # 5. Generate unique topup code for SePay
        from src.billing.repositories import sepay_queries

        await sepay_queries.get_or_create_topup_code(conn, internal_user_id)

        return internal_user_id

    async def get_user_comprehensive_info(self, user_id: str) -> Dict[str, Any]:
        """
        Get comprehensive user information including all related data.
        Returns structured data with user, roles, Facebook info, and page admin details.
        """
        async with async_db_transaction() as conn:
            user_info = await get_comprehensive_user_info(conn, user_id)

            if not user_info:
                logger.warning(f"User not found: {user_id}")
                return None

            logger.info(
                f"✅ USER SERVICE: Retrieved comprehensive info for user: {user_id}"
            )
            return user_info
