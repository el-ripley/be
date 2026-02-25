"""Logging configuration module."""

import sys
from pathlib import Path
from typing import Dict, Optional, Union

from loguru import logger

from src.settings import settings


def setup_logger(
    log_level: Optional[str] = None,
    log_file: Optional[Union[str, Path]] = None,
    rotation: str = "100 MB",
) -> None:
    """Configure loguru logger.

    Args:
        log_level: Log level (default: from settings)
        log_file: Log file path (optional)
        rotation: Log rotation policy
    """
    # Use settings log level if not specified
    level = log_level or settings.log_level

    # Configure log format
    log_format = (
        "<green>{time:YYYY-MM-DD HH:mm:ss.SSS}</green> | "
        "<level>{level: <8}</level> | "
        "<cyan>{name}</cyan>:<cyan>{function}</cyan>:<cyan>{line}</cyan> | "
        "<level>{message}</level>"
    )

    # Configure default handlers
    config: Dict = {
        "handlers": [
            {
                "sink": sys.stderr,
                "format": log_format,
                "level": level,
                "colorize": True,
            },
        ]
    }

    # Add file logger if specified
    if log_file:
        log_path = Path(log_file)
        log_path.parent.mkdir(parents=True, exist_ok=True)

        config["handlers"].append(
            {
                "sink": str(log_path),
                "format": log_format,
                "level": level,
                "rotation": rotation,
                "retention": "10 days",
                "compression": "zip",
            }
        )

    # Configure logger
    logger.configure(**config)


# Initialize logger with default settings
setup_logger()


def get_logger(name: str = "ai-agent"):
    """Get a logger instance with the given name.

    Args:
        name: Logger name

    Returns:
        Logger instance
    """
    bound_logger = logger.bind(name=name)

    return bound_logger
