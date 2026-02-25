"""
API Key Resolver - Simple function to get system API key.
BYOK (Bring Your Own Key) has been removed - all users use system key.
"""

from src.settings import settings
from src.utils.logger import get_logger

logger = get_logger()


def get_system_api_key() -> str:
    """
    Get system OpenAI API key from environment settings.

    Returns:
        str: System OpenAI API key

    Raises:
        ValueError: If OPENAI_API_KEY is not configured
    """
    if not settings.openai_api_key:
        logger.error("OPENAI_API_KEY not configured in environment")
        raise ValueError("System OpenAI API key not configured")

    return settings.openai_api_key
