"""Ground AI findings in the sample: exact / relocated / ungrounded / invalid_file / out_of_range.

A finding cites a file, a line range and up to three verbatim evidence
lines. Grounding checks, in order:

1. the file is in the sample, else ``invalid_file``;
2. ``1 <= start <= end <= len(file)``, else ``out_of_range``;
3. the whitespace-normalized evidence occurs within the cited range widened
   by ``window`` lines, then ``exact`` (lines kept as cited);
4. it occurs elsewhere in the file, then ``relocated`` (lines moved to the
   occurrence nearest the cited start);
5. otherwise ``ungrounded``.

Only exact and relocated findings can ever be counted. Matching is a
substring test over the file's non-blank lines with whitespace collapsed, so
re-indentation and a partial first/last line still match while paraphrase,
ellipses or invented code do not. Evidence too short to identify a location
(fewer than ``MIN_RELOCATE_CHARS`` significant characters, e.g. ``app.run``)
never grounds by relocation, and must match on identifier boundaries: a
fragment such as ``con`` or ``req`` cut out of ``conn`` / ``request`` occurs
almost anywhere, so it is not a quote of anything.

Line-number prefixes copied from what the reviewer was shown are tolerated
when every evidence line carries one and the evidence as written does not
match: the single-shot listing (``  12| x``), OpenCode's read tool
(``12: x``) and ``nl -ba`` / ``cat -n`` (``    12<TAB>x``, also with the tab
turned into spaces), so no engine or harness is graded on a copy format the
others are forgiven.

:func:`locate` also returns where the evidence itself sits (``evidence_line``
/ ``evidence_end_line``): consensus clusters on that point, not on a cited
range that may span a whole function or file.
"""

from __future__ import annotations

import bisect
import functools
import re
from typing import TYPE_CHECKING
from typing import NamedTuple

from backend.analysis.engine.sloc import split_lines

if TYPE_CHECKING:
    from backend.analysis.engine.types import Sample

STATUSES = ("exact", "relocated", "ungrounded", "invalid_file", "out_of_range")
GROUNDED = frozenset({"exact", "relocated"})
# Preference when several readings of the evidence (as written / prefixes stripped) ground differently.
_RANK = {"exact": 0, "relocated": 1, "ungrounded": 2}

# Minimum non-whitespace characters of evidence to ground at all, and to
# relocate (a short token occurs all over a file; moving a finding to the
# nearest "}" would invent a location).
MIN_EVIDENCE_CHARS = 3
MIN_RELOCATE_CHARS = 12

# A copied line-number prefix: the single-shot listing ("  12| "), OpenCode's
# read tool ("12: "), nl -ba / cat -n ("    12\t", or the tab as 2+ spaces).
_NUMBER_PREFIX_RE = re.compile(r"^\s*\d+(?:\| ?|: |\t| {2,})")
_WORD_RE = re.compile(r"\w")


class Grounding(NamedTuple):
    """Where a finding is: its status, its (possibly relocated) lines and the lines its evidence occupies."""

    status: str
    line: int | None
    end_line: int | None
    evidence_line: int | None = None  # None when the evidence was not found
    evidence_end_line: int | None = None


def empty_counts() -> dict[str, int]:
    return dict.fromkeys(STATUSES, 0)


def normalize_path(path: str, root: str | None = None) -> str:
    """A cited path as a sample path: posix separators, no leading ``./`` or ``/``.

    *root*: the absolute directory the reviewer saw the app in (the agent
    engine's ``/workspace``); a citation under it (``/workspace/app.py``, as
    the agent's own tools print paths) is taken relative to it.
    """
    p = (path or "").strip().replace("\\", "/")
    if root:
        prefix = "/" + root.strip("/") + "/"
        if p.startswith(prefix):
            p = p[len(prefix) :]
    while p.startswith("./"):
        p = p[2:]
    return p.lstrip("/")


def _collapse(text: str) -> str:
    return " ".join(text.split())


def _readings(evidence: str) -> list[list[str]]:
    """The evidence as written, then with copied line-number prefixes stripped (when every line has one).

    Each reading is a list of non-blank, whitespace-collapsed lines. A lone
    code line that happens to start with ``0|`` is code, not a prefix: the
    stripped reading exists only when every line carries a prefix, and it is
    only used when the evidence as written does not match.
    """
    nonblank = [line for line in split_lines(evidence or "") if line.strip()]
    readings = [nonblank]
    if nonblank and all(_NUMBER_PREFIX_RE.match(line) for line in nonblank):
        readings.append([_NUMBER_PREFIX_RE.sub("", line, count=1) for line in nonblank])
    return [[c for c in (_collapse(line) for line in reading) if c] for reading in readings]


def evidence_lines(evidence: str) -> list[str]:
    """Non-blank evidence lines, whitespace-collapsed, copied line numbers stripped (when every line has one)."""
    return _readings(evidence)[-1]


@functools.lru_cache(maxsize=128)
def _file_index(content: bytes) -> tuple[int, tuple[int, ...], tuple[int, ...], str]:
    """(line count, line numbers of non-blank lines, their offsets in ``joined``, joined text)."""
    lines = split_lines(content.decode("utf-8", errors="replace"))
    numbers: list[int] = []
    offsets: list[int] = []
    parts: list[str] = []
    pos = 0
    for number, line in enumerate(lines, start=1):
        collapsed = _collapse(line)
        if not collapsed:
            continue
        numbers.append(number)
        offsets.append(pos)
        parts.append(collapsed)
        pos += len(collapsed) + 1  # the joining "\n"
    return len(lines), tuple(numbers), tuple(offsets), "\n".join(parts)


def _cuts_a_word(joined: str, needle: str, start: int) -> bool:
    """Whether the occurrence at *start* begins or ends inside an identifier (``con`` in ``conn``)."""
    end = start + len(needle)
    if _WORD_RE.match(needle[0]) and start > 0 and _WORD_RE.match(joined[start - 1]):
        return True
    return bool(_WORD_RE.match(needle[-1]) and end < len(joined) and _WORD_RE.match(joined[end]))


def _occurrences(content: bytes, needle: str, *, whole_words: bool = False) -> list[tuple[int, int]]:
    """Every (first line, last line) where *needle* occurs in the collapsed file text.

    *whole_words*: skip occurrences that start or end inside an identifier.
    """
    _, numbers, offsets, joined = _file_index(content)
    found: list[tuple[int, int]] = []
    start = joined.find(needle)
    while start != -1:
        if not (whole_words and _cuts_a_word(joined, needle, start)):
            first = bisect.bisect_right(offsets, start) - 1
            last = bisect.bisect_right(offsets, start + len(needle) - 1) - 1
            found.append((numbers[first], numbers[last]))
        start = joined.find(needle, start + 1)
    return found


def line_count(content: bytes) -> int:
    return _file_index(content)[0]


def ground(
    *,
    file_path: str,
    start_line: int | None,
    end_line: int | None,
    evidence: str,
    sample: Sample,
    window: int = 3,
    root: str | None = None,
) -> tuple[str, int | None, int | None]:
    """(status, line, end_line) — line numbers moved when relocated."""
    found = locate(
        file_path=file_path,
        start_line=start_line,
        end_line=end_line,
        evidence=evidence,
        sample=sample,
        window=window,
        root=root,
    )
    return found.status, found.line, found.end_line


def locate(
    *,
    file_path: str,
    start_line: int | None,
    end_line: int | None,
    evidence: str,
    sample: Sample,
    window: int = 3,
    root: str | None = None,
) -> Grounding:
    """Grounding status, the finding's lines (moved when relocated) and the lines the evidence occupies."""
    f = sample.by_path().get(normalize_path(file_path, root))
    if f is None:
        return Grounding("invalid_file", start_line, end_line)
    end = start_line if end_line is None else end_line
    total = line_count(f.content)
    if start_line is None or not 1 <= start_line <= end <= total:
        return Grounding("out_of_range", start_line, end_line)

    best = Grounding("ungrounded", start_line, end)
    for lines in _readings(evidence):
        found = _match(f.content, lines, start_line, end, window)
        if _RANK[found.status] < _RANK[best.status]:
            best = found
        if best.status == "exact":
            break
    return best


def _match(content: bytes, lines: list[str], start: int, end: int, window: int) -> Grounding:
    needle = "\n".join(lines)
    significant = len(needle.replace(" ", "").replace("\n", ""))
    if significant < MIN_EVIDENCE_CHARS:
        return Grounding("ungrounded", start, end)
    short = significant < MIN_RELOCATE_CHARS
    occurrences = _occurrences(content, needle, whole_words=short)
    lo, hi = start - window, end + window
    inside = [(first, last) for first, last in occurrences if lo <= first and last <= hi]
    if inside:
        first, last = _nearest(inside, start)
        return Grounding("exact", start, end, first, last)
    if occurrences and not short:
        first, last = _nearest(occurrences, start)
        return Grounding("relocated", first, last, first, last)
    return Grounding("ungrounded", start, end)


def _nearest(occurrences: list[tuple[int, int]], line: int) -> tuple[int, int]:
    """The occurrence nearest *line*; the earlier one on a tie (deterministic)."""
    return min(occurrences, key=lambda occ: (abs(occ[0] - line), occ[0]))
