"""Source LOC and the shared test-code classifier."""

from __future__ import annotations

import pytest

from backend.reports.services.loc import is_test_path
from backend.reports.services.loc import loc_for_runs
from backend.reports.services.loc import loc_from_files
from backend.reports.services.loc import loc_from_run
from backend.reports.services.loc import loc_of_test_files
from backend.reports.services.loc import source_loc


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


@pytest.mark.parametrize(
    ("path", "text", "expected"),
    [
        ("backend/app.py", "import os\n\n# comment\nprint(1)\n", 2),
        ("frontend/src/App.jsx", "// c\nexport default 1;\n", 1),
        ("backend/tests/test_app.py", "def test_a():\n    assert 1\n", 0),
        ("frontend/package-lock.json", '{\n  "lockfileVersion": 3\n}\n', 0),
        ("README.md", "# Title\ntext\n", 0),
        ("frontend/node_modules/x/index.js", "var x = 1;\n", 0),
        ("frontend/dist/app.min.js", "var x=1;\n", 0),
        ("backend/empty.py", "", 0),
        ("backend/none.py", None, 0),
    ],
)
def test_source_loc_of_one_file(path, text, expected):
    assert source_loc(path, text) == expected


def test_source_loc_matches_the_map_counter():
    files = {"a.py": "x = 1\n# c\ny = 2\n", "b.js": "// c\nlet a;\n", "tests/test_a.py": "x\n"}
    assert sum(source_loc(p, t) for p, t in files.items()) == loc_from_files(files)["total_loc"]


def test_run_view_delegates_to_the_reports_rules():
    from backend.generation.runs.view import source_loc as view_source_loc

    assert view_source_loc("backend/app.py", "a = 1\nb = 2\n") == 2
    assert view_source_loc("tests/test_a.py", "a = 1\n") == 0


def _add(run, path, text, origin="authored", **extra):
    from backend.generation import blobs
    from backend.generation.testing import factories

    extra.setdefault("loc", source_loc(path, text))
    return factories.RunFileFactory(
        run=run,
        path=path,
        blob=blobs.put_text(text),
        size=len(text),
        origin=origin,
        **extra,
    )


@pytest.mark.django_db
def test_loc_from_run_counts_the_model_scope_by_layer_and_tests_apart():
    from backend.generation.testing import factories

    run = factories.GenerationRunFactory(tested=True)
    _add(run, "backend/app.py", "import flask\napp = 1\n", origin="starter")
    _add(run, "backend/routes.py", "def a():\n    return 1\n")
    _add(run, "frontend/src/App.jsx", "export default 1;\n", origin="modified")
    _add(run, "backend/tests/test_routes.py", "def test_a():\n    assert 1\n", is_test=True)
    _add(run, "frontend/package-lock.json", "{}\n", excluded="lockfile")
    _add(run, "frontend/dist/app.js", "x\n", origin="artifact")
    assert loc_from_run(run) == {
        "backend_loc": 2,
        "frontend_loc": 1,
        "total_loc": 3,
        "source_files": 2,
        "test_loc": 2,
        "test_files": 1,
    }


@pytest.mark.django_db
def test_loc_is_unknown_without_a_tree_and_zero_for_a_tree_without_model_code():
    """No collected tree is unknown (None), never a silent 0; a tree the model did not change is a measured 0."""
    from backend.generation.testing import factories

    pending = factories.GenerationRunFactory()
    assert set(loc_from_run(pending).values()) == {None}
    untouched = factories.GenerationRunFactory(tested=True)
    _add(untouched, "backend/app.py", "a = 1\n", origin="starter")
    assert loc_from_run(untouched)["total_loc"] == 0


@pytest.mark.django_db
def test_loc_for_runs_sums_only_runs_with_a_known_loc():
    from backend.generation.testing import factories

    pending = factories.GenerationRunFactory()
    measured = factories.GenerationRunFactory(tested=True)
    _add(measured, "backend/app.py", "a = 1\nb = 2\n")
    out = loc_for_runs([pending, measured])
    assert (out["total_loc"], out["counted"], len(out["per_run"])) == (2, 1, 2)
    assert loc_for_runs([pending])["total_loc"] is None


def test_files_in_dependency_or_scratch_dirs_are_not_source():
    from backend.reports.services.loc import is_source_file

    assert is_source_file("frontend/src/App.jsx")
    assert not is_source_file(".tmp/rr/package/index.js")
    assert not is_source_file("frontend/node_modules/x/index.js")
    assert not is_source_file("tmp/scratch.py")
