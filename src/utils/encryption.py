"""
AES-256 encryption/decryption utilities for sensitive data.
Used for encrypting API keys in BYOK (Bring Your Own Key) system.
"""

import base64
from typing import Optional
from cryptography.fernet import Fernet
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.kdf.pbkdf2 import PBKDF2HMAC
from src.utils.logger import get_logger

logger = get_logger()


class EncryptionManager:
    """Manages encryption and decryption of sensitive data using AES-256."""

    def __init__(self, encryption_key: str):
        if not encryption_key:
            raise ValueError("Encryption key cannot be empty")

        self._fernet = self._initialize_fernet(encryption_key)

    def _initialize_fernet(self, password: str) -> Fernet:
        kdf = PBKDF2HMAC(
            algorithm=hashes.SHA256(),
            length=32,
            salt=b"el-ripley-byok-salt",  # Static salt for consistency
            iterations=100000,
        )
        key = base64.urlsafe_b64encode(kdf.derive(password.encode()))
        return Fernet(key)

    def encrypt(self, plaintext: str) -> str:
        try:
            encrypted_bytes = self._fernet.encrypt(plaintext.encode())
            # Return as string for database storage
            return encrypted_bytes.decode("utf-8")
        except Exception as e:
            logger.error(f"Encryption failed: {str(e)}")
            raise

    def decrypt(self, encrypted_text: str) -> str:
        try:
            decrypted_bytes = self._fernet.decrypt(encrypted_text.encode())
            return decrypted_bytes.decode("utf-8")
        except Exception as e:
            logger.error(f"Decryption failed: {str(e)}")
            raise


# Singleton instance
_encryption_manager: Optional[EncryptionManager] = None


def get_encryption_manager(encryption_key: Optional[str] = None) -> EncryptionManager:
    global _encryption_manager

    if _encryption_manager is None:
        if encryption_key is None:
            raise ValueError("Encryption key required for first initialization")
        _encryption_manager = EncryptionManager(encryption_key)

    return _encryption_manager


def encrypt_api_key(api_key: str) -> str:
    manager = get_encryption_manager()
    return manager.encrypt(api_key)


def decrypt_api_key(encrypted_key: str) -> str:
    manager = get_encryption_manager()
    return manager.decrypt(encrypted_key)
