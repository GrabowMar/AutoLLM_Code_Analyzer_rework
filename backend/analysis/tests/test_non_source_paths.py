"""``is_non_source_path``: listed directories and Python virtual environments under any name."""

from __future__ import annotations

import pytest

from backend.analysis.services.base import is_non_source_path


@pytest.mark.parametrize(
    "path",
    [
        "node_modules/react/index.js",
        "frontend/node_modules/x/y.js",
        ".venv/lib/python3.13/site-packages/flask/app.py",
        ".test_venv/lib/python3.13/site-packages/pip/__init__.py",
        ".tmpv_25.3/lib64/python3.12/os.py",
        "backend/_venv/pyvenv.cfg",
        ".venvchk/bin/activate.fish",
        ".venv2/bin/pip3.13",
        "tmpvenv/bin/python3",
        "env/Scripts/Activate.ps1",
        ".tmp/react-router/index.js",
    ],
)
def test_dependency_and_scratch_paths_are_non_source(path: str) -> None:
    assert is_non_source_path(path)


@pytest.mark.parametrize(
    "path",
    [
        "backend/app.py",
        "backend/lib/utils.py",
        "frontend/src/lib/python.ts",
        "scripts/bin/run.sh",
        "backend/bin/seed.py",
        "frontend/src/components/Activate.tsx",
        "docs/site-packages.md",
        "venv.py",
    ],
)
def test_app_code_is_source(path: str) -> None:
    assert not is_non_source_path(path)


def test_generated_bundle_is_left_out_by_size_and_extension() -> None:
    from backend.analysis.services.base import BUNDLE_MIN_BYTES
    from backend.analysis.services.base import is_generated_bundle
    from backend.analysis.services.base import non_source_paths
    from backend.reports.services.loc import loc_from_files

    bundle = "var a = 1;\n" * (BUNDLE_MIN_BYTES // 11 + 1)
    code = {
        "frontend/dist-test/test-entry.js": bundle,
        "frontend/.ssrsmoke.mjs": bundle,
        "frontend/src/App.tsx": "export const a = 1;\n" * 2000,  # 40 KB of hand-written code stays
        "frontend/package-lock.json": bundle,  # a manifest, not a script: dependency scanners need it
        "backend/app.py": "x = 1\n",
        "node_modules/react/index.js": "x",
    }
    assert is_generated_bundle("a/b.js", bundle)
    assert not is_generated_bundle("a/b.js", bundle[: BUNDLE_MIN_BYTES - 1])
    assert not is_generated_bundle("a/b.py", bundle)
    assert non_source_paths(code) == [
        "frontend/.ssrsmoke.mjs",
        "frontend/dist-test/test-entry.js",
        "node_modules/react/index.js",
    ]
    assert loc_from_files(code)["frontend_loc"] == 2000
