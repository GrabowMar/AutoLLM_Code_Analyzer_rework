"""The starter rule list (spec section 11.4, Appendix A #3): what no starter may contain.

WP-05 owns the rules; ``library/lint.py`` (WP-03) reports them for kind ``starter`` by calling
``starter_issues`` (or ``starter_issues_from_blobs``), and ``build.starters.compose`` runs the same checks
to fill ``StarterContent.forbidden_content_check``. Each rule maps to one registered lint code:

``lint_starter_forbidden_content``
    A text file contains ``LLM:``, ``_comment`` or ``TODO: model`` (hidden instructions to the model).
``lint_starter_security_library``
    A manifest or lockfile names a package from ``data/starters/security_deny.yaml``. A starter never
    preinstalls a security library: whether the model reaches for one is what studies measure.
``lint_starter_unpinned_image``
    A ``FROM`` of the Dockerfile has no ``@sha256:`` digest, or ``base_images`` does not list exactly the
    Dockerfile's external ``FROM`` images.
``lint_starter_file_missing``
    The Dockerfile (fixed stacks) or a listed lockfile is not in ``files``, or a ``minimal``/``full`` manifest
    has no lockfile next to it.
``lint_starter_stack_mismatch``
    The tree does not fit its stack profile: a free-choice starter that is not ``bare`` or that ships a
    Dockerfile, a frontend project for a ``frontend: none`` profile (or none for a profile with a frontend,
    at ``minimal``), or a ``minimal`` manifest that does not name the profile's framework.
"""

from __future__ import annotations

import json
import re
import tomllib
from collections.abc import Callable
from collections.abc import Iterable
from collections.abc import Mapping
from functools import cache
from pathlib import Path
from pathlib import PurePosixPath
from typing import Any
from typing import Final

import yaml

from backend.generation.build import dockerfiles
from backend.generation.contracts.issues import Issue
from backend.generation.contracts.issues import make_issue

__all__ = [
    "FORBIDDEN_PATTERNS",
    "RULES_VERSION",
    "SECURITY_DENY_PATH",
    "denied_packages",
    "forbidden_content",
    "lock_for_manifest",
    "manifest_packages",
    "security_libraries",
    "starter_issues",
    "starter_issues_from_blobs",
]

RULES_VERSION: Final = 1
SECURITY_DENY_PATH: Final = dockerfiles.STARTERS_ROOT / "security_deny.yaml"

# (label, compiled pattern). `LLM:` and `_comment` are case-sensitive tokens; "TODO: model" in any case.
FORBIDDEN_PATTERNS: Final = (
    ("LLM:", re.compile(r"LLM:")),
    ("_comment", re.compile(r"_comment")),
    ("TODO: model", re.compile(r"TODO:\s*model", re.IGNORECASE)),
)

# Manifest file name -> (ecosystem, lockfile names that may sit next to it).
_MANIFESTS: Final = {
    "requirements.txt": ("pypi", ("requirements.lock",)),
    "requirements.in": ("pypi", ("requirements.txt", "requirements.lock")),
    "pyproject.toml": ("pypi", ("uv.lock", "poetry.lock", "requirements.lock")),
    "package.json": ("npm", ("package-lock.json", "pnpm-lock.yaml", "yarn.lock")),
    "go.mod": ("go", ("go.sum",)),
    "pom.xml": ("maven", ("dependency-list.txt",)),
}
_LOCKFILES: Final = {
    "requirements.lock": "pypi",
    "uv.lock": "pypi",
    "poetry.lock": "pypi",
    "package-lock.json": "npm",
    "go.sum": "go",
    "dependency-list.txt": "maven",
}
_FRAMEWORK_PACKAGE: Final = {
    "flask": ("pypi", "flask"),
    "fastapi": ("pypi", "fastapi"),
    "django": ("pypi", "django"),
    "express": ("npm", "express"),
    "react": ("npm", "react"),
    "vue": ("npm", "vue"),
    "svelte": ("npm", "svelte"),
}
_REQ_NAME: Final = re.compile(r"^\s*([A-Za-z0-9][A-Za-z0-9._-]*)")
_GO_REQUIRE: Final = re.compile(r"^\s*(?:require\s+)?([a-z0-9][\w.\-/]*\.[\w.\-/]+)\s+v[0-9]", re.MULTILINE)
_MAVEN_ARTIFACT: Final = re.compile(r"<artifactId>\s*([^<\s]+)\s*</artifactId>")


def _pep503(name: str) -> str:
    return re.sub(r"[-_.]+", "-", name).lower()


def _normalize(ecosystem: str, name: str) -> str:
    if ecosystem == "pypi":
        return _pep503(name)
    if ecosystem == "npm":
        return name.strip().lower()
    return name.strip()


@cache
def _deny_table(path: str) -> dict[str, frozenset[str]]:
    data = yaml.safe_load(Path(path).read_text(encoding="utf-8")) or {}
    packages = data.get("packages") or {}
    return {eco: frozenset(_normalize(eco, str(name)) for name in names or []) for eco, names in packages.items()}


def denied_packages(path: Path | None = None) -> dict[str, frozenset[str]]:
    """``{ecosystem: normalized names}`` from ``security_deny.yaml``."""
    return _deny_table(str(path or SECURITY_DENY_PATH))


# =====================================================================================
# Manifest and lockfile parsing
# =====================================================================================


def _requirements(text: str) -> list[str]:
    names: list[str] = []
    for raw in text.splitlines():
        line = raw.split("#", 1)[0].strip()
        if not line or line.startswith(("-", "--")):
            continue
        match = _REQ_NAME.match(line)
        if match:
            names.append(match.group(1))
    return names


def _package_json(text: str) -> list[str]:
    data = json.loads(text)
    names: list[str] = []
    for key in ("dependencies", "devDependencies", "optionalDependencies", "peerDependencies"):
        names += list((data.get(key) or {}).keys())
    return names


def _package_lock(text: str) -> list[str]:
    data = json.loads(text)
    names = {key.rsplit("node_modules/", 1)[-1] for key in (data.get("packages") or {}) if "node_modules/" in key}
    names |= set((data.get("dependencies") or {}).keys())  # lockfileVersion 1
    return sorted(names)


def _pyproject(text: str) -> list[str]:
    data = tomllib.loads(text)
    project = data.get("project") or {}
    specs = list(project.get("dependencies") or [])
    for extra in (project.get("optional-dependencies") or {}).values():
        specs += list(extra)
    for group in (data.get("dependency-groups") or {}).values():
        specs += [item for item in group if isinstance(item, str)]
    return [m.group(1) for spec in specs if (m := _REQ_NAME.match(spec))]


def _uv_lock(text: str) -> list[str]:
    data = tomllib.loads(text)
    return [str(package["name"]) for package in data.get("package") or [] if "name" in package]


def manifest_packages(path: str, data: bytes) -> list[tuple[str, str]]:
    """``[(ecosystem, normalized name)]`` named by a manifest or lockfile; ``[]`` for any other file.

    An unparseable manifest raises ``ValueError``: a starter whose manifest cannot be read cannot be checked.
    """
    name = PurePosixPath(path).name
    text = data.decode("utf-8")
    try:
        if name.startswith("requirements") and name.endswith((".txt", ".in", ".lock")):
            eco, names = "pypi", _requirements(text)
        elif name == "pyproject.toml":
            eco, names = "pypi", _pyproject(text)
        elif name == "uv.lock":
            eco, names = "pypi", _uv_lock(text)
        elif name == "package.json":
            eco, names = "npm", _package_json(text)
        elif name == "package-lock.json":
            eco, names = "npm", _package_lock(text)
        elif name in {"go.mod", "go.sum"}:
            eco, names = "go", _GO_REQUIRE.findall(text)
        elif name in {"pom.xml", "dependency-list.txt"}:
            eco, names = "maven", _MAVEN_ARTIFACT.findall(text) or re.findall(r":([\w.\-]+):[\w.\-]+:", text)
        else:
            return []
    except (json.JSONDecodeError, tomllib.TOMLDecodeError, UnicodeDecodeError) as exc:
        msg = f"cannot parse {path}: {exc}"
        raise ValueError(msg) from exc
    return sorted({(eco, _normalize(eco, n)) for n in names})


def lock_for_manifest(path: str, files: Iterable[str]) -> str | None:
    """The lockfile next to manifest ``path`` among ``files``, or ``None``."""
    pure = PurePosixPath(path)
    entry = _MANIFESTS.get(pure.name)
    if entry is None:
        return None
    present = set(files)
    for lock in entry[1]:
        candidate = (pure.parent / lock).as_posix()
        if candidate in present and candidate != path:
            return candidate
    return None


def security_libraries(files: Mapping[str, bytes], deny: Mapping[str, frozenset[str]] | None = None) -> list[dict]:
    """``[{path, ecosystem, package}]`` for every deny-listed package in a manifest or lockfile."""
    deny = deny if deny is not None else denied_packages()
    hits: list[dict] = []
    for path in sorted(files):
        for eco, name in manifest_packages(path, files[path]) if _is_manifest_or_lock(path) else []:
            if name in deny.get(eco, frozenset()) or _module_denied(eco, name, deny):
                hits.append({"path": path, "ecosystem": eco, "package": name})
    return hits


def _module_denied(eco: str, name: str, deny: Mapping[str, frozenset[str]]) -> bool:
    # A Go module path is denied with any of its sub-paths (github.com/golang-jwt/jwt/v5/...).
    return eco == "go" and any(name == d or name.startswith(f"{d}/") for d in deny.get("go", frozenset()))


def _is_manifest_or_lock(path: str) -> bool:
    name = PurePosixPath(path).name
    return name in _MANIFESTS or name in _LOCKFILES or (name.startswith("requirements") and name.endswith(".txt"))


def forbidden_content(files: Mapping[str, bytes]) -> list[dict]:
    """``[{path, pattern, line}]`` for every forbidden token in a UTF-8 text file (binary files are skipped)."""
    hits: list[dict] = []
    for path in sorted(files):
        try:
            text = files[path].decode("utf-8")
        except UnicodeDecodeError:
            continue
        for label, pattern in FORBIDDEN_PATTERNS:
            match = pattern.search(text)
            if match:
                hits.append({"path": path, "pattern": label, "line": text.count("\n", 0, match.start()) + 1})
    return hits


# =====================================================================================
# The rule list
# =====================================================================================


def _issue(code: str, message: str, where: str, **data: Any) -> Issue:
    return make_issue(code, message=message, path=where, data=data)


def _manifest_issues(files: Mapping[str, bytes]) -> tuple[list[Issue], list[tuple[str, list[tuple[str, str]]]]]:
    issues: list[Issue] = []
    parsed: list[tuple[str, list[tuple[str, str]]]] = []
    for path in sorted(files):
        if not _is_manifest_or_lock(path):
            continue
        try:
            parsed.append((path, manifest_packages(path, files[path])))
        except ValueError as exc:
            issues.append(_issue("lint_starter_file_missing", str(exc), f"files.{path}", file=path, unparseable=True))
    return issues, parsed


def _dockerfile_issues(content: Mapping[str, Any], files: Mapping[str, bytes], *, free: bool) -> list[Issue]:
    dockerfile = str(content.get("dockerfile") or "Dockerfile")
    if free:
        return []
    if dockerfile not in files:
        return [_issue("lint_starter_file_missing", f"the dockerfile {dockerfile} is not in files", "dockerfile")]
    text = files[dockerfile].decode("utf-8", errors="replace")
    issues: list[Issue] = []
    declared = {str(base.get("digest")) for base in content.get("base_images") or []}
    found: set[str] = set()
    for line in dockerfiles.parse_from_lines(text):
        if line.is_stage_ref:
            continue
        if line.ref is None or line.digest is None:
            issues.append(
                _issue(
                    "lint_starter_unpinned_image",
                    f"FROM {line.image!r} (line {line.line}) is not digest-pinned",
                    f"files.{dockerfile}",
                    line=line.line,
                    image=line.image,
                ),
            )
            continue
        found.add(f"{line.ref}@{line.digest}")
    if not issues and found != declared:
        issues.append(
            _issue(
                "lint_starter_unpinned_image",
                "base_images must list exactly the Dockerfile's FROM images",
                "base_images",
                declared=sorted(declared),
                dockerfile=sorted(found),
            ),
        )
    return issues


def _stack_issues(
    content: Mapping[str, Any],
    files: Mapping[str, bytes],
    stack: Mapping[str, Any],
    parsed: list[tuple[str, list[tuple[str, str]]]],
) -> list[Issue]:
    level = content.get("level")
    dockerfile = str(content.get("dockerfile") or "Dockerfile")
    issues: list[Issue] = []
    if stack.get("choice_mode") == "free":
        if level != "bare":
            issues.append(_issue("lint_starter_stack_mismatch", "free-choice starters are bare only", "level"))
        if dockerfile in files:
            issues.append(
                _issue("lint_starter_stack_mismatch", "a free-choice starter ships no Dockerfile", "dockerfile"),
            )
        return issues
    frontend = (stack.get("frontend") or {}).get("framework")
    backend = (stack.get("backend") or {}).get("framework")
    has_frontend_project = "frontend/package.json" in files
    if frontend == "none" and has_frontend_project:
        issues.append(
            _issue("lint_starter_stack_mismatch", "a frontend: none profile has no frontend project", "files"),
        )
    if level == "bare":
        return issues
    if frontend not in {None, "none", "free"} and not has_frontend_project:
        issues.append(_issue("lint_starter_stack_mismatch", f"the {frontend} frontend project is missing", "files"))
    named = {pair for _, pairs in parsed for pair in pairs}
    for framework in (backend, frontend):
        wanted = _FRAMEWORK_PACKAGE.get(str(framework))
        if wanted and wanted not in named:
            issues.append(
                _issue(
                    "lint_starter_stack_mismatch",
                    f"no manifest names the {framework} package",
                    "files",
                    framework=framework,
                ),
            )
    return issues


def starter_issues(
    content: Mapping[str, Any],
    files: Mapping[str, bytes],
    *,
    stack: Mapping[str, Any] | None = None,
) -> list[Issue]:
    """Every rule above for ``content`` (a ``StarterContent`` dump) and its file bytes (path -> bytes).

    ``stack`` is the profile's ``StackProfileContent`` dump; without it the stack rules are skipped.
    """
    issues: list[Issue] = [
        _issue(
            "lint_starter_forbidden_content",
            f"{hit['path']} contains {hit['pattern']!r} (line {hit['line']})",
            f"files.{hit['path']}",
            **hit,
        )
        for hit in forbidden_content(files)
    ]
    parse_issues, parsed = _manifest_issues(files)
    issues += parse_issues
    deny = denied_packages()
    for path, pairs in parsed:
        for eco, name in pairs:
            if name in deny.get(eco, frozenset()) or _module_denied(eco, name, deny):
                issues.append(
                    _issue(
                        "lint_starter_security_library",
                        f"{path} names the security library {name}",
                        f"files.{path}",
                        ecosystem=eco,
                        package=name,
                    ),
                )
    free = bool(stack and stack.get("choice_mode") == "free")
    issues += _dockerfile_issues(content, files, free=free)
    for lock in content.get("lockfiles") or []:
        if lock not in files:
            issues.append(_issue("lint_starter_file_missing", f"the lockfile {lock} is not in files", "lockfiles"))
    if content.get("level") in {"minimal", "full"}:
        for path in sorted(files):
            name = PurePosixPath(path).name
            if name in _MANIFESTS and name != "requirements.in" and lock_for_manifest(path, files) is None:
                issues.append(
                    _issue("lint_starter_file_missing", f"the manifest {path} has no lockfile", f"files.{path}"),
                )
    if stack is not None:
        issues += _stack_issues(content, files, stack, parsed)
    return issues


def starter_issues_from_blobs(
    content: Mapping[str, Any],
    *,
    stack: Mapping[str, Any] | None = None,
    read: Callable[[str], bytes] | None = None,
) -> list[Issue]:
    """``starter_issues`` for stored content: file bytes are read from the blob store by sha."""
    if read is None:
        from backend.generation import blobs

        read = blobs.get_bytes
    files = {path: read(str(entry["blob"])) for path, entry in (content.get("files") or {}).items()}
    return starter_issues(content, files, stack=stack)
