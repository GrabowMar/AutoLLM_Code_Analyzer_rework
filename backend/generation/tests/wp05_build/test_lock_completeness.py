"""``build.locks.lock_covers`` / ``check_tree_locks``: incomplete locks on the build seam (spec 6.10, 11.4)."""

from __future__ import annotations

import json

import pytest

from backend.generation import identity
from backend.generation.build import dockerfiles
from backend.generation.build import locks

FLASK = "backend/flask/minimal"


def starter_pair(fragment: str = FLASK) -> tuple[bytes, bytes]:
    manifest = (dockerfiles.STARTERS_ROOT / fragment / "backend/requirements.txt").read_bytes()
    lock = (locks.LOCK_ROOT / fragment / "backend/requirements.lock").read_bytes()
    return manifest, lock


def test_starter_lock_covers_its_own_manifest_only():
    manifest, lock = starter_pair()
    assert locks.lock_covers(manifest, lock)
    assert not locks.lock_covers(manifest + b"requests\n", lock)
    assert not locks.lock_covers(manifest + b"requests==2.32.5\n", lock)
    assert locks.lock_covers(b"Flask>=3\n# comment\n\ngunicorn\n", lock)  # PEP 503 names, specifiers
    assert not locks.lock_covers(b"flask<3\n", lock)
    assert locks.lock_covers(b"pywin32==306; sys_platform == 'win32'\nflask\n", lock)  # marker not for linux
    assert not locks.lock_covers(b"-r other.txt\n", lock)
    assert not locks.lock_covers(b"pkg @ https://example.invalid/pkg.whl\n", lock)
    assert not locks.lock_covers(b"flask\n", b"flask>=3\n")  # an unpinned "lock"


@pytest.mark.parametrize(
    ("package", "covered"),
    [
        ({"dependencies": {"vue": "^3.5.0"}}, True),
        ({"dependencies": {"vue": "^3.5.0", "axios": "^1"}}, False),
        ({"dependencies": {"vue": "^3.5.0"}, "devDependencies": {"vite": "^7"}}, False),
    ],
)
def test_npm_lock_must_declare_the_manifest_dependencies(package, covered):
    lock = {"lockfileVersion": 3, "packages": {"": {"dependencies": {"vue": "^3.5.0"}}}}
    assert locks.lock_covers(json.dumps(package).encode(), json.dumps(lock).encode(), ecosystem="npm") is covered
    assert not locks.lock_covers(b"{}", b"not json", ecosystem="npm")


def test_check_tree_locks_classifies_every_pair():
    manifest, lock = starter_pair()
    files = {
        "backend/requirements.txt": manifest + b"bcrypt\n",
        "backend/requirements.lock": lock,
        "frontend/package.json": b'{"dependencies": {"vue": "3"}}',
        "tools/requirements.txt": b"click\n",
        "frontend/node_modules/x/package.json": b"{}",
    }
    checks = {c.manifest: c for c in locks.check_tree_locks(files)}
    assert set(checks) == {"backend/requirements.txt", "frontend/package.json", "tools/requirements.txt"}
    stale = checks["backend/requirements.txt"]
    assert (stale.status, stale.reason, stale.source) == ("incomplete", "stale_starter_lock", "starter")
    assert checks["frontend/package.json"].status == "absent"
    assert not checks["frontend/package.json"].lock_present


def test_unindexed_starter_lock_uses_origins_and_model_locks_are_coverage_checked():
    manifest = b"flask==3.1.3\n"
    lock = b"flask==3.1.3 --hash=sha256:00\n"
    files = {"requirements.txt": manifest, "requirements.lock": lock}
    assert identity.bytes_sha(lock) not in {e["lock_sha"] for e in locks.lock_index().values()}
    origins = {"requirements.txt": "modified", "requirements.lock": "starter"}
    (check,) = locks.check_tree_locks(files, origins=origins)
    assert (check.status, check.reason) == ("incomplete", "stale_starter_lock")
    (check,) = locks.check_tree_locks(files, origins={"requirements.txt": "authored", "requirements.lock": "authored"})
    assert (check.status, check.source) == ("complete", "tree")
    (check,) = locks.check_tree_locks({**files, "requirements.txt": b"flask\nrequests\n"})
    assert (check.status, check.reason) == ("incomplete", "not_covered")
    (check,) = locks.check_tree_locks(files, platform_locks=["requirements.lock"])
    assert check.source == "platform"


GO = "backend/go-stdlib/minimal"


def go_pair() -> tuple[bytes, bytes]:
    manifest = (dockerfiles.STARTERS_ROOT / GO / "backend/go.mod").read_bytes()
    lock = (locks.LOCK_ROOT / GO / "backend/go.sum").read_bytes()
    return manifest, lock


def test_go_sum_must_hold_the_requirements_checksums():
    manifest, lock = go_pair()
    assert locks.lock_covers(manifest, lock, ecosystem="go")
    extended = manifest.replace(
        b"require modernc.org/sqlite",
        b"require example.com/extra v1.0.0\n\nrequire modernc.org/sqlite",
    )
    assert not locks.lock_covers(extended, lock, ecosystem="go")
    # A direct requirement needs its module checksum, an indirect one only its go.mod checksum.
    text = b"module app\n\ngo 1.25.0\n\nrequire (\n\tex.com/a v1.0.0\n\tex.com/b v1.0.0 // indirect\n)\n"
    sums = b"ex.com/a v1.0.0 h1:x=\nex.com/a v1.0.0/go.mod h1:y=\nex.com/b v1.0.0/go.mod h1:z=\n"
    assert locks.lock_covers(text, sums, ecosystem="go")
    assert not locks.lock_covers(text, sums.replace(b"ex.com/a v1.0.0 h1:x=\n", b""), ecosystem="go")
    assert not locks.lock_covers(text + b"replace ex.com/a => ./a\n", sums, ecosystem="go")


def test_check_tree_locks_covers_go_and_ignores_the_maven_record():
    manifest, lock = go_pair()
    pom = (dockerfiles.STARTERS_ROOT / "backend/spring-boot/minimal/backend/pom.xml").read_bytes()
    files = {
        "backend/go.mod": manifest,
        "backend/go.sum": lock,
        "api/pom.xml": pom + b"<!-- edited -->\n",
        "api/dependency-list.txt": b"The following files have been resolved:\n",
    }
    (check,) = locks.check_tree_locks(files, origins={"backend/go.mod": "modified", "backend/go.sum": "starter"})
    # Extra go.sum lines are harmless: a starter go.sum next to an edited go.mod is judged by coverage alone.
    assert (check.manifest, check.ecosystem, check.status, check.source) == (
        "backend/go.mod",
        "go",
        "complete",
        "starter",
    )
    extended = manifest.replace(
        b"require modernc.org/sqlite",
        b"require example.com/extra v1.0.0\n\nrequire modernc.org/sqlite",
    )
    (check,) = locks.check_tree_locks({**files, "backend/go.mod": extended})
    assert (check.status, check.reason) == ("incomplete", "not_covered")
    (check,) = locks.check_tree_locks({"backend/go.mod": manifest})
    assert check.status == "absent"  # the Dockerfile then runs `go mod download all` (unlocked install)
