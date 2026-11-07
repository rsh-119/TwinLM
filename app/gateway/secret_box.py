"""Encryption at rest for provider API keys added from the admin panel.
Fernet (AES-128-CBC + HMAC) with a key derived from PROVIDER_KEYS_SECRET,
so any random string works as the secret (use a random one: a single
SHA-256 does not slow down guessing a weak passphrase). Losing the secret
makes stored keys unreadable; they then have to be entered again. To change
it, set the old one as PROVIDER_KEYS_SECRET_PREVIOUS and run
`python -m app.ops rotate-provider-keys`."""
import base64
import hashlib
from pathlib import Path
from typing import Optional

from cryptography.fernet import Fernet, InvalidToken, MultiFernet

from app.config import Settings, get_settings


class SecretUnavailable(RuntimeError):
    """No PROVIDER_KEYS_SECRET configured, or it can't decrypt a stored key."""


def _secret(settings: Settings) -> str:
    if settings.provider_keys_secret:
        return settings.provider_keys_secret
    if settings.provider_keys_secret_file:
        path = Path(settings.provider_keys_secret_file)
        if path.is_file():
            return path.read_text(encoding="utf-8").strip()
    return ""


def _key(secret: str) -> Fernet:
    return Fernet(base64.urlsafe_b64encode(hashlib.sha256(secret.encode()).digest()))


def _fernet(settings: Optional[Settings] = None) -> MultiFernet:
    """Encrypts with the current secret; decrypts with it or the previous one."""
    settings = settings or get_settings()
    secret = _secret(settings)
    if not secret:
        raise SecretUnavailable(
            "set PROVIDER_KEYS_SECRET (or PROVIDER_KEYS_SECRET_FILE) to store provider API keys"
        )
    previous = settings.provider_keys_secret_previous
    return MultiFernet([_key(secret)] + ([_key(previous)] if previous else []))


def encrypt(plaintext: str, settings: Optional[Settings] = None) -> str:
    return _fernet(settings).encrypt(plaintext.encode()).decode()


def decrypt(token: str, settings: Optional[Settings] = None) -> str:
    try:
        return _fernet(settings).decrypt(token.encode()).decode()
    except InvalidToken as exc:
        raise SecretUnavailable("stored provider key can't be decrypted: PROVIDER_KEYS_SECRET changed?") from exc


def rotate(token: str, settings: Optional[Settings] = None) -> str:
    """Re-encrypt a stored key with the current secret."""
    try:
        return _fernet(settings).rotate(token.encode()).decode()
    except InvalidToken as exc:
        raise SecretUnavailable("stored provider key can't be decrypted with either secret") from exc


def hint(key: str) -> str:
    """What may be shown of a key: its last 4 characters."""
    return f"…{key[-4:]}" if len(key) >= 8 else "…"
