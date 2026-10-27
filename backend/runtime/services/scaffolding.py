"""Scaffolding: copy stack templates, patch generated code, prepare Docker build dirs."""

from __future__ import annotations

import json
import logging
import re
import shutil
from dataclasses import dataclass
from dataclasses import field
from enum import StrEnum
from pathlib import Path
from typing import TYPE_CHECKING
from typing import Any

from backend.generation.services.code_parser import infer_python_dependencies

if TYPE_CHECKING:
    from backend.generation.models import GenerationJob

logger = logging.getLogger(__name__)

_TEMPLATES_DIR = Path(__file__).parent.parent / "scaffolding"
_MANIFEST_PATH = _TEMPLATES_DIR / "manifest.json"
_LUCIDE_ICONS_PATH = _TEMPLATES_DIR / "lucide_icons.json"
# lucide_icons.json: every value export of the pinned lucide-react (frontend/package.json),
# regenerated from that release's dist/lucide-react.d.ts whenever the pin moves.
# The Vue stack pins @lucide/vue at the same release, whose icon exports are the
# same; only the framework helpers below differ (dist/lucide-vue.d.ts, 1.61.0).
_LUCIDE_FALLBACK = "CircleAlert"
_LUCIDE_REACT_ONLY = frozenset({"LucideProvider", "useLucideContext"})
_LUCIDE_VUE_ONLY = frozenset({"LUCIDE_CONTEXT", "setLucideProps", "useLucideProps"})

_SUBSTITUTION_PATTERN = re.compile(r"\{\{(\w+)(?:\|([^}]+))?\}\}")

# Loaded lazily on first use
_valid_lucide_icons: frozenset[str] | None = None
_manifest_cache: dict[str, Any] | None = None


class ScaffoldPhase(StrEnum):
    """seed: copilot workspace placeholders; build: job result code for Docker."""

    SEED = "seed"
    BUILD = "build"


class HealingMode(StrEnum):
    """How much the build repairs model output before Docker sees it.

    ``full`` (the default) infers missing requirements, applies the runtime
    patches (sqlite path, port, app context, unknown lucide icons,
    ``react-router-dom`` imports, the skeleton's Tailwind stylesheet entry)
    and fills a missing entry file with a placeholder. ``no_deps`` keeps the patches
    but leaves requirements.txt as the template wrote it. ``none`` builds the
    model output exactly as written, so its deploy rate is attributable to
    the model alone.
    """

    FULL = "full"
    NO_DEPS = "no_deps"
    NONE = "none"


@dataclass
class HealingReport:
    """What the build changed in the model output (filled by :func:`apply_scaffold`)."""

    mode: str = HealingMode.FULL
    # False for verbatim (agent) builds, which are never healed.
    applicable: bool = True
    added_requirements: list[str] = field(default_factory=list)
    # "<file>:<patch>" for every patch that changed the text.
    patches: list[str] = field(default_factory=list)

    @property
    def changed(self) -> bool:
        return bool(self.added_requirements or self.patches)

    def to_dict(self) -> dict[str, Any]:
        return {
            "mode": str(self.mode),
            "applicable": self.applicable,
            "changed": self.changed,
            "added_requirements": list(self.added_requirements),
            "patches": list(self.patches),
        }


def resolve_healing_mode(job: Any, override: str | None = None) -> HealingMode:
    """*override*, else the job's ``build_healing``, else ``full``."""
    raw = override or getattr(job, "build_healing", "") or HealingMode.FULL
    try:
        return HealingMode(raw)
    except ValueError:
        logger.warning("Unknown healing mode %r; using full", raw)
        return HealingMode.FULL


def load_manifest() -> dict[str, Any]:
    """Load and cache ``runtime/scaffolding/manifest.json``."""
    global _manifest_cache  # noqa: PLW0603
    if _manifest_cache is None:
        _manifest_cache = json.loads(_MANIFEST_PATH.read_text(encoding="utf-8"))
    return _manifest_cache


def _row_config(row: Any) -> dict[str, Any]:
    """A Stack row in the manifest-entry dict shape the call sites expect.

    Empty optional values are omitted so ``stack.get(key, default)`` call
    sites keep their defaults.
    """
    config: dict[str, Any] = {
        "canonical_slug": row.slug,
        "stack_version": row.version,
        "has_frontend": row.has_frontend,
        "default_port": row.default_port,
        "patch_profile": row.patch_profile,
        "aliases": row.aliases or [],
        "server_kind": row.server_kind,
    }
    if row.frontend_component:
        config["frontend_component"] = row.frontend_component
    if row.backend_filename:
        config["backend_filename"] = row.backend_filename
    return config


def _db_stack_entries() -> dict[str, dict[str, Any]]:
    """Latest non-archived Stack row per slug, keyed by slug and every alias.

    Returns an empty dict when the table is empty or unavailable (first boot
    before the post_migrate seeder ran) — callers fall back to the manifest.
    """
    from backend.runtime.models import Stack

    entries: dict[str, dict[str, Any]] = {}
    try:
        rows = list(Stack.objects.filter(is_archived=False).order_by("slug", "-version"))
    except Exception:  # noqa: BLE001 — table missing mid-migration
        return {}
    seen: set[str] = set()
    for row in rows:
        if row.slug in seen:
            continue
        seen.add(row.slug)
        config = _row_config(row)
        entries[row.slug] = config
        for alias in row.aliases or []:
            entries.setdefault(alias, config)
    return entries


def _manifest_stack_entries(manifest: dict[str, Any]) -> dict[str, dict[str, Any]]:
    """Map canonical slug and aliases to manifest config (canonical slug included)."""
    entries: dict[str, dict[str, Any]] = {}
    for canonical, config in manifest.get("stacks", {}).items():
        entry = {**config, "canonical_slug": canonical}
        entries[canonical] = entry
        for alias in config.get("aliases", []):
            entries[alias] = entry
    return entries


def _stack_entries() -> dict[str, dict[str, Any]]:
    """DB-backed stack entries, falling back to the manifest when empty."""
    return _db_stack_entries() or _manifest_stack_entries(load_manifest())


def canonical_stack_slug(slug: str) -> str:
    """Map a raw scaffolding slug (canonical or alias) to its canonical stack slug."""
    entries = _stack_entries()
    default = load_manifest().get("default_stack", "generic-python")

    if slug in entries:
        return entries[slug]["canonical_slug"]
    logger.warning("Unknown scaffolding slug %r; using %s", slug, default)
    return default


def is_known_stack_slug(slug: str) -> bool:
    """Whether *slug* is a known canonical stack slug or alias."""
    return slug in _stack_entries()


def resolve_stack_slug(job: GenerationJob) -> str:
    """Resolve a job's stack slug to a canonical stack slug."""
    default = load_manifest().get("default_stack", "generic-python")

    if not job.stack_slug:
        return default

    return canonical_stack_slug(job.stack_slug)


def get_stack_config(stack_slug: str) -> dict[str, Any]:
    """Return the config dict for a canonical stack slug (DB row or manifest entry)."""
    entries = _stack_entries()
    if stack_slug not in entries:
        msg = f"Unknown stack slug: {stack_slug}"
        raise KeyError(msg)
    return entries[stack_slug]


def get_stack_row(slug: str, version: int | None = None):
    """The Stack row for *slug* (exact *version*, or latest non-archived); None if absent."""
    from backend.runtime.models import Stack

    try:
        qs = Stack.objects.filter(slug=slug)
        if version is not None:
            return qs.filter(version=version).first()
        return qs.filter(is_archived=False).order_by("-version").first()
    except Exception:  # noqa: BLE001 — table missing mid-migration
        return None


def stack_snapshot_entry(slug: str) -> dict[str, Any] | None:
    """The ``{slug, version, content_hash}`` dict a job snapshot pins, or None."""
    row = get_stack_row(canonical_stack_slug(slug))
    if row is None:
        return None
    return {"slug": row.slug, "version": row.version, "content_hash": row.content_hash}


def stack_has_frontend(stack_slug: str) -> bool:
    return bool(get_stack_config(stack_slug).get("has_frontend"))


def apply_scaffold(
    job: GenerationJob,
    dest_path: Path,
    *,
    phase: ScaffoldPhase,
    healing: str | None = None,
    report: HealingReport | None = None,
) -> Path:
    """Copy stack template (or archive) into *dest_path* and write application code.

    *phase* ``seed`` uses placeholders for copilot; ``build`` uses ``job.result_data``.
    *healing* overrides the job's ``build_healing`` (see :class:`HealingMode`);
    *report*, when given, records what the build changed.
    """
    if report is None:
        report = HealingReport()
    report.mode = resolve_healing_mode(job, healing)
    dest_path.mkdir(parents=True, exist_ok=True)
    stack_slug = resolve_stack_slug(job)

    # Prefer the exact Stack version frozen into the job snapshot; legacy
    # snapshots (string-only scaffolding_slug) resolve the latest version.
    snapshot = job.resolved_bundle if isinstance(job.resolved_bundle, dict) else {}
    pinned = snapshot.get("stack") or {}
    row = get_stack_row(
        pinned.get("slug") or stack_slug,
        version=pinned.get("version"),
    )

    if row is not None:
        stack = _row_config(row)
        _materialize_stack_files(row.files or {}, dest_path)
        if row.dockerfile_mode == "generated":
            from backend.runtime.services.dockerfile_gen import generate_dockerfile

            (dest_path / "Dockerfile").write_text(generate_dockerfile(row), encoding="utf-8")
    else:
        # First-boot fallback: table not seeded yet — provision from disk.
        stack = get_stack_config(stack_slug)
        template_dir = _TEMPLATES_DIR / stack.get("directory", stack_slug)
        _copy_template_dir(template_dir, dest_path)

    _apply_substitutions(dest_path, job, stack)

    result = job.result_data or {}
    files = result.get("files")
    is_v2 = phase == ScaffoldPhase.BUILD and result.get("result_schema_version") == 2
    if is_v2 and isinstance(files, dict) and result.get("materialize") == "verbatim":
        report.applicable = False
        _materialize_verbatim(dest_path, files)
    elif is_v2 and isinstance(files, dict) and files:
        _materialize_files(dest_path, stack, files, result, report)
    else:
        _materialize_legacy(dest_path, stack, phase, result, report)

    return dest_path


# Platform-owned build files an agent's output may never replace.
_PROTECTED_BUILD_FILES = frozenset({"Dockerfile", ".dockerignore", "docker-compose.yml"})


def _materialize_verbatim(dest_path: Path, files: dict[str, str]) -> None:
    """Write an agent run's workspace over the skeleton exactly as the agent left it.

    No entry renaming, code patching or dependency inference: the agent saw
    the same skeleton and contract, so what gets built is what it wrote. The
    stack's build files stay the platform's.
    """
    root = dest_path.resolve()
    for rel, content in files.items():
        if rel in _PROTECTED_BUILD_FILES or not isinstance(content, str):
            continue
        target = (dest_path / rel).resolve()
        if root not in target.parents:
            continue
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(content, encoding="utf-8")


def _materialize_files(
    dest_path: Path,
    stack: dict[str, Any],
    files: dict[str, str],
    result: dict[str, Any],
    report: HealingReport,
) -> None:
    """Write a multi-file ``result_data["files"]`` map (result_schema_version 2)."""
    patch = report.mode != HealingMode.NONE
    backend_name = stack.get("backend_filename", "app.py")
    entry = result.get("backend_entry") or backend_name
    is_flask = stack.get("patch_profile") == "flask"
    component = stack.get("frontend_component", "App.jsx")
    is_vue = component.endswith(".vue")

    css_entry = _css_entry(dest_path, is_vue=is_vue) if stack.get("has_frontend") else None
    keeps_router_dom = _declares_router_dom(dest_path, files.get("frontend/package.json"))

    backend_python: list[str] = []
    for src_path, content in files.items():
        text = content
        # The entry file always lands at the stack's canonical filename,
        # regardless of what the model called it.
        out_path = backend_name if src_path == entry else src_path
        if src_path == entry and is_flask and patch:
            text = _patch_backend_code(text, report, out_path)
        if not out_path.startswith("frontend/") and out_path.endswith(".py"):
            backend_python.append(text)
        elif out_path.startswith("frontend/") and patch:
            text = _patch_frontend_file(
                text,
                out_path,
                is_vue=is_vue,
                css_entry=css_entry,
                keeps_router_dom=keeps_router_dom,
                report=report,
            )

        target = dest_path / out_path
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(text, encoding="utf-8")

    # A placeholder (or the skeleton's own stub) answers the health check by
    # itself, so a raw (``none``) build removes an entry the model never wrote.
    _fill_missing(
        dest_path / backend_name,
        written=entry in files,
        placeholder=_placeholder_backend_for_stack(stack),
        patch=patch,
        report=report,
        label=backend_name,
    )

    if report.mode == HealingMode.FULL:
        report.added_requirements = _patch_requirements(
            dest_path,
            "\n\n".join(backend_python),
            local_modules=_local_module_names(list(files)),
        )

    if stack.get("has_frontend"):
        src_dir = dest_path / "frontend" / "src"
        src_dir.mkdir(parents=True, exist_ok=True)
        rel = f"frontend/src/{component}"
        _fill_missing(
            dest_path / rel,
            written=rel in files,
            placeholder=_placeholder_frontend_vue() if is_vue else _placeholder_frontend(),
            patch=patch,
            report=report,
            label=rel,
        )


def _patch_frontend_file(
    text: str,
    out_path: str,
    *,
    is_vue: bool,
    css_entry: tuple[str, str] | None,
    keeps_router_dom: bool,
    report: HealingReport,
) -> str:
    """Healed builds: repair one model-written ``frontend/`` file against the skeleton.

    The skeleton's Tailwind stylesheet keeps its ``@import "tailwindcss"``
    entry and theme tokens, unknown lucide icons are aliased (React and Vue
    alike), and React imports of ``react-router-dom`` (not installed since
    the skeleton moved to react-router 8, which exports the same API) are
    pointed at ``react-router``.
    """
    if css_entry is not None and out_path == css_entry[0]:
        healed = _heal_tailwind_entry(text, css_entry[1])
        if healed != text:
            report.patches.append(f"{out_path}:tailwind_entry")
        return healed
    if is_vue:
        if out_path.endswith((".vue", ".js", ".ts")):
            text = _sanitize_lucide_imports(text, report, out_path, is_vue=True)
        return text
    if out_path.endswith((".jsx", ".tsx")):
        text = _sanitize_lucide_imports(text, report, out_path)
    if out_path.endswith((".jsx", ".tsx", ".js", ".ts")) and not keeps_router_dom:
        text = _fix_react_router_dom(text, report, out_path)
    return text


def _css_entry(dest_path: Path, *, is_vue: bool) -> tuple[str, str] | None:
    """``(path, text)`` of the skeleton's Tailwind 4 stylesheet entry, or None.

    None for skeletons without one (older pinned stack versions configure
    Tailwind 3 through tailwind.config.js, user stacks may differ), so their
    stylesheets are written as the model gave them.
    """
    rel = "frontend/src/style.css" if is_vue else "frontend/src/App.css"
    path = dest_path / rel
    if not path.is_file():
        return None
    text = path.read_text(encoding="utf-8")
    return (rel, text) if _TAILWIND_IMPORT_RE.search(text) else None


def _declares_router_dom(dest_path: Path, model_package_json: str | None) -> bool:
    """Whether the build installs ``react-router-dom`` (the model's or the skeleton's package.json lists it)."""
    text = model_package_json
    if text is None:
        path = dest_path / "frontend" / "package.json"
        text = path.read_text(encoding="utf-8") if path.is_file() else "{}"
    try:
        package = json.loads(text)
    except ValueError:
        # Unparseable: npm fails on it anyway; leave the imports alone.
        return True
    if not isinstance(package, dict):
        return True
    return any(
        isinstance(deps, dict) and "react-router-dom" in deps
        for deps in (package.get("dependencies"), package.get("devDependencies"))
    )


_TAILWIND_IMPORT_RE = re.compile(r"""@import\s+(?:url\(\s*)?["']tailwindcss["']""")
# Tailwind 3 entry points: ``@tailwind base;`` and ``@import "tailwindcss/base";``.
_TAILWIND3_DIRECTIVE_RE = re.compile(
    r"""^[ \t]*@(?:tailwind\s+[\w-]+|import\s+["']tailwindcss/[\w-]+(?:\.css)?["'])\s*;[ \t]*\n?""",
    re.MULTILINE,
)
_CSS_IMPORT_RE = re.compile(r"""^[ \t]*@import\b[^;]*;[ \t]*\n?""", re.MULTILINE)


def _heal_tailwind_entry(css: str, skeleton: str) -> str:
    """Keep the skeleton's Tailwind 4 entry in a model-written stylesheet that replaces it.

    A stylesheet that imports ``tailwindcss`` itself is the model's own
    complete entry and stays as written. Otherwise its Tailwind 3 directives
    are dropped and the skeleton's import and ``@theme`` tokens go first, so
    utilities are generated and ``@apply`` resolves; the model's own
    ``@import`` statements stay ahead of every rule, as CSS requires.
    """
    if _TAILWIND_IMPORT_RE.search(css):
        return css
    body = _TAILWIND3_DIRECTIVE_RE.sub("", css)
    model_imports = "".join(m.group(0) for m in _CSS_IMPORT_RE.finditer(body))
    body = _CSS_IMPORT_RE.sub("", body).strip()
    skeleton_imports = "".join(m.group(0) for m in _CSS_IMPORT_RE.finditer(skeleton))
    skeleton_rest = _CSS_IMPORT_RE.sub("", skeleton).strip()
    parts = [skeleton_imports + model_imports, skeleton_rest, body]
    return "\n\n".join(p.strip() for p in parts if p.strip()) + "\n"


_ROUTER_DOM_IMPORT_RE = re.compile(r"""(\b(?:from|import)\s*\(?\s*)(["'])react-router-dom\2""")


def _fix_react_router_dom(code: str, report: HealingReport | None = None, filename: str = "") -> str:
    """``from "react-router-dom"`` -> ``from "react-router"`` (v7+ re-exports one from the other)."""
    fixed = _ROUTER_DOM_IMPORT_RE.sub(r"\1\2react-router\2", code)
    if report is not None and fixed != code:
        report.patches.append(f"{filename}:react_router_dom")
    return fixed


def _fill_missing(
    path: Path,
    *,
    written: bool,
    placeholder: str,
    patch: bool,
    report: HealingReport,
    label: str,
) -> None:
    """Healed builds stub a file the model did not write; raw builds make sure it is absent."""
    if written:
        return
    if patch:
        if not path.is_file():
            path.write_text(placeholder, encoding="utf-8")
        report.patches.append(f"{label}:placeholder")
    elif path.is_file():
        path.unlink()


def _materialize_legacy(
    dest_path: Path,
    stack: dict[str, Any],
    phase: ScaffoldPhase,
    result: dict[str, Any],
    report: HealingReport,
) -> None:
    """Write single-string ``backend_code``/``frontend_code`` (pre-v2 jobs, copilot seed)."""
    backend_code = result.get("backend_code", "")
    frontend_code = result.get("frontend_code", "")

    backend_name = stack.get("backend_filename", "app.py")
    build = phase == ScaffoldPhase.BUILD
    # Seeding always gets placeholders; only a build is "healed".
    patch = not build or report.mode != HealingMode.NONE

    if not build:
        backend_text = _placeholder_backend_for_stack(stack)
        frontend_text = _placeholder_frontend() if stack.get("has_frontend") else ""
    elif backend_code or not patch:
        backend_text = backend_code
        frontend_text = frontend_code
    else:
        backend_text = _placeholder_backend_for_stack(stack)
        frontend_text = frontend_code
        report.patches.append(f"{backend_name}:placeholder")

    if stack.get("patch_profile") == "flask" and patch:
        backend_text = _patch_backend_code(backend_text, report if build else None, backend_name)

    if backend_text or patch:
        (dest_path / backend_name).write_text(backend_text, encoding="utf-8")
    elif (dest_path / backend_name).is_file():
        (dest_path / backend_name).unlink()
    if build and report.mode == HealingMode.FULL:
        report.added_requirements = _patch_requirements(dest_path, backend_code)

    if stack.get("has_frontend"):
        src_dir = dest_path / "frontend" / "src"
        src_dir.mkdir(parents=True, exist_ok=True)
        component = stack.get("frontend_component", "App.jsx")
        is_vue = component.endswith(".vue")
        front = frontend_text
        if not front and patch:
            front = _placeholder_frontend_vue() if is_vue else _placeholder_frontend()
            if build:
                report.patches.append(f"frontend/src/{component}:placeholder")
        if build and frontend_text and patch:
            front = _sanitize_lucide_imports(front, report, f"frontend/src/{component}", is_vue=is_vue)
            if not is_vue and not _declares_router_dom(dest_path, None):
                front = _fix_react_router_dom(front, report, f"frontend/src/{component}")
        if front:
            (src_dir / component).write_text(front, encoding="utf-8")
        elif (src_dir / component).is_file():
            (src_dir / component).unlink()


def prepare_build_dir(
    job: GenerationJob,
    dest_path: Path,
    *,
    healing: str | None = None,
    report: HealingReport | None = None,
) -> Path:
    """Write generated code + scaffolding templates to *dest_path* for Docker build."""
    return apply_scaffold(job, dest_path, phase=ScaffoldPhase.BUILD, healing=healing, report=report)


def _apply_substitutions(dest: Path, job: GenerationJob, stack: dict[str, Any]) -> None:
    manifest = load_manifest()
    rel_paths = manifest.get("substitution_files", [".env.example"])
    context = _build_substitution_context(job, stack)

    for rel in rel_paths:
        path = dest / rel
        if not path.is_file():
            continue
        text = path.read_text(encoding="utf-8")
        if "{{" not in text:
            continue
        path.write_text(_render_substitutions(text, context), encoding="utf-8")


def _build_substitution_context(job: GenerationJob, stack: dict[str, Any]) -> dict[str, str]:
    port = str(stack.get("default_port", 8000))
    if job.app_requirement_id and job.app_requirement:
        project_name = job.app_requirement.slug
    elif job.stack_slug:
        project_name = job.stack_slug
    else:
        project_name = "app"
    return {
        "PROJECT_NAME": project_name,
        "app_port": port,
    }


def _render_substitutions(text: str, context: dict[str, str]) -> str:
    def _replace(match: re.Match[str]) -> str:
        key = match.group(1)
        default = match.group(2)
        if key in context:
            return context[key]
        if default is not None:
            return default
        return match.group(0)

    return _SUBSTITUTION_PATTERN.sub(_replace, text)


def _materialize_stack_files(files: dict[str, str], dst: Path) -> None:
    """Write a Stack row's {relative_path: text} skeleton map into *dst*.

    Paths are validated at stack creation; the resolve() containment check
    here is defense in depth for rows written through other paths.
    """
    root = dst.resolve()
    for rel, content in files.items():
        target = (dst / rel).resolve()
        if not target.is_relative_to(root):
            logger.warning("Skipping stack file escaping the build dir: %r", rel)
            continue
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(content, encoding="utf-8")


def _copy_template_dir(src: Path, dst: Path) -> None:
    """Copy template files to dst, skipping if src does not exist."""
    if not src.exists():
        return
    for item in src.iterdir():
        dest_item = dst / item.name
        if item.is_dir():
            shutil.copytree(str(item), str(dest_item), dirs_exist_ok=True)
        else:
            shutil.copy2(str(item), str(dest_item))


_REQUIREMENT_NAME = re.compile(r"^\s*([A-Za-z0-9][A-Za-z0-9._-]*)")


def _requirement_key(name: str) -> str:
    """PEP 503 normalized project name (``Flask_SQLAlchemy`` == ``flask-sqlalchemy``)."""
    return re.sub(r"[-_.]+", "-", name).lower()


def _requirement_names(text: str) -> set[str]:
    """Normalized project names in a requirements.txt, ignoring extras, pins and markers.

    ``uvicorn[standard]==0.54.0`` and ``pwdlib[argon2]; python_version>='3.10'``
    yield ``uvicorn`` and ``pwdlib``; option lines (``-r``, ``--index-url``)
    and comments are skipped.
    """
    names: set[str] = set()
    for raw in text.splitlines():
        line = raw.split("#", 1)[0].strip()
        if not line or line.startswith("-"):
            continue
        match = _REQUIREMENT_NAME.match(line)
        if match:
            names.add(_requirement_key(match.group(1)))
    return names


def _local_module_names(paths: list[str]) -> frozenset[str]:
    """Top-level import names the written backend files provide (``db.py``, ``routers/``)."""
    names: set[str] = set()
    for rel in paths:
        if rel.startswith("frontend/"):
            continue
        top = rel.split("/", 1)[0]
        if "/" in rel or top.endswith(".py"):
            names.add(top.removesuffix(".py"))
    return frozenset(names)


def _patch_requirements(dest: Path, backend_code: str, local_modules: frozenset[str] = frozenset()) -> list[str]:
    """Merge inferred deps from generated code into the template requirements.txt; returns the added ones."""
    req_path = dest / "requirements.txt"
    if not req_path.exists() or not backend_code:
        return []

    existing_text = req_path.read_text()
    existing_pkgs = _requirement_names(existing_text)

    inferred = infer_python_dependencies(backend_code, local_modules=local_modules)
    # Compared by project name, so an inferred pin (``bcrypt==4.0.1``) never
    # duplicates a package the template already pins: the template's pin wins.
    new_pkgs = [pkg for pkg in inferred if not _requirement_names(pkg) & existing_pkgs]

    if new_pkgs:
        extra = "\n".join(new_pkgs)
        req_path.write_text(existing_text.rstrip() + "\n" + extra + "\n")
    return new_pkgs


def _patch_backend_code(code: str, report: HealingReport | None = None, filename: str = "app.py") -> str:
    """Apply runtime-invariant fixes to generated Flask backend code."""
    for name, fix in (
        ("sqlite_uri", _fix_sqlite_uri),
        ("flask_port", _fix_flask_port),
        ("app_context", _fix_db_create_all),
    ):
        patched = fix(code)
        if report is not None and patched != code:
            report.patches.append(f"{filename}:{name}")
        code = patched
    return code


def _fix_sqlite_uri(code: str) -> str:
    """Replace relative sqlite:/// URIs with an absolute /app/data/ path.

    Models often build the URI in an f-string (``sqlite:///{Path(...) /
    'app.db'}``); the whole ``{...}`` expression must be consumed as one
    unit — cutting it at the first quote leaves an unbalanced brace behind
    and the generated app dies on a SyntaxError before it can boot.
    """

    def _rewrite(m: re.Match[str]) -> str:
        original_path = m.group(1)
        if original_path.startswith("{"):
            named = re.search(r"([\w-]+\.(?:db|sqlite3?))", original_path)
            filename = named.group(1) if named else "app.db"
        else:
            filename = Path(original_path).name or "app.db"
        return f"sqlite:////app/data/{filename}"

    return re.sub(
        r"sqlite:///(\{[^{}]*\}|[^'\"{}]+)",
        _rewrite,
        code,
    )


def _fix_db_create_all(code: str) -> str:
    """Wrap the init block inside if __name__ in with app.app_context()."""
    if "with app.app_context()" in code:
        return code
    if "db.create_all()" not in code:
        return code

    lines = code.splitlines(keepends=True)
    result: list[str] = []
    i = 0
    while i < len(lines):
        line = lines[i]
        stripped = line.lstrip()

        if re.match(r"if __name__\s*==\s*['\"]__main__['\"]:", stripped):
            block_indent = len(line) - len(line.lstrip())
            body_indent = block_indent + 4
            result.append(line)
            i += 1

            init_lines: list[str] = []
            run_lines: list[str] = []
            in_run_section = False
            while i < len(lines):
                body_line = lines[i]
                if body_line.strip() and len(body_line) - len(body_line.lstrip()) <= block_indent:
                    break
                body_stripped = body_line.strip()
                if body_stripped.startswith(("port ", "app.run(")):
                    in_run_section = True
                if in_run_section:
                    run_lines.append(body_line)
                else:
                    init_lines.append(body_line)
                i += 1

            if init_lines:
                ctx_indent = " " * body_indent
                result.append(f"{ctx_indent}with app.app_context():\n")
                for il in init_lines:
                    result.append("    " + il if il.strip() else il)
            result.extend(run_lines)
            continue

        result.append(line)
        i += 1

    return "".join(result)


def _fix_flask_port(code: str) -> str:
    """Ensure Flask listens on PORT defaulting to 8000."""
    code = re.sub(
        r"os\.environ\.get\(['\"]PORT['\"],\s*5000\s*\)",
        "os.environ.get('PORT', 8000)",
        code,
    )
    return re.sub(
        r"app\.run\(([^)]*\b)port\s*=\s*5000([^)]*)\)",
        lambda m: f"app.run({m.group(1)}port=int(os.environ.get('PORT', 8000)){m.group(2)})",
        code,
    )


def _get_valid_lucide_icons(*, vue: bool = False) -> frozenset[str]:
    """Value exports of the pinned ``lucide-react`` (or, with *vue*, ``@lucide/vue``)."""
    global _valid_lucide_icons  # noqa: PLW0603
    if _valid_lucide_icons is None:
        try:
            data = json.loads(_LUCIDE_ICONS_PATH.read_text(encoding="utf-8"))
            _valid_lucide_icons = frozenset(data)
        except Exception:  # noqa: BLE001
            _valid_lucide_icons = frozenset()
    if vue and _valid_lucide_icons:
        return (_valid_lucide_icons - _LUCIDE_REACT_ONLY) | _LUCIDE_VUE_ONLY
    return _valid_lucide_icons


def _sanitize_lucide_imports(
    code: str,
    report: HealingReport | None = None,
    filename: str = "",
    *,
    is_vue: bool = False,
) -> str:
    """Import unknown lucide icon names as the fallback icon under their own name.

    ``lucide-react`` imports in React stacks, ``@lucide/vue`` imports in Vue
    stacks: an unknown name is a hard build error in both.
    """
    valid = _get_valid_lucide_icons(vue=is_vue)
    if not valid:
        return code
    sanitized = _replace_unknown_lucide(code, valid, package="@lucide/vue" if is_vue else "lucide-react")
    if report is not None and sanitized != code:
        report.patches.append(f"{filename}:lucide_icons")
    return sanitized


def _replace_unknown_lucide(code: str, valid: frozenset[str], package: str = "lucide-react") -> str:
    """``import { Github, House }`` -> ``import { CircleAlert as Github, House }``.

    Aliasing (rather than dropping the name) keeps every ``<Github />`` usage
    bound, so the page renders the fallback icon instead of crashing on an
    undefined component.
    """

    def _fix_import(match: re.Match[str]) -> str:
        prefix, names_str, suffix = match.group(1), match.group(2), match.group(3)
        names = [n.strip() for n in names_str.split(",") if n.strip()]
        fixed: list[str] = []
        for name in names:
            base, _, local = (part.strip() for part in name.partition(" as "))
            # ``type LucideProps`` (TSX) imports a type, not a value.
            if base in valid or base.startswith("type "):
                fixed.append(name)
            else:
                fixed.append(f"{_LUCIDE_FALLBACK} as {local or base}")
        if fixed == names:
            return match.group(0)
        return f"{prefix} {', '.join(fixed)} {suffix}"

    pattern = re.compile(
        rf"(import\s*\{{)([^}}]+)(\}}\s*from\s*['\"]{re.escape(package)}['\"])",
        re.DOTALL,
    )
    return pattern.sub(_fix_import, code)


# Pre-``server_kind`` placeholder choice (see _placeholder_backend_for_stack).
_LEGACY_FASTAPI_PLACEHOLDER_SLUGS = frozenset({"fastapi-vue"})


def _placeholder_backend_for_stack(stack: dict[str, Any]) -> str:
    """The stub entry file for *stack*: FastAPI for uvicorn-served stacks, else Flask.

    Decided by ``server_kind`` (what the image starts), so every uvicorn
    stack — builtin or user-authored — gets an ASGI ``app``. Builtin rows
    seeded before ``server_kind`` existed all carry the default ``python``;
    retries and re-evaluations pinned to such a row must keep the stub they
    got then, which chose by slug (FastAPI for ``fastapi-vue`` only).
    """
    if stack.get("server_kind") == "uvicorn" or stack.get("canonical_slug") in _LEGACY_FASTAPI_PLACEHOLDER_SLUGS:
        return _placeholder_fastapi()
    return _placeholder_backend()


def _placeholder_fastapi() -> str:
    return (
        "import os\n"
        "from pathlib import Path\n"
        "from fastapi import FastAPI, HTTPException\n"
        "from fastapi.responses import FileResponse\n"
        "from fastapi.staticfiles import StaticFiles\n\n"
        "app = FastAPI(title='Generated App')\n"
        "_STATIC = Path(__file__).parent / 'static'\n\n"
        "@app.get('/api/health')\n"
        "def health():\n"
        "    return {'status': 'ok'}\n\n"
        "if _STATIC.is_dir():\n"
        "    app.mount('/static', StaticFiles(directory=_STATIC), name='static')\n\n"
        "@app.get('/{full_path:path}', include_in_schema=False)\n"
        "def spa(full_path: str):\n"
        "    if full_path == 'api' or full_path.startswith('api/'):\n"
        "        raise HTTPException(status_code=404, detail='Not Found')\n"
        "    index = _STATIC / 'index.html'\n"
        "    if index.is_file():\n"
        "        return FileResponse(index)\n"
        "    raise HTTPException(status_code=404, detail='frontend not built')\n"
    )


def _placeholder_backend() -> str:
    return (
        "import os\n"
        "from pathlib import Path\n"
        "from flask import Flask, jsonify, send_from_directory\n\n"
        "app = Flask(__name__)\n"
        "_STATIC = Path(__file__).parent / 'static'\n\n"
        "@app.route('/api/health')\n"
        "def health():\n"
        "    return jsonify({'status': 'ok'})\n\n"
        "@app.route('/', defaults={'path': ''})\n"
        "@app.route('/<path:path>')\n"
        "def _serve_spa(path):\n"
        "    if path == 'api' or path.startswith('api/'):\n"
        "        return jsonify({'detail': 'Not found'}), 404\n"
        "    rel = path.removeprefix('static/')\n"
        "    if rel and (_STATIC / rel).is_file():\n"
        "        return send_from_directory(str(_STATIC), rel)\n"
        "    return send_from_directory(str(_STATIC), 'index.html')\n\n"
        "if __name__ == '__main__':\n"
        "    port = int(os.environ.get('PORT', 8000))\n"
        "    app.run(host='0.0.0.0', port=port, debug=False)\n"
    )


def _placeholder_frontend() -> str:
    return 'export default function App() {\n  return <div className="p-8">Hello</div>;\n}\n'


def _placeholder_frontend_vue() -> str:
    return '<template>\n  <div class="p-8">Hello</div>\n</template>\n\n<script setup></script>\n'
