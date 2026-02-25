from typing import Optional
from typing_extensions import TypedDict


class MessageMetadata(TypedDict, total=False):
    """
    Metadata attached to OpenAI messages for classification / consolidation.

    Keys are optional to keep backwards compatibility with other message sources.
    """

    # Generic
    source: str
    tool_name: str

    # Facebook context fetch specific
    item_id: str
    item_type: str
    normalized_item_type: str
    page: int
    page_size: int
    total_count: int
    total_pages: int
    has_next_page: bool
    next_page: Optional[int]  # None when has_next_page is False
