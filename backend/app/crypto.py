from __future__ import annotations

import base64
import hashlib
import secrets

import bcrypt
from cryptography.fernet import Fernet, InvalidToken


def _fernet_from_secret(secret_key: str) -> Fernet:
    digest = hashlib.sha256(secret_key.encode("utf-8")).digest()
    return Fernet(base64.urlsafe_b64encode(digest))


def encrypt_secret(plaintext: str, secret_key: str) -> str:
    return _fernet_from_secret(secret_key).encrypt(plaintext.encode("utf-8")).decode("ascii")


def decrypt_secret(token: str, secret_key: str) -> str:
    try:
        return _fernet_from_secret(secret_key).decrypt(token.encode("ascii")).decode("utf-8")
    except InvalidToken as error:
        raise ValueError("无法解密密钥，APP_SECRET_KEY 可能已更换") from error


def hash_api_key(plaintext: str) -> str:
    return hashlib.sha256(plaintext.encode("utf-8")).hexdigest()


def generate_api_key() -> str:
    return "sk-" + secrets.token_urlsafe(32)


def key_prefix(plaintext: str) -> str:
    if len(plaintext) <= 10:
        return plaintext
    return plaintext[:7] + "…"


def hash_password(password: str) -> str:
    return bcrypt.hashpw(password.encode("utf-8"), bcrypt.gensalt()).decode("ascii")


def verify_password(password: str, password_hash: str) -> bool:
    return bcrypt.checkpw(password.encode("utf-8"), password_hash.encode("ascii"))
