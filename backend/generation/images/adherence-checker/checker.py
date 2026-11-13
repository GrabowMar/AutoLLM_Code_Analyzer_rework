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
# Go: module path prefixes (go.mod requires and imports). A tree that serves HTTP through ``net/http`` with
# none of the router modules below is ``go-stdlib``.
_GO_BACKENDS = {
    "github.com/gin-gonic/gin": "gin",
    "github.com/labstack/echo": "echo",
    "github.com/gofiber/fiber": "fiber",
    "github.com/go-chi/chi": "chi",
    "github.com/gorilla/mux": "gorilla",
    "github.com/julienschmidt/httprouter": "httprouter",
    "github.com/beego/beego": "beego",
}
_GO_DB = {
    "modernc.org/sqlite": "sqlite",
    "github.com/mattn/go-sqlite3": "sqlite",
    "github.com/glebarez/sqlite": "sqlite",
    "github.com/ncruces/go-sqlite3": "sqlite",
    "github.com/lib/pq": "postgres",
    "github.com/jackc/pgx": "postgres",
    "github.com/go-sql-driver/mysql": "mysql",
    "go.mongodb.org/mongo-driver": "mongodb",
}
# database/sql driver names in ``sql.Open("<driver>", ...)``.
_GO_SQL_DRIVERS = {
    "sqlite": "sqlite",
    "sqlite3": "sqlite",
    "postgres": "postgres",
    "pgx": "postgres",
    "mysql": "mysql",
}
# Maven/Gradle: ``groupId:artifactId`` (exact) or ``groupId:`` (any artifact of the group).
_JAVA_BACKENDS = {
    "org.springframework.boot:": "spring-boot",
    "io.quarkus:": "quarkus",
    "io.micronaut:": "micronaut",
    "io.javalin:": "javalin",
    "com.sparkjava:": "spark",
    "io.dropwizard:": "dropwizard",
    "io.vertx:": "vertx",
}
_JAVA_DB = {
    "org.xerial:sqlite-jdbc": "sqlite",
    "org.postgresql:postgresql": "postgres",
    "com.mysql:mysql-connector-j": "mysql",
    "mysql:mysql-connector-java": "mysql",
    "org.mariadb.jdbc:mariadb-java-client": "mysql",
    "org.mongodb:": "mongodb",
    "org.springframework.boot:spring-boot-starter-data-mongodb": "mongodb",
    "com.h2database:h2": "h2",
    "org.hsqldb:hsqldb": "hsqldb",
}
_JAVA_IMPORTS = {
    "org.springframework.boot": "spring-boot",
    "io.quarkus": "quarkus",
    "io.micronaut": "micronaut",
    "io.javalin": "javalin",
    "spark": "spark",
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
_JDBC = re.compile(r"jdbc:(sqlite|postgresql|mysql|mariadb|h2|hsqldb):", re.IGNORECASE)
_JDBC_KINDS = {"postgresql": "postgres", "mariadb": "mysql"}
_GO_SQL_OPEN = re.compile(r"""\bsql\.Open\(\s*["'`](\w+)["'`]""")
_GO_IMPORT_BLOCK = re.compile(r"^import\s*\((.*?)^\)", re.MULTILINE | re.DOTALL)
_GO_IMPORT_LINE = re.compile(r'^import\s+(?:[\w.]+\s+)?"([^"]+)"', re.MULTILINE)
_JAVA_IMPORT = re.compile(r"^\s*import\s+(?:static\s+)?([\w.]+)", re.MULTILINE)
_GRADLE_DEP = re.compile(
    r"\b(?:implementation|api|compileOnly|runtimeOnly|testImplementation|annotationProcessor)\s*\(?\s*"
    r"""["']([\w.\-]+):([\w.\-]+)(?::([^"']+))?["']""",
)


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
            # go.mod pins exact (or pseudo-) versions and go.sum their checksums: every require is pinned.
            for name, version, _indirect in _go_requires(_read(path)):
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
        elif path.name == "pom.xml":
            for group, artifact, version, pinned in _pom_dependencies(_read(path)):
                out.append(
                    {
                        "ecosystem": "maven",
                        "name": f"{group}:{artifact}",
                        "version": version,
                        "pinned": pinned,
                        "declared_in": rel,
                        "exists": None,
                    },
                )
        elif path.name in ("build.gradle", "build.gradle.kts"):
            text = _read(path)
            managed = "org.springframework.boot" in text or "platform(" in text
            for group, artifact, version in _GRADLE_DEP.findall(text):
                literal = bool(version) and not re.search(r"[+$\[\](]|latest\.|SNAPSHOT", version)
                out.append(
                    {
                        "ecosystem": "maven",
                        "name": f"{group}:{artifact}",
                        "version": version or None,
                        "pinned": literal or (not version and managed),
                        "declared_in": rel,
                        "exists": None,
                    },
                )
    return out


_GO_REQUIRE_LINE = re.compile(r"^(\S+)\s+(v\S+)\s*(//\s*indirect\b)?")


def _go_requires(text: str) -> list[tuple[str, str, bool]]:
    """``[(module, version, indirect)]`` of a ``go.mod`` (``require`` blocks and single-line requires)."""
    out: list[tuple[str, str, bool]] = []
    block = False
    for raw in text.replace("\r\n", "\n").split("\n"):
        line = raw.strip()
        if block:
            if line.startswith(")"):
                block = False
            elif (match := _GO_REQUIRE_LINE.match(line)) is not None:
                out.append((match.group(1), match.group(2), bool(match.group(3))))
            continue
        if re.match(r"^require\s*\(", line):
            block = True
        elif line.startswith("require ") and (match := _GO_REQUIRE_LINE.match(line[8:].strip())) is not None:
            out.append((match.group(1), match.group(2), bool(match.group(3))))
    return out


def _xml_text(block: str, tag: str) -> str | None:
    match = re.search(rf"<{tag}>\s*([^<]*?)\s*</{tag}>", block)
    return match.group(1) if match else None


def _section(text: str, tag: str) -> str:
    start, end = text.find(f"<{tag}>"), text.find(f"</{tag}>")
    return text[start:end] if 0 <= start < end else ""


def _without(text: str, tag: str) -> str:
    while (start := text.find(f"<{tag}>")) >= 0:
        end = text.find(f"</{tag}>", start)
        if end < 0:
            return text[:start]
        text = text[:start] + text[end + len(tag) + 3 :]
    return text


def _literal_version(version: str | None) -> bool:
    return bool(version) and not re.search(r"[$\[\](,]|LATEST|RELEASE|SNAPSHOT", str(version))


def _pom_dependencies(text: str) -> list[tuple[str, str, str | None, bool]]:
    """``[(groupId, artifactId, version, pinned)]`` of a ``pom.xml``'s project dependencies.

    String scanning, not an XML parser (the tree is untrusted, and the checker is stdlib only): comments,
    ``<build>`` (plugin dependencies) and ``<dependencyManagement>`` are cut out first. ``${prop}`` versions are
    resolved from ``<properties>``. A dependency without a version is pinned when something manages it: a
    ``<parent>`` with a literal version (the Spring Boot BOM) or a ``<dependencyManagement>`` entry or BOM
    import with one. Ranges, ``LATEST``/``RELEASE`` and ``-SNAPSHOT`` are not pinned.
    """
    text = re.sub(r"<!--.*?-->", "", text, flags=re.DOTALL)
    props = {
        name: value.strip() for name, value in re.findall(r"<([\w.\-]+)>([^<]*)</\1>", _section(text, "properties"))
    }

    def resolve(version: str | None) -> str | None:
        if version is None:
            return None
        return re.sub(r"\$\{([\w.\-]+)\}", lambda m: props.get(m.group(1), m.group(0)), version)

    management = _section(text, "dependencyManagement")
    managed: set[str] = set()
    bom = False
    for block in re.findall(r"<dependency>(.*?)</dependency>", management, flags=re.DOTALL):
        if not _literal_version(resolve(_xml_text(block, "version"))):
            continue
        if _xml_text(block, "scope") == "import":
            bom = True
        managed.add(f"{_xml_text(block, 'groupId')}:{_xml_text(block, 'artifactId')}")
    parent = _section(text, "parent")
    parent_pinned = bool(parent) and _literal_version(resolve(_xml_text(parent, "version")))
    body = _without(_without(_without(text, "dependencyManagement"), "build"), "parent")
    out: list[tuple[str, str, str | None, bool]] = []
    for block in re.findall(r"<dependency>(.*?)</dependency>", body, flags=re.DOTALL):
        group, artifact = _xml_text(block, "groupId"), _xml_text(block, "artifactId")
        if not group or not artifact:
            continue
        version = resolve(_xml_text(block, "version"))
        if version:
            pinned = _literal_version(version)
        else:
            pinned = parent_pinned or bom or f"{group}:{artifact}" in managed
        out.append((group, artifact, version or None, pinned))
    return out


def _prefix_value(name: str, table: dict[str, str]) -> str | None:
    """The value of the first ``table`` key that ``name`` equals or extends (``key/...``, ``key:...``)."""
    for key, value in table.items():
        if key.endswith(":"):
            if name.startswith(key):
                return value
        elif name == key or name.startswith(key + "/"):
            return value
    return None


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
    go_routers: set[str] = set()  # Go router/framework modules seen (go.mod or imports)
    go_http: list[str] = []  # evidence of net/http imports (go-stdlib unless a router is used)
    go_mods: list[str] = []  # go.mod files with their go directive
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
        if pkg["ecosystem"] == "go":
            if (key := _prefix_value(pkg["name"], _GO_BACKENDS)) is not None:
                go_routers.add(key)
                backend.setdefault(key, []).append(f"manifest:{pkg['declared_in']}:{pkg['name']}")
            if (key := _prefix_value(pkg["name"], _GO_DB)) is not None:
                database.setdefault(key, []).append(f"manifest:{pkg['declared_in']}:{pkg['name']}")
        if pkg["ecosystem"] == "maven":
            if (key := _prefix_value(pkg["name"], _JAVA_BACKENDS)) is not None:
                backend.setdefault(key, []).append(f"manifest:{pkg['declared_in']}:{pkg['name']}")
            if (key := _prefix_value(pkg["name"], _JAVA_DB)) is not None:
                database.setdefault(key, []).append(f"manifest:{pkg['declared_in']}:{pkg['name']}")
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
        if path.name == "go.mod":
            go_version = re.search(r"^go\s+(\S+)", _read(path), re.M)
            go_mods.append(f"manifest:{rel}:go {go_version.group(1) if go_version else '?'}")
            continue
        if suffix in (".properties", ".yml", ".yaml"):
            for m in _JDBC.finditer(_read(path)):
                kind = m.group(1).lower()
                database.setdefault(_JDBC_KINDS.get(kind, kind), []).append(f"conn:{rel}")
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
        elif suffix == ".go":
            imports = list(_GO_IMPORT_LINE.findall(text))
            for block in _GO_IMPORT_BLOCK.findall(text):
                imports.extend(re.findall(r'"([^"]+)"', block))
            for mod in imports:
                if mod == "net/http":
                    go_http.append(f"import:{rel}:net/http")
                if (key := _prefix_value(mod, _GO_BACKENDS)) is not None:
                    go_routers.add(key)
                    backend.setdefault(key, []).append(f"import:{rel}:{mod}")
                if (key := _prefix_value(mod, _GO_DB)) is not None:
                    database.setdefault(key, []).append(f"import:{rel}:{mod}")
            for driver in _GO_SQL_OPEN.findall(text):
                if driver in _GO_SQL_DRIVERS:
                    database.setdefault(_GO_SQL_DRIVERS[driver], []).append(f"conn:{rel}:sql.Open({driver})")
        elif suffix == ".java":
            for mod in _JAVA_IMPORT.findall(text):
                for prefix, key in _JAVA_IMPORTS.items():
                    if mod == prefix or mod.startswith(prefix + "."):
                        backend.setdefault(key, []).append(f"import:{rel}:{mod}")
                        break
            for m in _JDBC.finditer(text):
                kind = m.group(1).lower()
                database.setdefault(_JDBC_KINDS.get(kind, kind), []).append(f"conn:{rel}")
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
    if go_http and not go_routers:
        # net/http with no router module: the standard library serves HTTP (the go.mod is a second kind).
        backend.setdefault("go-stdlib", []).extend(go_http + go_mods[:1])
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
