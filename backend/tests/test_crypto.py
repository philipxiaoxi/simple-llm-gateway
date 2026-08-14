import pytest

from app.crypto import (
    decrypt_secret,
    encrypt_secret,
    generate_api_key,
    hash_api_key,
    hash_password,
    verify_password,
)


def test_encrypt_decrypt_roundtrip() -> None:
    secret_key = "unit-test-master-key"
    token = encrypt_secret("sk-hello", secret_key)
    assert token != "sk-hello"
    assert decrypt_secret(token, secret_key) == "sk-hello"


def test_decrypt_fails_with_different_master_key() -> None:
    token = encrypt_secret("sk-hello", "key-a")
    with pytest.raises(ValueError):
        decrypt_secret(token, "key-b")


def test_hash_api_key_is_stable_and_not_plaintext() -> None:
    first = hash_api_key("sk-same")
    second = hash_api_key("sk-same")
    assert first == second
    assert first != "sk-same"
    assert len(first) == 64


def test_generate_api_key_has_prefix() -> None:
    value = generate_api_key()
    assert value.startswith("sk-")
    assert len(value) > 20


def test_password_hash_verifies() -> None:
    hashed = hash_password("admin123")
    assert hashed != "admin123"
    assert verify_password("admin123", hashed)
    assert not verify_password("wrong", hashed)
