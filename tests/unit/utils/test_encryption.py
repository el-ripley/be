"""Unit tests for EncryptionManager and encrypt/decrypt helpers."""

from unittest.mock import MagicMock, patch

import pytest

from src.utils.encryption import EncryptionManager, decrypt_api_key, encrypt_api_key


def test_encryption_manager_encrypt_decrypt_roundtrip() -> None:
    key = "test-secret-key-at-least-32-chars-long"
    manager = EncryptionManager(key)
    plain = "sensitive-data"
    encrypted = manager.encrypt(plain)
    assert encrypted != plain
    assert isinstance(encrypted, str)
    decrypted = manager.decrypt(encrypted)
    assert decrypted == plain


def test_encryption_manager_empty_key_raises() -> None:
    with pytest.raises(ValueError, match="cannot be empty"):
        EncryptionManager("")


def test_encryption_manager_wrong_key_decrypt_raises() -> None:
    m1 = EncryptionManager("key-one-at-least-32-characters-long-here")
    m2 = EncryptionManager("key-two-at-least-32-characters-long-here")
    encrypted = m1.encrypt("secret")
    with pytest.raises(Exception):
        m2.decrypt(encrypted)


def test_encrypt_api_key_requires_manager() -> None:
    with patch("src.utils.encryption.get_encryption_manager") as get_mgr:
        get_mgr.side_effect = ValueError(
            "Encryption key required for first initialization"
        )
        with pytest.raises(ValueError):
            encrypt_api_key("key")


def test_encrypt_decrypt_api_key_with_patched_manager() -> None:
    manager = EncryptionManager("test-secret-key-at-least-32-chars-long")
    with patch("src.utils.encryption.get_encryption_manager", return_value=manager):
        encrypted = encrypt_api_key("sk-secret")
        assert encrypted != "sk-secret"
        decrypted = decrypt_api_key(encrypted)
        assert decrypted == "sk-secret"
