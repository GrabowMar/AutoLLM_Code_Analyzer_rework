# Long-term TODOs

## Analyzer Improvements

### Reintegrate html-validator into reports
- **Status**: Removed from report static tools list (commit TBD)
- **Reason**: `html-validator-cli` was called with positional file args instead of `--file=<path>`, causing it to error silently with 0 findings across all 391 executions. Fixed invocation to use `--file=` per file, but existing result data still has 0 findings.
- **Action needed**: After re-running analyses with the fixed analyzer, add `'html-validator'` back to `_STATIC_TOOLS` in `report_service.py` (~line 2891).
- **Files**: `analyzer/services/static-analyzer/main.py` (fixed), `src/app/services/report_service.py` (excluded)

### Add SARIF parsers for remaining 6 tools
- **Tools without SARIF conversion**: pip-audit, radon, detect-secrets, npm-audit, stylelint, html-validator
- **Impact**: Only affects `sarif_export` document (supplementary). Severity data works correctly via `severity_breakdown` dict for all tools.
- **Priority**: Low — only needed if external SARIF interop is required.
- **File**: `analyzer/services/static-analyzer/sarif_parsers.py`

## Report Improvements

### Consider adding stylelint to report exclusion
- **Status**: Currently included but shows 0 findings for all models
- **Reason**: LLM-generated CSS consistently passes default stylelint rules
- **Decision**: Keep for now — may produce findings with stricter configs or different app templates

### Standardize report data pipeline
- **Status**: Functional but fragile — accumulated structural debt
- **Problem**: The 4 report types have no shared data contract. Each generator returns `Dict[str, Any]` with inconsistent field names (`tools_statistics` vs `tools`, `avg_duration` vs `average_duration`, `by_model` as dict vs list). Templates and routes compensate ad-hoc:
  - `tool_analysis` is the only type with route-level normalization and a dedicated partial template (`_tool_analysis.html`) — it uses bare attribute access, which crashes if normalization is skipped.
  - The other 3 types pass raw dicts to a 1,289-line monolith `view_report.html` that uses `.get()` everywhere — safe but hard to maintain.
  - 5+ orphaned partial templates (`_model_analysis.html`, `_app_comparison.html`, `_model_comparison.html`, `_executive_summary.html`, `_tool_effectiveness.html`) are never referenced — dead code that adds confusion.
- **Recommended fixes** (in priority order):
  1. **Define TypedDicts or Pydantic models** for each report type's output — makes the contract explicit and IDE-checkable
  2. **Add a shared `normalize_report_data(report_type, data)` function** called from the route for all report types, not just `tool_analysis`
  3. **Delete the orphaned partials** — zero value, high confusion
  4. **Split `view_report.html`** into per-type partials (like `_tool_analysis.html`), each with a normalization block in the route
- **Files**: `src/app/services/report_service.py`, `src/app/routes/jinja/reports.py`, `src/templates/pages/reports/view_report.html`, `src/templates/pages/reports/partials/`

### Smarter app number handling for reports
- **Status**: Currently uses `max_app_number` config (default 20) to cap apps per model
- **Problem**: Claude had 50 apps (first 20 study, apps 21-50 system tests). The cap is a blunt instrument — it filters by `app_number <= N` across all models, not per-model.
- **Improvement**: Add per-model `study_app_range` metadata (e.g., `{'claude': [1,20], 'gpt-5.2': [1,20]}`) to distinguish study apps from system test apps. Could be stored in `generated_applications.metadata_json` or a separate config.
- **Alternative**: Tag apps with `study_group` enum (study/test/debug) during generation, then filter by tag in reports.
- **Files**: `src/app/services/report_service.py`, `src/app/models/core.py`
