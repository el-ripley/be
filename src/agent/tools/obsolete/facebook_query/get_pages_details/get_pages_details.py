"""Get pages details tool - get detailed information about Facebook pages.

TOOL_RESULT STRUCTURE (what agent sees):

function_call_output (output_message.function_output):
   {
     "success": true,
     "pages": [
       {
         "id": "page_123",
         "name": "Page Name",
         "avatar": "https://...",
         "category": "Business",
         "fan_count": 1000,
         "followers_count": 950,
         "rating_count": 50,
         "overall_star_rating": 4.5,
         "about": "Short description",
         "description": "Long description",
         "link": "https://facebook.com/...",
         "website": "https://example.com",
         "phone": "+1234567890",
         "emails": ["contact@example.com"],
         "location": {
           "street": "123 Main St",
           "city": "City",
           "state": "State",
           "country": "Country"
         },
         "cover": "https://...",
         "hours": {
           "mon_1_open": "09:00",
           "mon_1_close": "17:00"
         },
         "is_verified": true,
         "created_at": 1234567890,
         "updated_at": 1234567890
       }
     ],
     "total_count": 1,
     "requested_fields": ["name", "fan_count", "followers_count"]
   }
"""

import uuid
import time
import textwrap
from typing import Any, Dict

import asyncpg

from src.agent.tools.base import BaseTool, ToolCallContext, ToolResult
from src.api.openai_conversations.schemas import MessageResponse
from src.database.postgres.repositories.facebook_queries.pages import (
    get_pages_by_ids_with_fields,
)
from src.utils.logger import get_logger

logger = get_logger()


TOOL_DESCRIPTION = """
Get detailed information about one or more Facebook pages by their IDs.

WHEN TO USE:
- When you need specific information about pages (e.g., fan count, description, location)
- To get page statistics like followers, ratings, or engagement metrics
- To retrieve page contact information (phone, email, website)
- To check page verification status or business hours
- When you want to select only specific fields to reduce response size

PARAMETERS:
- page_ids: List of Facebook page IDs to query. At least one page ID is required.
- fields: Optional list of specific fields to retrieve. If not specified, all fields are returned.

RETURNS: Array of page objects with requested fields. Only pages that exist in the database are returned.
"""


# Valid fields that can be requested
VALID_FIELDS = [
    "id",
    "name",
    "avatar",
    "category",
    "fan_count",
    "followers_count",
    "rating_count",
    "overall_star_rating",
    "about",
    "description",
    "link",
    "website",
    "phone",
    "emails",
    "location",
    "cover",
    "hours",
    "is_verified",
    "created_at",
    "updated_at",
]


def _create_function_call_output(
    conv_id: str,
    call_id: str,
    function_output: Any,
) -> MessageResponse:
    """Create a function_call_output message."""
    output_uuid = str(uuid.uuid4())
    current_time = int(time.time() * 1000)

    return MessageResponse(
        id=output_uuid,
        conversation_id=conv_id,
        sequence_number=0,
        type="function_call_output",
        role="tool",
        content=None,
        call_id=call_id,
        function_output=function_output,
        status="completed",
        metadata=None,
        created_at=current_time,
        updated_at=current_time,
    )


class GetPagesDetailsTool(BaseTool):
    """Tool to get detailed information about Facebook pages."""

    @property
    def name(self) -> str:
        return "get_pages_details"

    @property
    def definition(self) -> Dict[str, Any]:
        return {
            "type": "function",
            "name": self.name,
            "description": TOOL_DESCRIPTION,
            "parameters": {
                "type": "object",
                "properties": {
                    "page_ids": {
                        "type": "array",
                        "items": {"type": "string"},
                        "minItems": 1,
                        "description": 'List of Facebook page IDs to query. At least one page ID is required. Example: ["123456789", "987654321"]',
                    },
                    "fields": {
                        "type": "array",
                        "items": {
                            "type": "string",
                            "enum": VALID_FIELDS,
                        },
                        "description": textwrap.dedent(
                            """
                            Optional list of specific fields to retrieve. If not provided, all fields are returned.

                            Available fields:
                            - id, name: Basic page identifiers
                            - avatar, cover: Profile and cover images (URLs)
                            - category: Page category/type
                            - fan_count, followers_count: Audience metrics
                            - rating_count, overall_star_rating: Review statistics (0-5 scale)
                            - about: Short description
                            - description: Full description
                            - link: Facebook page URL
                            - website, phone, emails: Contact information
                            - location: Physical address (object with street, city, state, country)
                            - hours: Business hours (object with day_open/close times)
                            - is_verified: Verification badge status (boolean)
                            - created_at, updated_at: Timestamps (Unix milliseconds)

                            Example: ["name", "fan_count", "followers_count", "location"]
                        """
                        ).strip(),
                    },
                },
                "required": ["page_ids"],
                "additionalProperties": False,
            },
            "strict": False,
        }

    async def execute(
        self,
        conn: asyncpg.Connection,
        context: ToolCallContext,
        arguments: Dict[str, Any],
    ) -> Any:
        """Execute the tool - get pages details."""
        page_ids = arguments.get("page_ids", [])
        fields = arguments.get("fields")

        # Validate page_ids
        if not page_ids or not isinstance(page_ids, list):
            return {
                "success": False,
                "error": "page_ids is required and must be a non-empty array",
                "pages": [],
                "total_count": 0,
            }

        # Validate fields if provided
        if fields is not None:
            if not isinstance(fields, list):
                return {
                    "success": False,
                    "error": "fields must be an array if provided",
                    "pages": [],
                    "total_count": 0,
                }
            # Filter to only valid fields
            valid_fields = [f for f in fields if f in VALID_FIELDS]
            if fields and not valid_fields:
                return {
                    "success": False,
                    "error": "None of the provided fields are valid. Valid fields: "
                    + ", ".join(VALID_FIELDS),
                    "pages": [],
                    "total_count": 0,
                }
            fields = valid_fields if valid_fields else None

        try:
            # Query pages from database
            pages = await get_pages_by_ids_with_fields(
                conn, page_ids=page_ids, fields=fields
            )

            return {
                "success": True,
                "pages": pages,
                "total_count": len(pages),
                "requested_page_ids": page_ids,
                "requested_fields": fields if fields else "all",
            }

        except Exception as e:
            logger.error(f"Error getting pages details: {e}")
            return {
                "success": False,
                "error": str(e),
                "pages": [],
                "total_count": 0,
            }

    def process_result(self, context: ToolCallContext, raw_result: Any) -> ToolResult:
        """Process raw result into ToolResult."""
        if isinstance(raw_result, dict) and raw_result.get("success"):
            total_count = raw_result.get("total_count", 0)
            requested_count = len(raw_result.get("requested_page_ids", []))

            if total_count == 0:
                summary = "No pages found for the requested page IDs."
            elif total_count < requested_count:
                summary = f"Found {total_count} out of {requested_count} requested page(s). Some page IDs may not exist in the database."
            else:
                summary = f"Retrieved details for {total_count} page(s)."
        else:
            summary = "Failed to retrieve page details."
            error = (
                raw_result.get("error", "Unknown error")
                if isinstance(raw_result, dict)
                else str(raw_result)
            )
            summary = f"{summary} Error: {error}"

        output_message = _create_function_call_output(
            conv_id=context.conv_id,
            call_id=context.call_id,
            function_output=(
                raw_result
                if isinstance(raw_result, dict)
                else {"error": str(raw_result), "success": False}
            ),
        )

        return ToolResult(
            output_message=output_message,
            human_message=None,
            metadata=None,
        )
