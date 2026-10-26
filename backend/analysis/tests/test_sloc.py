"""sloc-v1: one documented code-line definition per language family."""

from __future__ import annotations

import pytest

from backend.analysis.engine import sloc


def test_python_comments_blank_lines_and_strings():
    text = (
        "#!/usr/bin/env python\n"  # 1 shebang: comment
        "import os  # trailing comment is still code\n"  # 2
        "\n"  # 3
        "    # indented comment\n"  # 4
        "x = '# not a comment'\n"  # 5
        "y = '''\n"  # 6 multi-line string: code
        "\n"  # 7 blank inside a string: not counted
        "text'''\n"  # 8
    )
    assert sloc.code_lines(text, "python") == {2, 5, 6, 8}
    assert sloc.count(text, "python") == 4


def test_python_docstrings_are_comments():
    text = (
        '"""Module docstring\n'  # 1
        'spanning lines."""\n'  # 2
        "\n"
        "class A:\n"  # 4
        "    '''Class doc.'''\n"  # 5
        "\n"
        "    def f(self):\n"  # 7
        '        """Method doc."""\n'  # 8
        "        return 1\n"  # 9
        "\n"
        'def g(): "inline doc"\n'  # 11 the def keeps the line
        "\n"
        "def h():\n"  # 13
        '    x = """not a docstring"""\n'  # 14
        "    return x\n"  # 15
    )
    assert sloc.code_lines(text, "python") == {4, 7, 9, 11, 13, 14, 15}


def test_unparseable_python_falls_back_to_hash_rule():
    text = "def broken(:\n    '''unterminated\n# comment\nreturn 1\n"
    assert sloc.code_lines(text, "python") == {1, 2, 4}


def test_python_syntax_error_still_counts_without_docstring_skip():
    # Lexes fine but does not parse (no docstrings known): every non-comment line counts.
    text = '"""doc"""\nx = = 1\n# c\n'
    assert sloc.code_lines(text, "python") == {1, 2}


@pytest.mark.parametrize("language", ["shell", "yaml", "toml", "dockerfile"])
def test_hash_family(language):
    text = "# comment\nkey: value\n\n  # indented\nother = 1 # trailing\n"
    assert sloc.code_lines(text, language) == {2, 5}


def test_javascript_comments_strings_and_templates():
    text = (
        "// line comment\n"  # 1
        "const a = 1; // trailing\n"  # 2
        "/* block\n"  # 3
        "   still block */\n"  # 4
        "const url = 'http://example.com'; /* inline */\n"  # 5
        "const t = `multi\n"  # 6
        "// inside template is code\n"  # 7
        "`;\n"  # 8
        "/* a */ /* b */\n"  # 9 only comments
        'let s = "/* not a comment */";\n'  # 10
    )
    assert sloc.code_lines(text, "javascript") == {2, 5, 6, 7, 8, 10}


def test_jsx_comment_lines_and_apostrophes():
    text = (
        "export default function App() {\n"  # 1
        "  return (\n"  # 2
        "    <div>\n"  # 3
        "      {/* a JSX comment */}\n"  # 4 comment-only
        "      <p>Don't panic</p>\n"  # 5 apostrophe must not swallow later lines
        "      <span>ok</span>\n"  # 6
        "    </div>\n"  # 7
        "  );\n"  # 8
        "}\n"  # 9
    )
    assert sloc.code_lines(text, "jsx") == {1, 2, 3, 5, 6, 7, 8, 9}
    assert 4 not in sloc.code_lines(text, "tsx")
    # Plain JS has no JSX comment rule: a lone `{}` there is code.
    assert 4 in sloc.code_lines(text, "javascript")


def test_jsx_multiline_comment_and_closing_braces():
    text = "<div>\n  {/*\n    multi\n  */}\n  <b/>\n</div>\n"
    assert sloc.code_lines(text, "jsx") == {1, 5, 6}
    # A brace that closes a block is code even next to a comment.
    assert sloc.code_lines("if (a) {\n  b();\n} // done\n", "jsx") == {1, 2, 3}


def test_css_has_no_line_comments():
    text = "/* header */\na { background: url(http://x/y.png); }\n\n/* multi\nline */\n.b{}\n"
    assert sloc.code_lines(text, "css") == {2, 6}


def test_html_comments_and_inline_script_style():
    text = (
        "<!doctype html>\n"  # 1
        "<!-- comment -->\n"  # 2
        "<html>\n"  # 3
        "<!--\n"  # 4
        "  multi-line comment\n"  # 5
        "-->\n"  # 6
        "<script>\n"  # 7
        "  // js comment\n"  # 8
        "  const a = 1;\n"  # 9
        "</script>\n"  # 10
        "<style>\n"  # 11
        "  /* css comment */\n"  # 12
        "  body { color: red; }\n"  # 13
        "</style>\n"  # 14
        "<p>text <!-- trailing --></p>\n"  # 15
        "<script src='x.js'></script>\n"  # 16 opened and closed on one line
        "<div>after</div>\n"  # 17
    )
    assert sloc.code_lines(text, "html") == {1, 3, 7, 9, 10, 11, 13, 14, 15, 16, 17}


def test_vue_single_file_component():
    text = (
        "<template>\n"  # 1
        "  <!-- template comment -->\n"  # 2
        "  <div v-html='x'></div>\n"  # 3
        "</template>\n"  # 4
        "<script setup lang='ts'>\n"  # 5
        "// script comment\n"  # 6
        "const x = ref('');\n"  # 7
        "</script>\n"  # 8
    )
    assert sloc.code_lines(text, "vue") == {1, 3, 4, 5, 7, 8}
    assert sloc.count(text, "svelte") == 6


@pytest.mark.parametrize("language", ["json", "markdown", "text", "env", "other", "unknown"])
def test_non_code_languages_count_zero(language):
    assert sloc.count('{"a": 1}\n# heading\nKEY=value\n', language) == 0
    assert sloc.code_lines("x\n", language) == set()


def test_empty_text():
    assert sloc.count("", "python") == 0
    assert sloc.count("\n\n  \n", "javascript") == 0


def test_crlf_line_endings():
    assert sloc.code_lines("a = 1\r\n# c\r\n\r\nb = 2\r\n", "python") == {1, 4}
    assert sloc.code_lines("const a = 1;\r\n// c\r\n", "javascript") == {1}


@pytest.mark.parametrize(
    "line",
    [
        "export const BASE = RAW.replace(/\\/*$/, '');",  # trailing slashes: '\/*' is not a comment
        "const parts = s.split(/[-+/*]/);",  # '/*' inside a character class
        "const t = path.replace(/^\\/*/, '');",
        "if (/\\/\\*/.test(src)) skip();",
        "const quote = /'/g;",  # a quote in a regex opens no string
    ],
)
@pytest.mark.parametrize("language", ["javascript", "jsx", "typescript", "tsx"])
def test_regex_literals_never_open_comments(line, language):
    functions = "".join(f"function f{i}() {{\n  return {i};\n}}\n" for i in range(30))
    text = line + "\n" + functions
    assert sloc.count(text, language) == 91  # every physical line is code


def test_division_is_not_a_regex():
    text = (
        "const half = total / 2; /* note\n"  # 1 division, then a real block comment
        "   still a comment */\n"  # 2
        "i++ / 2; /* x\n"  # 3
        "*/\n"  # 4
        "const r = (a) / (b) / c;\n"  # 5
        "return (\n"  # 6
        "  <div>\n"  # 7
        "    </div> {/* c */}\n"  # 8 a JSX closing tag is no regex
        "  <Foo />\n"  # 9
        ");\n"  # 10
    )
    assert sloc.code_lines(text, "jsx") == {1, 3, 5, 6, 7, 8, 9, 10}
    assert sloc.code_lines("<script>\nconst x = s.replace(/\\/*$/, '');\nconst y = 1;\n</script>\n", "html") == {
        1,
        2,
        3,
        4,
    }


def test_lines_are_numbered_like_the_tools():
    """Only '\\n' ends a line (as for ruff/bandit/tokenize and the reviewer's numbered prompt)."""
    assert sloc.split_lines("a\nb\u2028c\x0cd\r\ne\n") == ["a", "b\u2028c\x0cd", "e"]
    assert sloc.split_lines("") == []
    assert sloc.split_lines("x") == ["x"]
    text = "import os\nSEP = '\u2028'\n\ndef f():\n    pass\n\x0c\ndef g():\n    return 1\n"
    assert sloc.code_lines(text, "python") == {1, 2, 4, 5, 7, 8}
    assert sloc.code_lines("a = 1\n\x0c\nb = 2\n", "python") == {1, 3}
