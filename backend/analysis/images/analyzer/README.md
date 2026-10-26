# llmlab analyzer image

One image holds every static analysis tool of the analysis engine, exactly
pinned and baked at build time. Nothing is installed or downloaded when an
app is scanned.

* **Tag**: `llmlab/analyzer:<sha12>`, the content hash of this directory
  (`engine/sandbox.image_tag()`, same algorithm as the agent images). Any edit
  here — a pin, a config, a rule selection — is a new tag, and frozen plans
  record the tag they ran with.
* **Release cutoff**: every tool, plugin, rule snapshot and transitive
  dependency was published on or before **2026-10-08** (two weeks before the
  toolchain was assembled), so nothing is a same-week release.
* **Manifest**: the last build step runs every tool, checks its real version
  against the pins below (a mismatch fails the build), loads every semgrep
  rule once against `scripts/smoke/*` and writes `/opt/analyzer/manifest.json`
  (versions, version output, lock hashes, binary hashes, rule pack hashes,
  config hashes). The executor freezes it into every plan.

## Runtime contract (enforced by `engine/sandbox.run_tool`)

| | |
|---|---|
| user | uid/gid 10001 (`analyzer`), no capabilities, no-new-privileges |
| rootfs | read-only |
| `/src` | the sample, a read-only volume |
| `/tmp` | tmpfs, the working directory; `HOME`, `XDG_*` and all caches live here |
| `/out` | fresh volume per tool container, owned by 10001 in the image; reports go here |
| network | none |

Each adapter (`engine/adapters/<slug>.py`) passes the selected files
explicitly, so an app cannot widen or narrow a scan, and uses the isolation
flags listed below so an app cannot configure or silence its own scanner.

## Pins

| Tool | Version | Source | Isolation |
|---|---|---|---|
| ruff | 0.16.10 | `requirements/ruff.txt` | `--isolated --no-cache --ignore-noqa` |
| bandit | 1.9.4 | `requirements/bandit.txt` | explicit files, no `-c`, `--ignore-nosec`, `--exclude ""` (no substring excludes) |
| semgrep CE | 1.180.0 | `requirements/semgrep.txt` | vendored rules, `--metrics off --disable-nosem --no-git-ignore`, offline |
| pylint (+ astroid 4.3.4) | 4.1.2 | `requirements/pylint.txt` | `--rcfile config/pylintrc`; inline disables recovered via I0020; whole-file pragmas neutralized in staged copies (I0013 reported) |
| mypy | 2.4.0 | `requirements/mypy.txt` | `--config-file config/mypy.ini`, `--no-site-packages`, no cache; `scripts/mypy_prefilter.py` (broken files and imports of them, `# mypy:` inline config) |
| vulture | 2.16 | `requirements/vulture.txt` | `--config config/vulture.toml`; `scripts/vulture_isolated.py` (`# noqa` ignored) |
| detect-secrets | 1.5.0 | `requirements/detect-secrets.txt` | `--no-verify`, allowlist-pragma and `*swagger*` filters disabled |
| codespell | 2.4.3 | `requirements/codespell.txt` | `--config config/codespell.cfg`, `--check-hidden`, cwd `/tmp`; `codespell:ignore` neutralized in staged copies |
| radon | 6.0.1 | `requirements/radon.txt` | cwd `/tmp` (no `radon.cfg`/`setup.cfg` of the app) |
| lizard | 1.24.1 | `requirements/lizard.txt` | cwd `/tmp` (no `whitelizard.txt` of the app); `#lizard forgive` / `GENERATED CODE` neutralized in staged copies |
| ESLint | 10.12.0 | `npm/package-lock.json` | `--no-config-lookup -c config/eslint.config.mjs --no-inline-config` |
| jscpd | 4.3.0 | `npm/package-lock.json` | `--config config/jscpd.json --no-gitignore`; `jscpd:ignore-*` neutralized in staged copies |
| gitleaks | 8.30.1 | release binary, sha256 `551f6fc8…f2470eb` | staged copy, `--config`, `--ignore-gitleaks-allow` |
| hadolint | 2.15.1 | release binary, sha256 `c7187db9…97c8c507` | `--config`, `--disable-ignore-pragma` |
| flake8 (pyflakes 4.0.3, pycodestyle 2.15.0, mccabe 0.7.0) | 7.4.1 | `requirements/flake8.txt` | `--isolated --disable-noqa` |
| perflint (+ pylint 3.3.9, astroid 3.3.11) | 0.8.1 | `requirements/perflint.txt` (own venv: perflint requires pylint < 4) | `--rcfile config/perflint.pylintrc`; inline disables recovered via I0020, whole-file pragmas neutralized |
| deptry | 0.25.1 | `requirements/deptry.txt` | staged project copy, `--config config/deptry.toml`, `--exclude '^$'` |
| ShellCheck | 0.11.0 | `requirements/shellcheck.txt` (shellcheck-py 0.11.0.1 wheel) | `--norc`; `# shellcheck` directives neutralized in staged copies |
| depcheck | 1.4.7 | `npm/package-lock.json` | staged project copy, `--config config/depcheck.json` |
| HTMLHint | 1.9.2 | `npm/package-lock.json` | `--config config/htmlhint.json`; `<!-- htmlhint -->` comments neutralized in staged copies |
| Stylelint (+ stylelint-config-recommended 18.0.0) | 17.16.0 | `npm/package-lock.json` | `--config config/stylelint.json --config-basedir /opt/analyzer --ignore-disables` |

Files a tool would silently pass over (radon: anything but `*.py`; lizard: extensions without a
reader; detect-secrets: its non-text extensions and lock files; gitleaks: its default global path
allowlist) are never passed to it; the adapters report them as skipped coverage.

ESLint rule semantics do not depend on the file's language (`config/eslint.config.mjs` header):
@eslint/js recommended applies to every script file, typescript-eslint contributes only its
TypeScript-only rules (TypeScript inside `.vue` included), and there is no JSX pragma.

ESLint plugins: @eslint/js 10.0.1, typescript-eslint 8.71.1 (+ TypeScript
6.0.3, the newest version inside its peer range), eslint-plugin-vue 10.11.1 +
vue-eslint-parser 10.4.1, eslint-plugin-security 4.2.0,
eslint-plugin-no-unsanitized 4.1.5, eslint-plugin-react-hooks 7.1.1,
globals 17.13.0. ESLint 10 tracks JSX references itself, so
eslint-plugin-react (whose peer range stops at ESLint 9) is not needed for
components used only as `<Component />`.

Base images (pinned by digest): `python:3.12-slim` (Python 3.12.15, Debian
13) and `node:22-bookworm-slim` (Node 22.23.3, binary and node_modules only).
jscpd 4.3.0 was chosen over 5.x: the 5.x Rust rewrite's JSON report has no
per-file statistics, so which files it skipped could not be recorded.

## Tools ported from the v1 tool shop

flake8, perflint, deptry, depcheck, HTMLHint, Stylelint and ShellCheck came
from the v1 per-user tool shop (installed at scan time, fail-open parsers) and
are now pinned here with strict adapters. They are in the catalog with
`default_enabled: false`, so the default profiles measure exactly what they
measured before; a profile opts in. deptry and depcheck run once per project
directory (a `requirements.txt` / `package.json`) on a staged copy of that
project's selected files only (`engine/adapters/_projects.py`).

Not ported:

* **pip-audit, npm audit**: both look dependencies up in an online advisory
  database and resolve unpinned requirement ranges against the live package
  registry, so a result depends on the day of the scan, not on the code, and
  scans run without network. Neither tool has an offline advisory mode; an
  offline replacement (e.g. osv-scanner with an OSV database snapshot) would
  need a database of several hundred MB baked into the image, published
  under a URL whose content changes daily (no pinnable sha256), and still
  could not resolve unpinned ranges without a registry.
* **eslint-security, eslint-react** (separate ESLint runs): the ESLint tool
  already loads eslint-plugin-security and eslint-plugin-react-hooks; a
  second ESLint over the same files would count those rules twice and
  measure parse coverage twice. eslint-plugin-react itself does not support
  ESLint 10 (its peer range stops at 9).

## Semgrep rules

`scripts/build_semgrep_rules.py` fetches semgrep-rules at commit
`9bbf021d0acffa3095fcffd17b2bc7787eb4796d` (2026-10-08) at build time — the
Semgrep Rules License does not allow redistributing them, so they are not in
this repository — keeps the security rules of the directories it lists
(Python: lang, Flask, FastAPI, SQLAlchemy, JWT, requests, crypto, Jinja2,
PyMongo; JS/TS: lang, browser, Express, JWT libraries, node-crypto, Sequelize,
shelljs, serialize-javascript, vm2, sandbox, Vue, React; HTML), verifies the
content hash of every pack and writes one JSON file per pack
(`python`, `python-audit`, `javascript`, `javascript-audit`, `html`; 297
rules) with registry-style rule ids. JSON packs load in well under a second;
the same rules as YAML files took pysemgrep ~50 s to parse per scan.

## Updating a pin

1. Edit `requirements/<tool>.in` or `npm/package.json` (or the binary
   `ARG`s and their published sha256 in the `Dockerfile`).
2. Re-lock: `sh scripts/lock_python.sh` inside `python:3.12-slim`, or
   `sh scripts/lock_node.sh` inside `node:22-bookworm-slim` (both with
   network; `CUTOFF`/`BEFORE` bound every transitive release date).
3. Update `version:` in `backend/analysis/data/tools/<slug>.yaml` and bump its
   `adapter_version` if the output format changed.
4. Rebuild (`manage.py analysis_build_image` or the Tools page), re-capture
   the golden outputs (`python -m backend.analysis.tests.support.capture_tool_outputs`)
   and re-derive `data/canary/expectations.yaml` from a canary run.
