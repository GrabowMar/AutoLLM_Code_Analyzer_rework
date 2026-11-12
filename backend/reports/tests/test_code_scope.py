"""The study's switchable code scope: tests and an agent's scratch scripts counted apart from the app."""

from __future__ import annotations

import pytest

from backend.reports.services import loc
from backend.reports.services.loc import is_auxiliary_path
from backend.reports.services.loc import is_outside_app
from backend.reports.services.loc import use_code_scope

HIDDEN = [
    ".tmp_test_api.py",
    ".apitest.py",
    ".scratch/edge.py",
    ".run/jsdomtest/interact.mjs",
    "frontend/.fe_debug.mjs",
]
NAMED = [
    "backend/check_api.py",
    "backend/api_check.py",
    "scripts/check_persistence.sh",
    "probe_xml2.py",
    "npmprobe/package.json",
    "tmpjsdom/interaction.mjs",
    "tmpexp_api.sh",
]
APP = [
    "backend/app.py",
    "backend/checker.py",
    "backend/template.py",
    "backend/temperature.py",
    "frontend/src/components/Checker.tsx",
    "frontend/src/App.tsx",
    "frontend/.eslintrc.cjs",
    ".github/workflows/ci.yml",
    "backend/.env.example",
]
TESTS = ["backend/tests/test_api.py", "backend/test_app.py", "frontend/src/App.test.tsx", ".smoke.py"]


@pytest.mark.parametrize("path", HIDDEN + NAMED)
def test_scratch_scripts_are_auxiliary(path: str) -> None:
    assert is_auxiliary_path(path)


@pytest.mark.parametrize("path", APP + TESTS)
def test_app_code_and_tests_are_not_auxiliary(path: str) -> None:
    assert not is_auxiliary_path(path)


@pytest.mark.parametrize("path", NAMED)
def test_name_rule_can_be_switched_off(path: str) -> None:
    assert not is_auxiliary_path(path, names=False)


def test_scopes() -> None:
    expected = {
        # scope: (test, hidden scratch, named scratch, app)
        "app": (True, True, True, False),
        "app_named": (True, True, False, False),
        "with_scratch": (True, False, False, False),
        "all": (False, False, False, False),
    }
    for scope, outside in expected.items():
        with use_code_scope(scope) as in_force:
            assert in_force == scope
            got = tuple(is_outside_app(p) for p in (TESTS[0], HIDDEN[0], NAMED[0], APP[0]))
            assert got == outside, scope
    assert loc.code_scope() == "app"  # the default, restored after the blocks


def test_setting_and_unknown_scope(settings) -> None:
    settings.STUDY_CODE_SCOPE = "with_scratch"
    assert not is_outside_app(HIDDEN[0])
    with use_code_scope("app"):
        assert is_outside_app(HIDDEN[0])
    with pytest.raises(ValueError, match="unknown code scope"), use_code_scope("everything"):
        pass


def test_loc_follows_the_scope() -> None:
    files = {"backend/app.py": "a = 1\nb = 2\n", ".scratch/edge.py": "x = 1\n", "backend/test_app.py": "t = 1\n"}
    assert loc.loc_from_files(files)["total_loc"] == 2
    assert loc.loc_of_test_files(files)["test_loc"] == 2
    with use_code_scope("with_scratch"):
        assert loc.loc_from_files(files)["total_loc"] == 3
        assert loc.loc_of_test_files(files)["test_loc"] == 1
    with use_code_scope("all"):
        assert loc.loc_from_files(files)["total_loc"] == 4
        assert loc.loc_of_test_files(files)["test_loc"] == 0
