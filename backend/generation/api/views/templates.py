"""Template CRUD endpoints (stacks, app requirement templates)."""

from __future__ import annotations

from django.conf import settings
from django.db.models import Q
from django.shortcuts import get_object_or_404
from ninja.errors import HttpError

from backend.generation.api.schema import AppRequirementCreateSchema
from backend.generation.api.schema import AppRequirementTemplateSchema
from backend.generation.api.schema import StackSchema
from backend.generation.api.schema import StackWriteSchema
from backend.generation.api.views._router import router
from backend.generation.models import AppRequirementTemplate
from backend.generation.services.versioning import content_hash
from backend.runtime.services.dockerfile_gen import generate_dockerfile
from backend.runtime.services.scaffolding import load_manifest
from backend.runtime.services.stack_validation import validate_stack_config
from backend.runtime.services.stack_validation import validate_stack_files


def _visible_templates(model, user):
    return model.objects.filter(Q(is_default=True) | Q(created_by=user))


def _mutable_template_or_403(model, user, *, slug: str):
    obj = get_object_or_404(_visible_templates(model, user), slug=slug)
    if obj.created_by_id == getattr(user, "id", None):
        return obj
    if getattr(user, "is_staff", False) and obj.is_default:
        return obj
    raise HttpError(403, "You can only modify your own templates.")


# -- Stacks (DB rows seeded from runtime/scaffolding/, latest version per slug) --


def _visible_stacks_qs(user):
    from backend.runtime.models import Stack

    qs = Stack.objects.filter(is_archived=False)
    return qs.filter(Q(is_builtin=True) | Q(is_approved=True) | Q(created_by=user))


def _latest_stacks(user=None):
    """Latest visible Stack per slug (builtin, approved, or own)."""
    latest: dict = {}
    for row in _visible_stacks_qs(user).order_by("slug", "-version"):
        latest.setdefault(row.slug, row)
    return list(latest.values())


def _stack_payload(row, *, include_files: bool = False) -> dict:
    payload = {
        "slug": row.slug,
        "version": row.version,
        "name": row.name or row.slug,
        "description": row.description,
        "has_frontend": row.has_frontend,
        "aliases": row.aliases or [],
        "is_builtin": row.is_builtin,
        "default_port": row.default_port,
        "patch_profile": row.patch_profile,
        "file_count": len(row.files or {}),
        "is_approved": row.is_approved,
        "dockerfile_mode": row.dockerfile_mode,
        "backend_base_image": row.backend_base_image,
        "frontend_base_image": row.frontend_base_image,
        "server_kind": row.server_kind,
        "backend_filename": row.backend_filename,
        "frontend_component": row.frontend_component,
    }
    if include_files:
        payload["files"] = row.files or {}
        if row.dockerfile_mode == "generated":
            payload["generated_dockerfile"] = generate_dockerfile(row)
    return payload


@router.get("/stacks/", response=list[StackSchema])
def list_stacks(request):
    """List the stack skeletons available for scaffolding-mode jobs."""
    rows = _latest_stacks(request.auth)
    if rows:
        return [_stack_payload(row) for row in rows]
    # First boot before the post_migrate seeder ran: fall back to the manifest.
    manifest = load_manifest()
    return [
        {"slug": slug, "has_frontend": bool(config.get("has_frontend")), "aliases": config.get("aliases", [])}
        for slug, config in manifest.get("stacks", {}).items()
    ]


@router.get("/stacks/base-images/", response=dict)
def list_stack_base_images(request):
    """Allowed base images for user-authored stacks."""
    return settings.STACK_ALLOWED_BASE_IMAGES


@router.post("/stacks/preview-dockerfile/", response={200: dict, 400: dict})
def preview_stack_dockerfile(request, payload: StackWriteSchema):
    """Render the Dockerfile a stack with this config would get (no save)."""
    from backend.runtime.models import Stack

    errors = _stack_config_errors(payload)
    if errors:
        return 400, {"detail": "; ".join(errors)}
    row = Stack(
        slug=payload.slug or "preview",
        has_frontend=payload.has_frontend,
        default_port=payload.default_port,
        backend_filename=payload.backend_filename,
        backend_base_image=payload.backend_base_image,
        frontend_base_image=payload.frontend_base_image,
        server_kind=payload.server_kind,
        dockerfile_mode=Stack.DockerfileMode.GENERATED,
    )
    return 200, {"dockerfile": generate_dockerfile(row)}


def _stack_config_errors(payload: StackWriteSchema) -> list[str]:
    return validate_stack_config(
        backend_base_image=payload.backend_base_image,
        frontend_base_image=payload.frontend_base_image,
        has_frontend=payload.has_frontend,
        default_port=payload.default_port,
        backend_filename=payload.backend_filename,
    )


def _stack_defaults(payload: StackWriteSchema, user) -> dict:
    from backend.runtime.models import Stack

    approved = bool(getattr(user, "is_staff", False)) or not settings.STACK_REQUIRE_APPROVAL
    return {
        "name": payload.name,
        "description": payload.description,
        "is_builtin": False,
        "is_approved": approved,
        "created_by": user,
        "has_frontend": payload.has_frontend,
        "default_port": payload.default_port,
        "patch_profile": payload.patch_profile if payload.patch_profile in ("flask", "none") else "none",
        "frontend_component": payload.frontend_component,
        "backend_filename": payload.backend_filename,
        "aliases": [],
        "backend_base_image": payload.backend_base_image,
        "frontend_base_image": payload.frontend_base_image if payload.has_frontend else "",
        "server_kind": payload.server_kind if payload.server_kind in ("python", "uvicorn") else "python",
        "files": payload.files,
        "content_hash": _stack_content_hash(payload),
        "dockerfile_mode": Stack.DockerfileMode.GENERATED,
    }


def _stack_content_hash(payload: StackWriteSchema) -> str:
    return content_hash(
        {
            "has_frontend": payload.has_frontend,
            "default_port": payload.default_port,
            "patch_profile": payload.patch_profile,
            "frontend_component": payload.frontend_component,
            "backend_filename": payload.backend_filename,
            "backend_base_image": payload.backend_base_image,
            "frontend_base_image": payload.frontend_base_image,
            "server_kind": payload.server_kind,
            "files": payload.files,
        },
    )


def _validate_stack_payload(payload: StackWriteSchema) -> list[str]:
    return _stack_config_errors(payload) + validate_stack_files(payload.files)


@router.post("/stacks/", response={200: dict, 400: dict})
def create_stack(request, payload: StackWriteSchema):
    """Create a user-authored stack as version 1 of a new slug."""
    from backend.runtime.models import Stack
    from backend.runtime.services.scaffolding import is_known_stack_slug

    if not payload.slug:
        return 400, {"detail": "slug is required"}
    errors = _validate_stack_payload(payload)
    if errors:
        return 400, {"detail": "; ".join(errors)}
    if Stack.objects.filter(slug=payload.slug).exists() or is_known_stack_slug(payload.slug):
        return 400, {"detail": f"Stack slug {payload.slug} already exists. Use PUT to add a new version."}
    row = Stack.objects.create(slug=payload.slug, version=1, **_stack_defaults(payload, request.auth))
    return 200, _stack_payload(row, include_files=True)


@router.get("/stacks/{slug}/", response={200: dict, 404: dict})
def get_stack(request, slug: str):
    """Latest version of a stack, including its full skeleton file map."""
    from backend.runtime.services.scaffolding import get_stack_row

    row = get_stack_row(slug)
    if row is None:
        return 404, {"detail": f"Unknown stack slug: {slug}"}
    return 200, _stack_payload(row, include_files=True)


@router.put("/stacks/{slug}/", response={200: dict, 400: dict, 403: dict, 404: dict})
def update_stack(request, slug: str, payload: StackWriteSchema):
    """Create version+1 of a user stack (versions are immutable)."""
    from backend.runtime.models import Stack

    current = Stack.objects.filter(slug=slug).order_by("-version").first()
    if current is None:
        return 404, {"detail": f"Unknown stack slug: {slug}"}
    if current.is_builtin:
        return 403, {"detail": "Builtin stacks are shipped code — duplicate one instead."}
    if current.created_by_id != request.auth.id and not request.auth.is_staff:
        return 403, {"detail": "You can only modify your own stacks."}
    errors = _validate_stack_payload(payload)
    if errors:
        return 400, {"detail": "; ".join(errors)}
    new_hash = _stack_content_hash(payload)
    if new_hash == current.content_hash and payload.name == current.name and payload.description == current.description:
        return 400, {"detail": "No changes from the current version."}
    defaults = _stack_defaults(payload, current.created_by)
    # Re-approval on content change when the gate is on (staff edits stay approved).
    if settings.STACK_REQUIRE_APPROVAL and not request.auth.is_staff:
        defaults["is_approved"] = False
    row = Stack.objects.create(slug=slug, version=current.version + 1, **defaults)
    return 200, _stack_payload(row, include_files=True)


@router.delete("/stacks/{slug}/", response={200: dict, 403: dict, 404: dict})
def archive_stack(request, slug: str):
    """Archive every version of a user stack (jobs keep their pinned snapshots)."""
    from backend.runtime.models import Stack

    current = Stack.objects.filter(slug=slug).order_by("-version").first()
    if current is None:
        return 404, {"detail": f"Unknown stack slug: {slug}"}
    if current.is_builtin:
        return 403, {"detail": "Builtin stacks cannot be archived."}
    if current.created_by_id != request.auth.id and not request.auth.is_staff:
        return 403, {"detail": "You can only archive your own stacks."}
    updated = Stack.objects.filter(slug=slug).update(is_archived=True)
    return 200, {"success": True, "archived_versions": updated}


@router.post("/stacks/{slug}/approve/", response={200: dict, 403: dict, 404: dict})
def approve_stack(request, slug: str):
    """Staff: approve every version of a user stack for general use."""
    from backend.runtime.models import Stack

    if not request.auth.is_staff:
        return 403, {"detail": "Staff only."}
    if not Stack.objects.filter(slug=slug).exists():
        return 404, {"detail": f"Unknown stack slug: {slug}"}
    updated = Stack.objects.filter(slug=slug).update(is_approved=True)
    return 200, {"success": True, "approved_versions": updated}


# -- App Requirement Templates ------------------------------------------


@router.get("/app-specs/", response=list[AppRequirementTemplateSchema])
def list_app_templates(request):
    """List all app requirement templates."""
    return _visible_templates(AppRequirementTemplate, request.auth)


@router.post("/app-specs/", response=AppRequirementTemplateSchema)
def create_app_template(request, payload: AppRequirementCreateSchema):
    return AppRequirementTemplate.objects.create(
        **payload.dict(),
        created_by=request.auth,
    )


@router.get("/app-specs/{slug}/", response=AppRequirementTemplateSchema)
def get_app_template(request, slug: str):
    return get_object_or_404(_visible_templates(AppRequirementTemplate, request.auth), slug=slug)


@router.put("/app-specs/{slug}/", response=AppRequirementTemplateSchema)
def update_app_template(request, slug: str, payload: AppRequirementCreateSchema):
    template = _mutable_template_or_403(AppRequirementTemplate, request.auth, slug=slug)
    for attr, value in payload.dict().items():
        setattr(template, attr, value)
    template.save()
    return template


@router.delete("/app-specs/{slug}/")
def delete_app_template(request, slug: str):
    template = _mutable_template_or_403(AppRequirementTemplate, request.auth, slug=slug)
    template.delete()
    return {"success": True}
