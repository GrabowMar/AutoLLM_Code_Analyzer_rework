"""Pure-Python exact and MinHash clustering with effective-n statistics (spec section 7.9 step 2).

- **Exact** clusters group variants by ``normalized_sha``.
- **Near-duplicate** clusters use an own MinHash implementation (``num_perm`` universal hash functions
  ``(a*x + b) mod (2^61 - 1)`` over 64-bit blake2b shingle hashes, ``shingle``-token shingles), then
  single linkage over every pair whose estimated Jaccard similarity is ``>= threshold``. Clustering runs
  within one target (all conditions together); statistics are reported per (condition, target).
- Unparsable variants (``normalized_sha == ""``) are counted but never clustered.
- The original is matched against the clusters (``includes_original``) but is never a member or counted.

Per (condition, target): ``n`` (clustered variants), ``unparsed``, ``distinct`` clusters, the exact-duplicate
share ``(n - distinct exact shas) / n``, the top-1 share, Shannon entropy (bits) and Chao1 richness of the
cluster-size distribution. ``low_effective_n`` is set when the exact-duplicate share exceeds 0.5.
"""

from __future__ import annotations

import hashlib
import random
from collections import Counter
from collections import defaultdict
from dataclasses import dataclass
from dataclasses import field
from typing import TYPE_CHECKING
from typing import Any

from backend.generation.patterns import stats

if TYPE_CHECKING:
    from collections.abc import Hashable
    from collections.abc import Sequence

MERSENNE_61 = (1 << 61) - 1
MINHASH_SEED = 7_919  # fixed: signatures must be comparable across pattern runs
LOW_EFFECTIVE_N_SHARE = 0.5


@dataclass(frozen=True)
class Item:
    """One variant of a target: ``key`` is the run id; ``sha`` is ``""`` for an unparsable variant."""

    key: str
    sha: str
    tokens: tuple[str, ...]
    group: Hashable = None  # the condition


@dataclass
class Cluster:
    method: str
    key: str  # the exact sha (exact) or the representative's sha (minhash)
    members: list[str]  # run ids, input order
    distances: dict[str, float] = field(default_factory=dict)
    representative: str = ""
    includes_original: bool = False

    @property
    def size(self) -> int:
        return len(self.members)


# ---------------------------------------------------------------------------
# MinHash
# ---------------------------------------------------------------------------


def _permutations(num_perm: int, seed: int = MINHASH_SEED) -> list[tuple[int, int]]:
    rng = random.Random(seed)  # noqa: S311 - fixed hashing coefficients, not a secret
    return [(rng.randrange(1, MERSENNE_61), rng.randrange(0, MERSENNE_61)) for _ in range(num_perm)]


def shingles(tokens: Sequence[str], k: int) -> set[str]:
    """``k``-token shingles; a stream shorter than ``k`` is one shingle (never an empty set for code)."""
    if not tokens:
        return set()
    if len(tokens) <= k:
        return {"\x1f".join(tokens)}
    return {"\x1f".join(tokens[i : i + k]) for i in range(len(tokens) - k + 1)}


def _shingle_hash(shingle: str) -> int:
    return int.from_bytes(hashlib.blake2b(shingle.encode("utf-8"), digest_size=8).digest(), "big")


def signature(tokens: Sequence[str], *, num_perm: int = 128, shingle: int = 5) -> tuple[int, ...]:
    """The MinHash signature of a token stream (``num_perm`` minima)."""
    hashes = [_shingle_hash(s) for s in shingles(tokens, shingle)]
    if not hashes:
        return tuple([MERSENNE_61] * num_perm)
    return tuple(min((a * h + b) % MERSENNE_61 for h in hashes) for a, b in _permutations(num_perm))


def estimated_jaccard(sig_a: Sequence[int], sig_b: Sequence[int]) -> float:
    if not sig_a or len(sig_a) != len(sig_b):
        msg = "signatures must be non-empty and of equal length"
        raise ValueError(msg)
    return sum(1 for x, y in zip(sig_a, sig_b, strict=True) if x == y) / len(sig_a)


def exact_jaccard(tokens_a: Sequence[str], tokens_b: Sequence[str], k: int = 5) -> float:
    sa, sb = shingles(tokens_a, k), shingles(tokens_b, k)
    if not sa and not sb:
        return 1.0
    return len(sa & sb) / len(sa | sb)


class _UnionFind:
    def __init__(self, keys: Sequence[str]) -> None:
        self.parent = {key: key for key in keys}

    def find(self, key: str) -> str:
        root = key
        while self.parent[root] != root:
            root = self.parent[root]
        while self.parent[key] != root:
            self.parent[key], key = root, self.parent[key]
        return root

    def union(self, a: str, b: str) -> None:
        ra, rb = self.find(a), self.find(b)
        if ra != rb:
            self.parent[rb] = ra


# ---------------------------------------------------------------------------
# Clustering
# ---------------------------------------------------------------------------


def _representative(members: list[str], sha_of: dict[str, str]) -> str:
    """The member with the most common sha (ties: first in input order)."""
    counts = Counter(sha_of[m] for m in members)
    best = max(counts.values())
    return next(m for m in members if counts[sha_of[m]] == best)


def exact_clusters(items: Sequence[Item]) -> list[Cluster]:
    """Clusters by ``normalized_sha`` (unparsable items left out), largest first, then by first member."""
    groups: dict[str, list[str]] = defaultdict(list)
    for item in items:
        if item.sha:
            groups[item.sha].append(item.key)
    clusters = [
        Cluster(
            method="exact",
            key=sha,
            members=members,
            distances=dict.fromkeys(members, 0.0),
            representative=members[0],
        )
        for sha, members in groups.items()
    ]
    order = {item.key: i for i, item in enumerate(items)}
    clusters.sort(key=lambda c: (-c.size, order[c.members[0]]))
    return clusters


def minhash_clusters(
    items: Sequence[Item],
    *,
    threshold: float = 0.8,
    num_perm: int = 128,
    shingle: int = 5,
) -> list[Cluster]:
    """Near-duplicate clusters: single linkage over pairs with estimated Jaccard ``>= threshold``.

    Exact duplicates always end up together (identical signatures). The distance of a member is
    ``1 - J(member, representative)`` by the MinHash estimate.
    """
    parsed = [item for item in items if item.sha]
    if not parsed:
        return []
    # One signature per distinct sha (exact duplicates share it).
    by_sha: dict[str, tuple[int, ...]] = {}
    for item in parsed:
        if item.sha not in by_sha:
            by_sha[item.sha] = signature(item.tokens, num_perm=num_perm, shingle=shingle)
    shas = list(by_sha)
    uf = _UnionFind(shas)
    for i, sha_a in enumerate(shas):
        for sha_b in shas[i + 1 :]:
            if estimated_jaccard(by_sha[sha_a], by_sha[sha_b]) >= threshold:
                uf.union(sha_a, sha_b)
    sha_of = {item.key: item.sha for item in parsed}
    members_by_root: dict[str, list[str]] = defaultdict(list)
    for item in parsed:
        members_by_root[uf.find(item.sha)].append(item.key)
    clusters: list[Cluster] = []
    for members in members_by_root.values():
        rep = _representative(members, sha_of)
        rep_sig = by_sha[sha_of[rep]]
        distances = {m: round(1.0 - estimated_jaccard(by_sha[sha_of[m]], rep_sig), 6) for m in members}
        clusters.append(
            Cluster(method="minhash", key=sha_of[rep], members=members, distances=distances, representative=rep),
        )
    order = {item.key: i for i, item in enumerate(items)}
    clusters.sort(key=lambda c: (-c.size, order[c.members[0]]))
    return clusters


def cluster_items(items: Sequence[Item], params: dict[str, Any]) -> list[Cluster]:
    """Clusters by ``params["method"]`` (``exact`` or ``minhash``; ``contracts.slot.ClusterParams``)."""
    method = params.get("method", "minhash")
    if method == "exact":
        return exact_clusters(items)
    if method == "minhash":
        return minhash_clusters(
            items,
            threshold=float(params.get("threshold", 0.8)),
            num_perm=int(params.get("num_perm", 128)),
            shingle=int(params.get("shingle", 5)),
        )
    msg = f"unknown cluster method {method!r}"
    raise ValueError(msg)


def mark_original(
    clusters: Sequence[Cluster],
    original: Item | None,
    items: Sequence[Item],
    params: dict[str, Any],
) -> None:
    """Set ``includes_original`` on the cluster the original falls into (exact sha, or MinHash >= threshold)."""
    if original is None or not original.sha:
        return
    method = params.get("method", "minhash")
    sha_of = {item.key: item for item in items}
    if method == "exact":
        for cluster in clusters:
            if cluster.key == original.sha:
                cluster.includes_original = True
        return
    num_perm, shingle = int(params.get("num_perm", 128)), int(params.get("shingle", 5))
    threshold = float(params.get("threshold", 0.8))
    original_sig = signature(original.tokens, num_perm=num_perm, shingle=shingle)
    cache: dict[str, tuple[int, ...]] = {}
    for cluster in clusters:
        for member in cluster.members:
            item = sha_of[member]
            if item.sha == original.sha:
                cluster.includes_original = True
                break
            if item.sha not in cache:
                cache[item.sha] = signature(item.tokens, num_perm=num_perm, shingle=shingle)
            if estimated_jaccard(cache[item.sha], original_sig) >= threshold:
                cluster.includes_original = True
                break


# ---------------------------------------------------------------------------
# Effective-n statistics
# ---------------------------------------------------------------------------


def group_stats(items: Sequence[Item], clusters: Sequence[Cluster]) -> dict[str, Any]:
    """``{n, unparsed, distinct, exact_dup_share, top1_share, entropy, chao1, low_effective_n}`` of ``items``.

    ``items`` are one (condition, target)'s variants; ``clusters`` are the target's clusters (all
    conditions), so the sizes counted here are this group's members of each cluster.
    """
    parsed = [item for item in items if item.sha]
    n = len(parsed)
    unparsed = len(items) - n
    if n == 0:
        return {
            "n": 0,
            "unparsed": unparsed,
            "distinct": 0,
            "exact_dup_share": None,
            "top1_share": None,
            "entropy": None,
            "chao1": None,
            "low_effective_n": False,
        }
    keys = {item.key for item in parsed}
    sizes = [sum(1 for m in cluster.members if m in keys) for cluster in clusters]
    sizes = [s for s in sizes if s > 0]
    distinct_exact = len({item.sha for item in parsed})
    exact_dup_share = (n - distinct_exact) / n
    entropy = stats.shannon_entropy(sizes)
    chao = stats.chao1(sizes)
    return {
        "n": n,
        "unparsed": unparsed,
        "distinct": len(sizes),
        "exact_dup_share": round(exact_dup_share, 6),
        "top1_share": round(max(sizes) / n, 6),
        "entropy": None if entropy is None else round(entropy, 6),
        "chao1": None if chao is None else round(chao, 6),
        "low_effective_n": exact_dup_share > LOW_EFFECTIVE_N_SHARE,
    }
