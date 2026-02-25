"""
MongoDB schema for Facebook webhook events.

This schema defines the structure for storing raw Facebook webhook events
for analysis and processing.
"""

from datetime import datetime
from typing import Dict, Any, Optional, List
from dataclasses import dataclass
from bson import ObjectId


@dataclass
class WebhookEventData:
    """Data structure for Facebook webhook events."""

    # MongoDB document ID
    id: Optional[str] = None

    # Event metadata
    event_type: str = ""  # e.g., "messages", "messaging_postbacks", "feed"
    event_subtype: str = ""  # e.g., "message", "postback", "comment", "post"
    webhook_object: str = ""  # "page" for Facebook pages

    # Facebook specific identifiers
    page_id: Optional[str] = None
    sender_id: Optional[str] = None
    recipient_id: Optional[str] = None
    message_id: Optional[str] = None
    post_id: Optional[str] = None
    comment_id: Optional[str] = None

    # Raw event data
    raw_data: Dict[str, Any] = None

    # Request metadata
    signature: Optional[str] = None
    headers: Dict[str, str] = None

    # Timestamps
    created_at: datetime = None
    event_timestamp: Optional[int] = None  # Facebook's timestamp

    # Processing metadata
    processed: bool = False
    processed_at: Optional[datetime] = None
    processing_errors: List[str] = None

    # Analysis metadata
    tags: List[str] = None
    notes: Optional[str] = None

    def __post_init__(self):
        """Initialize default values after dataclass creation."""
        if self.created_at is None:
            self.created_at = datetime.utcnow()
        if self.raw_data is None:
            self.raw_data = {}
        if self.headers is None:
            self.headers = {}
        if self.processing_errors is None:
            self.processing_errors = []
        if self.tags is None:
            self.tags = []


class WebhookEventSchema:
    """Schema utilities for webhook events."""

    @staticmethod
    def to_document(data: WebhookEventData) -> Dict[str, Any]:
        """Convert WebhookEventData to MongoDB document."""
        doc = {
            "event_type": data.event_type,
            "event_subtype": data.event_subtype,
            "webhook_object": data.webhook_object,
            "page_id": data.page_id,
            "sender_id": data.sender_id,
            "recipient_id": data.recipient_id,
            "message_id": data.message_id,
            "post_id": data.post_id,
            "comment_id": data.comment_id,
            "raw_data": data.raw_data,
            "signature": data.signature,
            "headers": data.headers,
            "created_at": data.created_at,
            "event_timestamp": data.event_timestamp,
            "processed": data.processed,
            "processed_at": data.processed_at,
            "processing_errors": data.processing_errors,
            "tags": data.tags,
            "notes": data.notes,
        }

        # Add _id if provided
        if data.id:
            doc["_id"] = ObjectId(data.id) if isinstance(data.id, str) else data.id

        return doc

    @staticmethod
    def from_document(doc: Dict[str, Any]) -> WebhookEventData:
        """Convert MongoDB document to WebhookEventData."""
        return WebhookEventData(
            id=str(doc.get("_id")) if doc.get("_id") else None,
            event_type=doc.get("event_type", ""),
            event_subtype=doc.get("event_subtype", ""),
            webhook_object=doc.get("webhook_object", ""),
            page_id=doc.get("page_id"),
            sender_id=doc.get("sender_id"),
            recipient_id=doc.get("recipient_id"),
            message_id=doc.get("message_id"),
            post_id=doc.get("post_id"),
            comment_id=doc.get("comment_id"),
            raw_data=doc.get("raw_data", {}),
            signature=doc.get("signature"),
            headers=doc.get("headers", {}),
            created_at=doc.get("created_at"),
            event_timestamp=doc.get("event_timestamp"),
            processed=doc.get("processed", False),
            processed_at=doc.get("processed_at"),
            processing_errors=doc.get("processing_errors", []),
            tags=doc.get("tags", []),
            notes=doc.get("notes"),
        )

    @staticmethod
    def extract_event_info(webhook_data: Dict[str, Any]) -> Dict[str, Any]:
        """Extract key information from raw webhook data."""
        info = {
            "event_type": "",
            "event_subtype": "",
            "page_id": None,
            "sender_id": None,
            "recipient_id": None,
            "message_id": None,
            "post_id": None,
            "comment_id": None,
            "event_timestamp": None,
        }

        # Extract webhook object
        _ = webhook_data.get("object", "")

        # Process entries
        entries = webhook_data.get("entry", [])
        if not entries:
            return info

        entry = entries[0]  # Process first entry
        page_id = entry.get("id")
        info["page_id"] = page_id

        # Process different event types
        for event_type in ["messaging", "changes", "standby"]:
            if event_type in entry:
                info["event_type"] = event_type

                events = entry[event_type]
                if events and len(events) > 0:
                    event = events[0]  # Process first event

                    if event_type == "messaging":
                        info["sender_id"] = event.get("sender", {}).get("id")
                        info["recipient_id"] = event.get("recipient", {}).get("id")
                        info["event_timestamp"] = event.get("timestamp")

                        # Determine messaging subtype
                        if "message" in event:
                            info["event_subtype"] = "message"
                            info["message_id"] = event.get("message", {}).get("mid")
                        elif "postback" in event:
                            info["event_subtype"] = "postback"
                        elif "delivery" in event:
                            info["event_subtype"] = "delivery"
                        elif "read" in event:
                            info["event_subtype"] = "read"
                        elif "reaction" in event:
                            info["event_subtype"] = "reaction"

                    elif event_type == "changes":
                        info["event_timestamp"] = event.get("time")
                        field = event.get("field", "")

                        if field == "feed":
                            info["event_subtype"] = "feed"
                            value = event.get("value", {})
                            info["post_id"] = value.get("post_id")
                            info["comment_id"] = value.get("comment_id")
                            info["sender_id"] = value.get("from", {}).get("id")

                break

        return info

    @staticmethod
    def get_collection_indexes() -> List[Dict[str, Any]]:
        """Get recommended indexes for webhook events collection."""
        return [
            # Query by event type and timestamp
            [("event_type", 1), ("created_at", -1)],
            # Query by page and event type
            [("page_id", 1), ("event_type", 1), ("created_at", -1)],
            # Query by sender
            [("sender_id", 1), ("created_at", -1)],
            # Query by message ID
            [("message_id", 1)],
            # Query by post ID
            [("post_id", 1)],
            # Query by processing status
            [("processed", 1), ("created_at", -1)],
            # Query by event timestamp
            [("event_timestamp", -1)],
            # Compound index for common queries
            [("page_id", 1), ("event_type", 1), ("processed", 1), ("created_at", -1)],
        ]
