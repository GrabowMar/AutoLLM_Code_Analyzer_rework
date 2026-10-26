"""Consensus over review replicates: clustering, threshold, counted representative, agreement."""

from __future__ import annotations

import pytest

from backend.analysis.engine.reviewer import consensus
from backend.analysis.tests.reviewer_support import ai_finding as nf


def counted(samples):
    return [f for fs in samples for f in fs if f.counted]


def test_majority_of_three_is_consensus_and_counted_once():
    samples = [[nf(line=10, index=0)], [nf(line=12, index=1)], [nf(line=40, index=2, cwe=("CWE-79",))]]
    stats = consensus.apply_consensus(samples, successful=3)
    assert stats["threshold"] == 2
    assert stats["clusters"] == 2
    assert stats["consensus"] == 1
    assert stats["counted"] == 1
    a, b, c = samples[0][0], samples[1][0], samples[2][0]
    assert a.is_consensus
    assert b.is_consensus
    assert not c.is_consensus
    assert counted(samples) == [a]
    assert a.issue_key == b.issue_key != c.issue_key
    assert a.issue_key.startswith("ai-")
    assert stats["per_sample_counts"] == [1, 1, 1]


def test_singleton_is_not_consensus_with_three_samples():
    samples = [[nf(index=0)], [], []]
    stats = consensus.apply_consensus(samples, successful=3)
    assert stats["consensus"] == 0
    assert counted(samples) == []


def test_threshold_is_a_majority_of_the_planned_samples():
    # Two of three planned samples succeeded: still 2 votes needed (the failed one votes for nothing).
    samples = [[nf(index=0)], []]
    stats = consensus.apply_consensus(samples, successful=2, planned=3)
    assert stats["threshold"] == 2
    assert stats["planned_samples"] == 3
    assert stats["replicates_complete"] is False
    assert stats["consensus"] == 0
    assert counted(samples) == []
    # Without the plan's k: a strict majority of the successful samples (2 of 2), never one of two.
    assert consensus.apply_consensus(samples, successful=2)["threshold"] == 2


@pytest.mark.parametrize(("n", "votes"), [(1, 1), (2, 2), (3, 2), (4, 3), (5, 3), (6, 4)])
def test_majority(n, votes):
    assert consensus.majority(n) == votes


def test_a_failed_replicate_never_inflates_the_count():
    # Regression: each replicate reports one shared issue and one of its own. With ceil(m/2) of the
    # successful samples a failed replicate tripled the count (1 of 2 = "consensus"); the count must
    # instead be a lower bound of what the full review counts.
    def replicate(index):
        return [nf(line=10, index=index), nf(line=40 + 20 * index, index=index, category="bug", cwe=())]

    full = [replicate(0), replicate(1), replicate(2)]
    full_stats = consensus.apply_consensus(full, successful=3, planned=3)
    degraded = [replicate(0), replicate(1)]
    degraded_stats = consensus.apply_consensus(degraded, successful=2, planned=3)
    assert (full_stats["threshold"], full_stats["counted"]) == (2, 1)
    assert (degraded_stats["threshold"], degraded_stats["counted"]) == (2, 1)
    assert full_stats["replicates_complete"] is True
    assert degraded_stats["replicates_complete"] is False


def test_single_sample_threshold_one_and_no_agreement():
    samples = [[nf(index=0), nf(line=50, index=0, cwe=("CWE-79",))]]
    stats = consensus.apply_consensus(samples, successful=1)
    assert stats["threshold"] == 1
    assert stats["counted"] == 2
    assert stats["mean_pairwise_jaccard"] is None


def test_zero_successful_samples():
    stats = consensus.apply_consensus([], successful=0)
    assert stats["threshold"] is None
    assert stats["clusters"] == 0
    assert stats["mean_pairwise_jaccard"] is None


def test_ungrounded_consensus_is_not_counted():
    samples = [[nf(index=0, grounding="ungrounded")], [nf(index=1, grounding="invalid_file")], []]
    stats = consensus.apply_consensus(samples, successful=3)
    assert stats["consensus"] == 1
    assert stats["counted"] == 0
    assert all(f.is_consensus for fs in samples for f in fs)
    assert counted(samples) == []


def test_counted_representative_must_be_grounded_and_in_scope():
    grounded_out_of_scope = nf(index=0, in_scope=False)
    ungrounded = nf(index=1, grounding="ungrounded")
    relocated = nf(index=2, grounding="relocated")
    samples = [[grounded_out_of_scope], [ungrounded], [relocated]]
    consensus.apply_consensus(samples, successful=3)
    assert counted(samples) == [relocated]


def test_out_of_scope_only_cluster_is_not_counted():
    samples = [[nf(index=0, in_scope=False)], [nf(index=1, in_scope=False)]]
    stats = consensus.apply_consensus(samples, successful=2)
    assert stats["consensus"] == 1
    assert stats["counted"] == 0


@pytest.mark.parametrize("category", ["style", "complexity", "duplication"])
def test_uncounted_categories_are_consensus_but_never_counted(category):
    samples = [
        [nf(index=0, category=category, cwe=()), nf(line=40, index=0)],
        [nf(index=1, category=category, cwe=()), nf(line=41, index=1)],
    ]
    stats = consensus.apply_consensus(
        samples,
        successful=2,
        uncounted_categories=frozenset({"style", "complexity", "duplication"}),
    )
    assert stats["consensus"] == 2
    assert stats["counted"] == 1
    assert stats["uncounted_category"] == 1
    assert stats["uncounted_categories"] == ["complexity", "duplication", "style"]
    assert all(f.is_consensus for fs in samples for f in fs)
    assert [f.category for f in counted(samples)] == ["security"]
    # Without the rule (the plan counts them) the same cluster counts.
    stats = consensus.apply_consensus(samples, successful=2)
    assert (stats["counted"], stats["uncounted_category"]) == (2, 0)


def test_a_counted_category_member_can_represent_a_mixed_cluster():
    # Clustered on the shared CWE; only the security member may be counted.
    style = nf(index=0, category="style")
    security = nf(index=1, category="security")
    samples = [[style], [security]]
    stats = consensus.apply_consensus(samples, successful=2, uncounted_categories={"style"})
    assert style.issue_key == security.issue_key
    assert counted(samples) == [security]
    assert stats["uncounted_category"] == 0


def test_representative_has_the_majority_severity_more_severe_on_tie():
    samples = [
        [nf(index=0, severity="medium")],
        [nf(index=1, severity="high")],
        [nf(index=2, severity="medium")],
    ]
    consensus.apply_consensus(samples, successful=3)
    assert [f.severity for f in counted(samples)] == ["medium"]
    tie = [[nf(index=0, severity="critical")], [nf(index=1, severity="medium")]]
    consensus.apply_consensus(tie, successful=2)
    assert [f.severity for f in counted(tie)] == ["critical"]


def test_majority_severity_only_counts_eligible_members():
    samples = [
        [nf(index=0, severity="low", grounding="ungrounded")],
        [nf(index=1, severity="low", grounding="ungrounded")],
        [nf(index=2, severity="high")],
    ]
    consensus.apply_consensus(samples, successful=3)
    assert [f.severity for f in counted(samples)] == ["high"]


def test_different_cwes_do_not_cluster():
    samples = [[nf(index=0, cwe=("CWE-89",))], [nf(index=1, cwe=("CWE-79",))]]
    stats = consensus.apply_consensus(samples, successful=2)
    assert stats["clusters"] == 2


def test_missing_cwe_falls_back_to_category():
    samples = [[nf(index=0, cwe=())], [nf(index=1, cwe=("CWE-89",))], [nf(index=2, category="bug", cwe=())]]
    stats = consensus.apply_consensus(samples, successful=3)
    assert stats["clusters"] == 2
    assert samples[0][0].issue_key == samples[1][0].issue_key


def test_cwe_comparison_is_case_insensitive():
    samples = [[nf(index=0, cwe=("cwe-89",))], [nf(index=1, cwe=("CWE-89",))]]
    assert consensus.apply_consensus(samples, successful=2)["clusters"] == 1


def test_line_window():
    near = [[nf(line=10, index=0)], [nf(line=13, index=1)]]
    assert consensus.apply_consensus(near, successful=2)["clusters"] == 1
    far = [[nf(line=10, index=0)], [nf(line=14, index=1)]]
    assert consensus.apply_consensus(far, successful=2)["clusters"] == 2
    custom = [[nf(line=10, index=0)], [nf(line=14, index=1)]]
    assert consensus.apply_consensus(custom, successful=2, line_window=5)["clusters"] == 1


def test_the_same_issue_cited_at_different_widths_clusters_on_its_evidence():
    # A cites the function (10-20) quoting line 20; B cites line 22 only: the quotes are 2 lines apart.
    ranges = [[nf(line=10, end=20, index=0, evidence_at=(20, 20))], [nf(line=22, index=1, evidence_at=(22, 22))]]
    assert consensus.apply_consensus(ranges, successful=2)["clusters"] == 1
    # The same cited range clusters whatever lines each quoted (both ends within the window).
    same = [
        [nf(line=96, end=125, index=0, evidence_at=(96, 96))],
        [nf(line=97, end=125, index=1, evidence_at=(110, 110))],
    ]
    assert consensus.apply_consensus(same, successful=2)["clusters"] == 1


def test_a_wide_range_does_not_absorb_unrelated_findings():
    # Regression: a vague finding citing lines 1-300 used to swallow any same-category finding inside it,
    # turning two single-replicate opinions into one counted consensus issue.
    wide = nf(line=1, end=300, index=0, category="bug", cwe=(), evidence_at=(17, 17), title="error handling")
    other = nf(line=250, index=1, category="bug", cwe=(), evidence_at=(250, 250), title="off-by-one")
    samples = [[wide], [other], []]
    stats = consensus.apply_consensus(samples, successful=3, planned=3)
    assert (stats["clusters"], stats["consensus"], stats["counted"]) == (2, 0, 0)
    # A wide citation whose evidence was not found is a region too: it only matches the same range.
    ungrounded = nf(line=96, end=125, index=0, category="bug", cwe=(), grounding="ungrounded")
    inside = nf(line=110, index=1, category="bug", cwe=(), evidence_at=(110, 110))
    stats = consensus.apply_consensus([[ungrounded], [inside], []], successful=3, planned=3)
    assert (stats["clusters"], stats["counted"]) == (2, 0)
    # The same issue quoted at the same line still clusters, wide citation or not.
    narrow = nf(line=16, end=18, index=2, category="bug", cwe=(), evidence_at=(17, 17))
    stats = consensus.apply_consensus([[wide], [other], [narrow]], successful=3, planned=3)
    assert stats["consensus"] == 1


def test_different_files_never_cluster():
    samples = [[nf(path="a.py", index=0)], [nf(path="b.py", index=1)]]
    assert consensus.apply_consensus(samples, successful=2)["clusters"] == 2


def test_anchor_clustering_does_not_chain():
    # 10 -> 13 -> 16 -> 19 would chain transitively; anchors keep them apart.
    samples = [[nf(line=10, index=0), nf(line=16, index=0)], [nf(line=13, index=1), nf(line=19, index=1)]]
    stats = consensus.apply_consensus(samples, successful=2)
    assert stats["clusters"] == 2
    assert {f.line for f in samples[0] + samples[1] if f.issue_key == samples[0][0].issue_key} == {10, 13}


def test_two_findings_of_one_sample_never_merge_into_one_vote():
    samples = [[nf(line=10, index=0), nf(line=11, index=0)], [nf(line=10, index=1)], []]
    stats = consensus.apply_consensus(samples, successful=3)
    assert stats["clusters"] == 2
    assert stats["consensus"] == 1
    assert stats["per_sample_clusters"] == [2, 1, 0]


def test_jaccard_agreement():
    samples = [
        [nf(line=10, index=0), nf(path="b.py", index=0)],
        [nf(line=10, index=1)],
        [nf(line=10, index=2), nf(path="c.py", index=2)],
    ]
    stats = consensus.apply_consensus(samples, successful=3)
    # clusters: X (all three), B (s0), C (s2): J(s0,s1)=1/2, J(s0,s2)=1/3, J(s1,s2)=1/2
    assert stats["mean_pairwise_jaccard"] == pytest.approx((1 / 2 + 1 / 3 + 1 / 2) / 3, abs=1e-6)


def test_jaccard_of_two_empty_samples_is_full_agreement():
    stats = consensus.apply_consensus([[], []], successful=2)
    assert stats["mean_pairwise_jaccard"] == 1.0


def test_grounded_jaccard_ignores_ungrounded_members():
    samples = [[nf(index=0)], [nf(index=1, grounding="ungrounded")]]
    stats = consensus.apply_consensus(samples, successful=2)
    assert stats["mean_pairwise_jaccard"] == 1.0
    assert stats["mean_pairwise_jaccard_grounded"] == 0.0


def test_deterministic_regardless_of_input_order():
    def build():
        return [
            [nf(line=30, index=0, cwe=("CWE-79",)), nf(line=10, index=0)],
            [nf(line=11, index=1), nf(line=31, index=1, cwe=("CWE-79",))],
        ]

    a, b = build(), build()
    b = [list(reversed(fs)) for fs in b]
    consensus.apply_consensus(a, successful=2)
    consensus.apply_consensus(b, successful=2)
    keys_a = sorted((f.line, f.issue_key, f.counted) for fs in a for f in fs)
    keys_b = sorted((f.line, f.issue_key, f.counted) for fs in b for f in fs)
    assert keys_a == keys_b


def test_reapplying_resets_previous_marks():
    samples = [[nf(index=0)], [nf(index=1)]]
    consensus.apply_consensus(samples, successful=2)
    assert len(counted(samples)) == 1
    consensus.apply_consensus(samples, successful=2, planned=3)  # threshold 2: still consensus
    assert len(counted(samples)) == 1
    consensus.apply_consensus(samples, successful=2, planned=4)  # threshold 3: no longer consensus
    assert counted(samples) == []
    assert not any(f.is_consensus for fs in samples for f in fs)
