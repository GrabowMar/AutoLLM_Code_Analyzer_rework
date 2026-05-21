from __future__ import annotations

import pytest

from llm_lab.common import crypto


@pytest.mark.parametrize(
    "plaintext",
    ["sk-or-v1-abc123", "hello world", "🚀 unicode", "x" * 5000],
)
def test_encrypt_decrypt_roundtrip(plaintext):
    cipher = crypto.encrypt(plaintext)
    assert cipher != plaintext
    assert crypto.decrypt(cipher) == plaintext


def test_encrypt_changes_per_call():
    # Fernet uses a random IV so ciphertexts must differ even for same input.
    a = crypto.encrypt("same-input")
    b = crypto.encrypt("same-input")
    assert a != b
    assert crypto.decrypt(a) == crypto.decrypt(b) == "same-input"


def test_decrypt_garbage_raises():
    with pytest.raises(crypto.DecryptionError):
        crypto.decrypt("not-a-valid-token")


def test_encrypt_empty_string_roundtrip():
    cipher = crypto.encrypt("")
    assert crypto.decrypt(cipher) == ""


def test_decrypt_empty_raises():
    with pytest.raises(crypto.DecryptionError):
        crypto.decrypt("")
