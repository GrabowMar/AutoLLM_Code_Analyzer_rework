# Analyzer guide

The analysis tool catalog: the 21 static and metrics tools and the AI reviewer with their pinned versions, what each measures and how it is configured, the canary self-test, and how to add or update a tool.

How tools are executed and how their output becomes a measurement is in [Analysis pipeline](/docs/ANALYSIS_PIPELINE). Tools are defined in `backend/analysis/data/tools/<slug>.yaml` (what a tool is) and `backend/analysis/engine/adapters/<slug>.py` (how it is invoked and how its output is interpreted). All of them run from one pinned image, `backend/analysis/images/analyzer/`, whose README lists every pin, lock and hash.

## Tool catalog

Versions are the exact pins of the analyzer image. Every release, plugin, rule snapshot and transitive dependency was published on or before 2026-10-08. The image manifest checks each tool's real version against its pin at build time.

| Tool | Version | Kind / category | Languages | Default | What it measures |
| --- | --- | --- | --- | --- | --- |
| bandit | 1.9.4 | static / security | Python | on, required | Insecure calls (shell=True, pickle, eval, weak hashes, SQL built from strings, hard-coded passwords), each with a CWE |
| semgrep CE | 1.180.0 | static / security | Python, JS/TS (JSX), Vue, HTML | on, required | Security rules from a vendored semgrep-rules snapshot (297 rules: Python, Flask, FastAPI, SQLAlchemy, JWT, crypto, Jinja2, Express, React, Vue, HTML, …) |
| ruff | 0.16.10 | static / lint | Python | on, required | pyflakes (F), bugbear (B), flake8-bandit (S) and pylint errors (PLE); E9 syntax errors become coverage |
| pylint (astroid 4.3.4) | 4.1.2 | static / lint | Python | on, required | Errors and warnings (likely bugs, unused and unreachable code, eval/exec); no convention noise; duplicate-code left to jscpd |
| mypy | 2.4.0 | static / types | Python | on, required | Type errors; third-party imports are `Any` (dependencies are not installed); unparseable files pre-filtered into coverage |
| vulture | 2.16 | static / dead_code | Python | on, required | Unused imports, unreachable code, unused functions, classes and variables (min confidence 80; framework route decorators ignored) |
| ESLint | 10.12.0 | static / lint | JS, JSX, TS, TSX, Vue | on, required | @eslint/js recommended, the TypeScript-only rules of typescript-eslint, eslint-plugin-vue, security, no-unsanitized and react-hooks; components used only in JSX are not "unused" |
| gitleaks | 8.30.1 | static / secrets | any text | on, required | Hard-coded keys, tokens and passwords (upstream rule set) |
| detect-secrets | 1.5.0 | static / secrets | any text | on, required | Provider-specific secret detectors, secret keywords and high-entropy strings |
| codespell | 2.4.3 | static / style | any text | on, optional | Misspellings in comments, strings and identifiers (style: not counted by default) |
| hadolint | 2.15.1 | static / container | Dockerfile | on, optional | Dockerfile best practices; usually not applicable, because generated apps' Dockerfiles are platform-owned |
| radon | 6.0.1 | metrics / complexity | Python | on, required | Cyclomatic complexity per function (ranks A–F), maintainability index and raw counts; blocks at or above `finding_min_rank` become findings |
| lizard | 1.24.1 | metrics / complexity | Python, JS/TS, Vue | on, required | Cyclomatic complexity, length and parameter count per function across languages; the measurement's complexity source |
| jscpd | 4.3.0 | metrics / duplication | Python, JS/TS, Vue, CSS, HTML | on, required | Copy/paste clones (one finding per clone pair) and the share of duplicated lines, recomputed per (file, line) |
| flake8 (pyflakes 4.0.3, pycodestyle 2.15.0, mccabe 0.7.0) | 7.4.1 | static / lint | Python | opt-in | pyflakes and pycodestyle; mccabe complexity when `max_complexity` is set |
| perflint (pylint 3.3.9) | 0.8.1 | static / lint | Python | opt-in | Performance anti-patterns: loop-invariant statements, global lookups and dotted imports in loops, needless `list()` casts, dict iteration |
| deptry | 0.25.1 | static / lint | Python + `requirements.txt` | opt-in | Per project: imported but undeclared, declared but unused, transitive and misplaced dev dependencies (DEP001–DEP005) |
| depcheck | 1.4.7 | static / lint | JS/TS/Vue + `package.json` | opt-in | Per project: missing, unused and unused dev dependencies |
| HTMLHint | 1.9.2 | static / lint | HTML | opt-in | Unpaired tags, duplicate ids and attributes, empty `src`, images without alt text, missing doctype or title |
| Stylelint (config-recommended 18.0.0) | 17.16.0 | static / lint | CSS | opt-in | Unknown properties, units, functions and pseudo-selectors, invalid colors, duplicate selectors and properties |
| ShellCheck | 0.11.0 | static / lint | shell | opt-in | Unquoted expansions, word splitting and globbing bugs, unchecked `cd`, dangerous `rm -rf` expansions, portability problems |
| ai_review | (frozen per plan) | ai | any | per profile | LLM review: switchable engine, harness and model; grounded consensus findings, kept apart from static counts |

"Default" says whether `default_profile_config()` (and the `research-default` profile) includes the tool, and whether it is required: a required tool that fails makes the run `partial` and its outcomes NA. Opt-in tools have `default_enabled: false`; a profile adds them. Base images are `python:3.12-slim` (Python 3.12.15) and `node:22-bookworm-slim` (Node 22.23.3), both pinned by digest. `GET /api/analysis/tools/` returns the catalog, each tool's version from the built image's manifest, and its latest canary verdict.

### Tools ported from the v1 tool shop

flake8, perflint, deptry, depcheck, HTMLHint, Stylelint and ShellCheck come from the v1 per-user tool shop, where they were installed at scan time and parsed fail-open. They are now pinned in the image and have strict adapters. They are opt-in, so the default profiles measure exactly what they measured before. deptry and depcheck run once per project directory (a folder holding a `requirements.txt` or `package.json`) on a staged copy of that project's selected files only (`engine/adapters/_projects.py`); their `unused-dependency` findings sit on the manifest, which is not measured code, so they count as out of scope.

Deliberately not ported:

- **pip-audit and npm audit.** Both look dependencies up in an online advisory database and resolve unpinned requirement ranges against the live package registry, so a result depends on the day of the scan rather than on the code. Scans run without network. Neither tool has an offline advisory mode, and an offline replacement (osv-scanner with a database snapshot) would need several hundred MB baked into the image, from a URL whose content changes daily (no pinnable sha256), and still could not resolve unpinned ranges.
- **eslint-security and eslint-react as separate tools.** The ESLint tool already loads eslint-plugin-security and eslint-plugin-react-hooks, so a second ESLint run over the same files would count those rules and parse coverage twice. eslint-plugin-react itself does not support ESLint 10.

The v1 `llm_review` tool is replaced by `ai_review`. Saved automation steps that still name `llm_review` or `detect_secrets` are mapped to `ai_review` and `detect-secrets`.

## Configuration options

Each `config_schema` field becomes a form field in the profile editor (**Analysis → Profiles**), and its effective value is frozen into the plan (`config_hash`).

| Tool | Options (default) |
| --- | --- |
| ruff | `select` (`E9,F,B,S,PLE`), `ignore` (""), `target_version` (`py312`) |
| bandit | `severity` (all), `confidence` (all), `skip` (tests to skip) |
| semgrep | `rulesets` (vendored packs), `exclude_rules`, `per_file_timeout` |
| pylint | `include_refactor`, `include_convention`, `report_suppressed`, `extra_disable` |
| mypy | `check_untyped_defs`, `python_version` |
| vulture | `min_confidence` (80) |
| eslint | `opt_in_rules` |
| detect-secrets | `base64_limit`, `hex_limit` |
| codespell | `builtin` dictionaries |
| hadolint | `ignore_rules` |
| radon | `finding_min_rank` |
| lizard | `ccn_threshold`, `modified_ccn` |
| jscpd | `min_tokens`, `min_lines`, `mode` |
| flake8 | `select`, `extend_ignore`, `max_line_length`, `max_complexity` |
| perflint | `report_suppressed`, `extra_disable` |
| deptry | `ignore` (rule codes) |
| depcheck | `skip_missing` |
| shellcheck | `severity`, `exclude` |
| htmlhint, stylelint, gitleaks | none (baked configuration only) |

The AI reviewer is configured by the profile's `reviewer` block (engine, harness, model, samples, budget, …), not by `config_schema`. Its keys and defaults are in [Analysis pipeline](/docs/ANALYSIS_PIPELINE#the-ai-reviewer), and `GET /api/analysis/reviewer/options/` lists the engines, harnesses with their option schemas, prompt versions and limits.

## Isolation

Every tool gets its files as explicit paths, runs from `/tmp` (never inside the app), reads only the baked configuration under `/opt/analyzer/config/`, and has no network. On top of that:

| Tool | What keeps the app from configuring or silencing it |
| --- | --- |
| ruff | `--isolated --no-cache --ignore-noqa` |
| bandit | explicit files, no `-c`, `--ignore-nosec`, `--exclude ""` (bandit's substring excludes would drop paths such as `.github/…`) |
| semgrep | vendored rules, `--metrics off --disable-nosem --no-git-ignore`, offline |
| pylint, perflint | explicit `--rcfile`; inline disables recovered as findings (I0020); whole-file pragmas neutralized in staged copies |
| mypy | `--config-file`, `--no-site-packages`, no cache; `# mypy:` inline configuration neutralized by the prefilter |
| vulture | baked config; a wrapper ignores `# noqa` |
| detect-secrets | `--no-verify`; the allowlist-pragma and `*swagger*` filters disabled |
| codespell | baked config, `--check-hidden`; `codespell:ignore` neutralized in staged copies |
| radon, lizard | run from `/tmp` (no `radon.cfg` / `whitelizard.txt`); `#lizard forgive` and `GENERATED CODE` neutralized |
| ESLint | `--no-config-lookup -c config/eslint.config.mjs --no-inline-config` |
| jscpd | `--config`, `--no-gitignore`; `jscpd:ignore-*` neutralized |
| gitleaks | staged copy without `.gitleaksignore`, explicit `--config`, `--ignore-gitleaks-allow` |
| hadolint | `--config`, `--disable-ignore-pragma` |
| flake8 | `--isolated --disable-noqa` |
| deptry, depcheck | staged project copy, baked config (the app's `[tool.deptry]`, `.depcheckrc` and `.gitignore` are never seen) |
| ShellCheck | `--norc`; `# shellcheck` directives neutralized |
| HTMLHint | `--config`; `<!-- htmlhint -->` comments neutralized |
| Stylelint | `--config --config-basedir /opt/analyzer --ignore-disables` |

Files a tool would silently pass over (radon: anything but `*.py`; lizard: extensions without a reader; detect-secrets: its non-text extensions and lock files; gitleaks: its default path allowlist) are never handed to it. The adapter reports them as skipped coverage.

## Canary self-test

The canary is a positive and a negative control per tool, run through the real pipeline as an ordinary `AnalysisRun` with `purpose=canary`. The fixtures are in `backend/analysis/data/canary/`:

- `vulnerable/` (a Flask backend with SQL injection, `shell=True`, pickle, eval and weak hashes; React, TypeScript and Vue frontends with eval and XSS sinks; a Dockerfile, a requirements.txt and package.json with missing and unused dependencies, HTML, CSS and a shell script with known defects) must produce the expected rule ids;
- `clean/` (the same kinds of files written correctly) must produce no counted findings of the tool's listed categories.

`data/canary/expectations.yaml` holds, per tool, `must_find` entries (`{rule, path?}`, globs allowed; each must match at least one finding), `clean_categories` and an optional `min_findings`. The verdict per tool is `passed`, `failed` (the tool did not succeed, or an expectation was violated) or `unverified` (no expectations). Every catalog tool currently has expectations: for example bandit must find B608, B602, B301, B506, B307 and B324 among 8 entries, semgrep 14 rule patterns, and hadolint DL3007, DL3008, DL3020 and DL3025.

Run it from **Analysis → Tools** or with `POST /api/analysis/selftest/` (optional `{"tool_slugs": [...]}`; default: every static tool, each required, the AI reviewer off). `GET /api/analysis/selftest/latest/` returns the latest verdicts on the current image. A study's preflight uses them: a planned tool whose latest canary failed on the image blocks the lock. A control vouches only for the configuration it ran, so a tool whose plan config was never self-tested is `canary_unknown` (`config_untested`).

## Adding or updating a tool

### The spec

```yaml
# backend/analysis/data/tools/<slug>.yaml
slug: ruff
name: Ruff
kind: static               # static | metrics | ai
category: lint             # security | secrets | lint | types | dead_code | complexity | duplication | style | container
version: "0.16.10"         # must equal the image pin
adapter_version: 1         # bump when argv construction or interpretation changes
languages: [python]        # SampleFile languages; ["*"] = any text file
file_kinds: [source, config]
output_kind: findings      # findings | metrics | mixed
default_enabled: true      # in the default profile?
default_required: true
mem_limit: 512m
timeout_s: 180
config_schema:
  - name: select
    type: string           # string | number | integer | boolean | select | multiselect
    label: Rule selection
    default: "E9,F,B,S,PLE"
    pattern: '^[A-Z]+[0-9]*(,[A-Z]+[0-9]*)*$'
```

Specs are validated strictly: an unknown key, a wrong type or a default that fails its own field is a `SpecError`. `spec_hash` covers everything that changes a measurement (kind, category, languages, file kinds, output kind, version, adapter version, resources, config schema and defaults) but not presentation fields such as name, description, icon or docs URL. The catalog re-reads changed spec files on its own. `seed_analysis_tools` (also run after every migrate) mirrors the specs into `AnalyzerTool` rows for the UI; runs never rely on those rows.

### The adapter contract

Create `engine/adapters/<slug>.py` with a `ToolAdapter` subclass decorated with `@register`:

- `select_files(sample, config)`: the included files of the spec's languages and kinds (the default implementation). An empty list means `not_applicable`. Override `unsupported(file)` to name files the tool would silently pass over; they are listed as skipped and never handed to the tool.
- `invocation(files, config)`: an `Invocation` (argv inside the image with explicit file paths, baked config and isolation flags, report files under `/out`). Use `staged_argv` when the tool honours in-file markers that no flag disables.
- `interpret(outcome, files, config)`: call `check_exit(outcome, {allowed codes}, slug)`, read the report strictly (`stdout_of`, `read_report`, `load_json`, `report_structure`), check that the exit code agrees with the report, turn parse errors into coverage, and return `build_parse(...)`. Raise `ToolFailure(reason, message)` for anything untrustworthy, never an empty result.
- `RawFinding.native_severity` is the tool's own token unchanged; paths are relative to the sample root; CWE ids go in `cwe` when the tool reports them.

Then add the tool to the mapping tables (`data/mappings/severity.yaml`, `categories.yaml` including `emits`, and `cwe.yaml` or `rule_equivalence.yaml` where it overlaps with other tools). A changed table changes `mapping_hashes`, and with it the plan hash of newly frozen plans.

### Pin, build, capture, verify

1. **Pin.** Add `requirements/<tool>.in` (Python), an entry in `npm/package.json` (Node), or a binary `ARG` with its published sha256 in the `Dockerfile`. Re-lock with `sh scripts/lock_python.sh` inside `python:3.12-slim` or `sh scripts/lock_node.sh` inside `node:22-bookworm-slim` (both need network; `CUTOFF` / `BEFORE` bound every transitive release date). Baked configs go in `config/`, and `scripts/write_manifest.py` must report the tool's version.
2. **Build.** `just manage analysis_build_image` (or the Tools page). Any edit of the image directory is a new tag.
3. **Golden fixtures.** `python -m backend.analysis.tests.support.capture_tool_outputs --tool <slug>` runs cases through the production sandbox and stores each `ExecOutcome` under `backend/analysis/tests/fixtures/tool_outputs/<slug>/`. Adapter tests replay these real outputs, failure modes included (no files, broken input, a missing report, invalid JSON, timeouts).
4. **Canary.** Add positive and negative files under `data/canary/vulnerable/` and `clean/`, add the tool's `expectations.yaml` entry, and check it with a self-test run. Re-capture the other tools' fixtures if the canary files they scan changed.
5. **Tests.** Adapter unit tests (`tests/test_adapters*.py`), the catalog tests (`tests/test_catalog.py`: the tool set, spec versions against the image pins, every argv option changing the command line) and the Docker suite (`pytest backend/analysis -m docker`, which needs the apps daemon).

To update a pin, change the pin and re-lock, update `version:` in the spec (and `adapter_version` if the output format changed), rebuild, re-capture the golden outputs and re-derive the canary expectations from a canary run. Plans frozen before the change keep their own tag and are not comparable with new ones.
