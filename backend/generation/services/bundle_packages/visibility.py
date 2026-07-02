"""Visibility querysets: which templates/blocks/bundles a user may see."""

from __future__ import annotations

from typing import TYPE_CHECKING

from django.db.models import Q

from backend.generation.models import AppRequirementTemplate
from backend.generation.models import ContentBlock
from backend.generation.models import PromptTemplate
from backend.generation.models import ScaffoldingTemplate
from backend.generation.models import TemplateBundle

if TYPE_CHECKING:
    from django.contrib.auth.models import AbstractUser


def visible_scaffolding_templates_for(user: AbstractUser | None):
    qs = ScaffoldingTemplate.objects.all()
    if user and getattr(user, "is_authenticated", False):
        return qs.filter(Q(is_default=True) | Q(created_by=user))
    return qs.filter(is_default=True)


def visible_app_templates_for(user: AbstractUser | None):
    qs = AppRequirementTemplate.objects.all()
    if user and getattr(user, "is_authenticated", False):
        return qs.filter(Q(is_default=True) | Q(created_by=user))
    return qs.filter(is_default=True)


def visible_prompt_templates_for(user: AbstractUser | None):
    qs = PromptTemplate.objects.all()
    if user and getattr(user, "is_authenticated", False):
        return qs.filter(Q(is_default=True) | Q(created_by=user))
    return qs.filter(is_default=True)


def visible_blocks_for(user: AbstractUser | None):
    qs = ContentBlock.objects.all()
    if user and getattr(user, "is_authenticated", False):
        return qs.filter(Q(is_system=True) | Q(created_by=user))
    return qs.filter(is_system=True)


def visible_bundles_for(user: AbstractUser | None):
    qs = TemplateBundle.objects.all()
    if user and getattr(user, "is_authenticated", False):
        return qs.filter(Q(is_system=True) | Q(created_by=user))
    return qs.filter(is_system=True)
