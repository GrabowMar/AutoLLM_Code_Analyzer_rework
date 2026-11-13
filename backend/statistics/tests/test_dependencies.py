"""Who chose a vulnerable dependency version: the starter or the model."""

from __future__ import annotations

import json

import pytest

from backend.statistics.services.dedup import IssueCluster
from backend.statistics.services.dependencies import DependencyOwnership
from backend.statistics.services.dependencies import dependency_scope
from backend.statistics.services.dependencies import lock_requirers
from backend.statistics.services.dependencies import lock_versions
from backend.statistics.services.dependencies import manifest_specs
from backend.statistics.services.dependencies import spec_range
from backend.statistics.services.dependencies import starter_spec_affected


@pytest.mark.parametrize(
    ("spec", "expected"),
    [
        ("^1.6.0", ((1, 6, 0), (2, 0, 0))),
        ("^0.8.4", ((0, 8, 4), (0, 9, 0))),
        ("~1.2.3", ((1, 2, 3), (1, 3, 0))),
        ("1.6.8", ((1, 6, 8), (1, 6, 8))),
        ("1.2.x", ((1, 2, 0), (1, 3, 0))),
        ("==4.0.0", ((4, 0, 0), (4, 0, 0))),
        (">=2.0,<3", ((2, 0, 0), (3, 0, 0))),
        (">=1.2.3 <2.0.0", ((1, 2, 3), (2, 0, 0))),
        ("~=2.2", ((2, 2, 0), (3, 0, 0))),
        ("^1 || ^2", None),
        ("git+https://example.com/x.git", None),
    ],
)
def test_spec_range(spec, expected):
    assert spec_range(spec) == expected


def test_starter_spec_affected():
    # A range installs its newest version: fixed when a fix lies inside the range.
    assert starter_spec_affected("^1.6.0", ["1.7.4"]) is False
    assert starter_spec_affected("^5.0.0", ["6.0.9"]) is True
    # Backports on 4.x and 6.x leave 5.x without a fix.
    assert starter_spec_affected("^5.0.0", ["4.5.14", "6.1.7"]) is True
    assert starter_spec_affected("^5.0.0", ["5.4.12", "6.0.9"]) is False
    # An exact pin installs that version.
    assert starter_spec_affected("==3.0.1", ["2.2.3", "3.0.3"]) is True
    assert starter_spec_affected("==2.2.5", ["2.2.5", "2.3.2"]) is False
    assert starter_spec_affected("==4.0.0", []) is True  # no fix exists
    assert starter_spec_affected("^1 || ^2", ["1.0.0"]) is None


def test_manifest_specs_keep_the_declared_version():
    files = {
        "requirements.txt": "flask_cors==4.0.0  # kept\nrequests[socks] >= 2.0 ; python_version > '3'\n-r dev.txt\n",
        "frontend/package.json": json.dumps({"dependencies": {"axios": "1.6.8"}, "devDependencies": {"vite": "^5"}}),
        "frontend/node_modules/x/package.json": json.dumps({"dependencies": {"ignored": "1"}}),
    }
    assert manifest_specs(files) == {"flask-cors": "==4.0.0", "requests": ">=2.0", "axios": "1.6.8", "vite": "^5"}


def test_manifest_specs_keep_the_spaces_between_npm_comparators():
    """``>=1.2.3 <2.0.0`` is a bounded range and ``1.2.3 - 2.3.4`` a hyphen range, not ``>=1.2.3<2.0.0``/a pin."""
    files = {
        "package.json": json.dumps({"dependencies": {"x": ">=1.2.3  <2.0.0", "y": "1.2.3 - 2.3.4", "z": "^ 1.6.0"}}),
        "requirements.txt": "flask == 3.0.0\nrequests >= 2.0 , < 3\n",
    }
    specs = manifest_specs(files)
    assert specs == {
        "x": ">=1.2.3 <2.0.0",
        "y": "1.2.3 - 2.3.4",
        "z": "^1.6.0",
        "flask": "==3.0.0",
        "requests": ">=2.0,<3",
    }
    assert spec_range(specs["x"]) == ((1, 2, 3), (2, 0, 0))
    assert spec_range(specs["y"]) is None  # a hyphen range is not read, not taken for a pin
    assert spec_range(specs["requests"]) == ((2, 0, 0), (3, 0, 0))
    # Through the ownership: the starter's bounded range has no fix, so a pin inside it is not blamed.
    owner = DependencyOwnership(app={"x": "1.2.5"}, starter={"x": specs["x"]})
    assert not owner.is_models(_advisory("x", ["2.1.0"]))


def test_lock_versions_lists_every_installed_copy():
    v3 = {
        "lockfileVersion": 3,
        "packages": {
            "": {"version": "0.0.0", "dependencies": {"axios": "^1.6.0"}},
            "node_modules/axios": {"version": "1.20.0"},
            "node_modules/vitest/node_modules/tinypool": {"version": "0.8.4"},
            "node_modules/tinypool": {"version": "1.0.0"},
        },
    }
    assert lock_versions({"frontend/package-lock.json": json.dumps(v3)}) == {
        "axios": {"1.20.0"},
        "tinypool": {"0.8.4", "1.0.0"},
    }
    v1 = {"lockfileVersion": 1, "dependencies": {"axios": {"version": "1.6.8"}}}
    assert lock_versions({"package-lock.json": json.dumps(v1)}) == {"axios": {"1.6.8"}}


def test_lock_requirers_reads_v3_and_v1_lockfiles():
    v3 = {
        "lockfileVersion": 3,
        "packages": {
            "": {"dependencies": {"react": "^18"}, "devDependencies": {"vitest": "^1"}},
            "node_modules/vitest": {"version": "1.6.0", "dependencies": {"tinypool": "^0.8"}},
            "node_modules/vitest/node_modules/tinypool": {"version": "0.8.4"},
        },
    }
    assert lock_requirers({"frontend/package-lock.json": json.dumps(v3)}) == {
        "react": {""},
        "vitest": {""},
        "tinypool": {"vitest"},
    }
    v1 = {"lockfileVersion": 1, "dependencies": {"vitest": {"requires": {"tinypool": "^0.8"}}, "tinypool": {}}}
    assert lock_requirers({"package-lock.json": json.dumps(v1)}) == {"tinypool": {"vitest"}}


def _advisory(package, fixed_versions=None, tool="osv-scanner", *, run="r", installed=""):
    member = {"tool": tool, "package": package, "category": "dependency", "severity": "high", "line": None}
    if fixed_versions is not None:
        member["fixed_versions"] = fixed_versions
    if installed:
        member["installed_version"] = installed
    return IssueCluster(run_id=run, issue_class=f"vulnerable-dependency:{package}", file_path="", line=None,
                        members=[member])  # fmt: skip


def test_ownership_attributes_advisories_by_package_and_version():
    owner = DependencyOwnership(
        app={"axios": "1.6.8", "vite": "5.4.21", "vitest": "1.6.0", "flask-cors": "==4.0.0", "uuid": "9.0.1"},
        starter={"axios": "^1.6.0", "vite": "^5.0.0", "flask-cors": "==4.0.0", "uuid": "^9.0.0"},
        requirers={"tinypool": {"vitest"}, "esbuild": {"vite"}, "vitest": {""}},
    )
    # The model added vitest; tinypool comes in only through it.
    assert owner.is_models(_advisory("vitest"))
    assert owner.is_models(_advisory("tinypool"))
    # The starter's pin, unchanged.
    assert not owner.is_models(_advisory("Flask_CORS", ["6.0.0"]))
    # The model pinned an old axios; the starter's ^1.6.0 installs the fix.
    assert owner.is_models(_advisory("axios", ["1.7.4"]))
    # Same pin style, but the starter's ^5.0.0 is vulnerable too: still the starter's.
    assert not owner.is_models(_advisory("vite", ["6.0.9"]))
    # No fix data (npm audit): cannot tell, so not blamed on the model.
    assert not owner.is_models(_advisory("uuid", None, tool="npm-audit"))
    # Transitive through a starter package.
    assert not owner.is_models(_advisory("esbuild", ["0.25.0"]))
    assert not owner.is_models(_advisory(""))


def _owner(app, *, installed=None, requirers=None, starter=None):
    return DependencyOwnership(
        app=app,
        starter=starter or {"axios": "^1.6.0"},
        requirers=requirers or {},
        installed={k: set(v) for k, v in (installed or {}).items()},
    )


def test_same_installed_version_has_the_same_owner_whatever_the_pin_style():
    """An app that kept ``^1.6.0`` and one that pinned the 1.20.0 it resolved to own the same advisory alike."""
    kept = _owner({"axios": "^1.6.0"}, installed={"axios": [(1, 20, 0)]})
    pinned = _owner({"axios": "1.20.0"}, installed={"axios": [(1, 20, 0)]})
    stale = _owner({"axios": "1.6.8"}, installed={"axios": [(1, 6, 8)]})
    dependency_scope({"kept": kept, "pinned": pinned, "stale": stale})
    # 1.20.0 itself is vulnerable (fixed in 1.20.1): what the starter's range installed, not the model's choice.
    new_advisory = ["1.20.1"]
    assert not kept.is_models(_advisory("axios", new_advisory, run="kept", installed="1.20.0"))
    assert not pinned.is_models(_advisory("axios", new_advisory, run="pinned", installed="1.20.0"))
    # An old pin while the starter's range installed the fixed 1.20.0: the model's.
    assert stale.is_models(_advisory("axios", ["1.7.4"], run="stale", installed="1.6.8"))
    # Without a scope the range is assumed to install its newest (fixed) version; a scope of one app
    # holds no evidence either.
    alone = _owner({"axios": "1.20.0"}, installed={"axios": [(1, 20, 0)]})
    assert alone.is_models(_advisory("axios", new_advisory, installed="1.20.0"))
    dependency_scope({"alone": alone})
    assert alone.is_models(_advisory("axios", new_advisory, run="alone", installed="1.20.0"))


def test_changed_spec_without_starter_evidence_uses_versions_seen_in_scope():
    """No app kept the starter's range: a pin is the model's only if a newer, fixed version was in use."""
    newest = _owner({"axios": "1.20.0"}, installed={"axios": [(1, 20, 0)]})
    stale = _owner({"axios": "1.6.8"}, installed={"axios": [(1, 6, 8)]})
    dependency_scope({"newest": newest, "stale": stale})
    assert not newest.is_models(_advisory("axios", ["1.20.1"], run="newest", installed="1.20.0"))
    assert stale.is_models(_advisory("axios", ["1.7.4"], run="stale", installed="1.6.8"))
    # A vulnerability 1.20.0 shares is not the stale pin's fault either.
    assert not stale.is_models(_advisory("axios", ["1.20.1"], run="stale", installed="1.6.8"))


def test_scanner_versions_join_the_scope_evidence():
    kept = _owner({"axios": "^1.6.0"})
    stale = _owner({"axios": "1.6.8"})
    kept_advisory = _advisory("axios", ["1.20.1"], run="kept", installed="1.20.0")
    dependency_scope({"kept": kept, "stale": stale}, [kept_advisory])
    assert kept.installed == {"axios": {(1, 20, 0)}}
    assert stale.is_models(_advisory("axios", ["1.7.4"], run="stale", installed="1.6.8"))
    assert not stale.is_models(_advisory("axios", ["1.20.1"], run="stale", installed="1.6.8"))


def test_transitive_package_under_a_starter_package_the_model_repinned():
    """react-router comes with react-router-dom: what the apps that kept the starter's spec installed decides."""
    starter = {"react-router-dom": "^6.21.0"}
    requirers = {"react-router": {"react-router-dom"}, "react-router-dom": {""}}
    kept = _owner({"react-router-dom": "^6.21.0"}, starter=starter, requirers=requirers,
                  installed={"react-router": [(6, 30, 6)]})  # fmt: skip
    repinned = _owner({"react-router-dom": "6.21.3"}, starter=starter, requirers=requirers,
                      installed={"react-router": [(6, 21, 3)]})  # fmt: skip
    dependency_scope({"kept": kept, "repinned": repinned})
    # Fixed in 6.22.0: the starter's own spec installed the fixed 6.30.6.
    assert repinned.is_models(_advisory("react-router", ["6.22.0"], run="repinned"))
    # Fixed only in 6.31.0: the kept app has it too.
    assert not repinned.is_models(_advisory("react-router", ["6.31.0"], run="repinned"))
    assert not kept.is_models(_advisory("react-router", ["6.22.0"], run="kept"))
    # Without scope evidence: still the starter's (its package pulls it in).
    alone = _owner({"react-router-dom": "6.21.3"}, starter=starter, requirers=requirers)
    assert not alone.is_models(_advisory("react-router", ["6.22.0"]))


def test_nested_copy_outside_the_range_is_not_what_the_spec_installed():
    """A kept ``^1.6.0`` app whose lockfile also holds an old nested 0.27.2 copy: only 1.20.0 came from the range."""
    kept = _owner({"axios": "^1.6.0"}, installed={"axios": [(1, 20, 0), (0, 27, 2)]})
    stale = _owner({"axios": "1.6.8"}, installed={"axios": [(1, 6, 8)]})
    dependency_scope({"kept": kept, "stale": stale})
    # Fixed in 1.7.4: the range installed the fixed 1.20.0, so the stale pin is the model's choice.
    assert stale.is_models(_advisory("axios", ["1.7.4"], run="stale", installed="1.6.8"))
