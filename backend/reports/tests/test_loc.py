"""Source LOC and the shared test-code classifier."""

from __future__ import annotations

import pytest

from backend.reports.services.loc import is_test_path
from backend.reports.services.loc import loc_for_jobs
from backend.reports.services.loc import loc_from_files
from backend.reports.services.loc import loc_from_result_data
from backend.reports.services.loc import loc_of_test_files


@pytest.mark.parametrize(
    "path",
    [
        "tests/test_api.py",
        "backend/tests/helpers.py",
        "test/app.js",
        "frontend/src/__tests__/App.jsx",
        "e2e/login.ts",
        "frontend/cypress/support/commands.js",
        "playwright/home.mjs",
        "test_api.py",
        "backend/api_test.py",
        "conftest.py",
        "frontend/src/App.test.jsx",
        "frontend/src/utils.spec.ts",
        "frontend/src/setupTests.js",
        "scripts/api_smoke.py",
        "frontend/ui_smoke.mjs",
        "smoke.sh",
        "Tests/Test_Api.py",
    ],
)
def test_is_test_path_matches_test_code(path):
    assert is_test_path(path)


@pytest.mark.parametrize(
    "path",
    [
        "app.py",
        "backend/routes/testimonials.py",
        "frontend/src/components/TestResults.jsx",
        "contest.py",
        "frontend/src/latest.js",
        "attestation/models.py",
        "frontend/src/App.jsx",
        "frontend/src/spec.md",
        "",
    ],
)
def test_is_test_path_leaves_app_code_alone(path):
    assert not is_test_path(path)


def test_test_files_are_outside_the_source_loc_and_counted_apart():
    files = {
        "app.py": "import os\nprint(1)\n",
        "tests/test_api.py": "def test_x():\n    assert 1\n",
        "frontend/src/App.jsx": "export default 1;\n",
        "frontend/src/App.test.jsx": "it('x', () => {});\n// c\n",
        "scripts/api_smoke.py": "import urllib\n",
    }
    assert loc_from_files(files) == {"backend_loc": 2, "frontend_loc": 1, "total_loc": 3, "source_files": 2}
    assert loc_of_test_files(files) == {"test_loc": 4, "test_files": 3}
    out = loc_from_result_data({"files": files})
    assert (out["total_loc"], out["test_loc"], out["test_files"]) == (3, 4, 3)


def test_single_blob_results_have_unknown_test_code():
    """No paths, so no file can be told apart as a test: unknown, not zero (P8)."""
    out = loc_from_result_data({"backend_code": "a\nb\n"})
    assert (out["total_loc"], out["test_loc"], out["test_files"]) == (2, None, None)


def test_loc_for_jobs_sums_test_code_only_where_it_is_known():
    from types import SimpleNamespace

    blob = SimpleNamespace(id=1, result_data={"backend_code": "a\n"})
    files = SimpleNamespace(id=2, result_data={"files": {"app.py": "x\n", "tests/test_a.py": "a\nb\n"}})
    assert loc_for_jobs([blob])["test_loc"] is None
    out = loc_for_jobs([blob, files])
    assert (out["total_loc"], out["test_loc"], out["counted"]) == (2, 2, 2)


def test_files_in_dependency_or_scratch_dirs_are_not_source():
    from backend.reports.services.loc import is_source_file

    assert is_source_file("frontend/src/App.jsx")
    assert not is_source_file(".tmp/rr/package/index.js")
    assert not is_source_file("frontend/node_modules/x/index.js")
    assert not is_source_file("tmp/scratch.py")
