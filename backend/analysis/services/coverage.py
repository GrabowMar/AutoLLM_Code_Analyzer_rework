"""Which technologies the user's generated apps contain, and which tools fit them.

The shop's purpose is to pick instruments for measuring LLM-generated code. A
tool that scans nothing the models produce reports "0 findings", which reads
as "clean" in comparisons; this module lets the shop show, per tool, how many
of the user's generated apps it can actually say something about.

Detection is deliberately cheap (file names, extensions and a few import /
manifest substrings) over the code map an analysis run would scan.
"""

from __future__ import annotations

import re
from pathlib import PurePosixPath
from typing import Any

from backend.analysis.models import AnalysisRun
from backend.analysis.models import AnalyzerTool

# Most recent completed jobs considered: enough to describe what the user's
# models generate without scanning a whole archive on every page load.
MAX_APPS = 200

_EXT_TECH = {
    ".py": {"python"},
    ".js": {"javascript"},
    ".mjs": {"javascript"},
    ".cjs": {"javascript"},
    ".jsx": {"javascript", "react"},
    ".ts": {"javascript"},
    ".tsx": {"javascript", "react"},
    ".vue": {"javascript", "vue"},
    ".html": {"html"},
    ".css": {"css"},
    ".sh": {"shell"},
    ".tf": {"terraform"},
}
_PY_MANIFESTS = {"requirements.txt", "pyproject.toml", "pipfile", "setup.py", "requirements-dev.txt"}
_PY_FRAMEWORKS = {
    "flask": re.compile(r"^\s*(from|import)\s+flask\b|^flask\b", re.IGNORECASE | re.MULTILINE),
    "fastapi": re.compile(r"^\s*(from|import)\s+fastapi\b|^fastapi\b", re.IGNORECASE | re.MULTILINE),
    "django": re.compile(r"^\s*(from|import)\s+django\b|^django\b", re.IGNORECASE | re.MULTILINE),
}
_EXPRESS_RE = re.compile(r"""require\(\s*['"]express['"]\s*\)|from\s+['"]express['"]|"express"\s*:""")
_REACT_RE = re.compile(r"""from\s+['"]react['"]|require\(\s*['"]react['"]\s*\)|"react"\s*:""")
_VUE_RE = re.compile(r"""from\s+['"]vue['"]|"vue"\s*:""")
_TEMPLATE_RE = re.compile(r"\{\{.*?\}\}|\{%.*?%\}", re.DOTALL)
_K8S_RE = re.compile(r"^apiVersion:.*^kind:", re.MULTILINE | re.DOTALL)
_WEB_BACKENDS = {"flask", "fastapi", "django", "express"}


def detect_technologies(files: dict[str, str]) -> set[str]:
    """Technology slugs (catalog vocabulary) present in a ``{path: content}`` map."""
    found: set[str] = set()
    for path, content in files.items():
        name = PurePosixPath(path).name.lower()
        suffix = PurePosixPath(name).suffix
        text = content if isinstance(content, str) else ""
        found |= _EXT_TECH.get(suffix, set())
        if name in _PY_MANIFESTS or (name.startswith("requirements") and suffix == ".txt"):
            found.add("pypi")
        if name == "package.json":
            found.add("npm")
        if name == "dockerfile" or name.startswith("dockerfile.") or suffix == ".dockerfile":
            found.add("docker")
        if suffix == ".py" or name in _PY_MANIFESTS or name.startswith("requirements"):
            found |= {fw for fw, rx in _PY_FRAMEWORKS.items() if rx.search(text)}
        if suffix in (".js", ".mjs", ".cjs", ".ts", ".jsx", ".tsx") or name == "package.json":
            if _EXPRESS_RE.search(text):
                found.add("express")
            if _REACT_RE.search(text):
                found.add("react")
            if _VUE_RE.search(text):
                found.add("vue")
        if suffix == ".html" and _TEMPLATE_RE.search(text):
            found.add("jinja")
        if suffix in (".yaml", ".yml") and _K8S_RE.search(text):
            found.add("kubernetes")
    if found & _WEB_BACKENDS:
        # A web backend is something dynamic scanners can probe once it runs.
        found |= {"http", "rest-api"}
    elif "html" in found:
        found.add("http")
    if found:
        found.add("generic")
    return found


def app_coverage(user) -> dict[str, Any]:
    """Technology and per-tool fit counts over *user*'s recent generated apps."""
    from backend.analysis.services.runner import _materialize
    from backend.generation.models import GenerationJob

    jobs = (
        GenerationJob.objects.filter(created_by=user, status=GenerationJob.Status.COMPLETED)
        .only("id", "result_data")
        .order_by("-created_at")[:MAX_APPS]
    )
    per_app: list[set[str]] = []
    for job in jobs:
        code = AnalysisRun(generation_job=job).get_code_for_analysis()
        techs = detect_technologies(_materialize(code))
        if techs:
            per_app.append(techs)

    tech_counts: dict[str, int] = {}
    for techs in per_app:
        for slug in techs:
            tech_counts[slug] = tech_counts.get(slug, 0) + 1

    tool_counts: dict[str, int] = {}
    for tool in AnalyzerTool.objects.filter(is_enabled=True).only("slug", "technologies"):
        wanted = set(tool.technologies or [])
        tool_counts[tool.slug] = sum(1 for techs in per_app if techs & wanted)

    return {
        "apps_total": len(per_app),
        "apps_considered": MAX_APPS,
        "technologies": tech_counts,
        "tools": tool_counts,
    }
