"""EvalSuite v2: lock, build, start, tester, probes and adherence (spec section 8).

``credentials(run)`` reads the runtime credentials record of section 8.6: ``suite.py`` starts a sample with a
fresh random ``ADMIN_PASSWORD`` and ``SECRET_KEY``, encrypts them with the existing credentials Fernet key
(``backend.common.crypto``), stores the ciphertext as a blob and writes its sha to
``RunEvaluation.stages.start.detail.credentials_ref``. There is never a default password.
"""

from __future__ import annotations

import json
import secrets
from typing import TYPE_CHECKING
from typing import Any

if TYPE_CHECKING:
    from backend.generation.models import GenerationRun

__all__ = ["CredentialsUnavailableError", "credentials", "new_credentials", "store_credentials"]

ADMIN_USERNAME = "admin"
_RECORD_VERSION = 1


class CredentialsUnavailableError(LookupError):
    """The run has no readable credentials record (never started, or the record cannot be decrypted)."""


def new_credentials() -> dict[str, str]:
    """A fresh random record for one start of a sample."""
    return {
        "admin_username": ADMIN_USERNAME,
        "admin_password": secrets.token_urlsafe(18),
        "secret_key": secrets.token_urlsafe(48),
    }


def store_credentials(record: dict[str, str]) -> str:
    """Encrypt ``record`` and store it in the blob store; returns the ``credentials_ref`` sha."""
    from backend.common import crypto
    from backend.generation import blobs

    payload = json.dumps({"v": _RECORD_VERSION, **record}, sort_keys=True)
    return blobs.put_text(crypto.encrypt(payload), media_type="application/x-fernet")


def _read(ref: str) -> dict[str, str]:
    from backend.common import crypto
    from backend.generation import blobs

    try:
        data: Any = json.loads(crypto.decrypt(blobs.get_text(ref).strip()))
    except (crypto.DecryptionError, ValueError, OSError, LookupError) as exc:
        msg = f"credentials record {ref[:12]} is unreadable"
        raise CredentialsUnavailableError(msg) from exc
    if not isinstance(data, dict) or not data.get("admin_password"):
        msg = f"credentials record {ref[:12]} is malformed"
        raise CredentialsUnavailableError(msg)
    return {
        "admin_username": str(data.get("admin_username") or ADMIN_USERNAME),
        "admin_password": str(data["admin_password"]),
        "secret_key": str(data.get("secret_key") or ""),
    }


def credentials(run: GenerationRun) -> dict[str, str]:
    """The credentials record of the run's latest started evaluation (section 8.6).

    Returns ``{admin_username, admin_password, secret_key}``. Raises ``CredentialsUnavailableError`` when no
    evaluation of the run recorded a readable record; callers then show no credential (never a default).
    """
    rows = (
        run.evaluations.filter(retest_of__isnull=True).order_by("-created_at", "-pk").values_list("stages", flat=True)
    )
    for stages in rows:
        start = stages.get("start") if isinstance(stages, dict) else None
        detail = start.get("detail") if isinstance(start, dict) else None
        ref = detail.get("credentials_ref") if isinstance(detail, dict) else None
        if isinstance(ref, str) and ref:
            return _read(ref)
    msg = f"run {run.pk} has no runtime credentials record"
    raise CredentialsUnavailableError(msg)
