"""Visibility querysets: which templates/blocks/bundles a user may see."""

from __future__ import annotations

from typing import TYPE_CHECKING

from django.db.models import Q

from backend.generation.models import AppRequirementTemplate
from backend.generation.models import ContentBlock
from backend.generation.models import GenerationProfile

if TYPE_CHECKING:
    from django.contrib.auth.models import AbstractUser


def visible_app_templates_for(user: AbstractUser | None):
    """App specs *user* may see and run jobs on: catalog specs (``is_default``) and their own.

    The one rule for the Library, every create/preview path (agent,
    scaffolding, experiments) and exports. A spec without an owner is not
    public: ``created_by`` is SET_NULL, so a deleted user's private specs
    lose their owner and must stay unknown (404) to everyone else.
    """
    qs = AppRequirementTemplate.objects.all()
    if user and getattr(user, "is_authenticated", False):
        return qs.filter(Q(is_default=True) | Q(created_by=user))
    return qs.filter(is_default=True)


def visible_blocks_for(user: AbstractUser | None):
    qs = ContentBlock.objects.all()
    if user and getattr(user, "is_authenticated", False):
        return qs.filter(Q(is_system=True) | Q(created_by=user))
    return qs.filter(is_system=True)


def visible_profiles_for(user: AbstractUser | None):
    """Non-archived bundles visible to *user*, latest version of each slug first."""
    qs = GenerationProfile.objects.filter(is_archived=False).order_by("slug", "-version")
    if user and getattr(user, "is_authenticated", False):
        return qs.filter(Q(is_system=True) | Q(created_by=user))
    return qs.filter(is_system=True)
