r"""Source-lines-of-code counter, one definition for the whole platform (``sloc-v1``).

A *code line* is a line holding at least one non-whitespace character that is
not part of a comment. Blank lines and comment-only lines never count. SLOC is
the density denominator of every research outcome, so the rules are fixed per
language family and versioned (``types.SLOC_VERSION``):

``#`` family: python, shell, yaml, toml, dockerfile
    A line whose first non-blank character is ``#`` is a comment (shebangs
    included). Python is tokenized rather than scanned, so a ``#`` inside a
    string is code and the non-blank lines of a multi-line string are code.
    **Python docstrings are comments**: the string literal opening a module,
    class or (async) function body documents the code the way a comment
    does, so its lines do not count (a line that also holds code, such as
    ``def f(): "doc"``, still counts). Python the lexer cannot read falls
    back to the plain ``#`` rule.

C family: javascript, jsx, typescript, tsx
    ``//`` line comments and ``/* ... */`` block comments. ``'``/``"``
    strings end at the line end; template literals may span lines (their
    non-blank lines are code). A JSX comment line ``{/* ... */}`` is a
    comment line. Regex literals are recognised where an expression may
    start (line start, after an operator or punctuator other than ``)``,
    ``]``, ``}``, ``<``, ``++``/``--``, or after ``return``/``typeof``/...),
    so the ``/*`` of ``s.replace(/\/*$/, "")`` or ``/[+/*]/`` is regex body,
    not a comment; a regex literal never spans lines.

css
    ``/* ... */`` only: ``//`` is not a comment in CSS (``url(http://..)``).

Markup family: html, vue, svelte
    ``<!-- ... -->`` comments; inside ``<script>`` blocks the C-family rules
    apply and inside ``<style>`` blocks the css rules, so a single-file
    component (or a page with an inline script) is counted section by section.

Every other language (json, markdown, text, env, other) is not code and
:func:`count` returns 0 for it.

Lines are numbered the way the static tools and the AI reviewer number them
(:func:`split_lines`): split on ``\n`` only, a trailing ``\r`` dropped. A
form feed, ``\x1c``-``\x1e``, ``\x85`` or U+2028/U+2029 inside a line does
not start a new one (``str.splitlines`` would, shifting every later line).
"""

from __future__ import annotations

import ast
import io
import re
import token
import tokenize

HASH_LANGUAGES = frozenset({"python", "shell", "yaml", "toml", "dockerfile"})
C_LANGUAGES = frozenset({"javascript", "jsx", "typescript", "tsx"})
CSS_LANGUAGES = frozenset({"css"})
MARKUP_LANGUAGES = frozenset({"html", "vue", "svelte"})
CODE_LANGUAGES = HASH_LANGUAGES | C_LANGUAGES | CSS_LANGUAGES | MARKUP_LANGUAGES

_NON_CODE_TOKENS = frozenset(
    {
        token.COMMENT,
        token.NL,
        token.NEWLINE,
        token.INDENT,
        token.DEDENT,
        token.ENCODING,
        token.ENDMARKER,
    },
)
_JSX_COMMENT_RE = re.compile(r"\{\s*/\*.*\*/\s*\}")
_JSX_COMMENT_OPEN_RE = re.compile(r"\{\s*/\*")
_JSX_COMMENT_CLOSE_RE = re.compile(r"\*/\s*\}$")
_SECTION_OPEN_RE = re.compile(r"<(script|style)\b[^>]*>", re.IGNORECASE)
_SECTION_CLOSE_RE = re.compile(r"</(script|style)\s*>", re.IGNORECASE)
# A '/' after one of these characters starts a regex literal, not a division.
# Not after ')' ']' '}' (an expression just ended), '<' (a JSX closing tag
# ``</div>``) or an identifier/number/string.
_REGEX_PRECEDERS = frozenset("(,=:[!&|?{};+-*%~^>")
# Keywords after which an expression (so a regex literal) may start.
_REGEX_KEYWORDS = frozenset(
    {
        "return",
        "typeof",
        "case",
        "do",
        "else",
        "in",
        "of",
        "new",
        "delete",
        "void",
        "throw",
        "instanceof",
        "yield",
        "await",
    },
)
_TRAILING_WORD_RE = re.compile(r"[A-Za-z_$][\w$]*$")


def split_lines(text: str) -> list[str]:
    r"""File lines as editors, the static tools and the AI reviewer number them.

    Split on ``\n`` only, a trailing ``\r`` dropped (CRLF files). Unlike
    ``str.splitlines`` a form feed, ``\x1c``-``\x1e``, ``\x85`` or U+2028/2029
    inside a line does not start a new line, so line N here is line N in a
    ruff/bandit/semgrep report and in the reviewer's numbered prompt.
    """
    if not text:
        return []
    lines = text.split("\n")
    if lines[-1] == "":
        lines.pop()
    return [line[:-1] if line.endswith("\r") else line for line in lines]


def count(text: str, language: str) -> int:
    """Non-blank, non-comment lines (0 for non-code languages)."""
    return len(code_lines(text, language))


def code_lines(text: str, language: str) -> set[int]:
    """1-based numbers of the lines that :func:`count` counts."""
    if not text or language not in CODE_LANGUAGES:
        return set()
    lines = split_lines(text)
    if language == "python":
        return _python_code_lines(text, lines)
    if language in HASH_LANGUAGES:
        return _hash_code_lines(lines)
    if language in C_LANGUAGES:
        return _CScanner(line_comments=True, jsx=language in ("jsx", "tsx")).scan(lines)
    if language in CSS_LANGUAGES:
        return _CScanner(line_comments=False).scan(lines)
    return _markup_code_lines(lines)


# --------------------------------------------------------------------------
# '#' family
# --------------------------------------------------------------------------


def _hash_code_lines(lines: list[str]) -> set[int]:
    return {i for i, line in enumerate(lines, 1) if (s := line.strip()) and not s.startswith("#")}


def _python_code_lines(text: str, lines: list[str]) -> set[int]:
    try:
        tokens = list(tokenize.generate_tokens(io.StringIO(text).readline))
    except (tokenize.TokenError, SyntaxError, ValueError):
        # Unterminated strings or inconsistent dedents: the lexer cannot
        # classify the file, so the plain '#' rule keeps the count defined.
        return _hash_code_lines(lines)
    docstring_lines = _python_docstring_lines(text)
    covered: set[int] = set()
    for tok in tokens:
        if tok.type in _NON_CODE_TOKENS:
            continue
        if tok.type == token.STRING and tok.start[0] in docstring_lines and tok.end[0] in docstring_lines:
            continue
        covered.update(range(tok.start[0], tok.end[0] + 1))
    return {i for i in covered if 1 <= i <= len(lines) and lines[i - 1].strip()}


def _python_docstring_lines(text: str) -> set[int]:
    """Lines spanned by module/class/function docstring statements."""
    try:
        tree = ast.parse(text)
    except (SyntaxError, ValueError, RecursionError):
        return set()
    out: set[int] = set()
    for node in ast.walk(tree):
        if not isinstance(node, (ast.Module, ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        first = node.body[0] if node.body else None
        if (
            isinstance(first, ast.Expr)
            and isinstance(first.value, ast.Constant)
            and isinstance(first.value.value, str)
            and first.end_lineno is not None
        ):
            # Only the docstring's STRING tokens are skipped, so a code token
            # on the same line (``def f(): "doc"``) still counts the line.
            out.update(range(first.lineno, first.end_lineno + 1))
    return out


# --------------------------------------------------------------------------
# C family + css
# --------------------------------------------------------------------------


class _CScanner:
    """Character scanner for ``//`` + ``/* */`` comments and string literals.

    Carried across lines: an open block comment and an open template literal.
    Quote strings end at the line end (JS strings cannot span lines; this also
    contains the damage of an apostrophe in JSX text).
    """

    def __init__(self, *, line_comments: bool, jsx: bool = False) -> None:
        self.line_comments = line_comments
        # Regex literals exist in the JS family only (css has no line comments either).
        self.regex = line_comments
        self.jsx = jsx
        self.in_block = False
        self.in_template = False

    def scan(self, lines: list[str]) -> set[int]:
        return {i for i, line in enumerate(lines, 1) if self.scan_line(line)}

    def scan_line(self, line: str) -> bool:
        """True when *line* holds code; updates the cross-line state."""
        started_in_block = self.in_block
        code: list[str] = []
        quote = ""
        i = 0
        n = len(line)
        while i < n:
            ch = line[i]
            nxt = line[i + 1] if i + 1 < n else ""
            if self.in_block:
                if ch == "*" and nxt == "/":
                    self.in_block = False
                    i += 2
                else:
                    i += 1
                continue
            if self.in_template or quote:
                code.append(ch)
                if ch == "\\":
                    i += 2
                    continue
                if self.in_template and ch == "`":
                    self.in_template = False
                elif quote and ch == quote:
                    quote = ""
                i += 1
                continue
            if ch == "/" and nxt == "*":
                self.in_block = True
                i += 2
                continue
            if self.line_comments and ch == "/" and nxt == "/":
                break
            if self.regex and ch == "/" and _regex_may_start(code):
                end = _regex_end(line, i)
                if end is not None:
                    code.append(line[i:end])
                    i = end
                    continue
            if ch in ("'", '"'):
                quote = ch
            elif ch == "`" and self.line_comments:
                self.in_template = True
            code.append(ch)
            i += 1
        rest = "".join(code).strip()
        if self.jsx and rest in ("{}", "{", "}") and self._jsx_comment_braces(line.strip(), rest, started_in_block):
            return False
        return bool(rest)

    def _jsx_comment_braces(self, stripped: str, rest: str, started_in_block: bool) -> bool:
        """The braces only delimit a JSX comment: ``{/* c */}``, or its multi-line ends."""
        if rest == "{}":
            return bool(_JSX_COMMENT_RE.fullmatch(stripped))
        if rest == "{":
            return self.in_block and bool(_JSX_COMMENT_OPEN_RE.match(stripped))
        return started_in_block and bool(_JSX_COMMENT_CLOSE_RE.search(stripped))


def _regex_may_start(code: list[str]) -> bool:
    """Whether a '/' after the code scanned so far on this line opens a regex literal.

    The usual tokenizer heuristic: a regex literal stands where an expression
    starts (line start, after an operator/punctuator or an expression
    keyword); after an operand ('a / b', 'f(x) / 2', 'i++ / 2') it is division.
    """
    before = "".join(code).rstrip()
    if not before:
        return True
    if before.endswith(("++", "--")):
        return False
    if before[-1] in _REGEX_PRECEDERS:
        return True
    word = _TRAILING_WORD_RE.search(before)
    return bool(word) and word.group(0) in _REGEX_KEYWORDS


def _regex_end(line: str, start: int) -> int | None:
    r"""Index just past the regex literal opening at *start* (flags included), or None.

    Escapes and ``[...]`` classes are honoured, so ``/[/*]/`` and ``/\/*$/``
    end at their real closing slash. A literal that does not close on its
    line is not a regex (a division spanning lines, or broken code).
    """
    i = start + 1
    n = len(line)
    in_class = False
    while i < n:
        ch = line[i]
        if ch == "\\":
            i += 2
            continue
        if in_class:
            if ch == "]":
                in_class = False
        elif ch == "[":
            in_class = True
        elif ch == "/":
            i += 1
            while i < n and (line[i].isalnum() or line[i] in "_$"):
                i += 1
            return i
        i += 1
    return None


# --------------------------------------------------------------------------
# Markup family (html, vue, svelte)
# --------------------------------------------------------------------------


def _markup_code_lines(lines: list[str]) -> set[int]:
    out: set[int] = set()
    section = ""  # "", "script" or "style"
    scanner: _CScanner | None = None
    in_comment = False
    for i, line in enumerate(lines, 1):
        if section and scanner is not None:
            close = _SECTION_CLOSE_RE.search(line)
            if close and close.group(1).lower() == section:
                # The closing tag itself is markup, so the line counts.
                out.add(i)
                section = ""
                scanner = None
                continue
            if scanner.scan_line(line):
                out.add(i)
            continue
        has_code, in_comment = _markup_line(line, in_comment)
        if has_code:
            out.add(i)
        if not in_comment:
            section = _open_section(line)
            if section:
                scanner = _CScanner(line_comments=section == "script", jsx=section == "script")
    return out


def _markup_line(line: str, in_comment: bool) -> tuple[bool, bool]:
    """(line has markup outside ``<!-- -->``, comment still open at line end)."""
    rest: list[str] = []
    i = 0
    while i < len(line):
        if in_comment:
            end = line.find("-->", i)
            if end < 0:
                return bool("".join(rest).strip()), True
            in_comment = False
            i = end + 3
            continue
        start = line.find("<!--", i)
        if start < 0:
            rest.append(line[i:])
            break
        rest.append(line[i:start])
        in_comment = True
        i = start + 4
    return bool("".join(rest).strip()), in_comment


def _open_section(line: str) -> str:
    """The section a markup line leaves open: ``<script ...>`` without its closing tag."""
    pos = 0
    while True:
        m = _SECTION_OPEN_RE.search(line, pos)
        if not m:
            return ""
        name = m.group(1).lower()
        close = re.compile(rf"</{name}\s*>", re.IGNORECASE).search(line, m.end())
        if not close:
            return name
        pos = close.end()
