"""Signal wiring of the co-authors app (connected from ``CoauthorsConfig.ready``).

- ``user_logged_in`` → :func:`restore_selection` puts the workspace the person
  last chose into the new session (one query; login never fails because of it).
- allauth ``user_signed_up`` → :func:`count_signup` counts a sign-up that an
  invite link let through while registration is closed, and drops the session flag.

Both run inside the login / sign-up request transaction, so the database work
sits in a savepoint: an error there is logged and rolled back on its own.
"""

from __future__ import annotations

import logging

from allauth.account.signals import user_signed_up
from django.contrib.auth.signals import user_logged_in
from django.db import transaction

logger = logging.getLogger(__name__)


def restore_selection(sender, request, user, **kwargs) -> None:
    """Restore the selected workspace after login (never raises)."""
    from backend.coauthors import services

    if request is None:
        return
    try:
        with transaction.atomic():
            services.restore_selection(request, user)
    except Exception:
        logger.exception("Could not restore the selected workspace at login")


def count_signup(sender, request, user, **kwargs) -> None:
    """Count a sign-up allowed by an invite link (never raises)."""
    from backend.coauthors import services

    if request is None:
        return
    try:
        with transaction.atomic():
            services.count_signup(request)
    except Exception:
        logger.exception("Could not count a sign-up on its invite link")


def connect() -> None:
    """Connect the receivers (idempotent through ``dispatch_uid``)."""
    user_logged_in.connect(restore_selection, dispatch_uid="coauthors.restore_selection")
    user_signed_up.connect(count_signup, dispatch_uid="coauthors.count_signup")
