# Analyzer guide

The analysis tool catalog: what each tool does, how tools are defined in YAML, and how to add one.

## Tool catalog

Fourteen tools, defined one YAML file per tool in `backend/analysis/data/tools/`:

| Tool | Category | Target | Finds |
| --- | --- | --- | --- |
| bandit | security | Python | Insecure patterns, known-vulnerable calls |
| semgrep | security | polyglot | Rule-based security/correctness patterns |
| detect-secrets | secrets | any | Hardcoded credentials |
| gitleaks | secrets | any | Leaked secrets and keys |
| ruff | lint | Python | Bugs, unused imports, style |
| pylint | lint | Python | Deeper static analysis |
| mypy | lint | Python | Type errors |
| eslint | lint | JS/TS | Bugs and style in frontend code |
| codespell | lint | any | Typos in code and comments |
| vulture | lint | Python | Dead code |
| hadolint | lint | Dockerfile | Dockerfile mistakes |
| radon | performance | Python | Complexity/maintainability **metrics** |
| jscpd | performance | polyglot | Copy-paste duplication **metrics** |
| llm_review | ai | any | LLM code review via OpenRouter |

Most tools emit findings only; radon and jscpd are `output_kind: mixed` — findings plus a structured metrics JSON channel on the tool result. Display categories come from `catalog.yaml`.

## Tool YAML format

Abridged from `ruff.yaml`:

```yaml
slug: ruff
name: Ruff
category: lint          # display category from catalog.yaml
kind: container         # container = runs in the workspace; ai = ai_runner
target_language: python
install_cmd: "pip install --user 'ruff==0.6.*'"
verify_cmd: "ruff --version"
run_cmd: "ruff check {target} --output-format json"   # {target} -> /work
parser_key: ruff        # which parser in services/parsers.py reads stdout
run_timeout: 90
config_schema:          # renders as a settings form in the UI
  - name: select
    type: string
    label: Rule selection
    default: ""
sample_code: |          # used by the "test tool" action
  import os
  ...
```

## Seeding

The catalog upserts into the `AnalyzerTool` table, keyed by slug:

- automatically after every `migrate` (post-migrate hook in the analysis app), and
- manually via `just manage seed_analysis_tools`.

Seeding is idempotent; edits to the YAML land on the next migrate or manual seed. In tests, run `just test --create-db` after changing tool YAML — the reused test database keeps the old catalog.

## Adding a tool

1. Drop a new `<slug>.yaml` in `backend/analysis/data/tools/` with the fields above. The tool must be installable inside the analyzer-base container by `install_cmd` and print machine-readable output from `run_cmd`.
2. If existing parsers don't fit its output, register a parser under your `parser_key` in `backend/analysis/services/parsers.py`.
3. Reseed, install the tool into your workspace from the Analyzers page, and use the test action (runs `run_cmd` against the YAML's `sample_code`) to check the parse end to end.

## Per-run configuration

Each `config_schema` entry becomes a form field on the installed tool. Values are stored per user on the workspace's installed tool and passed to the tool at run time — see [Analysis pipeline](/docs/ANALYSIS_PIPELINE) for where that happens.
