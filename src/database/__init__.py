"""
Multi-database abstraction layer.

This module provides unified access to different database systems
while maintaining database-specific optimizations and native performance.

Architecture:
- Each database (postgres, mongo, etc.) has its own async implementation
- Repository pattern provides clean interface to service layer
- Native async database drivers for maximum performance
"""

# Database-specific imports
from . import postgres
from . import mongo

# Future database imports
# from . import redis
# from . import neo4j
# from . import qdrant
# from . import elasticsearch

__all__ = [
    "postgres",
    "mongo",
    # "redis",
    # "neo4j",
    # "qdrant",
    # "elasticsearch",
]
