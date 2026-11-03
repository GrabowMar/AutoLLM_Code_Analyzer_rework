"""Spend models (spec section 3.10): the append-only ledger, spend caps and empirical cost priors.

Running totals: ``spent(study) = Σ usage + Σ adjust`` (in the study's ``cap_basis``) and
``open_reserved(study) = Σ reserve + Σ release`` (a release is a negative amount; every reservation is
released when its attempt ends). ``budget/ledger.py`` (WP-11) writes the rows idempotently through
``dedupe_key``.
"""

from __future__ import annotations

from typing import Any

from django.conf import settings
from django.db import models
from django.db.models import Q
from django.utils.translation import gettext_lazy as _

from backend.generation.models.base import ImmutableVersionError
from backend.generation.models.base import WriteOnceQuerySet
from backend.generation.models.choices import CapAction
from backend.generation.models.choices import CapOnHit
from backend.generation.models.choices import CapPeriod
from backend.generation.models.choices import CapScope
from backend.generation.models.choices import CapUnit
from backend.generation.models.choices import LedgerKind
from backend.generation.models.choices import LedgerSource

__all__ = ["CostPrior", "SpendCap", "SpendLedgerEntry"]


class SpendLedgerEntry(models.Model):
    """One reservation, release, usage or adjustment. Append-only: rows are never updated."""

    id = models.BigAutoField(primary_key=True)
    kind = models.CharField(_("kind"), max_length=12, choices=LedgerKind.choices)
    study = models.ForeignKey(
        "generation.Study",
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name="ledger_entries",
    )
    run = models.ForeignKey(
        "generation.GenerationRun",
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name="ledger_entries",
    )
    attempt = models.SmallIntegerField(_("attempt"), null=True, blank=True)
    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="spend_ledger_entries")
    endpoint = models.ForeignKey(
        "inference.LLMEndpoint",
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name="spend_ledger_entries",
    )
    model_slug = models.CharField(_("model slug"), max_length=200, blank=True, default="")
    amount_usd = models.DecimalField(_("amount (billed USD)"), max_digits=14, decimal_places=8, default=0)
    list_cost_usd = models.DecimalField(
        _("amount (list USD)"),
        max_digits=14,
        decimal_places=8,
        default=0,
        help_text="Catalog list price; equals billed for cost_basis=billed",
    )
    tokens_in = models.IntegerField(_("tokens in"), default=0)
    tokens_out = models.IntegerField(_("tokens out"), default=0)
    requests = models.IntegerField(_("requests"), default=0)
    source = models.CharField(_("source"), max_length=24, choices=LedgerSource.choices)
    dedupe_key = models.CharField(_("dedupe key"), max_length=128, unique=True)
    created_at = models.DateTimeField(auto_now_add=True)

    objects = WriteOnceQuerySet.as_manager()

    class Meta:
        verbose_name = _("Spend ledger entry")
        verbose_name_plural = _("Spend ledger entries")
        ordering = ["created_at", "id"]
        indexes = [
            models.Index(fields=["study", "kind"], name="spend_study_kind"),
            models.Index(fields=["run", "attempt"], name="spend_run_attempt"),
            models.Index(fields=["user", "created_at"], name="spend_user_time"),
            models.Index(fields=["endpoint", "model_slug", "created_at"], name="spend_quota_window"),
        ]

    def __str__(self) -> str:
        return f"{self.kind} {self.amount_usd} USD ({self.source})"

    def save(self, *args: Any, **kwargs: Any) -> None:
        if not self._state.adding:
            msg = f"spend ledger entry {self.pk} is append-only; record an adjust entry instead"
            raise ImmutableVersionError(msg)
        super().save(*args, **kwargs)


class SpendCap(models.Model):
    """A rolling-window spend or token cap per user, endpoint, model or globally.

    Subscription quotas (e.g. the OpenCode Go per-model 5 h and weekly caps) are
    ``SpendCap(scope=model, endpoint=..., period=5h|week, unit=list_usd|tokens)``. Hitting one pauses the
    study (infra, re-queue); it never stops it.
    """

    scope = models.CharField(_("scope"), max_length=10, choices=CapScope.choices)
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        null=True,
        blank=True,
        related_name="spend_caps",
    )
    endpoint = models.ForeignKey(
        "inference.LLMEndpoint",
        on_delete=models.CASCADE,
        null=True,
        blank=True,
        related_name="spend_caps",
    )
    model_slug = models.CharField(_("model slug"), max_length=200, blank=True, default="")
    period = models.CharField(_("period"), max_length=6, choices=CapPeriod.choices)
    unit = models.CharField(_("unit"), max_length=8, choices=CapUnit.choices)
    limit = models.DecimalField(_("limit"), max_digits=16, decimal_places=4)
    action = models.CharField(_("action"), max_length=8, choices=CapAction.choices, default=CapAction.BLOCK)
    on_hit = models.CharField(_("on hit"), max_length=8, choices=CapOnHit.choices, default=CapOnHit.PAUSE)
    enabled = models.BooleanField(_("enabled"), default=True)
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="+",
    )
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        verbose_name = _("Spend cap")
        verbose_name_plural = _("Spend caps")
        ordering = ["scope", "period"]
        constraints = [
            models.CheckConstraint(condition=Q(limit__gte=0), name="generation_spendcap_limit_gte_0"),
            models.CheckConstraint(
                condition=(
                    Q(scope=CapScope.USER, user__isnull=False)
                    | Q(scope=CapScope.ENDPOINT, endpoint__isnull=False)
                    | (Q(scope=CapScope.MODEL) & ~Q(model_slug=""))
                    | Q(scope=CapScope.GLOBAL)
                ),
                name="generation_spendcap_scope_target",
            ),
        ]

    def __str__(self) -> str:
        target = self.model_slug or self.endpoint_id or self.user_id or "global"
        return f"{self.scope}:{target} {self.limit} {self.unit}/{self.period} ({self.action})"


class CostPrior(models.Model):
    """Empirical cost and time priors for estimates (section 4.4); refreshed by WP-11."""

    key = models.CharField(
        _("key"),
        max_length=200,
        unique=True,
        help_text="kind|harness|model_slug|size_bucket|policy_tier",
    )
    n = models.IntegerField(_("runs"), default=0)
    cost_p10 = models.FloatField(_("cost p10"), null=True, blank=True)
    cost_p50 = models.FloatField(_("cost p50"), null=True, blank=True)
    cost_p90 = models.FloatField(_("cost p90"), null=True, blank=True)
    list_cost_p50 = models.FloatField(_("list cost p50"), null=True, blank=True)
    requests_p50 = models.FloatField(_("requests p50"), null=True, blank=True)
    prompt_tokens_p50 = models.FloatField(_("prompt tokens p50"), null=True, blank=True)
    cached_tokens_p50 = models.FloatField(_("cached tokens p50"), null=True, blank=True)
    completion_tokens_p50 = models.FloatField(_("completion tokens p50"), null=True, blank=True)
    wall_s_p50 = models.FloatField(_("wall seconds p50"), null=True, blank=True)
    wall_s_p90 = models.FloatField(_("wall seconds p90"), null=True, blank=True)
    price_at = models.JSONField(
        _("prices at computation"),
        default=dict,
        blank=True,
        help_text="Blended prices when the prior was computed (drift scaling)",
    )
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = _("Cost prior")
        verbose_name_plural = _("Cost priors")
        ordering = ["key"]

    def __str__(self) -> str:
        return f"{self.key} (n={self.n})"
