"""Seed detectors against their fixtures, drafts and diff-vs-original, and the WP-13 grep invariant.

Invariant (13.4, WP-13): only tool severities are shown -- no severity is computed in ``patterns``,
``evaluation`` or the pattern rule pack (``severity =`` assignments are refused; ``tool_severity`` is a
verbatim copy).
"""

from __future__ import annotations

import json
import re
import shutil
from pathlib import Path

import pytest

from backend.analysis.services.rulepack import LocalRunner
from backend.generation.patterns import detectors as det
from backend.generation.patterns import drafts
from backend.generation.patterns.catalog import load_seed_detectors

SEEDS = load_seed_detectors()
FIXTURE_CASES = [
    (seed, fixture)
    for seed in SEEDS
    for fixture in seed.fixtures
    if seed.content["engine"] in {"regex", "ts_query", "semgrep"}
]


def spec_of(seed):
    return det.DetectorSpec(slug=seed.slug, version=1, content_hash="0" * 64, content=seed.content, pk=1)


SPEC_SLUGS = {  # spec 11.6
    "py-sql-fstring", "py-sql-concat", "py-md5-sha1-password", "py-bcrypt-used", "py-argon2-used",
    "py-plaintext-password-compare", "py-hardcoded-secret", "py-jwt-none-or-no-verify", "py-debug-true",
    "py-open-redirect", "py-path-join-user-input", "py-yaml-load-unsafe",
    "js-innerhtml-user-data", "js-dangerouslysetinnerhtml", "js-eval-like", "js-jwt-secret-literal",
    "js-sql-template-literal", "js-cors-wildcard-credentials",
    "go-sql-sprintf", "java-sql-concat",
}  # fmt: skip


def test_every_seed_detector_is_valid_and_has_fixtures():
    assert len(SEEDS) >= 20
    keys = [s.content["feature_key"] for s in SEEDS]
    assert len(keys) == len(set(keys))
    assert all(s.slug == s.content["feature_key"] for s in SEEDS)
    for seed in SEEDS:
        assert det.validate_content(seed.content) == [], seed.slug
        if seed.content["engine"] != "manual":
            assert {f.expected for f in seed.fixtures} == {"present", "absent"}, seed.slug


def test_the_seed_set_covers_the_spec_catalogue_and_every_probe_kind():
    from typing import get_args

    from backend.generation.contracts.library import ProbeKind

    slugs = {s.slug for s in SEEDS}
    assert slugs >= SPEC_SLUGS, sorted(SPEC_SLUGS - slugs)
    probe_kinds = {s.content["rule"].removeprefix("kind:") for s in SEEDS if s.content["rule"].startswith("kind:")}
    assert probe_kinds == set(get_args(ProbeKind))
    assert all(f"probe-{kind.replace('_', '-')}" in slugs for kind in get_args(ProbeKind))


def test_fixtures_live_under_tests_which_the_library_seeder_skips():
    from backend.generation.patterns.catalog import DETECTORS_DIR

    assert not (DETECTORS_DIR / "fixtures").exists()
    assert all(f.path.parent.parent == DETECTORS_DIR / "tests" for s in SEEDS for f in s.fixtures)


@pytest.mark.parametrize(
    ("seed", "fixture"),
    [(seed, fixture) for seed in SEEDS for fixture in seed.fixtures if seed.content["engine"] == "probe"],
    ids=lambda v: getattr(v, "slug", None) or getattr(getattr(v, "path", None), "name", ""),
)
def test_probe_seed_detectors_against_their_fixtures(seed, fixture):
    probes = json.loads(fixture.text)["probes"]
    variant = det.VariantInput(run_id="r", path="a.py", language="python", file_text="x = 1\n", probes=probes)
    verdict = det.run_in_process(spec_of(seed), variant, ["span"])[0]
    assert verdict.status == fixture.expected, (seed.slug, fixture.path.name, verdict.message)


def test_probe_kind_rules_are_validated():
    content = {"engine": "probe", "rule": "kind:sqli_nope", "cwe": []}
    assert det.validate_content(content) == ["unknown probe kind 'sqli_nope'"]
    assert det.validate_content({"engine": "probe", "rule": "", "cwe": []}) != []


@pytest.mark.parametrize(
    ("seed", "fixture"),
    [c for c in FIXTURE_CASES if c[0].content["engine"] != "semgrep"],
    ids=lambda v: getattr(v, "slug", None) or getattr(getattr(v, "path", None), "name", ""),
)
def test_in_process_seed_detectors_against_their_fixtures(seed, fixture):
    variant = det.VariantInput(run_id="r", path=fixture.path.name, language=fixture.language, file_text=fixture.text)
    verdict = det.run_in_process(spec_of(seed), variant, ["file"])[0]
    assert verdict.status == fixture.expected, (seed.slug, fixture.path.name, verdict.message)


@pytest.mark.skipif(shutil.which("semgrep") is None, reason="needs a semgrep binary on the PATH")
@pytest.mark.parametrize(
    ("seed", "fixture"),
    [c for c in FIXTURE_CASES if c[0].content["engine"] == "semgrep"],
    ids=lambda v: getattr(v, "slug", None) or getattr(getattr(v, "path", None), "name", ""),
)
def test_semgrep_seed_detectors_against_their_fixtures(seed, fixture):
    variant = det.VariantInput(run_id="r", path=fixture.path.name, language=fixture.language, file_text=fixture.text)
    batch = det.run_semgrep([spec_of(seed)], [variant], ["file"], runner=LocalRunner())
    assert batch.verdicts[("r", f"{seed.slug}@1")][0].status == fixture.expected


def test_drafts_from_a_selection_compile_and_match_the_selection():
    text = 'import subprocess\n\n\ndef ping(host):\n    return subprocess.run("ping " + host, shell=True)\n'
    for engine in ("regex", "ts_query"):
        draft = drafts.draft_from_selection(
            text=text,
            language="python",
            path="a.py",
            start_line=5,
            end_line=5,
            engine=engine,
            feature_key="drafted",
            polarity="insecure",
        )
        assert det.validate_content(draft) == []
        spec = det.DetectorSpec(slug="drafted", version=1, content_hash="0" * 64, content=draft)
        variant = det.VariantInput(run_id="r", path="a.py", language="python", file_text=text)
        assert det.run_in_process(spec, variant, ["file"])[0].status == "present", engine


def test_a_one_line_ts_query_draft_covers_the_whole_line_not_its_first_token():
    """E2E-26: a row-only cover let a single token (``string_start``, one identifier) become the whole rule."""
    text = (
        "def login(row, password):\n"
        '    if row is None or not check_password_hash(row["password_hash"], password):\n'
        "        return 401\n"
        "    return 200\n"
    )
    rule = drafts.ts_query_for(text, "python", 2, 2)
    assert rule.startswith("((if_statement"), rule
    assert "string_start" not in rule
    # A selection of one call on its own line drafts that call.
    call = 'x = 1\nfoo(bar, "baz")\n'
    assert drafts.ts_query_for(call, "python", 2, 2).startswith(("((expression_statement", "((call")), call
    # Unrelated strings elsewhere do not match the drafted rule.
    draft = drafts.draft_from_selection(
        text=text,
        language="python",
        path="a.py",
        start_line=2,
        end_line=2,
        engine="ts_query",
        feature_key="drafted",
        polarity="secure",
    )
    spec = det.DetectorSpec(slug="drafted", version=1, content_hash="0" * 64, content=draft)
    other = 'MESSAGE = "hello"\n'
    variant = det.VariantInput(run_id="r", path="a.py", language="python", file_text=other)
    assert det.run_in_process(spec, variant, ["file"])[0].status == "absent"


def test_diff_vs_original():
    original = "def f(x):\n    return x + 1\n"
    renamed = "def f(y):\n    # c\n    return y + 1\n"
    out = drafts.diff_vs_original(original, renamed, "python")
    assert out["normalized_equal"] is True
    assert out["ast_edit_distance"] == 0  # comments are not AST nodes here; renaming keeps the node types
    assert drafts.diff_vs_original(original, "def f(x):\n    return x * 1 + 2\n", "python")["ast_edit_distance"] > 0
    assert (out["lines_added"], out["lines_removed"]) == (3, 2)
    assert 0 < out["similarity"] < 1
    assert drafts.diff_vs_original(original, "def f(:\n", "python")["normalized_equal"] is None


ROOT = Path(__file__).resolve().parents[3]
SCANNED = [
    *sorted((ROOT / "generation" / "patterns").glob("*.py")),
    *sorted((ROOT / "generation" / "evaluation").rglob("*.py")),
    ROOT / "analysis" / "services" / "rulepack.py",
    ROOT / "human_eval" / "services" / "detector_audit.py",
]
_ASSIGN = re.compile(r"""(?<![\w.])severity\s*=(?!=)|\[\s*["']severity["']\s*\]\s*=(?!=)|["']severity["']\s*:""")


def test_no_severity_is_computed_in_patterns_evaluation_or_the_rulepack():
    offenders = []
    for path in SCANNED:
        for number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
            code = line.split("#", 1)[0]
            if _ASSIGN.search(code):
                offenders.append(f"{path.relative_to(ROOT)}:{number}: {line.strip()}")
    assert offenders == [], "\n".join(offenders)
