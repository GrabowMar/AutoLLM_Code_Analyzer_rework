"""Exact and MinHash clustering on synthetic variants; effective-n statistics (spec 7.9 step 2)."""

from __future__ import annotations

from backend.generation.patterns import cluster
from backend.generation.patterns import normalize as norm

BASE = """def handler(db, user_id):
    rows = db.execute("SELECT * FROM todos WHERE owner = ?", (user_id,))
    result = []
    for row in rows:
        result.append({"id": row[0], "title": row[1], "done": bool(row[2])})
    return result
"""
RENAMED = BASE.replace("rows", "items").replace("result", "acc").replace("row", "it")
NEAR = BASE.replace('"done": bool(row[2])', '"done": bool(row[2]), "owner": user_id')
OTHER = """def handler(db, user_id):
    return [dict(r) for r in db.execute(f"SELECT * FROM todos WHERE owner = {user_id}")]
"""


def item(key, code, group=0):
    result = norm.normalized_sha(code, "python")
    return cluster.Item(key=key, sha=result.sha, tokens=result.tokens, group=group)


def test_exact_clusters_group_by_normalized_sha_and_skip_unparsable():
    items = [item("a", BASE), item("b", RENAMED), item("c", OTHER), cluster.Item("bad", "", ())]
    clusters = cluster.exact_clusters(items)
    assert [sorted(c.members) for c in clusters] == [["a", "b"], ["c"]]
    assert clusters[0].representative == "a"
    assert all("bad" not in c.members for c in clusters)


def test_minhash_merges_near_duplicates_but_not_different_code():
    items = [item("a", BASE), item("b", RENAMED), item("n", NEAR), item("c", OTHER)]
    assert cluster.exact_jaccard(items[0].tokens, items[2].tokens) >= 0.8
    clusters = cluster.minhash_clusters(items, threshold=0.8)
    assert [sorted(c.members) for c in clusters] == [["a", "b", "n"], ["c"]]
    near = clusters[0]
    assert near.method == "minhash"
    assert near.distances["a"] == 0.0
    assert 0.0 < near.distances["n"] < 0.2


def test_minhash_estimate_tracks_the_exact_jaccard():
    a, n = item("a", BASE), item("n", NEAR)
    sa = cluster.signature(a.tokens)
    sn = cluster.signature(n.tokens)
    assert abs(cluster.estimated_jaccard(sa, sn) - cluster.exact_jaccard(a.tokens, n.tokens)) < 0.12
    assert cluster.signature(a.tokens) == sa  # fixed coefficients: comparable across runs


def test_the_original_is_marked_but_never_a_member():
    items = [item("a", BASE), item("c", OTHER)]
    original = item("original", RENAMED)
    clusters = cluster.cluster_items(items, {"method": "minhash"})
    cluster.mark_original(clusters, original, items, {"method": "minhash"})
    assert [c.includes_original for c in clusters] == [True, False]
    assert all("original" not in c.members for c in clusters)


def test_group_stats_and_low_effective_n():
    copies = [item(f"x{i}", BASE) for i in range(8)] + [item("y", OTHER), cluster.Item("u", "", ())]
    clusters = cluster.exact_clusters(copies)
    stats = cluster.group_stats(copies, clusters)
    assert stats["n"] == 9
    assert stats["unparsed"] == 1
    assert stats["distinct"] == 2
    assert stats["exact_dup_share"] == round(7 / 9, 6)
    assert stats["top1_share"] == round(8 / 9, 6)
    assert stats["low_effective_n"] is True
    assert stats["chao1"] == 2 + 1 * 0 / 2  # one singleton, no doubletons

    diverse = [item("a", BASE), item("c", OTHER)]
    stats = cluster.group_stats(diverse, cluster.exact_clusters(diverse))
    assert stats["low_effective_n"] is False
    assert stats["entropy"] == 1.0


def test_empty_group_has_no_statistics():
    stats = cluster.group_stats([cluster.Item("u", "", ())], [])
    assert stats["n"] == 0
    assert stats["exact_dup_share"] is None
    assert stats["low_effective_n"] is False
