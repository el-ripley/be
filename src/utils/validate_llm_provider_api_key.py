"""
OpenAI API Key Validation Utility
Only supports OpenAI for BYOK (Bring Your Own Key)
"""

import time

import openai

from src.utils.logger import get_logger

logger = get_logger()


def validate_openai_api_key(api_key: str) -> tuple[bool, str]:
    """
    Validate OpenAI API key by making a test call.

    Args:
        api_key: OpenAI API key to validate

    Returns:
        Tuple of (is_valid, error_message)
        - (True, "") if valid
        - (False, "error message") if invalid
    """
    if not api_key or not api_key.strip():
        return False, "Empty API key"

    logger.info("Validating OpenAI API key...")

    try:
        client = openai.OpenAI(api_key=api_key, timeout=10.0)

        # Make a minimal test call to validate the key
        start_time = time.time()
        client.models.list()
        elapsed_time = time.time() - start_time

        logger.info(f"OpenAI API key validation successful ({elapsed_time:.2f}s)")
        return True, ""

    except openai.AuthenticationError as e:
        logger.warning(f"OpenAI API key authentication failed: {str(e)}")
        return False, "Invalid OpenAI API key - authentication failed"

    except openai.RateLimitError:
        logger.warning("OpenAI API rate limit exceeded (but key is valid)")
        # Rate limit means the key is valid but quota exceeded
        return True, ""

    except openai.APIConnectionError as e:
        logger.error(f"OpenAI API connection error: {str(e)}")
        return False, "Cannot connect to OpenAI API - please check your network"

    except openai.APITimeoutError:
        logger.error("OpenAI API timeout")
        return False, "OpenAI API request timed out - please try again"

    except Exception as e:
        logger.error(f"OpenAI API key validation error: {str(e)}")
        return False, f"OpenAI API key validation failed: {str(e)}"
