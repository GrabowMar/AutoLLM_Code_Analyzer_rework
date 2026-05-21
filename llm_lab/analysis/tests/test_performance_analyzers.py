"""Tests for performance analyzers — Lighthouse."""

from __future__ import annotations

from unittest.mock import patch

from llm_lab.analysis.services.performance_analyzers import LighthouseAnalyzer
from llm_lab.analysis.services.performance_analyzers import _average_score_to_grade
from llm_lab.analysis.services.performance_analyzers import _score_to_severity

EXPECTED_CONSOLE_FINDINGS = 2
EXPECTED_SHIFT_FINDINGS = 2


# -- Helper functions --------------------------------------------------


class TestScoreToSeverity:
    def test_score_to_severity_thresholds(self):
        assert _score_to_severity(0.3) == "high"
        assert _score_to_severity(0.6) == "medium"
        assert _score_to_severity(0.8) == "low"
        assert _score_to_severity(0.95) == "info"
        assert _score_to_severity(None) == "info"
        # Boundary: exactly at thresholds
        assert _score_to_severity(0.5) == "medium"
        assert _score_to_severity(0.75) == "low"
        assert _score_to_severity(0.9) == "info"


class TestAverageScoreToGrade:
    def test_average_score_to_grade(self):
        assert _average_score_to_grade(0.95) == "A"
        assert _average_score_to_grade(0.90) == "A"
        assert _average_score_to_grade(0.85) == "B"
        assert _average_score_to_grade(0.80) == "B"
        assert _average_score_to_grade(0.75) == "C"
        assert _average_score_to_grade(0.70) == "C"
        assert _average_score_to_grade(0.55) == "D"
        assert _average_score_to_grade(0.50) == "D"
        assert _average_score_to_grade(0.40) == "F"
        assert _average_score_to_grade(0.0) == "F"


# -- LighthouseAnalyzer static mode -----------------------------------


class TestLighthouseStaticMode:
    def test_lighthouse_static_mode(self):
        """Large inline style and blocking script produce findings."""
        analyzer = LighthouseAnalyzer()
        large_style = "body { color: red; }" * 50  # >500 chars
        html = (
            "<html><head>"
            f"<style>{large_style}</style>"
            '<script src="app.js"></script>'
            "</head><body>"
            '<img src="photo.png">'
            "</body></html>"
        )
        code = {"frontend/index.html": html}
        output = analyzer.analyze(code)

        assert not output.has_error
        assert len(output.findings) >= 1
        rule_ids = [f.rule_id for f in output.findings]
        assert any("large-inline" in r for r in rule_ids)
        assert any("sync-script" in r for r in rule_ids)
        assert output.summary["analysis_type"] == "code-based"

    def test_lighthouse_no_frontend_code(self):
        """Backend-only code still gets a grade and summary."""
        analyzer = LighthouseAnalyzer()
        code = {
            "backend/views.py": ("def index(request):\n    return HttpResponse('ok')\n"),
        }
        output = analyzer.analyze(code)

        assert not output.has_error
        assert "grade" in output.summary
        assert output.summary["analysis_type"] == "code-based"

    @patch("shutil.which", return_value=None)
    def test_lighthouse_check_available_no_docker(self, mock_which):
        analyzer = LighthouseAnalyzer()
        available, msg = analyzer.check_available()

        assert available is False
        assert "npx" in msg.lower() or "docker" in msg.lower()

    def test_lighthouse_live_rejects_blocked_url(self):
        analyzer = LighthouseAnalyzer()
        output = analyzer.analyze(
            {},
            config={"target_url": "http://127.0.0.1:3000"},
        )

        assert output.has_error
        assert "Blocked hostname" in output.error or "Invalid target URL" in output.error

    def test_lighthouse_missing_meta_tags(self):
        """Missing viewport/description/title produce SEO findings."""
        analyzer = LighthouseAnalyzer()
        html = "<html><head></head><body><p>Hello</p></body></html>"
        code = {"frontend/index.html": html}
        output = analyzer.analyze(code)

        assert not output.has_error
        rule_ids = [f.rule_id for f in output.findings]
        assert "lighthouse/missing-viewport" in rule_ids
        assert "lighthouse/missing-meta-description" in rule_ids
        assert "lighthouse/missing-title" in rule_ids

    def test_lighthouse_missing_lazy_loading(self):
        """Images without loading=lazy should be flagged."""
        analyzer = LighthouseAnalyzer()
        html = '<img src="photo.jpg"><img src="other.jpg" loading="lazy">'
        code = {"frontend/page.html": html}
        output = analyzer.analyze(code)

        assert not output.has_error
        lazy_findings = [f for f in output.findings if f.rule_id == "lighthouse/missing-lazy-loading"]
        assert len(lazy_findings) == 1

    def test_lighthouse_missing_alt_attribute(self):
        """Images without alt flagged for accessibility."""
        analyzer = LighthouseAnalyzer()
        html = '<img src="photo.jpg"><img src="other.jpg" alt="description">'
        code = {"frontend/page.html": html}
        output = analyzer.analyze(code)

        assert not output.has_error
        alt_findings = [f for f in output.findings if f.rule_id == "lighthouse/missing-alt"]
        assert len(alt_findings) == 1

    def test_lighthouse_console_logs(self):
        """Console statements flagged in frontend code."""
        analyzer = LighthouseAnalyzer()
        code = {
            "frontend/app.js": ("console.log('debug');\nconsole.error('oops');"),
        }
        output = analyzer.analyze(code)

        assert not output.has_error
        console_findings = [f for f in output.findings if f.rule_id == "lighthouse/no-console"]
        assert len(console_findings) == EXPECTED_CONSOLE_FINDINGS

    def test_lighthouse_layout_shift_elements(self):
        """Media elements without dimensions flagged."""
        analyzer = LighthouseAnalyzer()
        html = '<img src="a.jpg" width="100" height="100"><img src="b.jpg"><video src="c.mp4">'
        code = {"frontend/page.html": html}
        output = analyzer.analyze(code)

        assert not output.has_error
        shift_findings = [f for f in output.findings if f.rule_id == "lighthouse/layout-shift-elements"]
        assert len(shift_findings) == EXPECTED_SHIFT_FINDINGS

    def test_lighthouse_unoptimized_images(self):
        """Traditional image formats should be flagged."""
        analyzer = LighthouseAnalyzer()
        html = '<img src="photo.bmp"><img src="modern.webp">'
        code = {"frontend/page.html": html}
        output = analyzer.analyze(code)

        assert not output.has_error
        img_findings = [f for f in output.findings if f.rule_id == "lighthouse/unoptimized-image"]
        assert len(img_findings) == 1
