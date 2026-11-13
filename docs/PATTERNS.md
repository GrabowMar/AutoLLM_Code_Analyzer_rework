# Pattern analysis (slot mode)

Pattern analysis finds what a model reliably writes when it regenerates one part of an app many times
(spec `docs/design/SAMPLE_GEN_V3.md` §7.9, API §9.7). It runs on the variants of a slot study and costs no
LLM money: every re-run creates a new, immutable `PatternRun`.

Code: `backend/generation/patterns/`, the Semgrep rule pack `backend/analysis/services/rulepack.py`, the
audit tasks `backend/human_eval/services/detector_audit.py`, seed detectors `backend/generation/data/detectors/`.

## Lifecycle

1. **Launch.** A slot study attaches `StudyPostRun(kind=patterns, detectors="default")`; the executor's
   post_run lane calls `patterns.run(pattern_run)` once every variant is evaluated. More runs can be queued
   with `POST /api/generation/patterns/runs/`.
2. **Live scoring.** After each variant's evaluation `patterns.live.score_variant(run)` runs the study's
   `Design.slot_primary_features` and writes provisional hits into the study's live PatternRun. The
   `ci_width_reached` stop rule calls `patterns.live.ci_width_reached(study, rule)`; every call is one look,
   and the final prevalence of `adaptive_n` cells uses Wilson at alpha/K.
3. **The run** (`patterns/service.py`): load the variants (`variants.py`), normalize, cluster, run the
   detectors, build the feature matrix, compute prevalence, comparisons, pooled estimates and cluster
   statistics, store hits and clusters, and keep the result tables in `PatternRun.summary`.

## Steps

| Step | Module | Notes |
|---|---|---|
| Normalize | `normalize.py` (`NORMALIZER_VERSION = 1`) | tree-sitter token stream; comments and docstrings dropped; locals alpha-renamed; imports sorted; literals kept. Python, JS, TS, TSX renamed; Vue/Svelte scripts via JS/TS. Unparsable: `normalized_sha=""` + reason, counted, never dropped. |
| Cluster | `cluster.py` | exact (normalized sha) or own MinHash (128 perms, 5-token shingles, Jaccard >= 0.8, single linkage per target). Stats per (condition, target): n, distinct, exact-dup share, top-1 share, entropy, Chao1, `low_effective_n` (> 0.5). |
| Detect | `detectors.py` | `regex`, `ts_query`, `semgrep` (one rule-pack call per run, whole assembled file, so taint flows through parent helpers are seen), `probe`, `manual`. Scopes `span` (the slot) and `file`. Errors are `error`, never `absent`. `tool_severity` verbatim. |
| Features | `features.py` | `<feature>@<scope>` plus derived `equal_to_original`, `uses_new_dependency`, `out_of_slot_edit`, `slot_tests_all_pass`, `exploited_any`, numeric `leak_score`. |
| Prevalence | `prevalence.py`, `stats.py` | ITT n, measured n, errors, n/a, missing; Wilson (CP when n < 30); bounds when unknown > 5%; correctness next to security features; paired (Newcombe + McNemar) or independent (Newcombe + Fisher) comparisons by `seed_forwarded`; MH pooled RD + exact target-level permutation (`few_clusters`). |
| Audit | `audit.py` | stratified, seeded hits/non-hits sample -> blinded human-eval task; precision and NPV with Wilson CIs. |
| Export | `export.py` | `variants.csv`, `features.csv`, `prevalence.csv`, `comparisons.csv`, `pooled.json`, `clusters.json`, `audit.csv`, raw parts, originals, `manifest.json`, README with the offline mixed model. |
| Discovery | `drafts.py`, `manual.py` | "Create detector from selection" (regex / ts_query drafts), cluster labels for manual detectors, `diff_vs_original`. |

## Denominators and validity

- **ITT n** = every planned main-stage cell of the active plan (cells excluded by `stopped_by_rule`,
  `budget_stop` or `cancelled` never ran and are not planned). A planned variant without usable output
  (no parsed replacement) is **missing**: it counts in ITT n, never as absent.
- **Correctness under ITT** (spec 6.13): a usable `build_fail`, `start_fail` or `output_invalid` variant is
  data with 0 pass, so its `slot_tests_all_pass` is `absent` (counted in the correctness rate next to every
  security feature, in its comparisons and as "not passing" for `p(feature | slot_tests_all_pass)`). `n/a`
  is kept for uninformative targets and eval levels that skipped the checks. Detector features of an
  `output_invalid` variant stay missing (there is no code).
- `error` (a detector or measurement failed) and `n/a` (not applicable, not measured) are counted separately.
- p-values: only for planned comparisons of a confirmatory study (`label=planned`): the raw `p` plus
  `p_adjusted` under the study plan's `correction` (`holm` by default, `bh`, `none`) over the planned family
  (condition pairs x primary features x scopes; the pooled permutation p's are their own family), the plan's
  `alpha` and `significant = p_adjusted < alpha`. Exploratory rows show Benjamini-Hochberg q-values and the
  family size. While the study runs (`interim`) all of them are withheld.
- **Frozen record set.** A completed run stores what it analysed in `summary["records"]` (cell, run id,
  outcome, usable, output, parse/eval status and the derived values). The export (and the study export)
  rebuilds its dataset from those run ids and stored values, never from `current_runs` or the latest
  evaluations, so it agrees with the stored prevalence and comparisons after retries, retests or top-ups.
- **Planned vs exploratory.** `planned` only when the study is confirmatory and locked and the run's
  `detector_set_hash`, `normalizer_version`, `cluster_params` and `scope` equal
  `analysis_plan.patterns`. `detector_set_hash = H({"version": 1, "detectors": sorted([[slug@v, content_hash]])})`
  (`patterns.detectors.detector_set_hash`).

## Semgrep

`PATTERNS_SEMGREP_BACKEND=workspace` (default) runs in the study owner's analyzer workspace (Semgrep must be
installed from the tool shop; otherwise every Semgrep hit is `error/tool_error`), `local` uses a `semgrep`
binary on the host. Files go to a private scratch dir (`/tmp/llmlab-rulepack/<uuid>`), never `/work`.
`PATTERNS_SEMGREP_TIMEOUT_S` bounds one invocation.

## Seed detectors

`data/detectors/<engine>/<slug>.yaml` (`{name, description, content}`), fixtures under
`data/detectors/tests/<slug>/{positive,negative}[-N].<ext>` (spec 11.6; not entities, the library seeder skips
`tests/`). The set is the spec 11.6 catalogue (`py-sql-fstring`, `py-sql-concat`, `py-md5-sha1-password`,
`py-bcrypt-used`, `py-argon2-used`, `py-plaintext-password-compare`, `py-hardcoded-secret`,
`py-jwt-none-or-no-verify`, `py-debug-true`, `py-open-redirect`, `py-path-join-user-input`,
`py-yaml-load-unsafe`, `js-innerhtml-user-data`, `js-dangerouslysetinnerhtml`, `js-eval-like`,
`js-jwt-secret-literal`, `js-sql-template-literal`, `js-cors-wildcard-credentials`, `go-sql-sprintf`,
`java-sql-concat`), one probe detector per probe kind (`probe-<kind>`, rule `kind:<kind>`, 17 kinds), plus
additions (`py-flask-sqli-taint`, `py-parameterized-sql`, `py-eval-exec`, `py-subprocess-shell-true`,
`py-unsafe-deserialization`, `py-weak-hash`, `js-hardcoded-secret`, `js-token-in-localstorage`,
`vue-v-html`, `svelte-html-tag`, the CWE-mapped `probe-sqli`/`probe-xss`/`probe-idor` and
`manual-owner-check`). Probe fixtures are `{"probes": [...]}` JSON. The regex, ts_query and probe fixtures
run in `tests/wp13_patterns/test_seed_and_invariants.py`; the Semgrep ones run when a `semgrep` binary is
available.

## Tests

`./.wt-test.sh wp13 -- pytest -q backend/generation/tests/wp13_patterns`. Fakes: `testing/fake_semgrep.py`
(regex-emulating Semgrep runner with scripted errors) and `testing/fake_patterns.py` (synthetic slot study
and `FakeSlotExecutor`).
