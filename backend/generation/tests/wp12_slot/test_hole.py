"""Hole modes per language; docstrings and in-span comments are stripped by default (spec section 7.4)."""

from __future__ import annotations

import textwrap

import pytest

from backend.generation import identity
from backend.generation.contracts.slot import SlotAddress
from backend.generation.slot.hole import apply_hole
from backend.generation.slot.index import build_from_files


def _address(path: str, source: bytes, symbol_id: str, kind: str = "symbol") -> SlotAddress:
    build = build_from_files({path: source})
    symbol = next(s for s in build.index.symbols if s.id == symbol_id)
    return SlotAddress(kind=kind, path=path, span=symbol.span, symbol_id=symbol.id, content_sha=symbol.content_sha)


PY = (
    textwrap.dedent(
        '''
    import os


    @app.get("/x")  # route
    # a comment between decorators
    @login_required
    def handler(item_id: int) -> dict:
        """Return the item."""
        # look it up
        value = os.environ.get("X")  # trailing
        return {"id": item_id, "v": value}


    def other():
        return 1
    ''',
    )
    .lstrip()
    .encode()
)


def test_python_stub_keeps_signature_and_decorators_and_strips_docstring_and_comments() -> None:
    address = _address("app.py", PY, "app.py::handler")
    result = apply_hole(PY, address, "stub")
    hole = result.hole.decode()
    assert hole.startswith('@app.get("/x")\n@login_required\ndef handler(item_id: int) -> dict:\n')
    assert "# __SLOT__\n    raise NotImplementedError" in hole
    for gone in ("Return the item", "look it up", "a comment between", "# route", "os.environ", "trailing"):
        assert gone not in hole
    # Everything outside the span is byte-identical.
    assert result.holed[: result.start] == PY[: address.span.start_byte]
    assert result.suffix == PY[address.span.end_byte :]
    assert b"def other():\n    return 1" in result.holed


def test_python_stub_keep_docstring() -> None:
    address = _address("app.py", PY, "app.py::handler")
    hole = apply_hole(PY, address, "stub", keep_docstring=True).hole.decode()
    assert '"""Return the item."""' in hole
    assert "# a comment between decorators" in hole
    assert hole.rstrip().endswith("raise NotImplementedError")


def test_python_one_line_body() -> None:
    source = b"def f(x): return x * 2\n"
    address = _address("f.py", source, "f.py::f")
    hole = apply_hole(source, address, "stub").hole.decode()
    assert hole == "def f(x):\n    # __SLOT__\n    raise NotImplementedError"


def test_remove_replaces_the_span_with_the_marker_and_rewrite_keeps_the_original() -> None:
    address = _address("app.py", PY, "app.py::handler")
    removed = apply_hole(PY, address, "remove")
    assert removed.hole == b"# __SLOT__"
    assert b"def handler" not in removed.holed
    rewrite = apply_hole(PY, address, "rewrite")
    assert rewrite.holed == PY
    assert rewrite.hole == PY[address.span.start_byte : address.span.end_byte]


@pytest.mark.parametrize(
    ("path", "source", "symbol_id", "placeholder"),
    [
        (
            "a.js",
            b"// header\nexport function load(id) {\n  // fetch it\n  return fetch(`/api/${id}`);\n}\n",
            "a.js::load",
            'throw new Error("not implemented");',
        ),
        (
            "a.ts",
            b"export const sum = (a: number, b: number): number => a + b;\n",
            "a.ts::sum",
            'throw new Error("not implemented");',
        ),
        (
            "c.tsx",
            b"export default function Card({ title }: { title: string }) {\n  return <h1>{title}</h1>;\n}\n",
            "c.tsx::Card",
            'throw new Error("not implemented");',
        ),
        (
            "main.go",
            b'package main\n\n// Get returns it.\nfunc Get(id int) string {\n\t// look up\n\treturn "x"\n}\n',
            "main.go::Get",
            'panic("not implemented")',
        ),
        (
            "T.java",
            (
                b"class T {\n"
                b"    /** doc */\n"
                b"    @Override\n"
                b"    public String name() {\n"
                b"        // x\n"
                b'        return "t";\n'
                b"    }\n"
                b"}\n"
            ),
            "T.java::T.name",
            'throw new UnsupportedOperationException("not implemented");',
        ),
    ],
)
def test_brace_language_stubs(path: str, source: bytes, symbol_id: str, placeholder: str) -> None:
    address = _address(path, source, symbol_id)
    result = apply_hole(source, address, "stub")
    hole = result.hole.decode()
    assert placeholder in hole
    assert "/* __SLOT__ */" in hole
    assert "fetch it" not in hole
    assert "look up" not in hole
    assert "return" not in hole
    assert result.holed.startswith(source[: address.span.start_byte])
    assert result.holed.endswith(source[address.span.end_byte :])


def test_java_annotation_kept_in_stub() -> None:
    source = b'class T {\n    @Override\n    public String name() {\n        return "t";\n    }\n}\n'
    address = _address("T.java", source, "T.java::T.name")
    hole = apply_hole(source, address, "stub").hole.decode()
    assert hole.startswith("    @Override\n    public String name() {")


def test_class_stub_stubs_every_method() -> None:
    source = (
        textwrap.dedent(
            '''
        class Service:
            """A service."""

            def a(self):
                return 1

            def b(self):
                return 2
        ''',
        )
        .lstrip()
        .encode()
    )
    address = _address("s.py", source, "s.py::Service")
    hole = apply_hole(source, address, "stub").hole.decode()
    assert hole.count("raise NotImplementedError") == 2
    assert "A service" not in hole
    assert "return 1" not in hole


def test_file_kind_remove_and_stub_and_region() -> None:
    sha = identity.text_sha(PY.decode())
    file_address = SlotAddress(kind="file", path="app.py", content_sha=sha)
    assert apply_hole(PY, file_address, "remove").removed
    stub = apply_hole(PY, file_address, "stub").holed.decode()
    assert stub.count("raise NotImplementedError") == 2
    assert "import os" in stub
    lines = PY.split(b"\n")
    start = len(b"\n".join(lines[:8])) + 1
    end = start + len(lines[8])
    from backend.generation.slot.index.treesitter import span_from_bytes

    region = SlotAddress(
        kind="region",
        path="app.py",
        span=span_from_bytes(PY, start, end),
        content_sha=identity.text_sha(PY[start:end].decode()),
    )
    stubbed = apply_hole(PY, region, "stub")
    assert stubbed.hole == b"    # __SLOT__"
    assert apply_hole(PY, region, "remove").hole == b""


def test_crlf_files_get_crlf_holes() -> None:
    source = b"def f():\r\n    return 1\r\n\r\ndef g():\r\n    return 2\r\n"
    address = _address("f.py", source, "f.py::f")
    result = apply_hole(source, address, "stub")
    assert b"\r\n    raise NotImplementedError" in result.hole
    assert b"\n" not in result.hole.replace(b"\r\n", b"")


def test_vue_component_stub_keeps_script_and_replaces_template() -> None:
    source = (
        b"<template>\n"
        b"  <p>{{ msg }}</p>\n"
        b"</template>\n"
        b"<script>\n"
        b"export default { data() { return { msg: 'x' }; } };\n"
        b"</script>\n"
    )
    address = SlotAddress(kind="component", path="Note.vue", content_sha=identity.text_sha(source.decode()))
    result = apply_hole(source, address, "stub")
    holed = result.holed.decode()
    assert "<!-- __SLOT__ -->" in holed
    assert "{{ msg }}" not in holed
    assert "export default" in holed
    assert apply_hole(source, address, "remove").removed
