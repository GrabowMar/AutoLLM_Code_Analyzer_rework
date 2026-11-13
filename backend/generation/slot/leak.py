"""Leak scan: 8-token shingles of the original against the context and the workspace (spec section 7.4).

``LEAK_VERSION = 1``. The original span is normalised (comments stripped, whitespace dropped by the
tokenizer) into overlapping 8-token shingles, which are searched in

* every context file (the resolved context, slot file with the hole applied), scope ``context``;
* for restricted-agent executors, the whole agent workspace (the parent tree with the hole, minus tests
  unless ``include_tests``, minus artifact rows), scope ``workspace``.

``score`` is the share of the original's shingles found anywhere in scope; each match lists its path and
how many shingles it holds. A file holding at least 90% of the shingles as one contiguous run is a
``verbatim`` copy: issue ``slot_leak_verbatim`` (error, the target cannot be used). ``score > 0.3`` is
``slot_leak_high`` (warn). The slot file itself is always scanned with the original span removed, also under
``hole_mode=rewrite`` (showing the original there is the treatment, not a leak).
"""

from __future__ import annotations

import re
from collections.abc import Mapping
from typing import TYPE_CHECKING
from typing import Final

from backend.generation.contracts.issues import Issue
from backend.generation.contracts.issues import make_issue
from backend.generation.contracts.slot import LEAK_VERSION
from backend.generation.contracts.slot import ContextPolicy
from backend.generation.contracts.slot import LeakMatch
from backend.generation.contracts.slot import LeakReport
from backend.generation.slot.part import Part
from backend.generation.slot.part import part_of

if TYPE_CHECKING:
    from backend.generation.models import SlotTarget

__all__ = [
    "HIGH_LEAK_SCORE",
    "LEAK_VERSION",
    "SHINGLE",
    "VERBATIM_SHARE",
    "agent_executor",
    "blocks_use",
    "leak_issues",
    "scan",
    "score_texts",
    "shingles",
    "tokens",
]

SHINGLE: Final = 8
VERBATIM_SHARE: Final = 0.9
HIGH_LEAK_SCORE: Final = 0.3
_TOKEN_RE: Final = re.compile(r"\w+|[^\w\s]")
_HASH_COMMENT_LANGS: Final = frozenset({"python"})
_C_COMMENT_RE: Final = re.compile(r"//[^\n]*|/\*.*?\*/", re.S)
_HASH_COMMENT_RE: Final = re.compile(r"(?m)#[^\n]*")
_HTML_COMMENT_RE: Final = re.compile(r"<!--.*?-->", re.S)
_AGENT_EXECUTORS: Final = frozenset({"restricted_agent", "slot_agent"})


def agent_executor(executor: str | None) -> bool:
    return executor in _AGENT_EXECUTORS


def _strip_comments(text: str, language: str) -> str:
    text = _HTML_COMMENT_RE.sub(" ", text)
    if language in _HASH_COMMENT_LANGS:
        return _HASH_COMMENT_RE.sub(" ", text)
    return _C_COMMENT_RE.sub(" ", text)


def tokens(text: str, language: str) -> list[str]:
    """The normalised token stream (comments stripped, whitespace dropped)."""
    return _TOKEN_RE.findall(_strip_comments(text, language))


def shingles(stream: list[str]) -> list[str]:
    """Overlapping 8-token shingles (one shingle of the whole stream when it is shorter)."""
    if not stream:
        return []
    if len(stream) < SHINGLE:
        return ["\x1f".join(stream)]
    return ["\x1f".join(stream[i : i + SHINGLE]) for i in range(len(stream) - SHINGLE + 1)]


def _longest_run(original: list[str], positions: Mapping[str, list[int]]) -> int:
    """The longest run of consecutive original shingles found consecutively in the other file."""
    best = 0
    previous: dict[int, int] = {}
    for shingle in original:
        current: dict[int, int] = {}
        for position in positions.get(shingle, ()):
            current[position] = previous.get(position - 1, 0) + 1
            best = max(best, current[position])
        previous = current
    return best


def score_texts(original_text: str, language: str, others: Mapping[str, str]) -> LeakReport:
    """The leak report of ``original_text`` against ``{path: text}`` (scope left empty for the caller)."""
    original = shingles(tokens(original_text, language))
    if not original:
        return LeakReport(score=0.0, matches=[], scope=[], version=LEAK_VERSION)
    found: set[int] = set()
    matches: list[LeakMatch] = []
    for path in sorted(others):
        other = shingles(tokens(others[path], language))
        positions: dict[str, list[int]] = {}
        for index, shingle in enumerate(other):
            positions.setdefault(shingle, []).append(index)
        hits = {index for index, shingle in enumerate(original) if shingle in positions}
        if not hits:
            continue
        found |= hits
        verbatim = _longest_run(original, positions) >= VERBATIM_SHARE * len(original)
        matches.append(LeakMatch(path=path, shingles=len(hits), verbatim=verbatim))
    return LeakReport(score=round(len(found) / len(original), 6), matches=matches, scope=[], version=LEAK_VERSION)


def _workspace_texts(part: Part, include_tests: bool) -> dict[str, str]:
    """The agent workspace: the parent tree with the hole (span removed), minus tests unless included."""
    hole = part.hole("stub" if part.default_hole_mode == "rewrite" else None)
    texts: dict[str, str] = {}
    tree = part.parent.tree
    for path in tree.source_paths(include_tests=include_tests):
        if path not in part.parent.texts:
            continue
        if path == part.path:
            if hole.removed:
                continue
            texts[path] = hole.holed_text
        else:
            texts[path] = part.parent.texts[path]
    return texts


def scan(
    target: SlotTarget | Part,
    *,
    context_policy: ContextPolicy | None = None,
    executor: str | None = None,
    hole_mode: str | None = None,
) -> LeakReport:
    """The leak report of a target; a verbatim copy is ``slot_leak_verbatim``.

    ``executor=None`` (the target form) scans both scopes, ``single_shot`` the context only and
    ``restricted_agent`` the context and the workspace.
    """
    from backend.generation.slot.context import materialize

    part = target if isinstance(target, Part) else part_of(target)
    policy = context_policy or part.default_context_policy
    mode = hole_mode or part.default_hole_mode
    # The slot file never counts against itself: scan it with the span removed, also for rewrite.
    context = materialize(part, policy, hole_mode="stub" if mode == "rewrite" else mode)
    others = {f.path: f.text for f in context.files}
    scope: list[str] = ["context"]
    if executor is None or agent_executor(executor):
        others.update(_workspace_texts(part, policy.include_tests))
        scope.append("workspace")
    report = score_texts(part.original_text, part.language, others)
    return report.model_copy(update={"scope": scope})


def leak_issues(report: LeakReport | Mapping[str, object] | None, study_kind: str = "exploratory") -> list[Issue]:
    """``slot_leak_verbatim`` (error) and ``slot_leak_high`` (warn) for a report."""
    if report is None:
        return []
    parsed = report if isinstance(report, LeakReport) else LeakReport.model_validate(report)
    issues: list[Issue] = []
    verbatim = [match.path for match in parsed.matches if match.verbatim]
    if verbatim:
        issues.append(
            make_issue(
                "slot_leak_verbatim",
                study_kind,  # type: ignore[arg-type]
                message=f"the original part appears verbatim in {', '.join(verbatim)}",
                data={"paths": verbatim},
            ),
        )
    if parsed.score > HIGH_LEAK_SCORE:
        issues.append(
            make_issue(
                "slot_leak_high",
                study_kind,  # type: ignore[arg-type]
                message=f"{parsed.score:.0%} of the original's 8-token shingles appear elsewhere in scope",
                data={"score": parsed.score},
            ),
        )
    return issues


def blocks_use(report: LeakReport | Mapping[str, object] | None) -> bool:
    """True when the report has a verbatim match (the target is not usable in a study)."""
    return any(issue.code == "slot_leak_verbatim" for issue in leak_issues(report))
