# Analyzer guide

The 33-tool analyzer catalog and shop: what each tool scans, how tools fit your apps, the tool YAML format and how to add a tool.

## Tool catalog

The catalog has 33 tools, one YAML file per tool in `backend/analysis/data/tools/`: 29 deterministic static tools, 1 AI reviewer and 3 dynamic (DAST) scanners. The shop groups them three ways: by **category** (what kind of problem), by **mode** (static or dynamic) and by the **technologies** they scan. The vocabulary for all three lives in `data/tools/catalog.yaml` and is served by `GET /api/analyzers/catalog/`.

"Reads" is the tool's `file_patterns`: a run whose code contains none of these files skips the tool as not applicable (see Applicability below). "always" means the tool declares no patterns.

### Static tools (read the source)

| Tool | Category | Scans | Reads | Finds |
| --- | --- | --- | --- | --- |
| bandit | security | Python, Flask, Django | `*.py` | Insecure calls, hardcoded secrets, weak crypto |
| trivy | security | pip/PyPI, npm, Docker, Kubernetes, Terraform | always | Vulnerable packages, Dockerfile/IaC misconfigurations, secrets |
| semgrep | security | Python, JavaScript, Flask, FastAPI, Django, Express | `*.py`, `*.js`, `*.jsx`, `*.ts`, `*.tsx` | Rule-based security and correctness patterns (`p/python`, `p/javascript`) |
| eslint-security | security | JavaScript, Express | `*.js`, `*.jsx`, `*.cjs`, `*.mjs` | eval, ReDoS, unsafe child processes, timing-unsafe comparisons |
| pip-audit | dependencies | Python, pip/PyPI | `requirements*.txt` | Vulnerable Python packages; nonexistent packages and versions |
| npm-audit | dependencies | JavaScript, npm | `package.json` | Vulnerable JavaScript packages; nonexistent packages and versions |
| osv-scanner | dependencies | pip/PyPI, npm | `requirements*.txt`, `package.json`, lockfiles | Vulnerable packages from OSV.dev, at the declared versions (`--no-resolve`: its own resolution invented old versions and bogus critical CVEs) |
| deptry | dependencies | Python, pip/PyPI | `requirements*.txt` | Missing, unused and misplaced Python dependencies |
| retire | dependencies | JavaScript, HTML, npm | `*.js`, `*.html`, `package.json` | Vulnerable JS libraries, including vendored copies |
| depcheck | dependencies | JavaScript, npm | `package.json` | Missing and unused JavaScript dependencies |
| ruff | lint | Python | `*.py` | Bugs, unused imports, style |
| flake8 | lint | Python | `*.py` | pyflakes + pycodestyle + mccabe |
| eslint | lint | JavaScript | `*.js`, `*.jsx`, `*.cjs`, `*.mjs` | Undefined names, unused variables |
| eslint-react | lint | JavaScript, React | `*.js`, `*.jsx`, `*.cjs`, `*.mjs` | Rules of Hooks, missing keys, unsafe links, `dangerouslySetInnerHTML` |
| pylint | lint | Python | `*.py` | Errors, code smells, refactoring candidates |
| mypy | lint | Python | `*.py` | Type errors |
| vulture | lint | Python | `*.py` | Dead code |
| codespell | lint | any source | always | Typos in code and comments |
| djlint | lint | HTML, Jinja/Django templates | `*.html`, `*.jinja`, `*.jinja2`, `*.j2` | Template accessibility and markup problems |
| jscpd | lint | any source | always | Copy-paste duplication (**metrics** + findings) |
| hadolint | lint | Docker | `Dockerfile`, `Dockerfile.*`, `*.dockerfile` | Dockerfile mistakes |
| shellcheck | lint | Shell | `*.sh`, `*.bash` | Quoting, globbing and portability bugs |
| htmlhint | lint | HTML | `*.html` | Missing doctype/title, duplicate ids, unclosed tags |
| stylelint | lint | CSS | `*.css`, `*.scss` | Invalid CSS, duplicate selectors |
| gitleaks | secrets | any source | always | Leaked secrets and keys |
| detect-secrets | secrets | any source | always | Hardcoded credentials |
| radon | performance | Python | `*.py` | Cyclomatic complexity, maintainability (**metrics** + findings) |
| lizard | performance | Python, JavaScript | `*.py`, `*.js`, `*.jsx`, `*.ts`, `*.tsx` | Complexity and function length (**metrics** + findings) |
| perflint | performance | Python | `*.py` | Performance anti-patterns |
| llm_review | ai | any source | always | LLM code review (`kind: ai`, see [The AI reviewer](/docs/ANALYSIS_PIPELINE)) |

Every other tool is `output_kind: findings`; radon, jscpd and lizard are `output_kind: mixed`: findings plus a structured metrics JSON on the tool result (radon's `average_complexity` and `max_complexity` feed the study report's code metrics).

### Dynamic tools (probe the running app, DAST)

| Tool | Category | Scans | Finds | Timeout |
| --- | --- | --- | --- | --- |
| zap | security | Web app, REST API | OWASP ZAP: spider, passive and active scan (headers, XSS, SQLi, leaks, misconfigurations) | 1200 s |
| nuclei | security | Web app, REST API | Template checks: misconfigurations, exposures, headers, CORS, common injections | 900 s |
| wapiti | security | Web app, REST API | Crawls and fuzzes forms and parameters: XSS, SQL and command injection, CRLF, CSRF, cookie flags | 900 s |

Dynamic tools need the generated app's container to be **running** when the analysis starts (start it from the application page). The runner resolves the target from the run's generation job only: a running `ContainerInstance` of that job, created by the run's owner or belonging to the owner's job, reached from the analyzer workspace at `http://<app container>:8000` (by name on `DOCKER_APPS_NETWORK`, else by its bridge IP). If the app isn't running, or the run analyzes pasted code, the result is `skipped` with `skip_reason: app_not_running`; the analysis wizard drops dynamic tools for pasted code. Findings point at URL paths (`file_path` = `/api/items`), with the full URL, method and parameter in `tool_specific_data`.

Experiment post-run analysis does not offer dynamic tools: evaluation deploys each app in a throwaway sandbox and removes it, so no app is running when the post-run analysis starts.

## The analyzer shop

The Analyzers page (`/analyzers`) has three tabs:

| Tab | What it does |
| --- | --- |
| Shop | The catalog with search plus category, mode and technology filters, per-tool fit counts, single install and an "Install N shown" bulk install |
| Installed | Installed tools with status, version and install log; per-tool options (from `config_schema`), test, update, "update all", uninstall |
| Workspace | Provision, start, stop or delete the analyzer workspace ([Analysis pipeline](/docs/ANALYSIS_PIPELINE)) |

### Choosing tools for your apps

The shop's **What your generated apps contain** panel (`GET /api/analyzers/coverage/`, `services/coverage.py`) scans your 200 most recent completed generation jobs for technologies (file extensions, manifests, framework imports, template syntax) and shows, per technology, how many apps contain it and which installed tools scan it. A technology no installed tool targets is flagged as a gap. Each tool row shows how many of your apps contain one of its technologies. A tool that fits none of them (Hadolint, when generated apps contain no Dockerfile) can only report zero findings, which says nothing about the code's quality.

### Tool versions

Each YAML pins a `version`; an install records the catalog version it was made from (`catalog_version`) and the version its `verify_cmd` reports (`installed_version`). When the catalog pin changes, the installed tool shows `update_available`. Updating a tool can change what it reports, so the shop asks for confirmation before a version update: finish a study on one version. Each tool result records the version it ran with in `summary.tool_version`.

### Analyzer API

All under `/api/analyzers/` (see the [API reference](/docs/api-reference)):

| Endpoint | Purpose |
| --- | --- |
| `GET tools/`, `GET tools/<slug>/` | Enabled catalog tools with the caller's install state |
| `GET catalog/` | Categories, modes, technology groups and technologies |
| `GET coverage/` | Technology and per-tool fit counts over your recent apps |
| `GET workspace/`, `POST workspace/provision/`, `start/`, `stop/`, `DELETE workspace/` | Workspace lifecycle |
| `GET workspace/tools/`, `POST workspace/tools/` (`tool_slug`), `POST workspace/tools/bulk/` (`tool_slugs`) | List and install; installs run in the background |
| `POST workspace/tools/update-all/` (`only_outdated`), `POST workspace/tools/<slug>/update/` | Re-run installs to update |
| `PUT workspace/tools/<slug>/` (`config`), `DELETE workspace/tools/<slug>/` | Per-user options, uninstall |
| `POST workspace/tools/<slug>/test/` | Verify the install and run the tool on its sample |

## Tool YAML format

Abridged from `ruff.yaml`:

```yaml
slug: ruff
name: Ruff
description: >-
  Extremely fast Python linter.
category: lint          # categories in catalog.yaml
kind: container         # container = runs in the workspace; ai = ai_runner
target_language: python # python | javascript | html | css | shell | any
analysis_mode: static   # static (reads {target}) | dynamic (probes {url})
technologies: [python]  # slugs from catalog.yaml `technologies`
icon: sparkles
version: "0.6"          # catalog pin; a change offers an update to installs
docs_url: https://docs.astral.sh/ruff/
install_cmd: "pip install --user --upgrade 'ruff==0.6.*'"
verify_cmd: "ruff --version"
uninstall_cmd: "pip uninstall -y ruff"
run_cmd: "sel={config:select}; ruff check {target} --no-cache --output-format json ${sel:+--select \"$sel\"}"
parser_key: ruff        # which registered parser reads stdout
file_patterns: ["*.py"] # files the tool reads; none present -> skipped
run_timeout: 90
install_timeout: 180
display_order: 20
config_schema:          # renders as an options form on the installed tool
  - name: select
    type: string
    label: Rule selection
    default: ""
    placeholder: "E,F,W"
default_config:
  select: ""
sample_code: |          # used by the test action (static tools)
  import os
  ...
```

| Field | Default | Notes |
| --- | --- | --- |
| `slug`, `name`, `description` | file stem for `slug` | `slug` is the upsert key |
| `category` | `lint` | `security`, `dependencies`, `lint`, `secrets`, `performance`, `ai` |
| `kind` | `container` | `ai` skips install and runs through `ai_runner.py` |
| `analysis_mode` | `static` | `dynamic` tools get `{url}` and ignore `file_patterns` |
| `output_kind` | `findings` | `findings`, `metrics` or `mixed` |
| `run_timeout`, `install_timeout` | 120, 300 seconds | `run_timeout` is enforced with `timeout -s KILL` |
| `sample_filename` | `sample.py` or `sample.js` | For filename-sensitive tools (`Dockerfile`, `requirements.txt`) |
| `display_order` | 100 | Shop ordering |
| `is_enabled` | true | Disabled tools leave the shop and are skipped as `unavailable` |

`config_schema` entries take `name`, `type` (`string`, `number`, `boolean` or `select` with `options: [{value, label}]`), `label`, `description`, `default` and optionally `placeholder`, `min` and `max`.

`run_cmd` placeholders:

- `{target}`: the code directory (`/work`).
- `{url}`: the running app's base URL, without a trailing slash (dynamic tools).
- `{config:<name>}`: the user's value for a `config_schema` field, falling back to `default_config`. Values are shell-quoted (booleans become `true`/`false`, lists are comma-joined), so they can be used as plain arguments. Every placeholder needs a `config_schema` field and a `default_config` entry (`tests/test_seed.py` checks this).

Commands run as the unprivileged `analyzer` user under `sh -c` (no root, no apt). Tools with many Python dependencies (djlint, wapiti) install into their own venv under `~/.local/opt/<tool>` and symlink the entry point into `~/.local/bin`, so their pins never clash with other pip tools. Release binaries are pinned to a version, and every one of them (Trivy, osv-scanner, Gitleaks, Hadolint, Nuclei and ZAP with its JRE) is verified with `sha256sum -c` against the checksum the release publishes before use. A test (`test_release_binary_downloads_are_checksummed`) fails for a new tool that downloads a release asset without a check.

### Applicability: `file_patterns`

`file_patterns` lists the files a tool reads, as case-sensitive globs. A pattern without `/` matches the file name (`"*.py"`, `"Dockerfile"`); a pattern with `/` matches the path relative to the code root (`"templates/*.html"`). If none of a run's files match, the tool is not run: its result is `skipped` with `summary.skip_reason = "not_applicable"`, instead of a clean-looking 0 findings that would count as "no issues" in model comparisons. An empty list means always applicable, and dynamic tools ignore the field (they scan the running app).

### Failure rules (no silent clean results)

The runner marks a tool result `failed`, never `completed` with 0 findings, when:

- the command exits **137** (killed by the `run_timeout` or out of memory),
- it exits **126/127** (tool not installed or not executable: reinstall it),
- it exits non-zero and the parser extracted neither findings nor metrics,
- no parser is registered under the `parser_key`, or the parser raises an exception, or
- the parser itself reports an error (every manifest of a sectioned tool failed).

A non-zero exit *with* parsed findings is normal (many linters exit 1 when they find something). Every result records the exact command in `raw_output.command` and the effective configuration (defaults plus the user's options) in `summary.config`, alongside `summary.tool_version`.

### Tools that scan several manifests

Dependency tools (pip-audit, npm audit, deptry, depcheck) run once per manifest and print one `### <relative path>` header followed by that manifest's JSON report. Register their parser with `register(key, sectioned=True)`; the registry then checks the output per section:

- an empty or unparsable section counts as "not analyzed" and is listed in `summary.warnings`;
- if every section fails, the whole result is `failed`, but any findings the parser derived from the failure are still stored on it;
- the run command may print `!! <reason>` lines inside a section to say why that manifest failed. pip-audit and npm audit use these to turn resolution errors into `nonexistent-package`/`nonexistent-version` findings: a requirement on a package or version that doesn't exist is a hallucinated dependency (they are stored even when the result is `failed`; see [What a tool result means](/docs/ANALYSIS_PIPELINE)).

### Keep `/work` clean

`/work` holds only the code under analysis, and tools such as detect-secrets and jscpd scan everything in it. A tool must not write caches or reports there: point caches at `/tmp` (mypy uses `--cache-dir /tmp/mypy-cache`, ruff runs with `--no-cache`) and write report files under `/tmp` (`/tmp/jscpd/`, `/tmp/gitleaks-report.json`).

## Seeding

The YAML files upsert into the `AnalyzerTool` table, keyed by slug (`backend/analysis/seeding.py`):

- automatically after every `migrate` (post-migrate hook in the analysis app; a seeding error is logged and never breaks `migrate`), and
- manually via `just manage seed_analysis_tools`.

Seeding is idempotent; edits to the YAML land on the next migrate or manual seed. It never deletes a tool whose YAML file was removed: set `is_enabled: false` to retire one. The seed logs a warning for technology slugs missing from `catalog.yaml` and for a container tool whose `parser_key` has no registered parser. In tests, run `just test --create-db` after changing tool YAML: the reused test database keeps the old catalog.

## Adding a tool

1. Drop a new `<slug>.yaml` in `backend/analysis/data/tools/` with the fields above. The tool must be installable inside the analyzer-base container by `install_cmd` and print machine-readable output from `run_cmd`. Add any new technology to `catalog.yaml`, and set `file_patterns` so the tool is skipped (not "clean") on apps it can't read.
2. If no existing parser fits its output, register one under your `parser_key` with `@register("<key>")` in `services/parsers.py`, `extra_parsers.py` (dependency, extra lint and web parsers) or `scanner_parsers.py` (DAST and multi-ecosystem scanners). A parser returns a list of `FindingData` or a `ParsedOutput` (findings, metrics, warnings, error). Set `tool_specific_data` keys the deduplicator understands where they apply: `cwe`, `end_line`, `package` (vulnerability scanners) and `issue_key`.
3. Reseed, install the tool into your workspace from the Analyzers page, and use the test action to check the parse end to end: it copies `sample_code` (as `sample_filename`) into `/tmp/analyzer-test` and runs `run_cmd` there with a timeout of at most 60 seconds. For a dynamic tool the test action only verifies the install; run it against a started app to check the parse. For the AI reviewer it makes a real model call.

## Per-user configuration

Each `config_schema` entry becomes a form field on the installed tool. Values are stored per user on the workspace's `InstalledTool.config` (seeded from `default_config` at install) and reach the tool through `{config:<name>}` placeholders in `run_cmd`, or directly for the AI reviewer (`model`, `max_tokens`). Tools with options today: bandit (`confidence`, `skip`), pylint (`disable`), ruff (`select`), npm-audit (`production_only`), trivy (`scanners`), djlint (`profile`), llm_review (`model`, `max_tokens`), zap (`spider_minutes`, `scan_minutes`), nuclei (`tags`) and wapiti (`modules`, `max_scan_seconds`). See [Analysis pipeline](/docs/ANALYSIS_PIPELINE) for the rest of the run.
