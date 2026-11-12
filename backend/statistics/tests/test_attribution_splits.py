"""Attribution splits of the study tables: starter-inherited issues, formatting rules, tool confidence."""

from __future__ import annotations

from backend.statistics.services.dedup import IssueCluster
from backend.statistics.services.study import CODE
from backend.statistics.services.study import _inherited_from_starter
from backend.statistics.services.study import is_formatting
from backend.statistics.services.study import is_low_confidence
from backend.statistics.services.study import is_low_confidence_static


def _cluster(run: str, path: str, cls: str, line: int, **member) -> IssueCluster:
    base = {
        "id": f"{run}-{path}-{cls}-{line}",
        "tool": "hadolint",
        "rule_id": cls,
        "severity": "low",
        "category": "quality",
    }
    return IssueCluster(run_id=run, issue_class=cls, file_path=path, line=line, members=[{**base, **member}])


def test_starter_lines_in_an_edited_file_are_inherited_up_to_the_starter_count() -> None:
    kept = [_cluster("kept", "Dockerfile", "hadolint:DL3003", n, file_origin="starter") for n in (15, 18)]
    edited = [_cluster("edited", "Dockerfile", "hadolint:DL3003", n, file_origin="modified") for n in (15, 18, 40)]
    own = _cluster("edited", "Dockerfile", "hadolint:DL3059", 41, file_origin="modified")
    other_starter = _cluster("other", "Dockerfile", "hadolint:DL3003", 15, file_origin="modified")
    clusters = [*kept, *edited, own, other_starter]
    kind = {id(c): CODE for c in clusters}
    in_model = {id(c): c.run_id != "kept" for c in clusters}
    starter_of = {"kept": 1, "edited": 1, "other": 2}
    inherited = _inherited_from_starter(clusters, kind, in_model, starter_of)
    # The two the starter has, lowest lines first; the third DL3003, the other rule and the app of another
    # starter (no evidence for it) stay the model's.
    assert inherited == {id(edited[0]), id(edited[1])}


def test_formatting_rules() -> None:
    def one(tool: str, rule: str, category: str = "quality") -> IssueCluster:
        return _cluster("r", "backend/app.py", f"{tool}:{rule}", 1, tool=tool, rule_id=rule, category=category)

    assert is_formatting(one("pylint", "line-too-long"))
    assert is_formatting(one("flake8", "E501", "style"))
    assert is_formatting(one("ruff", "E203"))
    assert is_formatting(one("flake8", "W291"))
    assert not is_formatting(one("ruff", "E722"))  # bare except: a real smell
    assert not is_formatting(one("pylint", "raise-missing-from"))
    assert not is_formatting(one("ruff", "F401"))


def test_confidence_splits() -> None:
    low = _cluster("r", "backend/app.py", "vulture:unused-attribute", 1, tool="vulture", tool_confidence="60%")
    sure = _cluster("r", "backend/app.py", "vulture:unused-variable", 2, tool="vulture", tool_confidence="100%")
    unstated = _cluster("r", "backend/app.py", "pylint:x", 3, tool="pylint", tool_confidence="")
    assert is_low_confidence_static(low)
    assert not is_low_confidence_static(sure)
    assert not is_low_confidence_static(unstated)
    zap_low = _cluster("r", "/api/x", "zap:6", 0, tool="zap", tool_confidence="Low", tool_severity="High")
    zap_info = _cluster(
        "r",
        "/api/x",
        "zap:10111",
        0,
        tool="zap",
        tool_confidence="High",
        tool_severity="Informational",
    )
    zap_real = _cluster("r", "/api/x", "zap:40018", 0, tool="zap", tool_confidence="Medium", tool_severity="High")
    assert is_low_confidence(zap_low)
    assert is_low_confidence(zap_info)
    assert not is_low_confidence(zap_real)
