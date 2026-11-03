"""Normalizer invariance per language (spec 7.9 step 1; WP-13 tests).

Renamed locals, comments, whitespace and import order give an equal sha; a changed literal gives a different
sha; an unparsable variant gets ``sha=""`` with a reason (counted, never dropped).
"""

from __future__ import annotations

import pytest

from backend.generation import identity
from backend.generation.patterns import normalize as norm

PY_A = '''import os
from flask import request


def list_todos(user_id, limit=10):
    """List the todos of one user."""
    rows = db.execute(f"SELECT * FROM todos WHERE owner = {user_id}")  # the bug
    out = [row for row in rows]
    return out[:limit], os.sep, request.args.get("q", default="x")
'''

PY_B = """from flask import request
import os
def list_todos(uid,   n=10):
    # a different comment
    result = db.execute(f"SELECT * FROM todos WHERE owner = {uid}")
    acc = [r for r in result]


    return acc[:n], os.sep, request.args.get("q", default="x")
"""

JS_A = """import b from 'b';
import a from 'a';
// helper
export function f(x, { y }) {
  const z = x + y; /* inline */
  for (const item of items) { total += item; }
  return obj.z + z + `id-${x}`;
}
"""

JS_B = """import a from 'a'
import b from 'b'
export function f(p, { y }) {
  const q = p + y
  for (const it of items) { total += it }
  return obj.z + q + `id-${p}`
}
"""

TS_A = "function f(a: number, b?: string): number { let c = a; return c + (b ? 1 : 0); }"
TS_B = "function f(left: number, right?: string): number {\n  let acc = left\n  return acc + (right ? 1 : 0)\n}\n"

TSX_A = 'export const A = ({ p }) => { const [v, setV] = useState(p); return <div className="a">{v}</div>; };'
TSX_B = 'export const A = ({ p }) => {\n  const [w, setW] = useState(p)\n  return <div className="a">{w}</div>\n}\n'

CASES = [
    ("python", PY_A, PY_B),
    ("javascript", JS_A, JS_B),
    ("typescript", TS_A, TS_B),
    ("tsx", TSX_A, TSX_B),
]


@pytest.mark.parametrize(("language", "a", "b"), CASES, ids=[c[0] for c in CASES])
def test_renamed_locals_comments_whitespace_and_import_order_give_an_equal_sha(language, a, b):
    ra, rb = norm.normalized_sha(a, language), norm.normalized_sha(b, language)
    assert ra.ok, ra.reason
    assert ra.sha == rb.sha
    assert ra.sha == identity.text_sha(norm.normalize(a, language, norm.NORMALIZER_VERSION))


@pytest.mark.parametrize(
    ("language", "a", "changed"),
    [
        ("python", PY_A, PY_A.replace("SELECT *", "SELECT id")),
        ("javascript", JS_A, JS_A.replace("'a'", "'a2'")),
        ("typescript", TS_A, TS_A.replace("1 : 0", "2 : 0")),
        ("tsx", TSX_A, TSX_A.replace('"a"', '"b"')),
    ],
    ids=["python", "javascript", "typescript", "tsx"],
)
def test_a_changed_literal_gives_a_different_sha(language, a, changed):
    assert norm.normalized_sha(a, language).sha != norm.normalized_sha(changed, language).sha


def test_free_and_global_names_attributes_and_keywords_are_kept():
    tokens = norm.tokens(PY_A, "python")
    for kept in ("db", "execute", "request", "args", "get", "default", "os", "sep", "list_todos"):
        assert kept in tokens
    for renamed in ("user_id", "rows", "out", "limit", "row"):
        assert renamed not in tokens


def test_docstrings_are_dropped_but_other_strings_kept():
    tokens = norm.tokens(PY_A, "python")
    assert not any("List the todos" in t for t in tokens)
    assert '"q"' in tokens


def test_a_global_name_declared_in_a_function_is_not_renamed():
    code = "counter = 0\n\ndef bump():\n    global counter\n    counter = counter + 1\n"
    assert "counter" in norm.tokens(code, "python")[6:]


def test_unparsable_code_gets_an_empty_sha_with_a_reason():
    result = norm.normalized_sha("def broken(:\n    return\n", "python")
    assert result.sha == ""
    assert result.reason == "parse_error"
    assert norm.normalized_sha("   \n", "python").reason == "empty"
    with pytest.raises(norm.NormalizationError):
        norm.normalize("def broken(:\n", "python", 1)


def test_unknown_language_and_version_are_refused():
    assert norm.normalized_sha("x", "cobol").reason == "unsupported_language"
    with pytest.raises(ValueError, match="normalizer version"):
        norm.normalize("x = 1", "python", 99)


def test_vue_script_is_normalized_with_the_ts_rules():
    script = '<script setup lang="ts">\nconst a: number = 1'
    a = f'<template><div v-html="x">hi</div></template>\n{script} // c\n</script>\n'
    b = f'<template>\n  <div v-html="x">hi</div>\n</template>\n{script}\n</script>\n'
    assert norm.normalized_sha(a, "vue").sha == norm.normalized_sha(b, "vue").sha


def test_python_block_structure_survives():
    a = "if a:\n    x()\ny()\n"
    b = "if a:\n    x()\n    y()\n"
    assert norm.normalized_sha(a, "python").sha != norm.normalized_sha(b, "python").sha
