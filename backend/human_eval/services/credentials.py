"""App admin credentials (owner B2): the hosted app's real admin login, read from its runtime record.

Participants with ``blinding.show_admin_credentials`` see the admin login of the app they test. The
password is never a platform default: it is the value the evaluation suite started the app with,
read through ``backend.generation.evaluation.credentials(run)`` (spec section 8.6, WP-09 contract:
the decrypted ``{admin_password, secret_key, ...}`` record of the run's start stage).

Participants test the instance human_eval hosts (``container_service.build_for_job``), not the eval
suite's own start container, so the record is shown only when that hosted container **was started
with it**: runtime stamps ``ContainerInstance.metadata["runtime_env"]`` with
``{"source": "evaluation.credentials", "admin_password_sha256": <sha256 hex of ADMIN_PASSWORD>}``
when it injects the record's ``ADMIN_PASSWORD``/``SECRET_KEY`` into the container env (U1 ask to the
runtime owner, SAMPLE_GEN_V3_CHANGES.md). Without that stamp, or when it names another password (a
later start rotated the record), the credential is **not measured**.

When the record is missing (the run was never started by the evaluation suite, the record cannot be
read, the contract is not implemented yet) or the app has no hosted container whose env matches it,
the portal shows no password (``admin_credentials: null``, ``admin_credentials_status:
"not_measured"``), never a guess.
"""

from __future__ import annotations

import hashlib
import hmac
import logging
from typing import Any

logger = logging.getLogger(__name__)

#: The auth contracts' admin account name (``AuthDriverConfig.admin_username`` default).
ADMIN_USERNAME = "admin"
STATUS_AVAILABLE = "available"
STATUS_NOT_MEASURED = "not_measured"

#: ``ContainerInstance.metadata`` key runtime writes when the container env carries the record.
RUNTIME_ENV_KEY = "runtime_env"
RUNTIME_ENV_SOURCE = "evaluation.credentials"


def password_fingerprint(password: str) -> str:
    """The stamp runtime stores for the ``ADMIN_PASSWORD`` it injected (sha256 hex, never the password)."""
    return hashlib.sha256(password.encode("utf-8")).hexdigest()


def container_env_matches(container: Any, password: str) -> bool:
    """Whether *container* was started with ``ADMIN_PASSWORD == password`` (runtime's metadata stamp)."""
    if container is None:
        return False
    stamp = (getattr(container, "metadata", None) or {}).get(RUNTIME_ENV_KEY)
    if not isinstance(stamp, dict) or stamp.get("source") != RUNTIME_ENV_SOURCE:
        return False
    recorded = stamp.get("admin_password_sha256")
    return isinstance(recorded, str) and hmac.compare_digest(recorded, password_fingerprint(password))


def admin_credentials(run: Any, container: Any = None) -> tuple[dict[str, str] | None, str]:
    """``({username, password}, "available")``, or ``(None, "not_measured")``.

    *container* is the hosted ``ContainerInstance`` the participant tests. The record is shown only
    when that container's env was started with it (see the module docstring).
    """
    if run is None:
        return None, STATUS_NOT_MEASURED
    from backend.generation import evaluation

    try:
        record = evaluation.credentials(run)
    except NotImplementedError:
        return None, STATUS_NOT_MEASURED
    except Exception:  # noqa: BLE001 - an unreadable record is not measured, never a default password
        logger.warning("human_eval: credentials record of run %s could not be read", run.pk, exc_info=True)
        return None, STATUS_NOT_MEASURED
    password = record.get("admin_password") if isinstance(record, dict) else None
    if not isinstance(password, str) or not password:
        return None, STATUS_NOT_MEASURED
    if not container_env_matches(container, password):
        # The hosted instance was not started with this record (today build_for_job starts it with an
        # empty env), so this password would not work on the app the participant tests.
        return None, STATUS_NOT_MEASURED
    username = record.get("admin_username") if isinstance(record.get("admin_username"), str) else ""
    return {"username": username or ADMIN_USERNAME, "password": password}, STATUS_AVAILABLE
