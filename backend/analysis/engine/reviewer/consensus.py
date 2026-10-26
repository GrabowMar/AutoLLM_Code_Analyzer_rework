"""Cluster findings across review replicates and measure agreement.

Each successful replicate is one list of findings (an empty list is a
replicate that found nothing — it still votes). Findings are clustered
across replicates when they point at the same file, at the same place, and
describe the same weakness: the same CWE when both carry one, else the same
category. Clustering is anchor-based rather than transitive so a run of
nearby findings cannot chain into one blob, and a finding prefers a cluster
its own replicate is not yet in, so two distinct findings of one replicate
never collapse into one vote.

*The same place* is the finding's point: the lines its quoted evidence
occupies (``extra["evidence_at"]``, set by grounding), or its cited range
when the evidence was not found. Two findings are at the same place when
their points are within ``line_window`` lines of each other, or when they
cite the same range (both ends within the window). A point wider than
``2 * line_window + 1`` lines is a region (a whole function, a whole file),
not a place: it only clusters with a finding citing the same range, so one
vague wide finding cannot absorb unrelated findings anywhere inside it.

A cluster is *consensus* when a strict majority of the **planned** k
replicates reported it (``k // 2 + 1``, the rule the plan freezes): a
replicate that failed reports nothing, so a review with a failed replicate
can only count fewer issues than the full review would have, never more,
and every run of one plan applies the same rule. (Called without *planned*
the majority is over the successful replicates.) Every member of a consensus
cluster gets ``is_consensus``; exactly one member — the representative —
gets ``counted``, and only when it is grounded (exact/relocated), in the
measured scope and of a category the plan counts (style, complexity and
duplication follow the plan's ``count_style`` / ``count_metric_findings``,
exactly as static findings do), so counting ``counted`` rows (or distinct
``issue_key`` among them, as ``measurement`` does) counts each consensus
issue once, under the same rules as the static numerator. The
representative carries the majority severity label of the cluster's
grounded in-scope members (ties to the more severe, the rule of
``dedup.issue_summary(mode="consensus")``). All members share ``issue_key``.

Agreement is the mean pairwise Jaccard index of the replicates' cluster sets
(two replicates that both found nothing agree fully: J(∅, ∅) = 1).
"""

from __future__ import annotations

import hashlib
import itertools
from collections import Counter
from collections.abc import Collection
from dataclasses import dataclass
from dataclasses import field
from typing import Any

from backend.analysis.engine.reviewer.grounding import GROUNDED
from backend.analysis.engine.types import SEVERITY_RANK
from backend.analysis.engine.types import NormalizedFinding

THRESHOLD_RULE = "majority of the k planned samples (k // 2 + 1); a failed sample votes for nothing"
QUORUM_RULE = "majority of the k planned samples (k // 2 + 1)"


def majority(n: int) -> int:
    """Strict majority of *n* votes (2 of 2, 2 of 3, 3 of 4, 3 of 5)."""
    return n // 2 + 1


@dataclass
class _Cluster:
    ordinal: int
    anchor: NormalizedFinding
    members: list[tuple[int, NormalizedFinding]] = field(default_factory=list)  # (replicate position, finding)

    @property
    def replicates(self) -> set[int]:
        return {pos for pos, _ in self.members}


def _start(f: NormalizedFinding) -> int:
    return f.line or 0


def _end(f: NormalizedFinding) -> int:
    return max(f.end_line or 0, _start(f))


def _point(f: NormalizedFinding) -> tuple[int, int]:
    """The lines the finding's evidence occupies (grounded findings), else its cited range."""
    at = (f.extra or {}).get("evidence_at")
    if isinstance(at, (list, tuple)) and len(at) == 2 and all(isinstance(v, int) and v > 0 for v in at):
        return at[0], max(at[0], at[1])
    return _start(f), _end(f)


def _cwes(f: NormalizedFinding) -> frozenset[str]:
    return frozenset(c.upper() for c in (f.cwe or []) if c)


def _same_place(f: NormalizedFinding, anchor: NormalizedFinding, window: int) -> bool:
    if abs(_start(f) - _start(anchor)) <= window and abs(_end(f) - _end(anchor)) <= window:
        return True  # the same cited range
    (fs, fe), (as_, ae) = _point(f), _point(anchor)
    widest = 2 * window + 1
    if fe - fs + 1 > widest or ae - as_ + 1 > widest:
        return False  # a region only matches the same range
    return not (fs - window > ae or as_ - window > fe)


def _compatible(f: NormalizedFinding, anchor: NormalizedFinding, window: int) -> bool:
    if f.file_path != anchor.file_path or not _same_place(f, anchor, window):
        return False
    a, b = _cwes(f), _cwes(anchor)
    if a and b:
        return bool(a & b)
    return f.category == anchor.category


def _order_key(item: tuple[int, NormalizedFinding]) -> tuple:
    pos, f = item
    return (
        f.file_path,
        *_point(f),
        _start(f),
        _end(f),
        pos,
        f.category,
        tuple(sorted(_cwes(f))),
        SEVERITY_RANK.get(f.severity, len(SEVERITY_RANK)),
        f.title,
    )


def _issue_key(cluster: _Cluster) -> str:
    a = cluster.anchor
    basis = "\0".join(
        [a.file_path, str(_start(a)), a.category, ",".join(sorted(_cwes(a))), str(cluster.ordinal)],
    )
    return "ai-" + hashlib.sha256(basis.encode("utf-8")).hexdigest()[:32]


def _jaccard(a: set[int], b: set[int]) -> float:
    union = a | b
    return 1.0 if not union else len(a & b) / len(union)


def _mean_pairwise(sets: list[set[int]]) -> float | None:
    pairs = list(itertools.combinations(sets, 2))
    if not pairs:
        return None
    return round(sum(_jaccard(a, b) for a, b in pairs) / len(pairs), 6)


def cluster(samples: list[list[NormalizedFinding]], *, line_window: int = 3) -> list[_Cluster]:
    """Deterministic anchor-based clusters over all replicates' findings."""
    clusters: list[_Cluster] = []
    by_file: dict[str, list[_Cluster]] = {}
    items = sorted(((pos, f) for pos, fs in enumerate(samples) for f in fs), key=_order_key)
    for pos, f in items:
        candidates = [
            c
            for c in by_file.get(f.file_path, [])
            if pos not in c.replicates and _compatible(f, c.anchor, line_window)
        ]
        if candidates:
            target = min(candidates, key=lambda c: (abs(_point(c.anchor)[0] - _point(f)[0]), c.ordinal))
        else:
            target = _Cluster(ordinal=len(clusters), anchor=f)
            clusters.append(target)
            by_file.setdefault(f.file_path, []).append(target)
        target.members.append((pos, f))
    return clusters


def _representative(
    members: list[tuple[int, NormalizedFinding]],
    uncounted_categories: Collection[str] = (),
) -> NormalizedFinding | None:
    """The member that is counted: grounded, in scope, of a counted category, with the majority severity label."""
    eligible = [
        f
        for _, f in members
        if f.grounding in GROUNDED and f.in_measured_scope and f.category not in uncounted_categories
    ]
    if not eligible:
        return None
    votes = Counter(f.severity for f in eligible)
    label = min(votes, key=lambda s: (-votes[s], SEVERITY_RANK.get(s, len(SEVERITY_RANK))))
    return next(f for f in eligible if f.severity == label)


def apply_consensus(
    samples: list[list[NormalizedFinding]],
    *,
    successful: int,
    planned: int | None = None,
    line_window: int = 3,
    uncounted_categories: Collection[str] = (),
) -> dict[str, Any]:
    """Mark ``is_consensus`` / ``counted`` (grounded consensus only) / ``issue_key``
    in place; return {successful_samples, planned_samples, threshold, clusters,
    consensus, counted, uncounted_category, mean_pairwise_jaccard,
    per_sample_counts, ...}.

    *samples*: one findings list per successful replicate (empty lists
    included). *successful*: m. *planned*: k, the replicates the plan asked
    for; the threshold is a strict majority of k (of m when not given), so a
    failed replicate is a vote for nothing rather than a smaller electorate.
    *uncounted_categories*: categories the plan's outcome rules do not count
    (``normalize.uncounted_categories``); a consensus cluster made only of
    such findings is reported (``uncounted_category``) but never counted, as
    for static findings.
    """
    electorate = planned if planned is not None else successful
    threshold = majority(electorate) if successful > 0 and electorate > 0 else None
    clusters = cluster(samples, line_window=line_window)
    consensus = 0
    counted = 0
    uncounted_category = 0
    per_sample_sets: list[set[int]] = [set() for _ in samples]
    per_sample_grounded: list[set[int]] = [set() for _ in samples]
    for c in clusters:
        is_consensus = threshold is not None and len(c.replicates) >= threshold
        consensus += is_consensus
        rep = _representative(c.members, uncounted_categories) if is_consensus else None
        if is_consensus and rep is None and _representative(c.members) is not None:
            uncounted_category += 1
        key = _issue_key(c)
        for pos, f in c.members:
            f.is_consensus = is_consensus
            f.counted = f is rep
            f.issue_key = key
            per_sample_sets[pos].add(c.ordinal)
            if f.grounding in GROUNDED:
                per_sample_grounded[pos].add(c.ordinal)
        counted += rep is not None
    return {
        "successful_samples": successful,
        "planned_samples": planned,
        "threshold": threshold,
        "threshold_rule": THRESHOLD_RULE if planned is not None else "majority of the successful samples",
        # Missing replicates vote for nothing: the count is then a lower bound of the full review's.
        "replicates_complete": planned is None or successful >= planned,
        "line_window": line_window,
        "clusters": len(clusters),
        "consensus": consensus,
        "counted": counted,
        "uncounted_category": uncounted_category,
        "uncounted_categories": sorted(uncounted_categories),
        "mean_pairwise_jaccard": _mean_pairwise(per_sample_sets),
        "mean_pairwise_jaccard_grounded": _mean_pairwise(per_sample_grounded),
        "per_sample_counts": [len(fs) for fs in samples],
        "per_sample_clusters": [len(s) for s in per_sample_sets],
    }
