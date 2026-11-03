"""Starter composition (spec section 11.4; WP-05 tests: deterministic composition, no planted content,
digest-pinned FROMs, lockfiles included).

``starter_hashes.json`` freezes the ``tree_hash`` of every phase-A starter. A change to a fragment, a
template, a base digest or a lock changes the starters on purpose: regenerate the file with
``write_starter_hashes()`` in the same change (and expect new Starter versions on the next seed).
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from backend.generation import identity
from backend.generation.build import dockerfiles
from backend.generation.build import locks
from backend.generation.build import starter_rules
from backend.generation.build import starters
from backend.generation.contracts.library import StarterContent

from .helpers import compose_entry
from .helpers import phase_a_entries

HASHES_PATH = Path(__file__).with_name("starter_hashes.json")
MANIFESTS = ("requirements.txt", "package.json", "pyproject.toml", "go.mod", "pom.xml")


def write_starter_hashes(path: Path = HASHES_PATH) -> Path:
    """Regenerate the frozen tree hashes (only for an intended change of the starter inputs)."""
    hashes = {starters.starter_slug(e.stack, e.level): compose_entry(e).tree_hash for e in phase_a_entries()}
    path.write_text(json.dumps(dict(sorted(hashes.items())), indent=2) + "\n", encoding="utf-8")
    return path


def test_every_phase_a_starter_composes_and_passes_the_rules(composed):
    assert len(composed) == 33  # 16 profiles x {bare, minimal} + free-choice bare
    for slug, starter in composed.items():
        assert starter.issues == [], (slug, [issue.message for issue in starter.issues])
        assert starter.passed
        assert starter.content["forbidden_content_check"] == {
            "passed": True,
            "linter_version": starter_rules.RULES_VERSION,
        }
        StarterContent.model_validate(starter.content)


def test_composition_is_deterministic(composed):
    for entry in phase_a_entries():
        again = compose_entry(entry)
        before = composed[again.slug]
        assert again.tree_hash == before.tree_hash
        assert again.content == before.content
        assert again.files == before.files


def test_tree_hashes_are_frozen(composed):
    frozen = json.loads(HASHES_PATH.read_text(encoding="utf-8"))
    assert {slug: starter.tree_hash for slug, starter in composed.items()} == frozen


def test_file_entries_match_the_bytes(composed):
    for starter in composed.values():
        assert set(starter.content["files"]) == set(starter.files)
        for path, entry in starter.content["files"].items():
            assert entry["blob"] == identity.bytes_sha(starter.files[path])
            assert entry["mode"] in {0o644, 0o755}
        assert starter.tree_hash == identity.tree_hash(
            (path, entry["mode"], entry["blob"]) for path, entry in starter.content["files"].items()
        )


def test_no_planted_content_in_any_starter(composed):
    for slug, starter in composed.items():
        assert starter_rules.forbidden_content(starter.files) == [], slug
        for path, data in starter.files.items():
            text = data.decode("utf-8")
            assert "LLM:" not in text, (slug, path)
            assert "_comment" not in text, (slug, path)
            assert "todo: model" not in text.lower(), (slug, path)


def test_no_deny_listed_package_in_any_manifest_or_lock(composed):
    deny = starter_rules.denied_packages()
    assert deny["pypi"] >= {"bcrypt", "passlib", "pwdlib", "argon2", "pyjwt", "python-jose", "flask-login"}
    assert deny["npm"] >= {"jsonwebtoken", "passport", "helmet", "csurf", "express-rate-limit"}
    checked = 0
    for slug, starter in composed.items():
        assert starter_rules.security_libraries(starter.files, deny) == [], slug
        for path, data in starter.files.items():
            checked += len(starter_rules.manifest_packages(path, data))
    assert checked > 100  # the locks were really parsed (transitive packages included)


def test_every_from_is_digest_pinned_from_base_images(composed):
    pinned = {spec.pinned for spec in dockerfiles.base_images().values()}
    for slug, starter in composed.items():
        if slug == "free-choice-bare":
            continue
        dockerfile = starter.files["Dockerfile"].decode()
        lines = [line for line in dockerfiles.parse_from_lines(dockerfile) if not line.is_stage_ref]
        assert lines, slug
        for line in lines:
            assert line.digest, (slug, line)
            assert line.digest.startswith("sha256:"), (slug, line)
            assert f"{line.ref}@{line.digest}" in pinned, (slug, line)
        assert starter.content["base_images"] == dockerfiles.pinned_bases(dockerfile)
        assert "alpine" not in dockerfile.lower()


def test_minimal_starters_ship_manifests_with_their_locks(composed):
    for slug, starter in composed.items():
        level = starter.level
        manifests = [p for p in starter.files if p.rsplit("/", 1)[-1] in MANIFESTS]
        if level == "bare":
            assert manifests == [], slug
            assert starter.content["lockfiles"] == []
            continue
        assert manifests, slug
        for manifest in manifests:
            lock = starter_rules.lock_for_manifest(manifest, starter.files)
            assert lock is not None, (slug, manifest)
            assert lock in starter.content["lockfiles"]
        assert sorted(starter.content["lockfiles"]) == starter.content["lockfiles"]


def test_minimal_manifests_hold_only_framework_server_and_build_tool(composed):
    allowed = {
        "flask": {"flask", "gunicorn"},
        "fastapi": {"fastapi", "uvicorn"},
        "django": {"django", "gunicorn"},
        "express": {"express"},
        "react": {"react", "react-dom", "vite", "@vitejs/plugin-react"},
        "vue": {"vue", "vite", "@vitejs/plugin-vue"},
        "svelte": {"svelte", "vite", "@sveltejs/vite-plugin-svelte"},
    }
    for slug, starter in composed.items():
        if starter.level != "minimal":
            continue
        backend, frontend = slug.removesuffix("-minimal").split("-", 1)
        for path in ("backend/requirements.txt", "backend/package.json"):
            if path in starter.files:
                names = {n for _, n in starter_rules.manifest_packages(path, starter.files[path])}
                assert names == allowed[backend], (slug, names)
        if frontend != "none":
            names = {
                n
                for _, n in starter_rules.manifest_packages(
                    "frontend/package.json",
                    starter.files["frontend/package.json"],
                )
            }
            assert names == allowed[frontend], (slug, names)
        else:
            assert not any(p.startswith("frontend/") for p in starter.files)


def test_minimal_starters_serve_the_health_check_and_a_shell(composed):
    for slug, starter in composed.items():
        if starter.level != "minimal":
            continue
        code = b"\n".join(data for path, data in starter.files.items() if path.startswith("backend/")).decode()
        assert "/api/health" in code or "api/health" in code, slug
        assert '"status": "ok"' in code or 'status: "ok"' in code, slug
        if "frontend/index.html" in starter.files:
            assert any(p.startswith("frontend/src/App.") for p in starter.files), slug
        else:
            assert any(p.endswith("index.html") for p in starter.files), slug


def test_bare_starters_are_dockerfile_plus_readme(composed):
    for slug, starter in composed.items():
        if starter.level != "bare":
            continue
        expected = {"README.md"} if slug == "free-choice-bare" else {"Dockerfile", "README.md", ".dockerignore"}
        assert set(starter.files) == expected, slug
        readme = starter.files["README.md"].decode()
        for fact in ("8000", "/api/health", "./data"):
            assert fact in readme, (slug, fact)


def test_free_choice_has_no_dockerfile_and_no_bases(composed):
    starter = composed["free-choice-bare"]
    assert starter.content["base_images"] == []
    assert starter.content["composition"] == {
        "backend_fragment": "backend/free/bare",
        "frontend_fragment": None,
        "dockerfile_template": "none",
    }
    with pytest.raises(starters.StarterCompositionError):
        starters.compose_tree(
            "free-choice",
            starters.load_stack_yaml("free-choice")["content"],
            "minimal",
            stack_ref="free-choice@1#000000000000",
        )


def test_frontend_none_starters_serve_a_page_without_a_frontend_project(composed):
    for slug in ("flask-none-minimal", "fastapi-none-minimal", "django-none-minimal", "express-none-minimal"):
        files = composed[slug].files
        assert "frontend/package.json" not in files
        assert "AS frontend" not in files["Dockerfile"].decode()
        assert any(p.endswith("index.html") for p in files)


def test_a_stale_lock_refuses_to_compose(tmp_path, monkeypatch):
    stack = starters.load_stack_yaml("flask-react")["content"]
    index = locks.lock_index()
    key = "backend/flask/minimal:backend/requirements.txt"
    stale = {**index, key: {**index[key], "manifest_sha": "0" * 64}}
    monkeypatch.setattr(locks, "lock_index", lambda path=None: stale)
    with pytest.raises(locks.StaleLockError, match="build_starters --lock"):
        starters.compose_tree("flask-react", stack, "minimal", stack_ref="flask-react@1#000000000000")
    missing = {k: v for k, v in index.items() if k != key}
    monkeypatch.setattr(locks, "lock_index", lambda path=None: missing)
    with pytest.raises(locks.MissingLockError):
        starters.compose_tree("flask-react", stack, "minimal", stack_ref="flask-react@1#000000000000")


def test_a_base_digest_change_changes_every_dockerfile_starter(monkeypatch, composed):
    real = dockerfiles.base_images()
    moved = dict(real)
    moved["python"] = dockerfiles.BaseImageSpec("python", real["python"].ref, "sha256:" + "1" * 64)
    monkeypatch.setattr(dockerfiles, "base_images", lambda path=None: moved)
    entry = starters.ComposeEntry("flask-react", "minimal")
    assert compose_entry(entry).tree_hash != composed["flask-react-minimal"].tree_hash
    entry = starters.ComposeEntry("express-react", "minimal")
    assert compose_entry(entry).tree_hash == composed["express-react-minimal"].tree_hash
