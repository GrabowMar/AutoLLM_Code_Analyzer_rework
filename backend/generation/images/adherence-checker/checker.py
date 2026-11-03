#!/usr/bin/env python3
"""adherence-checker v1 (spec 8.7): which stack, styling and auth a sample actually uses.

Standard library only, so the same file runs in the ``adherence-checker`` image (which adds ``syft`` for the
SBOM) and in-process in the backend (``backend.generation.evaluation.adherence``). It reads the tree and never
executes it.

CLI::  checker.py --tree /tree --expect /in/expect.json --out /out/adherence.json [--sbom /out/sbom.json]

``expect.json``: ``{"stack": StackProfileContent, "style": StyleContent | null, "auth_mode": str,
"runtime": {"auth": "cookie" | "bearer" | null, "server": str | null}}`` (``runtime`` is what the tester saw).

Output: an ``AdherenceRecord`` (``contracts.evaluation``). Adherence never filters a sample: it is a column
and a note. In ``choice_mode=free`` stacks the record lists what was detected and the status is
``compliant`` by definition (the pick is an outcome, never a covariate).
"""

from __future__ import annotations

import argparse
import ast
import json
import os
import re
import subprocess
import sys
from pathlib import Path
from typing import Any

CHECKER_VERSION = 1
_SKIP_DIRS = {"node_modules", ".git", "__pycache__", ".venv", "venv", "dist", "build", ".next", "data"}
_MAX_FILE = 512 * 1024

_PY_BACKENDS = {"flask": "flask", "fastapi": "fastapi", "django": "django", "starlette": "starlette"}
_JS_BACKENDS = {"express": "express", "fastify": "fastify", "koa": "koa", "@nestjs/core": "nest", "hono": "hono"}
_FRONTENDS = {"react": "react", "vue": "vue", "svelte": "svelte", "@angular/core": "angular", "solid-js": "solid"}
_DB_DEPS = {
    "psycopg": "postgres",
    "psycopg2": "postgres",
    "psycopg2-binary": "postgres",
    "asyncpg": "postgres",
    "pg": "postgres",
    "postgres": "postgres",
    "mysqlclient": "mysql",
    "pymysql": "mysql",
    "mysql2": "mysql",
    "pymongo": "mongodb",
    "mongoose": "mongodb",
    "mongodb": "mongodb",
    "better-sqlite3": "sqlite",
    "sqlite3": "sqlite",
    "aiosqlite": "sqlite",
}
_COMPONENT_LIBS = (
    "@mui/material",
    "@material-ui/core",
    "antd",
    "@chakra-ui/react",
    "react-bootstrap",
    "bootstrap",
    "vuetify",
    "element-plus",
    "@headlessui/react",
    "@radix-ui",
    "primevue",
    "quasar",
    "@mantine/core",
    "flowbite",
    "daisyui",
)
_TW = re.compile(
    r"^(?:[a-z]+:)*-?(?:p[trblxy]?|m[trblxy]?|w|h|min-w|min-h|max-w|max-h|gap|space-[xy]|text|bg|border|rounded|"
    r"shadow|flex|grid|col-span|row-span|items|justify|content|self|font|leading|tracking|opacity|z|top|left|"
    r"right|bottom|inset|ring|outline|cursor|overflow|transition|duration|ease|translate-[xy]|scale|rotate|"
    r"divide-[xy]|place|order|object|aspect|fill|stroke|from|to|via|decoration|underline|truncate|hidden|block|"
    r"inline|inline-block|inline-flex|table|absolute|relative|fixed|sticky|container|sr-only|uppercase|"
    r"lowercase|capitalize|italic|whitespace|break|list|grow|shrink|basis)(?:-[\w./\[\]#%-]+)?$",
)
_CLASS_ATTR = re.compile(r"""\bclass(?:Name)?\s*=\s*(?:"([^"]*)"|'([^']*)'|\{\s*[`"']([^`"']*)[`"']\s*\})""")
_CONN = re.compile(r"(postgres(?:ql)?|mysql|mongodb|sqlite)(?:\+\w+)?:/{2,3}", re.IGNORECASE)


def _files(root: Path) -> list[Path]:
    out: list[Path] = []
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = [d for d in dirnames if d not in _SKIP_DIRS]
        for name in filenames:
            out.append(Path(dirpath) / name)
    return sorted(out)


def _read(path: Path) -> str:
    try:
        if path.stat().st_size > _MAX_FILE:
            return ""
        return path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return ""


def _req_name(line: str) -> tuple[str, str | None, bool] | None:
    line = line.split("#", 1)[0].strip()
    if not line or line.startswith(("-", "--")):
        return None
    m = re.match(r"^([A-Za-z0-9_.\-]+)(\[[^\]]*\])?\s*(.*)$", line)
    if not m:
        return None
    spec = m.group(3).split(";", 1)[0].strip()
    pinned = spec.startswith("==") and "*" not in spec
    version = spec[2:].strip() if pinned else (spec or None)
    return m.group(1).lower().replace("_", "-"), version, pinned


def _packages(root: Path, files: list[Path]) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for path in files:
        rel = path.relative_to(root).as_posix()
        if path.name.startswith("requirements") and path.suffix in (".txt", ".in"):
            for line in _read(path).splitlines():
                parsed = _req_name(line)
                if parsed:
                    name, version, pinned = parsed
                    out.append(
                        {
                            "ecosystem": "pypi",
                            "name": name,
                            "version": version,
                            "pinned": pinned,
                            "declared_in": rel,
                            "exists": None,
                        },
                    )
        elif path.name == "pyproject.toml":
            for dep in re.findall(r'^\s*"([A-Za-z0-9_.\-\[\]]+\s*[^"]*)"\s*,?\s*$', _read(path), re.M):
                parsed = _req_name(dep)
                if parsed:
                    name, version, pinned = parsed
                    out.append(
                        {
                            "ecosystem": "pypi",
                            "name": name,
                            "version": version,
                            "pinned": pinned,
                            "declared_in": rel,
                            "exists": None,
                        },
                    )
        elif path.name == "package.json":
            try:
                doc = json.loads(_read(path) or "{}")
            except ValueError:
                continue
            for section in ("dependencies", "devDependencies"):
                for name, spec in (doc.get(section) or {}).items():
                    spec = str(spec)
                    pinned = bool(re.fullmatch(r"\d+\.\d+\.\d+(?:[-+][\w.]+)?", spec))
                    out.append(
                        {
                            "ecosystem": "npm",
                            "name": name,
                            "version": spec,
                            "pinned": pinned,
                            "declared_in": rel,
                            "exists": None,
                        },
                    )
        elif path.name == "go.mod":
            for name, version in re.findall(r"^\s*([\w./\-]+)\s+(v[\w.\-+]+)", _read(path), re.M):
                if name not in ("module", "go"):
                    out.append(
                        {
                            "ecosystem": "go",
                            "name": name,
                            "version": version,
                            "pinned": True,
                            "declared_in": rel,
                            "exists": None,
                        },
                    )
    return out


def _detected(votes: dict[str, list[str]]) -> dict[str, Any]:
    if not votes:
        return {"value": None, "evidence": [], "confidence": "low"}
    value = max(sorted(votes), key=lambda k: len(votes[k]))
    evidence = votes[value][:8]
    kinds = {e.split(":", 1)[0] for e in votes[value]}
    confidence = "high" if len(kinds) >= 2 else "medium"
    if len(votes) > 1:
        confidence = "medium" if confidence == "high" else "low"
    return {"value": value, "evidence": evidence, "confidence": confidence}


def analyse(root: Path, expect: dict[str, Any]) -> dict[str, Any]:
    files = _files(root)
    packages = _packages(root, files)
    names = {p["name"].lower() for p in packages}
    backend: dict[str, list[str]] = {}
    frontend: dict[str, list[str]] = {}
    database: dict[str, list[str]] = {}
    component_imports: dict[str, int] = {}
    class_tokens = 0
    tw_tokens = 0
    css_files = css_modules = 0
    for pkg in packages:
        name = pkg["name"].lower()
        if name in _PY_BACKENDS:
            backend.setdefault(_PY_BACKENDS[name], []).append(f"manifest:{pkg['declared_in']}:{name}")
        if name in _JS_BACKENDS:
            backend.setdefault(_JS_BACKENDS[name], []).append(f"manifest:{pkg['declared_in']}:{name}")
        if name in _FRONTENDS:
            frontend.setdefault(_FRONTENDS[name], []).append(f"manifest:{pkg['declared_in']}:{name}")
        if name in _DB_DEPS:
            database.setdefault(_DB_DEPS[name], []).append(f"manifest:{pkg['declared_in']}:{name}")
        if pkg["ecosystem"] == "go" and "gin-gonic/gin" in name:
            backend.setdefault("gin", []).append(f"manifest:{pkg['declared_in']}:{name}")
    for path in files:
        rel = path.relative_to(root).as_posix()
        suffix = path.suffix.lower()
        if suffix in (".db", ".sqlite", ".sqlite3"):
            database.setdefault("sqlite", []).append(f"file:{rel}")
            continue
        if suffix == ".css" or suffix == ".scss":
            css_files += 1
            if path.name.endswith((".module.css", ".module.scss")):
                css_modules += 1
            continue
        if suffix == ".vue":
            frontend.setdefault("vue", []).append(f"ext:{rel}")
        elif suffix == ".svelte":
            frontend.setdefault("svelte", []).append(f"ext:{rel}")
        if suffix not in (
            ".py",
            ".js",
            ".jsx",
            ".ts",
            ".tsx",
            ".mjs",
            ".cjs",
            ".vue",
            ".svelte",
            ".html",
            ".java",
            ".go",
        ):
            continue
        text = _read(path)
        if not text:
            continue
        if suffix == ".py":
            try:
                tree = ast.parse(text)
            except SyntaxError:
                tree = None
            for node in ast.walk(tree) if tree is not None else []:
                mods = []
                if isinstance(node, ast.Import):
                    mods = [a.name for a in node.names]
                elif isinstance(node, ast.ImportFrom) and node.module:
                    mods = [node.module]
                for mod in mods:
                    top = mod.split(".", 1)[0].lower()
                    if top in _PY_BACKENDS:
                        backend.setdefault(_PY_BACKENDS[top], []).append(f"import:{rel}:{mod}")
                    if top in ("sqlite3", "aiosqlite"):
                        database.setdefault("sqlite", []).append(f"import:{rel}:{mod}")
                    if top in ("psycopg", "psycopg2", "asyncpg"):
                        database.setdefault("postgres", []).append(f"import:{rel}:{mod}")
        else:
            for mod in re.findall(r"""(?:from\s+|require\(\s*|import\s+)['"]([@\w./\-]+)['"]""", text):
                top = mod if mod.startswith("@") and "/" in mod else mod.split("/", 1)[0]
                full = "/".join(mod.split("/")[:2]) if mod.startswith("@") else top
                if full in _JS_BACKENDS or top in _JS_BACKENDS:
                    key = _JS_BACKENDS.get(full) or _JS_BACKENDS[top]
                    backend.setdefault(key, []).append(f"import:{rel}:{mod}")
                if top in _FRONTENDS or full in _FRONTENDS:
                    key = _FRONTENDS.get(full) or _FRONTENDS[top]
                    frontend.setdefault(key, []).append(f"import:{rel}:{mod}")
                for lib in _COMPONENT_LIBS:
                    if mod == lib or mod.startswith(lib + "/") or (lib.endswith("/") and mod.startswith(lib)):
                        component_imports[lib] = component_imports.get(lib, 0) + 1
            if suffix in (".jsx", ".tsx", ".vue", ".svelte", ".html", ".js", ".ts"):
                for m in _CLASS_ATTR.finditer(text):
                    for token in (m.group(1) or m.group(2) or m.group(3) or "").split():
                        class_tokens += 1
                        if _TW.match(token):
                            tw_tokens += 1
            for imp in re.findall(r"""import\s+\w+\s+from\s+['"]([^'"]+\.module\.s?css)['"]""", text):
                css_modules += 0 if imp else 0
        for m in _CONN.finditer(text):
            kind = m.group(1).lower()
            database.setdefault("postgres" if kind.startswith("postgres") else kind, []).append(f"conn:{rel}")
    runtime = expect.get("runtime") or {}
    auth_votes: dict[str, list[str]] = {}
    if runtime.get("auth") in ("cookie", "bearer"):
        auth_votes[str(runtime["auth"])] = [f"runtime:login:{runtime['auth']}", "runtime:tester"]
    if runtime.get("server"):
        server = str(runtime["server"]).lower()
        for key in ("werkzeug", "uvicorn", "gunicorn", "express"):
            if key in server:
                mapped = {"werkzeug": "flask", "uvicorn": "fastapi", "express": "express"}.get(key)
                if mapped:
                    backend.setdefault(mapped, []).append(f"runtime:server:{server}")
    if "tailwindcss" in names or "@tailwindcss/vite" in names:
        tailwind_dep = True
    else:
        tailwind_dep = False
    share = (tw_tokens / class_tokens) if class_tokens else None
    detected = {
        "backend": _detected(backend),
        "frontend": _detected(frontend) if frontend else {"value": "none", "evidence": [], "confidence": "medium"},
        "database": _detected(database),
        "styling": _styling(tailwind_dep, share, css_files, css_modules, component_imports),
        "auth": _detected(auth_votes),
    }
    deviations = _deviations(detected, expect)
    stack = expect.get("stack") or {}
    if stack.get("choice_mode") == "free":
        status = "compliant"
    elif any(d["dimension"] in ("backend", "frontend", "database", "auth") for d in deviations):
        status = "violated"
    elif deviations:
        status = "partial"
    else:
        status = "compliant"
    return {
        "checker_version": CHECKER_VERSION,
        "status": status,
        "detected": detected,
        "deviations": deviations,
        "packages": packages[:500],
        "hallucinated": [],
        "unpinned": sorted({p["name"] for p in packages if not p["pinned"]})[:200],
        "style_metrics": {
            "tailwind_class_share": round(share, 4) if share is not None else None,
            "css_files": css_files,
            "css_modules": css_modules,
            "component_library_imports": component_imports,
        },
        "sbom_blob": None,
    }


def _styling(
    tailwind_dep: bool,
    share: float | None,
    css_files: int,
    css_modules: int,
    libs: dict[str, int],
) -> dict[str, Any]:
    votes: dict[str, list[str]] = {}
    if tailwind_dep:
        votes.setdefault("tailwind", []).append("manifest:tailwindcss")
    if share is not None and share >= 0.3:
        votes.setdefault("tailwind", []).append(f"classes:share={share:.2f}")
    if libs:
        votes.setdefault("component-library", []).extend(f"import:{k}" for k in sorted(libs))
    if css_modules:
        votes.setdefault("css-modules", []).append(f"files:{css_modules} css modules")
    if css_files and not votes:
        votes.setdefault("plain-css", []).append(f"files:{css_files} css files")
    out = _detected(votes)
    if len(votes) > 1:
        out["evidence"] = [e for v in votes.values() for e in v][:8]
        out["value"] = "mixed:" + "+".join(sorted(votes))
    return out


def _deviations(detected: dict[str, Any], expect: dict[str, Any]) -> list[dict[str, Any]]:
    stack = expect.get("stack") or {}
    style = expect.get("style") or {}
    out: list[dict[str, Any]] = []

    def dev(dim: str, want: str | None, got: dict[str, Any]) -> None:
        value = got.get("value")
        if want in (None, "", "free", "model-choice") or value is None:
            return
        if want != value:
            out.append(
                {
                    "dimension": dim,
                    "expected": str(want),
                    "observed": str(value),
                    "evidence": list(got.get("evidence") or [])[:5],
                },
            )

    if stack.get("choice_mode") != "free":
        dev("backend", (stack.get("backend") or {}).get("framework"), detected["backend"])
        dev("frontend", (stack.get("frontend") or {}).get("framework"), detected["frontend"])
        dev("database", stack.get("database"), detected["database"])
    system = style.get("system")
    styling = detected["styling"]
    if system and system != "model-choice" and styling.get("value"):
        value = str(styling["value"])
        if value.startswith("mixed:") and system in value:
            out.append(
                {
                    "dimension": "styling",
                    "expected": system,
                    "observed": value,
                    "evidence": list(styling["evidence"])[:5],
                },
            )
        else:
            dev("styling", system, styling)
    want_auth = {"contract_bearer": "bearer", "cookie_session": "cookie"}.get(str(expect.get("auth_mode") or ""))
    dev("auth", want_auth, detected["auth"])
    return out


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="adherence-checker v1")
    parser.add_argument("--tree", default="/tree")
    parser.add_argument("--expect", default="/in/expect.json")
    parser.add_argument("--out", default="/out/adherence.json")
    parser.add_argument("--sbom", default="")
    args = parser.parse_args(argv)
    with open(args.expect, encoding="utf-8") as fh:
        expect = json.load(fh)
    record = analyse(Path(args.tree), expect)
    if args.sbom:
        try:
            done = subprocess.run(  # noqa: S603
                ["syft", f"dir:{args.tree}", "-o", "cyclonedx-json", "-q"],  # noqa: S607
                capture_output=True,
                timeout=300,
                check=False,
            )
            if done.returncode == 0:
                Path(args.sbom).write_bytes(done.stdout)
        except (OSError, subprocess.TimeoutExpired):
            pass
    with open(args.out, "w", encoding="utf-8") as fh:
        json.dump(record, fh)
    print("LLMLAB-ADHERENCE " + json.dumps({"status": record["status"]}))
    return 0


if __name__ == "__main__":
    sys.exit(main())
