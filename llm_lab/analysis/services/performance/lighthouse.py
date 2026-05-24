"""Lighthouse-based performance analyzer (live and code-heuristic modes)."""

from __future__ import annotations

import json
import logging
import re
import shutil
import subprocess
import tempfile
from pathlib import Path
from typing import Any
from typing import ClassVar

from llm_lab.analysis.services.base import AnalyzerOutput
from llm_lab.analysis.services.base import BaseAnalyzer
from llm_lab.analysis.services.base import ConfigField
from llm_lab.analysis.services.base import FindingData
from llm_lab.analysis.services.base import validate_target_url

from ._common import _BACKEND_DIRS
from ._common import _BACKEND_EXTS
from ._common import _CODE_SPLIT_MIN_SIZE
from ._common import _FRONTEND_DIRS
from ._common import _FRONTEND_EXTS
from ._common import _INLINE_BLOCK_THRESHOLD
from ._common import _SEVERITY_WEIGHTS
from ._common import _average_score_to_grade
from .parsers import parse_lighthouse_report

logger = logging.getLogger(__name__)


class LighthouseAnalyzer(BaseAnalyzer):
    name: ClassVar[str] = "lighthouse"
    analyzer_type: ClassVar[str] = "performance"
    display_name: ClassVar[str] = "Lighthouse Performance Auditor"
    description: ClassVar[str] = (
        "Analyzes web application performance, accessibility, best practices, and SEO using Google Lighthouse"
    )
    supports_live_target: ClassVar[bool] = True
    supported_code_types: ClassVar[list[str]] = ["frontend"]
    config_schema: ClassVar[list[ConfigField]] = [
        ConfigField(
            name="categories",
            type="multiselect",
            label="Audit categories",
            description="Which Lighthouse audit categories to run.",
            default=["performance", "accessibility", "best-practices", "seo"],
            options=[
                {"value": "performance", "label": "Performance"},
                {"value": "accessibility", "label": "Accessibility"},
                {"value": "best-practices", "label": "Best Practices"},
                {"value": "seo", "label": "SEO"},
            ],
        ),
        ConfigField(
            name="throttling",
            type="boolean",
            label="Apply network throttling",
            description="Simulate slow 4G network conditions during the audit.",
            default=True,
        ),
    ]

    def check_available(self) -> tuple[bool, str]:
        if shutil.which("npx"):
            return True, "Available via npx"
        if shutil.which("docker"):
            return True, "Available via Docker"
        return False, "Neither npx nor docker found on PATH"

    def analyze(
        self,
        code: dict[str, str],
        config: dict[str, Any] | None = None,
    ) -> AnalyzerOutput:
        config = config or {}
        target_url: str | None = config.get("target_url")
        is_live_task: bool = bool(config.get("live_target"))

        if target_url:
            return self._analyze_live(target_url, is_live_task=is_live_task)
        return self._analyze_code(code)

    # ------------------------------------------------------------------
    # Live Lighthouse run
    # ------------------------------------------------------------------

    def _analyze_live(
        self,
        target_url: str,
        *,
        is_live_task: bool = False,
    ) -> AnalyzerOutput:
        if is_live_task:
            from llm_lab.analysis.services.live_target import validate_live_target_url

            valid, err = validate_live_target_url(target_url)
        else:
            valid, err = validate_target_url(target_url)
        if not valid:
            return AnalyzerOutput(error=f"Invalid target URL: {err}")

        has_npx = shutil.which("npx") is not None
        has_docker = shutil.which("docker") is not None

        if not has_npx and not has_docker:
            return AnalyzerOutput(
                error=("Lighthouse is not available: neither npx nor docker found"),
            )

        try:
            with tempfile.TemporaryDirectory() as tmpdir:
                output_path = f"{tmpdir}/report.json"

                if has_npx:
                    cmd = [
                        "npx",
                        "lighthouse",
                        target_url,
                        "--output=json",
                        f"--output-path={output_path}",
                        "--chrome-flags=--headless --no-sandbox",
                        "--quiet",
                    ]
                else:
                    cmd = [
                        "docker",
                        "run",
                        "--rm",
                        "-v",
                        f"{tmpdir}:{tmpdir}",
                        "lighthouse",
                        target_url,
                        "--output=json",
                        f"--output-path={output_path}",
                    ]

                result = subprocess.run(  # noqa: S603
                    cmd,
                    capture_output=True,
                    text=True,
                    timeout=120,
                    check=False,
                )

                if result.returncode != 0:
                    logger.error(
                        "Lighthouse failed: %s",
                        result.stderr,
                    )
                    stderr = result.stderr[:500]
                    return AnalyzerOutput(
                        error=(f"Lighthouse exited with code {result.returncode}: {stderr}"),
                    )

                with Path(output_path).open() as f:
                    report = json.load(f)

        except subprocess.TimeoutExpired:
            return AnalyzerOutput(
                error="Lighthouse timed out after 120 seconds",
            )
        except (OSError, json.JSONDecodeError) as exc:
            logger.exception(
                "Failed to run or parse Lighthouse output",
            )
            return AnalyzerOutput(
                error=f"Lighthouse error: {exc}",
            )

        return parse_lighthouse_report(report)

    # ------------------------------------------------------------------
    # Code-based heuristic analysis
    # ------------------------------------------------------------------

    def _analyze_code(
        self,
        code: dict[str, str],
    ) -> AnalyzerOutput:
        findings: list[FindingData] = []

        for file_key, content in code.items():
            if self._is_frontend(file_key):
                findings.extend(
                    self._check_frontend(file_key, content),
                )
            if self._is_backend(file_key):
                findings.extend(
                    self._check_backend(file_key, content),
                )

        scores = self._estimate_scores(findings)
        avg = sum(scores.values()) / len(scores) if scores else 0.0

        summary: dict[str, Any] = {
            "category_scores": scores,
            "total_issues": len(findings),
            "average_score": round(avg, 2),
            "grade": _average_score_to_grade(avg),
            "analysis_type": "code-based",
        }

        return AnalyzerOutput(
            findings=findings,
            summary=summary,
            raw_output={
                "files_analyzed": list(code.keys()),
            },
        )

    @staticmethod
    def _is_frontend(file_key: str) -> bool:
        lower = file_key.lower()
        return any(lower.endswith(ext) for ext in _FRONTEND_EXTS) or any(d in lower for d in _FRONTEND_DIRS)

    @staticmethod
    def _is_backend(file_key: str) -> bool:
        lower = file_key.lower()
        return any(lower.endswith(ext) for ext in _BACKEND_EXTS) or any(d in lower for d in _BACKEND_DIRS)

    # -- Frontend checks ------------------------------------------------

    def _check_frontend(
        self,
        file_key: str,
        content: str,
    ) -> list[FindingData]:
        findings: list[FindingData] = []
        lines = content.splitlines()

        findings.extend(
            self._check_large_inline_blocks(
                file_key,
                content,
                lines,
            ),
        )
        findings.extend(
            self._check_missing_lazy_loading(
                file_key,
                lines,
            ),
        )
        findings.extend(
            self._check_code_splitting(file_key, content),
        )
        findings.extend(
            self._check_unoptimized_images(
                file_key,
                lines,
            ),
        )
        findings.extend(
            self._check_missing_meta_tags(
                file_key,
                content,
            ),
        )
        findings.extend(
            self._check_missing_accessibility(
                file_key,
                lines,
            ),
        )
        findings.extend(
            self._check_console_logs(file_key, lines),
        )
        findings.extend(
            self._check_sync_scripts(file_key, lines),
        )
        findings.extend(
            self._check_layout_shift_css(file_key, lines),
        )

        return findings

    def _check_large_inline_blocks(
        self,
        file_key: str,
        content: str,
        lines: list[str],
    ) -> list[FindingData]:
        findings: list[FindingData] = []

        for pattern, label in [
            (r"<script(?:\s[^>]*)?>(.+?)</script>", "script"),
            (r"<style(?:\s[^>]*)?>(.+?)</style>", "style"),
        ]:
            for match in re.finditer(
                pattern,
                content,
                re.DOTALL,
            ):
                inner = match.group(1)
                if len(inner) > _INLINE_BLOCK_THRESHOLD:
                    line_no = content[: match.start()].count("\n") + 1
                    findings.append(
                        FindingData(
                            severity="medium",
                            category="performance",
                            title=(f"Large inline {label} block ({len(inner)} chars)"),
                            description=(
                                f"Inline {label} with"
                                f" {len(inner)} characters."
                                " Large inline blocks delay"
                                " parsing and are not"
                                " cacheable."
                            ),
                            suggestion=(
                                f"Extract this {label} into an external file for better caching and parallel loading."
                            ),
                            file_path=file_key,
                            line_number=line_no,
                            code_snippet=inner[:120],
                            rule_id=(f"lighthouse/large-inline-{label}"),
                            confidence="high",
                        ),
                    )
        return findings

    @staticmethod
    def _check_missing_lazy_loading(
        file_key: str,
        lines: list[str],
    ) -> list[FindingData]:
        findings: list[FindingData] = []
        img_re = re.compile(
            r"<img\s[^>]*>",
            re.IGNORECASE,
        )
        loading_re = re.compile(
            r"loading\s*=",
            re.IGNORECASE,
        )
        for idx, line in enumerate(lines, 1):
            for match in img_re.finditer(line):
                tag = match.group(0)
                if not loading_re.search(tag):
                    findings.append(
                        FindingData(
                            severity="low",
                            category="performance",
                            title=("Image missing lazy loading attribute"),
                            description=('<img> tag without loading="lazy" delays offscreen image loading.'),
                            suggestion=('Add loading="lazy" to offscreen images.'),
                            file_path=file_key,
                            line_number=idx,
                            code_snippet=tag[:120],
                            rule_id=("lighthouse/missing-lazy-loading"),
                            confidence="medium",
                        ),
                    )
        return findings

    @staticmethod
    def _check_code_splitting(
        file_key: str,
        content: str,
    ) -> list[FindingData]:
        findings: list[FindingData] = []
        splitting_indicators = [
            r"import\s*\(",
            r"React\.lazy\s*\(",
            r"loadable\s*\(",
            r"defineAsyncComponent\s*\(",
            r"const\s+\w+\s*=\s*\(\)\s*=>\s*import\(",
        ]
        lower = file_key.lower()
        entry_kws = (
            "index",
            "main",
            "app",
            "entry",
            "layout",
            "root",
        )
        is_entry = any(kw in lower for kw in entry_kws)
        if not is_entry:
            return findings
        has_indicator = any(re.search(p, content) for p in splitting_indicators)
        if not has_indicator and len(content) > _CODE_SPLIT_MIN_SIZE:
            findings.append(
                FindingData(
                    severity="medium",
                    category="performance",
                    title=("No code-splitting detected in entry file"),
                    description=(
                        "Entry-point file has no dynamic"
                        " imports or code-splitting"
                        " patterns. This can lead to"
                        " large initial bundles."
                    ),
                    suggestion=("Use dynamic import() or framework-specific lazy loading to split code."),
                    file_path=file_key,
                    rule_id="lighthouse/no-code-splitting",
                    confidence="medium",
                ),
            )
        return findings

    @staticmethod
    def _check_unoptimized_images(
        file_key: str,
        lines: list[str],
    ) -> list[FindingData]:
        findings: list[FindingData] = []
        unoptimized_re = re.compile(
            r"(?:src|href)\s*=\s*[\"']"
            r"([^\"']+\.(?:bmp|tiff?|png|jpe?g))[\"']",
            re.IGNORECASE,
        )
        modern_fmts = (".webp", ".avif", ".svg")
        for idx, line in enumerate(lines, 1):
            for match in unoptimized_re.finditer(line):
                src = match.group(1)
                if any(src.lower().endswith(ext) for ext in modern_fmts):
                    continue
                findings.append(
                    FindingData(
                        severity="low",
                        category="performance",
                        title=("Potentially unoptimized image format"),
                        description=(f"Image '{src}' uses a traditional format. Modern formats reduce payload."),
                        suggestion=("Use WebP or AVIF formats with <picture> fallback for smaller file sizes."),
                        file_path=file_key,
                        line_number=idx,
                        code_snippet=line.strip()[:120],
                        rule_id="lighthouse/unoptimized-image",
                        confidence="low",
                    ),
                )
        return findings

    @staticmethod
    def _check_missing_meta_tags(
        file_key: str,
        content: str,
    ) -> list[FindingData]:
        findings: list[FindingData] = []
        if "<head" not in content.lower():
            return findings

        viewport_re = re.compile(
            r"<meta\s[^>]*name\s*=\s*[\"']viewport[\"']",
            re.IGNORECASE,
        )
        if not viewport_re.search(content):
            findings.append(
                FindingData(
                    severity="high",
                    category="seo",
                    title="Missing viewport meta tag",
                    description=("No viewport meta tag found. Mobile rendering will not be optimized."),
                    suggestion=('Add <meta name="viewport" content="width=device-width, initial-scale=1">.'),
                    file_path=file_key,
                    rule_id="lighthouse/missing-viewport",
                    confidence="high",
                ),
            )

        desc_re = re.compile(
            r"<meta\s[^>]*"
            r"name\s*=\s*[\"']description[\"']",
            re.IGNORECASE,
        )
        if not desc_re.search(content):
            findings.append(
                FindingData(
                    severity="medium",
                    category="seo",
                    title="Missing meta description",
                    description=("No meta description found. Search engines use this for result snippets."),
                    suggestion=('Add <meta name="description" content="..."> with a concise page summary.'),
                    file_path=file_key,
                    rule_id=("lighthouse/missing-meta-description"),
                    confidence="high",
                ),
            )

        title_re = re.compile(
            r"<title\b[^>]*>.+?</title>",
            re.IGNORECASE | re.DOTALL,
        )
        if not title_re.search(content):
            findings.append(
                FindingData(
                    severity="medium",
                    category="seo",
                    title="Missing or empty <title> tag",
                    description=("Page has no <title> element. Search engines rely on this for indexing."),
                    suggestion=("Add a descriptive <title> element inside <head>."),
                    file_path=file_key,
                    rule_id="lighthouse/missing-title",
                    confidence="high",
                ),
            )

        return findings

    @staticmethod
    def _check_missing_accessibility(
        file_key: str,
        lines: list[str],
    ) -> list[FindingData]:
        findings: list[FindingData] = []

        img_re = re.compile(
            r"<img\s[^>]*>",
            re.IGNORECASE,
        )
        alt_re = re.compile(r"alt\s*=", re.IGNORECASE)
        for idx, line in enumerate(lines, 1):
            for match in img_re.finditer(line):
                tag = match.group(0)
                if not alt_re.search(tag):
                    findings.append(
                        FindingData(
                            severity="high",
                            category="accessibility",
                            title=("Image missing alt attribute"),
                            description=("<img> tag without alt text is inaccessible to screen readers."),
                            suggestion=('Add a descriptive alt attribute, or alt="" for decorative images.'),
                            file_path=file_key,
                            line_number=idx,
                            code_snippet=tag[:120],
                            rule_id="lighthouse/missing-alt",
                            confidence="high",
                        ),
                    )

        interactive_re = re.compile(
            r"<(button|a|input|select|textarea)"
            r"\s[^>]*>",
            re.IGNORECASE,
        )
        aria_re = re.compile(
            r"aria-(?:label|labelledby|describedby)\s*=",
            re.IGNORECASE,
        )
        text_re = re.compile(r">([^<]+)<", re.IGNORECASE)
        form_els = ("input", "select", "textarea")
        for idx, line in enumerate(lines, 1):
            for match in interactive_re.finditer(line):
                tag = match.group(0)
                element = match.group(1).lower()
                has_aria = aria_re.search(tag)
                has_title = re.search(
                    r"title\s*=",
                    tag,
                    re.IGNORECASE,
                )
                after = line[match.end() :]
                has_text = text_re.search(after)
                if element in form_els:
                    has_id = re.search(
                        r"id\s*=\s*[\"']\w+[\"']",
                        tag,
                        re.IGNORECASE,
                    )
                    if not has_aria and not has_id:
                        findings.append(
                            FindingData(
                                severity="medium",
                                category="accessibility",
                                title=(f"Form element <{element}> may lack accessible label"),
                                description=(f"<{element}> without aria-label or associated <label> element."),
                                suggestion=(
                                    "Add aria-label or ensure a <label> with matching 'for' attribute exists."
                                ),
                                file_path=file_key,
                                line_number=idx,
                                code_snippet=tag[:120],
                                rule_id=("lighthouse/missing-form-label"),
                                confidence="medium",
                            ),
                        )
                elif element in ("button", "a") and not has_aria and not has_text and not has_title:
                    findings.append(
                        FindingData(
                            severity="medium",
                            category="accessibility",
                            title=(f"<{element}> may lack accessible name"),
                            description=(f"<{element}> without visible text, aria-label, or title attribute."),
                            suggestion=("Add text content, aria-label, or title to the element."),
                            file_path=file_key,
                            line_number=idx,
                            code_snippet=tag[:120],
                            rule_id=("lighthouse/missing-accessible-name"),
                            confidence="low",
                        ),
                    )

        return findings

    @staticmethod
    def _check_console_logs(
        file_key: str,
        lines: list[str],
    ) -> list[FindingData]:
        findings: list[FindingData] = []
        console_re = re.compile(
            r"\bconsole\."
            r"(log|debug|info|warn|error|trace)\s*\(",
        )
        for idx, line in enumerate(lines, 1):
            if console_re.search(line):
                findings.append(
                    FindingData(
                        severity="low",
                        category="best_practice",
                        title=("Console statement left in code"),
                        description=("console.log/debug statements should be removed from production code."),
                        suggestion=("Remove or replace with a proper logging framework."),
                        file_path=file_key,
                        line_number=idx,
                        code_snippet=line.strip()[:120],
                        rule_id="lighthouse/no-console",
                        confidence="high",
                    ),
                )
        return findings

    @staticmethod
    def _check_sync_scripts(
        file_key: str,
        lines: list[str],
    ) -> list[FindingData]:
        findings: list[FindingData] = []
        script_re = re.compile(
            r"<script\s[^>]*"
            r"src\s*=\s*[\"'][^\"']+[\"'][^>]*>",
            re.IGNORECASE,
        )
        async_defer_re = re.compile(
            r"\b(async|defer)\b",
            re.IGNORECASE,
        )
        module_re = re.compile(
            r"type\s*=\s*[\"']module[\"']",
            re.IGNORECASE,
        )
        for idx, line in enumerate(lines, 1):
            for match in script_re.finditer(line):
                tag = match.group(0)
                if not async_defer_re.search(tag) and not module_re.search(tag):
                    findings.append(
                        FindingData(
                            severity="medium",
                            category="performance",
                            title=("Synchronous script loading blocks rendering"),
                            description=(
                                'External <script> without async, defer, or type="module" blocks HTML parsing.'
                            ),
                            suggestion=('Add async or defer attribute, or use type="module".'),
                            file_path=file_key,
                            line_number=idx,
                            code_snippet=tag[:120],
                            rule_id="lighthouse/sync-script",
                            confidence="high",
                        ),
                    )
        return findings

    @staticmethod
    def _check_layout_shift_css(
        file_key: str,
        lines: list[str],
    ) -> list[FindingData]:
        findings: list[FindingData] = []

        media_re = re.compile(
            r"<(?:img|video|iframe|canvas)\s[^>]*>",
            re.IGNORECASE,
        )
        dims_re = re.compile(
            r"\b(?:width|height)\s*=",
            re.IGNORECASE,
        )
        for idx, line in enumerate(lines, 1):
            for match in media_re.finditer(line):
                tag = match.group(0)
                if not dims_re.search(tag):
                    findings.append(
                        FindingData(
                            severity="medium",
                            category="performance",
                            title=("Element without explicit dimensions may cause layout shift"),
                            description=("Media elements without width/height cause Cumulative Layout Shift (CLS)."),
                            suggestion=("Set explicit width and height attributes to reserve space."),
                            file_path=file_key,
                            line_number=idx,
                            code_snippet=tag[:120],
                            rule_id=("lighthouse/layout-shift-elements"),
                            confidence="medium",
                        ),
                    )

        font_face_re = re.compile(
            r"@font-face\s*\{",
            re.IGNORECASE,
        )
        font_display_re = re.compile(
            r"font-display\s*:",
            re.IGNORECASE,
        )
        for idx, line in enumerate(lines, 1):
            if font_face_re.search(line):
                block = "\n".join(
                    lines[idx - 1 : idx + 5],
                )
                if not font_display_re.search(block):
                    findings.append(
                        FindingData(
                            severity="low",
                            category="performance",
                            title=("@font-face without font-display property"),
                            description=(
                                "Custom font without font-display can cause invisible text during loading (FOIT)."
                            ),
                            suggestion=("Add font-display: swap (or optional) to your @font-face rule."),
                            file_path=file_key,
                            line_number=idx,
                            code_snippet=line.strip()[:120],
                            rule_id="lighthouse/font-display",
                            confidence="medium",
                        ),
                    )

        return findings

    # -- Backend checks -------------------------------------------------

    def _check_backend(
        self,
        file_key: str,
        content: str,
    ) -> list[FindingData]:
        findings: list[FindingData] = []
        lines = content.splitlines()

        findings.extend(
            self._check_n_plus_one(file_key, lines),
        )
        findings.extend(
            self._check_missing_pagination(
                file_key,
                content,
                lines,
            ),
        )
        findings.extend(
            self._check_no_caching(file_key, content),
        )
        findings.extend(
            self._check_large_payloads(
                file_key,
                content,
                lines,
            ),
        )
        findings.extend(
            self._check_missing_compression(
                file_key,
                content,
            ),
        )
        findings.extend(
            self._check_sync_blocking(file_key, lines),
        )

        return findings

    @staticmethod
    def _check_n_plus_one(
        file_key: str,
        lines: list[str],
    ) -> list[FindingData]:
        findings: list[FindingData] = []
        loop_re = re.compile(r"^\s*for\s+")
        db_call_re = re.compile(
            r"\.\s*(?:objects|filter|exclude|get|all"
            r"|select_related|prefetch_related|values"
            r"|annotate|aggregate|execute|fetchone"
            r"|fetchall|fetchmany|query|find|find_one"
            r"|find_many|select|where|join)\s*\(",
            re.IGNORECASE,
        )
        raw_sql_re = re.compile(
            r"(?:cursor\.execute"
            r"|connection\.execute"
            r"|session\.execute"
            r"|\.raw\s*\(|\.extra\s*\()",
            re.IGNORECASE,
        )

        in_loop = False
        loop_indent = 0
        loop_start = 0

        for idx, line in enumerate(lines, 1):
            stripped = line.lstrip()
            indent = len(line) - len(stripped)

            if loop_re.match(line):
                in_loop = True
                loop_indent = indent
                loop_start = idx
                continue

            if in_loop:
                skip_prefixes = ("#", "//", "/*")
                if (
                    stripped
                    and indent <= loop_indent
                    and not stripped.startswith(
                        skip_prefixes,
                    )
                ):
                    in_loop = False
                    continue

                if db_call_re.search(line) or raw_sql_re.search(line):
                    findings.append(
                        FindingData(
                            severity="high",
                            category="performance",
                            title=("Potential N+1 query pattern"),
                            description=(
                                "Database call inside loop"
                                f" (loop at line {loop_start}"
                                "). Each iteration may"
                                " trigger a separate query."
                            ),
                            suggestion=(
                                "Use select_related/"
                                "prefetch_related (Django),"
                                " eager loading, or batch"
                                " the query outside"
                                " the loop."
                            ),
                            file_path=file_key,
                            line_number=idx,
                            code_snippet=(line.strip()[:120]),
                            rule_id="lighthouse/n-plus-one",
                            confidence="medium",
                        ),
                    )

        return findings

    @staticmethod
    def _check_missing_pagination(
        file_key: str,
        content: str,
        lines: list[str],
    ) -> list[FindingData]:
        findings: list[FindingData] = []
        queryset_all_re = re.compile(
            r"\.\s*(?:objects\.all"
            r"|find\(\)"
            r"|select\s*\(\s*\))\s*(?:\)|$)",
            re.IGNORECASE,
        )
        pagination_re = re.compile(
            r"(?:paginate|pagination|page_size"
            r"|per_page|limit|offset"
            r"|Paginator|PageNumberPagination"
            r"|LimitOffsetPagination"
            r"|CursorPagination"
            r"|\.paginate_queryset"
            r"|\.limit\(|LIMIT\s+\d)",
            re.IGNORECASE,
        )

        if pagination_re.search(content):
            return findings

        for idx, line in enumerate(lines, 1):
            if queryset_all_re.search(line):
                findings.append(
                    FindingData(
                        severity="medium",
                        category="performance",
                        title=("Unbounded query without pagination"),
                        description=(
                            "Query returns all records with no pagination. Large tables will cause slow responses."
                        ),
                        suggestion=("Add pagination (e.g., Django Paginator, LIMIT/OFFSET) to bound result sets."),
                        file_path=file_key,
                        line_number=idx,
                        code_snippet=line.strip()[:120],
                        rule_id=("lighthouse/missing-pagination"),
                        confidence="medium",
                    ),
                )

        return findings

    @staticmethod
    def _check_no_caching(
        file_key: str,
        content: str,
    ) -> list[FindingData]:
        findings: list[FindingData] = []
        cache_re = re.compile(
            r"(?:cache|@cache_page|@cache_control"
            r"|@method_decorator\(cache"
            r"|from django\.core\.cache"
            r"|caches\[|redis|memcache"
            r"|Cache-Control|ETag|Last-Modified"
            r"|@cached_property|lru_cache"
            r"|functools\.cache|@memoize)",
            re.IGNORECASE,
        )
        view_re = re.compile(
            r"(?:def\s+(?:get|list|retrieve|index)\s*\("
            r"|class\s+\w+(?:View|ViewSet|APIView)"
            r"|@router\.get|@api_view"
            r"|@app\.(?:get|route))",
            re.IGNORECASE,
        )

        has_views = view_re.search(content)
        has_caching = cache_re.search(content)

        if has_views and not has_caching:
            findings.append(
                FindingData(
                    severity="low",
                    category="performance",
                    title=("No caching indicators in view module"),
                    description=("View/endpoint module has no cache headers, decorators, or cache backend usage."),
                    suggestion=(
                        "Consider adding cache_page,"
                        " cache_control, ETags, or"
                        " application-level caching"
                        " for read-heavy endpoints."
                    ),
                    file_path=file_key,
                    rule_id="lighthouse/no-caching",
                    confidence="low",
                ),
            )

        return findings

    @staticmethod
    def _check_large_payloads(
        file_key: str,
        content: str,
        lines: list[str],
    ) -> list[FindingData]:
        findings: list[FindingData] = []

        serialize_all_re = re.compile(
            r"(?:Serializer|Schema)\s*\("
            r"\s*\w+\.objects\.all\(\)",
            re.IGNORECASE,
        )
        json_dump_re = re.compile(
            r"(?:json\.dumps|JsonResponse|jsonify)"
            r"\s*\(\s*(?:list\(|dict\(|\[)",
        )
        fields_all_re = re.compile(
            r"fields\s*=\s*[\"']__all__[\"']",
        )

        for idx, line in enumerate(lines, 1):
            if serialize_all_re.search(line):
                findings.append(
                    FindingData(
                        severity="medium",
                        category="performance",
                        title=("Serializing entire queryset may produce large payload"),
                        description=(
                            "Serializing all objects"
                            " without field selection or"
                            " pagination can create very"
                            " large responses."
                        ),
                        suggestion=("Select only needed fields and paginate the queryset."),
                        file_path=file_key,
                        line_number=idx,
                        code_snippet=line.strip()[:120],
                        rule_id=("lighthouse/large-payload-serialize"),
                        confidence="medium",
                    ),
                )

        for idx, line in enumerate(lines, 1):
            if json_dump_re.search(line):
                findings.append(
                    FindingData(
                        severity="low",
                        category="performance",
                        title=("JSON serialization of potentially large data structure"),
                        description=("Direct JSON serialization without size limits may produce oversized responses."),
                        suggestion=("Consider streaming responses or limiting the data size."),
                        file_path=file_key,
                        line_number=idx,
                        code_snippet=line.strip()[:120],
                        rule_id=("lighthouse/large-payload-json"),
                        confidence="low",
                    ),
                )

        if fields_all_re.search(content):
            findings.append(
                FindingData(
                    severity="low",
                    category="performance",
                    title=('Serializer uses fields="__all__"'),
                    description=("Exposing all model fields may include unnecessary data in responses."),
                    suggestion=("Explicitly list only the required fields."),
                    file_path=file_key,
                    rule_id="lighthouse/fields-all",
                    confidence="medium",
                ),
            )

        return findings

    @staticmethod
    def _check_missing_compression(
        file_key: str,
        content: str,
    ) -> list[FindingData]:
        findings: list[FindingData] = []
        settings_indicators = (
            "MIDDLEWARE",
            "INSTALLED_APPS",
            "REST_FRAMEWORK",
        )
        is_settings = any(ind in content for ind in settings_indicators)
        if not is_settings:
            return findings

        compression_re = re.compile(
            r"(?:GZipMiddleware"
            r"|WhiteNoiseMiddleware"
            r"|BrotliMiddleware"
            r"|CompressionMiddleware"
            r"|django\.middleware\.gzip"
            r"|Content-Encoding"
            r"|compress)",
            re.IGNORECASE,
        )
        if not compression_re.search(content):
            findings.append(
                FindingData(
                    severity="medium",
                    category="performance",
                    title=("No compression middleware detected"),
                    description=("Settings file has no GZip or Brotli middleware. Responses will be uncompressed."),
                    suggestion=("Add GZipMiddleware or WhiteNoise compression to MIDDLEWARE."),
                    file_path=file_key,
                    rule_id=("lighthouse/missing-compression"),
                    confidence="medium",
                ),
            )

        return findings

    @staticmethod
    def _check_sync_blocking(
        file_key: str,
        lines: list[str],
    ) -> list[FindingData]:
        findings: list[FindingData] = []
        blocking_re = re.compile(
            r"(?:time\.sleep\s*\("
            r"|subprocess\."
            r"(?:run|call|check_output|Popen)\s*\("
            r"|urllib\.request\.urlopen\s*\("
            r"|requests\."
            r"(?:get|post|put|patch|delete|head)\s*\("
            r"|open\s*\([^)]*\)\.read\s*\()",
        )
        async_re = re.compile(
            r"(?:async\s+def|await\s"
            r"|aiohttp|httpx\.AsyncClient|asyncio)",
        )

        for idx, line in enumerate(lines, 1):
            if blocking_re.search(line) and not async_re.search(line):
                findings.append(
                    FindingData(
                        severity="low",
                        category="performance",
                        title=("Synchronous blocking call in request path"),
                        description=(
                            "Blocking I/O (sleep, subprocess, HTTP request) can degrade response time under load."
                        ),
                        suggestion=("Use async alternatives, background tasks (Celery), or connection pooling."),
                        file_path=file_key,
                        line_number=idx,
                        code_snippet=line.strip()[:120],
                        rule_id="lighthouse/sync-blocking",
                        confidence="low",
                    ),
                )

        return findings

    # -- Score estimation -----------------------------------------------

    @staticmethod
    def _estimate_scores(
        findings: list[FindingData],
    ) -> dict[str, float]:
        category_deductions: dict[str, float] = {
            "Performance": 0.0,
            "Accessibility": 0.0,
            "Best Practices": 0.0,
            "SEO": 0.0,
        }

        category_score_map: dict[str, str] = {
            "performance": "Performance",
            "accessibility": "Accessibility",
            "best_practice": "Best Practices",
            "seo": "SEO",
        }

        for finding in findings:
            score_key = category_score_map.get(
                finding.category,
            )
            if score_key:
                deduction = _SEVERITY_WEIGHTS.get(
                    finding.severity,
                    0.0,
                )
                category_deductions[score_key] += deduction

        return {cat: round(max(1.0 - ded, 0.0), 2) for cat, ded in category_deductions.items()}
