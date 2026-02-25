#!/usr/bin/env python3
"""
Generate a secure API key for SePay webhook authentication.

Usage:
    poetry run python scripts/generate_sepay_api_key.py

This will generate a random secure API key that you can use in SePay webhook configuration.
The key will be printed to stdout and can be added to your .env file as SEPAY_WEBHOOK_API_KEY.
"""

import secrets
import string


def generate_sepay_api_key(length: int = 32) -> str:
    """
    Generate a secure random API key for SePay webhook authentication.

    Args:
        length: Length of the API key (default: 32)

    Returns:
        A secure random API key string
    """
    # Use alphanumeric characters for the API key
    alphabet = string.ascii_letters + string.digits
    api_key = "".join(secrets.choice(alphabet) for _ in range(length))
    return api_key


if __name__ == "__main__":
    api_key = generate_sepay_api_key(32)
    print("\n" + "=" * 60)
    print("SePay Webhook API Key Generated")
    print("=" * 60)
    print(f"\nAPI Key: {api_key}\n")
    print("Add this to your .env file:")
    print(f"SEPAY_WEBHOOK_API_KEY={api_key}\n")
    print("Then configure this API key in SePay webhook settings.")
    print("=" * 60 + "\n")
