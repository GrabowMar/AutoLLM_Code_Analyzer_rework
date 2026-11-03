"""Run-to-run similarity: source selection, hand-checked Jaccards, finding overlap and the replay probe."""

from __future__ import annotations

import pytest
from django.core.cache import cache

from backend.generation.models import GenerationArtifact
from backend.generation.testing.factories import GenerationRunFactory
from backend.statistics.services import similarity as sim
from backend.statistics.tests.helpers import make_run


@pytest.fixture(autouse=True)
def _clear_cache():
    cache.clear()
    yield
    cache.clear()


class TestPureMeasures:
    def test_jaccard_hand_computed(self):
        assert sim.jaccard({1, 2, 3}, {2, 3, 4}) == 0.5
        assert sim.jaccard({1}, set()) == 0.0
        # Nothing to compare is not "identical": None.
        assert sim.jaccard(set(), set()) is None
        assert sim.jaccard({"a"}, {"a"}) == 1.0

    def test_tokens_and_shingles(self):
        assert sim.tokens("def f(x): return x+1") == ["def", "f", "(", "x", ")", ":", "return", "x", "+", "1"]
        # 10 tokens -> 6 distinct 5-grams.
        assert len(sim.shingles("def f(x): return x+1")) == 6
        # Shorter than k: one shingle of everything; empty text: none.
        assert len(sim.shingles("a b")) == 1
        assert sim.shingles("") == set()
        # Whitespace does not change tokens, so identical code in another layout shares every shingle.
        assert sim.shingles("x = foo(a, b)") == sim.shingles("x=foo( a,b )")

    def test_normalized_lines_drop_blank_and_bracket_lines(self):
        lines = sim.normalized_lines("if x:\n    y  =  1\n\n}\n  });\n")
        assert lines == {hash("if x:"), hash("y = 1")}

    def test_ast_ngrams(self):
        grams = sim.ast_ngrams("x = 1\n")
        assert ("Module", "Assign", "Name") in grams
        assert sim.ast_ngrams("def (:") is None

    def test_normalize_path(self):
        assert sim.normalize_path("/work/app.py") == "app.py"
        assert sim.normalize_path("./frontend/src/App.jsx") == "frontend/src/App.jsx"
        assert sim.normalize_path("C:\\x\\y.py") == "C:/x/y.py"


class TestModelSource:
    def test_keeps_only_non_test_source(self):
        files = {
            "app.py": "print('hi')\n",
            "requirements.txt": "flask\n",  # not source
            "frontend/package-lock.json": "{}",  # not source
            "tests/test_app.py": "def test(): pass\n",  # test code
            "frontend/node_modules/x/index.js": "x\n",  # non-source dir
            "frontend/src/App.jsx": "export default 1\n",
        }
        assert sorted(sim.model_source_map(files)) == ["app.py", "frontend/src/App.jsx"]

    @pytest.mark.django_db
    def test_store_reads_the_model_scope_only(self):
        """Starter files the model left alone, artifacts and excluded rows are not the model's code."""
        run = make_run(
            files={
                "app.py": "print('hi')\n",
                "frontend/src/main.jsx": "render()\n",
                "frontend/src/App.jsx": "export default 1\n",
                "dist/bundle.js": "var x = 1;\n",
            },
            origins={
                "frontend/src/main.jsx": "starter",
                "frontend/src/App.jsx": "modified",
                "dist/bundle.js": "artifact",
            },
        )
        store = sim.SourceStore()
        feats = store.features(str(run.pk))
        assert feats.paths == frozenset({"app.py", "frontend/src/App.jsx"})
        assert store.loc[str(run.pk)]["total_loc"] == 2

    @pytest.mark.django_db
    def test_a_run_without_a_tree_has_no_loc(self):
        run = GenerationRunFactory(status="failed")
        store = sim.SourceStore()
        store.load([str(run.pk)])
        assert store.loc[str(run.pk)]["total_loc"] is None
        # A collected tree where the model wrote nothing is a measured 0.
        untouched = make_run(files={"app.py": "x = 1\n"}, origins={"app.py": "starter"})
        store.load([str(untouched.pk)])
        assert store.loc[str(untouched.pk)]["total_loc"] == 0


class TestCompareSources:
    def test_hand_computed_pair(self):
        a = sim.source_features({"app.py": "x = 1\n", "util.py": "y = 2\n"})
        b = sim.source_features({"app.py": "x = 1\n", "other.py": "z = 3\n"})
        result = sim.compare_sources(a, b)
        # Paths {app, util} vs {app, other}: 1 / 3.
        assert result["path_jaccard"] == pytest.approx(0.3333, abs=1e-4)
        assert result["identical_files"] == 1  # app.py, same content hash
        # Lines {x = 1, y = 2} vs {x = 1, z = 3}: 1 / 3.
        assert result["line_jaccard"] == pytest.approx(0.3333, abs=1e-4)
        # Each file is 3 tokens (< k): one shingle per file -> {x=1, y=2} vs {x=1, z=3}.
        assert result["shingle_jaccard"] == pytest.approx(0.3333, abs=1e-4)
        assert result["ast_jaccard"] is not None
        assert (result["files_a"], result["files_b"]) == (2, 2)

    def test_empty_sources_compare_as_none(self):
        empty = sim.source_features({"requirements.txt": "flask\n"})
        result = sim.compare_sources(empty, empty)
        assert result["shingle_jaccard"] is None
        assert result["path_jaccard"] is None


@pytest.mark.django_db
class TestPairCache:
    def test_pairs_are_computed_once_and_cached(self, django_assert_max_num_queries):
        a = make_run(files={"app.py": "a = 1\nb = 2\n"})
        b = make_run(files={"app.py": "a = 1\nc = 3\n"})
        first = sim.pair_similarities([(str(a.id), str(b.id))])
        value = first[(str(a.id), str(b.id))]
        assert value["path_jaccard"] == 1.0
        assert value["line_jaccard"] == pytest.approx(0.3333, abs=1e-4)
        # Cached: no query, and the reversed pair reads the same entry.
        with django_assert_max_num_queries(0):
            again = sim.pair_similarities([(str(b.id), str(a.id))])
        assert again[(str(b.id), str(a.id))] == value

    def test_store_loads_loc_and_bounded_features(self):
        jobs = [make_run(files={"app.py": f"v{i} = {i}\n"}) for i in range(4)]
        store = sim.SourceStore(capacity=2)
        store.load([str(j.id) for j in jobs])
        assert all(store.loc[str(j.id)]["total_loc"] == 1 for j in jobs)
        assert len(store._features) == 2
        pairs = [(str(jobs[i].id), str(jobs[k].id)) for i in range(4) for k in range(i + 1, 4)]
        out = sim.pair_similarities(pairs, store)
        assert len(out) == 6
        assert all(v["path_jaccard"] == 1.0 and v["shingle_jaccard"] == 0.0 for v in out.values())


class TestFindingOverlap:
    def test_overlap_only_over_tools_that_measured_both(self):
        sets = sim.FindingSets(
            measured={"a": {"ruff", "pip-audit", "bandit"}, "b": {"ruff", "pip-audit"}},
            code={
                "a": {"ruff": {("F401", "app.py"), ("E722", "app.py")}, "bandit": {("B105", "app.py")}},
                "b": {"ruff": {("F401", "app.py")}},
            },
            dependency={
                "a": {"pip-audit": {("GHSA-1", "")}},
                "b": {"pip-audit": {("GHSA-1", "")}},
            },
        )
        ov = sim.finding_overlap(sets, "a", "b")
        assert ov["tools"] == ["pip-audit", "ruff"]  # bandit measured only a: left out
        assert ov["code"]["jaccard"] == 0.5  # {F401, E722} vs {F401}
        assert ov["dependency"]["jaccard"] == 1.0
        per_tool = {t["tool"]: t for t in ov["per_tool"]}
        assert per_tool["ruff"]["code"] == {"jaccard": 0.5, "a": 2, "b": 1, "both": 1}
        # pip-audit found no code issues in either job: nothing to compare (None), not a disagreement.
        assert per_tool["pip-audit"]["code"]["jaccard"] is None

    def test_unmeasured_pair_is_none_not_zero(self):
        sets = sim.FindingSets(measured={"a": {"ruff"}}, code={}, dependency={})
        assert sim.finding_overlap(sets, "a", "b") is None


@pytest.mark.django_db
class TestFindingSets:
    def test_latest_result_signatures_split_by_kind(self):
        from backend.analysis.models import AnalysisRun
        from backend.analysis.models import ToolResult
        from backend.analysis.tests.factories import AnalysisRunFactory
        from backend.analysis.tests.factories import FindingFactory
        from backend.analysis.tests.factories import ToolResultFactory

        job = make_run()
        run = AnalysisRunFactory(
            created_by=job.created_by,
            generation_job=job,
            status=AnalysisRun.Status.COMPLETED,
            source_code=None,
            tool_slugs=["ruff", "pip-audit", "bandit"],
        )
        ruff = ToolResultFactory(run=run, tool_slug="ruff")
        audit = ToolResultFactory(run=run, tool_slug="pip-audit")
        failed = ToolResultFactory(run=run, tool_slug="bandit", status=ToolResult.Status.FAILED)
        # Not applicable measured nothing either (spec 8.8).
        ToolResultFactory(
            run=run,
            tool_slug="eslint",
            status=ToolResult.Status.SKIPPED,
            summary={"skip_reason": "not_applicable", "measurement": "not_measured"},
        )
        FindingFactory(result=ruff, rule_id="F401", file_path="/work/app.py", category="quality")
        FindingFactory(result=ruff, rule_id="F401", file_path="tests/test_x.py", category="quality")
        FindingFactory(result=audit, rule_id="GHSA-1", file_path="requirements.txt", category="dependency")
        FindingFactory(result=failed, rule_id="B105", file_path="app.py", category="security")
        sets = sim.finding_sets([str(job.id)])
        jid = str(job.id)
        assert sets.measured[jid] == {"ruff", "pip-audit"}  # the failed result measured nothing
        assert sets.code[jid] == {"ruff": {("F401", "app.py")}}  # test code left out, path normalized
        assert sets.dependency[jid] == {"pip-audit": {("GHSA-1", "")}}


@pytest.mark.django_db
class TestCallDivergence:
    @staticmethod
    def _calls(job, contents, *, attempt=1, prefix="agent"):
        for i, content in enumerate(contents, start=1):
            GenerationArtifact.objects.create(
                run=job,
                attempt=attempt,
                stage=f"{prefix}_call_{i:04d}",
                response_payload={
                    "status": 200,
                    "provider": "P1",
                    "content": content,
                    "tool_calls": [{"id": f"call_{i}_{job.id}", "name": "bash", "arguments": f"ls {i}"}],
                },
            )

    def test_first_divergent_call_ignores_random_tool_call_ids(self):
        a = GenerationRunFactory(tested=True)
        b = GenerationRunFactory(tested=True)
        self._calls(a, ["one", "two", "three"])
        self._calls(b, ["one", "two!", "three", "four"])
        probe = sim.call_divergence(str(a.id), str(b.id))
        assert probe["comparable"] is True
        assert probe["identical_first_response"] is True
        assert probe["first_divergent_call"] == 2
        assert probe["reproduced"] is False
        assert (probe["calls_a"], probe["calls_b"]) == (3, 4)
        assert probe["same_providers"] is True
        # The reversed (cached) pair swaps the per-job counts.
        assert sim.call_divergence(str(b.id), str(a.id))["calls_a"] == 4

    def test_identical_sequences_reproduce_and_shorter_one_diverges_after_its_end(self):
        a = GenerationRunFactory(tested=True)
        b = GenerationRunFactory(tested=True)
        c = GenerationRunFactory(tested=True)
        self._calls(a, ["x", "y"])
        self._calls(b, ["x", "y"])
        self._calls(c, ["x"])
        assert sim.call_divergence(str(a.id), str(b.id))["reproduced"] is True
        assert sim.call_divergence(str(a.id), str(b.id))["first_divergent_call"] is None
        assert sim.call_divergence(str(a.id), str(c.id))["first_divergent_call"] == 2

    def test_only_the_last_attempt_and_call_stages_count(self):
        a = GenerationRunFactory(tested=True)
        b = GenerationRunFactory(tested=True)
        self._calls(a, ["old", "older"], attempt=1)
        self._calls(a, ["x", "y"], attempt=2)
        self._calls(b, ["x", "y"], prefix="slot")
        GenerationArtifact.objects.create(run=b, stage="evaluation_log", response_payload={"content": "z"})
        probe = sim.call_divergence(str(a.id), str(b.id))
        assert probe["reproduced"] is True
        assert (probe["calls_a"], probe["calls_b"]) == (2, 2)

    def test_runs_without_calls_are_not_comparable(self):
        a = GenerationRunFactory(tested=True)
        b = GenerationRunFactory(tested=True)
        probe = sim.call_divergence(str(a.id), str(b.id))
        assert (probe["comparable"], probe["reproduced"], probe["first_divergent_call"]) == (False, False, None)
