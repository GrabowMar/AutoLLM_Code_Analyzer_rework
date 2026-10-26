"""Analysis profile endpoints: list, defaults, validate, create, read, new version, archive, unarchive, clone.

Profiles are versioned (``engine/plan.py``): a content change creates
version+1 under the same slug; a name/description change edits the version
in place (it is not content). Runs and studies pin a specific version row,
so editing never changes what an existing run or study was analysed with.
System profiles (seeded from ``data/profiles/*.yaml``) are read-only: clone
one to customise it. Static paths are registered before ``{uuid}`` ones.
"""

from __future__ import annotations

from typing import Any
from uuid import UUID

from django.db import transaction
from django.db.models import Max
from django.db.models import Q
from django.utils.text import slugify
from ninja import Query
from ninja import Status
from ninja.errors import HttpError

from backend.analysis.api import common
from backend.analysis.api.schema import ProfileCloneSchema
from backend.analysis.api.schema import ProfileCreateSchema
from backend.analysis.api.schema import ProfileDetailSchema
from backend.analysis.api.schema import ProfileSchema
from backend.analysis.api.schema import ProfileUpdateSchema
from backend.analysis.api.schema import ProfileValidateSchema
from backend.analysis.api.serializers import iso
from backend.analysis.api.serializers import profile_payload
from backend.analysis.api.views._router import router
from backend.analysis.api.views.tools import catalog_payloads
from backend.analysis.engine import plan as plan_mod
from backend.analysis.engine import sample as sample_mod
from backend.analysis.engine.catalog import ConfigError
from backend.analysis.engine.reviewer import config as reviewer_config
from backend.analysis.models import AnalysisProfile
from backend.analysis.models import AnalysisRun
from backend.analysis.research import study as study_mod


def _latest_versions(slugs: list[str]) -> dict[str, int]:
    rows = AnalysisProfile.objects.filter(slug__in=slugs).values("slug").annotate(latest=Max("version"))
    return {row["slug"]: row["latest"] for row in rows}


def _payload(profile: AnalysisProfile, user) -> dict[str, Any]:
    latest = _latest_versions([profile.slug]).get(profile.slug, profile.version)
    return profile_payload(profile, user=user, latest_version=latest)


def _detail(profile: AnalysisProfile, user) -> dict[str, Any]:
    payload = _payload(profile, user)
    versions = AnalysisProfile.objects.filter(slug=profile.slug).order_by("-version")
    payload["versions"] = [
        {
            "id": str(v.id),
            "version": v.version,
            "content_hash": v.content_hash,
            "is_archived": v.is_archived,
            "created_at": iso(v.created_at),
        }
        for v in versions
    ]
    payload["usage"] = {
        "runs": AnalysisRun.objects.filter(profile=profile).count(),
        "studies": profile.studies.count(),
    }
    return payload


def _unique_slug(base: str) -> str:
    base = (slugify(base) or "profile")[:90]
    slug, n = base, 2
    while AnalysisProfile.objects.filter(slug=slug).exists():
        slug = f"{base}-{n}"
        n += 1
    return slug


def _writable(profile: AnalysisProfile, user) -> None:
    if profile.is_system:
        raise HttpError(403, "System profiles are read-only; clone the profile to customise it.")
    if profile.created_by_id != getattr(user, "id", None) and not common.is_staff(user):
        raise HttpError(403, "You can only modify your own profiles.")


@router.get("/profiles/", response=list[ProfileSchema])
def list_profiles(
    request,
    include_archived: bool = Query(default=False),
    q: str = Query(""),
    mine: bool = Query(default=False, description="only profiles you created (no system profiles)"),
):
    """Latest version per slug: system profiles first, then yours (staff: everyone's)."""
    qs = common.visible_profiles(request.auth)
    if mine:
        qs = qs.filter(is_system=False, created_by_id=getattr(request.auth, "id", None))
    if q:
        qs = qs.filter(Q(name__icontains=q) | Q(slug__icontains=q))
    latest_ids = qs.order_by("slug", "-version").distinct("slug").values_list("id", flat=True)
    profiles = list(AnalysisProfile.objects.filter(id__in=list(latest_ids)).order_by("-is_system", "name", "slug"))
    if not include_archived:
        profiles = [p for p in profiles if not p.is_archived]
    return [profile_payload(p, user=request.auth, latest_version=p.version) for p in profiles]


@router.get("/profiles/defaults/", response=dict)
def profile_defaults(request):
    """Everything the profile editor needs: default config, tool catalog, reviewer options, scope/rules/outcomes."""
    try:
        reviewer = reviewer_config.reviewer_options()
    except Exception as exc:  # noqa: BLE001 - the editor still works without reviewer metadata
        reviewer = {"error": str(exc)}
    try:
        from backend.analysis.engine import measurement

        outcomes = measurement.OUTCOMES
    except Exception:  # noqa: BLE001 - outcomes are descriptive here
        outcomes = {}
    return {
        "config": plan_mod.default_profile_config(),
        "tools": catalog_payloads(),
        "reviewer": reviewer,
        "scope": sample_mod.DEFAULT_SCOPE,
        "exclusion_rules": sample_mod.DEFAULT_EXCLUSION_RULES,
        "outcomes": outcomes,
        "design": study_mod.DEFAULT_DESIGN,
        "design_choices": {
            "factors": list(study_mod.GROUP_BY),
            "corrections": list(study_mod.CORRECTIONS),
            "cell_aggregates": list(study_mod.CELL_AGGREGATES),
        },
    }


@router.post("/profiles/validate/", response=dict)
def validate_profile(request, payload: ProfileValidateSchema):
    """Normalize a draft config: ``{ok, errors: {dotted.path: msg}, config, content_hash}``."""
    try:
        config = plan_mod.validate_profile_config(payload.config)
    except ConfigError as exc:
        return {"ok": False, "errors": exc.field_errors, "config": None, "content_hash": None}
    return {"ok": True, "errors": {}, "config": config, "content_hash": plan_mod.profile_content_hash(config)}


@router.post("/profiles/", response={201: ProfileSchema})
def create_profile(request, payload: ProfileCreateSchema):
    """Version 1 of a new profile owned by you."""
    if payload.slug and AnalysisProfile.objects.filter(slug=payload.slug).exists():
        return common.conflict("A profile with this slug already exists.", field="slug")
    slug = payload.slug or _unique_slug(payload.name)
    try:
        with transaction.atomic():
            profile = plan_mod.save_profile(
                user=request.auth,
                name=payload.name,
                description=payload.description,
                config=payload.config,
                slug=slug,
            )
    except ConfigError as exc:
        return common.config_error(exc, prefix="config")
    return Status(201, _payload(profile, request.auth))


@router.get("/profiles/{uuid:profile_id}/", response=ProfileDetailSchema)
def get_profile(request, profile_id: UUID):
    """Any visible version (archived included: runs and studies pin versions)."""
    return _detail(common.get_profile(request.auth, profile_id), request.auth)


@router.put("/profiles/{uuid:profile_id}/", response=ProfileDetailSchema)
def update_profile(request, profile_id: UUID, payload: ProfileUpdateSchema):
    """A content change creates version+1 of the slug's latest version; name/description edit it in place."""
    profile = common.get_profile(request.auth, profile_id)
    _writable(profile, request.auth)
    latest = AnalysisProfile.objects.filter(slug=profile.slug).order_by("-version").first()
    name = payload.name if payload.name is not None else latest.name
    description = payload.description if payload.description is not None else latest.description
    if payload.config is not None:
        try:
            config = plan_mod.validate_profile_config(payload.config)
        except ConfigError as exc:
            return common.config_error(exc, prefix="config")
        if plan_mod.profile_content_hash(config) != plan_mod.profile_content_hash(latest.config or {}):
            try:
                with transaction.atomic():
                    new = plan_mod.save_profile(
                        user=request.auth,
                        name=name,
                        description=description,
                        config=config,
                        slug=latest.slug,
                        base=latest,
                    )
            except ConfigError as exc:
                return common.config_error(exc, prefix="config")
            return _detail(new, request.auth)
    latest.name = name
    latest.description = description
    latest.save(update_fields=["name", "description", "updated_at"])
    return _detail(latest, request.auth)


@router.delete("/profiles/{uuid:profile_id}/", response=dict)
def archive_profile(request, profile_id: UUID):
    """Archive every version of the slug (pinned runs and studies keep their version)."""
    profile = common.get_profile(request.auth, profile_id)
    _writable(profile, request.auth)
    count = AnalysisProfile.objects.filter(slug=profile.slug).update(is_archived=True)
    return {"archived": True, "slug": profile.slug, "versions": count}


@router.post("/profiles/{uuid:profile_id}/unarchive/", response=ProfileDetailSchema)
def unarchive_profile(request, profile_id: UUID):
    """Undo an archive: every version of the slug is listed (and usable for new runs) again."""
    profile = common.get_profile(request.auth, profile_id)
    _writable(profile, request.auth)
    AnalysisProfile.objects.filter(slug=profile.slug).update(is_archived=False)
    latest = AnalysisProfile.objects.filter(slug=profile.slug).order_by("-version").first()
    return _detail(latest, request.auth)


@router.post(
    "/profiles/{uuid:profile_id}/clone/",
    response={201: ProfileSchema},
    openapi_extra=common.optional_body_openapi(ProfileCloneSchema),
)
def clone_profile(request, profile_id: UUID):
    """A new profile (version 1, yours) with this version's config (optional ``{"name", "slug"}``)."""
    source = common.get_profile(request.auth, profile_id)
    payload, error = common.optional_body(request, ProfileCloneSchema)
    if error is not None:
        return error
    if payload.slug and AnalysisProfile.objects.filter(slug=payload.slug).exists():
        return common.conflict("A profile with this slug already exists.", field="slug")
    name = payload.name or f"{source.name} (copy)"[:200]
    slug = payload.slug or _unique_slug(name)
    try:
        with transaction.atomic():
            profile = plan_mod.save_profile(
                user=request.auth,
                name=name,
                description=source.description,
                config=dict(source.config or {}),
                slug=slug,
            )
    except ConfigError as exc:
        return common.config_error(exc, prefix="config")
    return Status(201, _payload(profile, request.auth))
