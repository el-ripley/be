"""
MongoDB schemas for document data structures.

This module defines document schemas for MongoDB collections,
focusing on rich document data like user profiles and analytics.
"""

from .webhook_event_schema import WebhookEventSchema, WebhookEventData

__all__ = [
    # Webhook event schemas
    "WebhookEventSchema",
    "WebhookEventData",
]
