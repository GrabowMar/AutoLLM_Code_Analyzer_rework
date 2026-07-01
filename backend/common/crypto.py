"""Symmetric encryption helpers backed by Django's SECRET_KEY.

Used to encrypt user-provided third-party API credentials at rest. The
Fernet key is derived deterministically from ``settings.SECRET_KEY`` so
existing ciphertexts remain decryptable across restarts as long as the
SECRET_KEY is unchanged.

If you rotate ``SECRET_KEY`` in production, existing encrypted credentials
will become unreadable and users will need to re-enter them. This is the
intended security property: secrets are bound to the deployment's key.
"""

from __future__ import annotations

import base64
import hashlib

from cryptography.fernet import Fernet
from cryptography.fernet import InvalidToken
from django.conf import settings


class DecryptionError(Exception):
    """Raised when ciphertext cannot be decrypted (corrupt or wrong key)."""


def _derive_fernet_key() -> bytes:
    """Derive a 32-byte url-safe base64 Fernet key from SECRET_KEY."""
    digest = hashlib.sha256(settings.SECRET_KEY.encode("utf-8")).digest()
    return base64.urlsafe_b64encode(digest)


def _fernet() -> Fernet:
    return Fernet(_derive_fernet_key())


def encrypt(plaintext: str) -> str:
    """Encrypt a string, returning url-safe base64 ciphertext."""
    if plaintext is None:
        msg = "Cannot encrypt None"
        raise ValueError(msg)
    token = _fernet().encrypt(plaintext.encode("utf-8"))
    return token.decode("ascii")


def decrypt(ciphertext: str) -> str:
    """Decrypt a token produced by :func:`encrypt`."""
    if not ciphertext:
        msg = "Cannot decrypt empty ciphertext"
        raise DecryptionError(msg)
    try:
        return _fernet().decrypt(ciphertext.encode("ascii")).decode("utf-8")
    except InvalidToken as exc:
        msg = "Invalid ciphertext or wrong SECRET_KEY"
        raise DecryptionError(msg) from exc
