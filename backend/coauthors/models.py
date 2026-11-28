"""Models of the co-authors module.

A user (the *owner*) creates an invite link; whoever accepts it becomes the
owner's *co-author*: a :class:`Coauthorship` row. While a co-author has that row
selected as their workspace, ``middleware.WorkspaceMiddleware`` resolves their API
requests to the owner. Rows are never deleted by the application: removal and
leaving set ``revoked_at``.

Invite tokens are stored hashed (sha256 hex in a unique column) with a short
display prefix; the raw value is shown once, when the link is created.
"""

from __future__ import annotations

import datetime as dt
from typing import TYPE_CHECKING

from django.conf import settings
from django.db import models
from django.db.models import F
from django.db.models import Q
from django.utils import timezone

if TYPE_CHECKING:
    from datetime import datetime

DEFAULT_EXPIRY_DAYS = 7


def default_expiry() -> datetime:
    """Seven days from now (the default lifetime of an invite link)."""
    return timezone.now() + dt.timedelta(days=DEFAULT_EXPIRY_DAYS)


class CoauthorInvite(models.Model):
    """An invite link to the workspace of ``created_by`` (token stored as a hash)."""

    STATUS_ACTIVE = "active"
    STATUS_EXPIRED = "expired"
    STATUS_USED_UP = "used_up"
    STATUS_REVOKED = "revoked"

    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="coauthor_invites",
    )
    token_hash = models.CharField(max_length=64, unique=True)
    token_prefix = models.CharField(max_length=12)
    label = models.CharField(max_length=120, blank=True, default="")
    max_uses = models.PositiveSmallIntegerField(default=1)
    uses = models.PositiveSmallIntegerField(default=0)
    signup_uses = models.PositiveSmallIntegerField(
        default=0,
        help_text="Sign-ups this link let through while registration was closed",
    )
    expires_at = models.DateTimeField(default=default_expiry)
    revoked_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        verbose_name = "Co-author invite"
        verbose_name_plural = "Co-author invites"
        ordering = ["-created_at"]
        indexes = [models.Index(fields=["created_by", "-created_at"])]

    def __str__(self) -> str:
        return f"invite {self.token_prefix}… of user {self.created_by_id}"

    def status(self, now: datetime | None = None) -> str:
        """``revoked``, ``expired``, ``used_up`` or ``active``, checked in that order."""
        now = now or timezone.now()
        if self.revoked_at is not None:
            return self.STATUS_REVOKED
        if self.expires_at <= now:
            return self.STATUS_EXPIRED
        if self.uses >= self.max_uses:
            return self.STATUS_USED_UP
        return self.STATUS_ACTIVE

    def is_usable(self, now: datetime | None = None) -> bool:
        """Whether somebody can still join with this link.

        The link must be active, and its owner an active account that is not
        limited to the evaluation portal (nobody can work as such an account).
        """
        from backend.human_eval.services.access import is_restricted_evaluator

        if self.status(now) != self.STATUS_ACTIVE:
            return False
        owner = self.created_by
        return bool(owner.is_active) and not is_restricted_evaluator(owner)


class Coauthorship(models.Model):
    """``member`` is a co-author of ``owner``: active while ``revoked_at`` is null."""

    owner = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="coauthorships_owned",
    )
    member = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="coauthorships_joined",
    )
    invite = models.ForeignKey(
        CoauthorInvite,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="coauthorships",
    )
    created_at = models.DateTimeField(auto_now_add=True)
    revoked_at = models.DateTimeField(null=True, blank=True)
    revoked_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="+",
    )
    last_active_at = models.DateTimeField(
        null=True,
        blank=True,
        help_text="Last request the member made while working in this workspace",
    )
    last_selected_at = models.DateTimeField(
        null=True,
        blank=True,
        help_text="Set while this is the member's chosen workspace; restores the choice at the next login",
    )

    class Meta:
        verbose_name = "Co-authorship"
        verbose_name_plural = "Co-authorships"
        constraints = [
            models.UniqueConstraint(
                fields=["owner", "member"],
                condition=Q(revoked_at__isnull=True),
                name="coauthors_unique_active_pair",
            ),
            models.CheckConstraint(condition=~Q(owner=F("member")), name="coauthors_not_self"),
        ]
        indexes = [
            models.Index(fields=["member", "revoked_at"]),
            models.Index(fields=["owner", "revoked_at"]),
        ]

    def __str__(self) -> str:
        return f"user {self.member_id} in the workspace of user {self.owner_id}"

    @property
    def is_active(self) -> bool:
        return self.revoked_at is None


class CoauthorAction(models.Model):
    """One changing request a co-author made while working in a workspace (the actor is ``coauthorship.member``)."""

    coauthorship = models.ForeignKey(Coauthorship, on_delete=models.CASCADE, related_name="actions")
    method = models.CharField(max_length=8)
    path = models.CharField(max_length=300)
    status_code = models.PositiveSmallIntegerField()
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        verbose_name = "Co-author action"
        verbose_name_plural = "Co-author actions"
        ordering = ["-created_at", "-id"]
        indexes = [
            models.Index(fields=["coauthorship", "-created_at"]),
            # The activity list filters by co-authorship and pages by id (services.list_actions).
            models.Index(fields=["coauthorship", "-id"], name="coauthors_action_trail_idx"),
        ]

    def __str__(self) -> str:
        return f"{self.method} {self.path} ({self.status_code})"
