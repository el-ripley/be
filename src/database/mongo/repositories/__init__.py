"""
MongoDB repositories for data access layer.

This module provides repository classes for MongoDB collections,
handling data operations for webhook events and other entities.
"""

from .webhook_event_repository import WebhookEventRepository

__all__ = [
    "WebhookEventRepository",
]
