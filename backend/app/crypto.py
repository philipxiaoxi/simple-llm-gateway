from __future__ import annotations

import base64
import hashlib
import secrets

import bcrypt
from cryptography.fernet import Fernet, InvalidToken
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.kdf.pbkdf2 import PBKDF2HMAC

EXPORT_KDF = "pbkdf2_sha256"
EXPORT_ITERATIONS = 600_000
MIN_EXPORT_PASSWORD_LENGTH = 8


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


def _fernet_from_password(password: str, salt: bytes, iterations: int) -> Fernet:
    derived = PBKDF2HMAC(
        algorithm=hashes.SHA256(),
        length=32,
        salt=salt,
        iterations=iterations,
    ).derive(password.encode("utf-8"))
    return Fernet(base64.urlsafe_b64encode(derived))


def encrypt_with_password(plaintext: str, password: str) -> dict[str, str | int]:
    if len(password) < MIN_EXPORT_PASSWORD_LENGTH:
        raise ValueError(f"密码至少 {MIN_EXPORT_PASSWORD_LENGTH} 位")
    salt = secrets.token_bytes(16)
    token = _fernet_from_password(password, salt, EXPORT_ITERATIONS).encrypt(plaintext.encode("utf-8"))
    return {
        "version": 1,
        "kdf": EXPORT_KDF,
        "iterations": EXPORT_ITERATIONS,
        "salt": base64.b64encode(salt).decode("ascii"),
        "ciphertext": token.decode("ascii"),
    }


def decrypt_with_password(envelope: dict[str, object], password: str) -> str:
    if envelope.get("kdf") != EXPORT_KDF:
        raise ValueError("不支持的加密格式")
    try:
        iterations = int(envelope.get("iterations") or 0)
        salt = base64.b64decode(str(envelope.get("salt") or ""))
        ciphertext = str(envelope.get("ciphertext") or "")
    except (TypeError, ValueError) as error:
        raise ValueError("加密文件格式不正确") from error
    if iterations < 100_000 or not salt or not ciphertext:
        raise ValueError("加密文件格式不正确")
    try:
        return _fernet_from_password(password, salt, iterations).decrypt(ciphertext.encode("ascii")).decode("utf-8")
    except InvalidToken as error:
        raise ValueError("密码错误或文件已损坏") from error


def hash_password(password: str) -> str:
    return bcrypt.hashpw(password.encode("utf-8"), bcrypt.gensalt()).decode("ascii")


def verify_password(password: str, password_hash: str) -> bool:
    return bcrypt.checkpw(password.encode("utf-8"), password_hash.encode("ascii"))
