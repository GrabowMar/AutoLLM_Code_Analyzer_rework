# Analysis methodology

The formal definitions behind every analysis number, written as a thesis appendix: SLOC, measured scope and line attribution, the severity, category and CWE mapping tables, cross-tool deduplication, the outcome definitions, AI grounding, consensus and agreement, and the statistics, with references.

Every definition below is versioned, and the versions are frozen into each analysis plan (`engine_versions`, `mapping_hashes`), so a reader can tell exactly which rules produced a number. The current versions are `sample-v2` (sample resolver), `sloc-v2`, `norm-v2` (normalizer), `dedup-v2`, `measure-v2`, `reviewer-v2` with prompt `review-v1` and schema `findings-v1`, and `stats-v1`. The pipeline that applies them is described in [Analysis pipeline](/docs/ANALYSIS_PIPELINE); how to use them in a study in the [Research guide](/docs/RESEARCH_GUIDE).

## Principles

1. A tool result is `succeeded`, `failed`, `timed_out`, `not_applicable`, `skipped` or `cancelled`. Zero findings means "clean" only for a tool that succeeded.
2. Missing data is `None` (NA), never 0, in measurements, outcomes and statistics.
3. Numerator and denominator cover the same code: findings are counted and SLOC is measured over the same files and lines.
4. Comparability is explicit: runs are comparable if and only if their `plan_hash` is equal.
5. Provenance is recorded for every number: tool versions from the image manifest, image id, argv, config hashes, rule-pack hashes, prompt and schema sha256, the reviewer model and its pricing snapshot, the sample hash and the engine stage versions.
6. The analysed app cannot configure or disable its scanner, and tools run without network.
7. AI findings are opinions: kept apart from static measurements, grounded against the code, replicated, and counted only by consensus.

## SLOC (`sloc-v2`)

A **code line** holds at least one non-whitespace character that is not part of a comment. Blank lines and comment-only lines never count. Lines are split on `\n` only (a trailing `\r` is dropped), exactly as the static tools and the AI reviewer number them; a form feed, `\x1c`–`\x1e`, `\x85` or U+2028/U+2029 inside a line does not start a new one.

| Language family | Comment rules |
| --- | --- |
| `#` family: Python, shell, YAML, TOML, Dockerfile | A line whose first non-blank character is `#` is a comment. Python is tokenized: a `#` inside a string is code, the non-blank lines of a multi-line string are code, and **docstrings are comments** (the string literal opening a module, class or function body). Python the lexer cannot read falls back to the plain `#` rule. |
| C family: JavaScript, JSX, TypeScript, TSX | `//` line comments and `/* … */` blocks. Quoted strings end at the line end; template literals may span lines. A JSX comment line `{/* … */}` is a comment. Regex literals are recognised where an expression may start, so `/[+/*]/` is not a comment. |
| CSS | `/* … */` only (`//` is not a comment in CSS). |
| Markup: HTML, Vue, Svelte | `<!-- … -->`; `<script>` sections use the C-family rules and `<style>` sections the CSS rules. |
| Everything else (JSON, Markdown, text, env, …) | Not code: 0 SLOC. |

## Measured scope and line attribution (`sample-v2`)

Each file of a sample has an **origin**, the author of its content:

| Origin | Meaning | Measured by default |
| --- | --- | --- |
| `authored` | Written by the model in a non-agent job (scaffolding, custom, legacy, inline) | yes |
| `added` | A file the agent created | yes |
| `modified` | A changed line of a scaffold file the agent edited | yes |
| `scaffold` | Stack skeleton content the model did not change | no |
| `platform` | Platform-owned build files (`Dockerfile`, `.dockerignore`, `docker-compose.yml`) | no |
| `unknown` | Origin not recorded | no |

For an agent's `modified` file, line-level attribution diffs the final file against the scaffold the agent started from (Python `difflib.SequenceMatcher` with autojunk off). Lines equal to the scaffold are `scaffold`; the others are `modified`. The scaffold is regenerated with generation's own seeding and trusted only when it reproduces every `scaffold` file of the agent's workspace manifest byte for byte. Otherwise the file is attributed whole (every line `modified`), and the sample records `attribution: whole`. Study preflights report it per target as the `whole_file_attribution` eligibility note.

Each file also has a **kind** (`source`, `config`, `manifest`, `lockfile`, `doc`, `asset`, `build`, `env`, `other`). The default scope excludes lockfiles, build output (minified JS/CSS: a line of at least 1000 characters and an average line length of at least 200), assets, vendored and build directories, and files over 512 KiB.

The **measured scope** is the set of lines whose origin is in `scope.measured_origins` (default `authored`, `added`, `modified`) in included files whose kind is in `scope.measured_kinds` (default `source`). **Measured SLOC** is the number of code lines in the measured scope; it is the denominator of every density. A finding is **in the measured scope** when its line is. Both are computed from the same attribution, and the measurement checks every finding's line origin against the attribution stored at submission.

The sample is identified by `sample_hash` = sha256 over the sorted lines `path\0sha256(content)\n` of its included files. The attribution has its own digest, so two resolutions that attribute lines differently never look identical.

## Mapping tables (`norm-v2`)

The tables live in `backend/analysis/data/mappings/`. Their sha256 hashes are frozen into every plan (`mapping_hashes`) and stamped on every finding, so changing a table makes new plans incomparable with old ones and makes runs that mix tables NA (`mapping_drift`).

| Table | sha256 (current) | Content |
| --- | --- | --- |
| `severity.yaml` | `0a28f87cab12ec24…` | Per tool: `message_rules` → `rules` → `prefixes` → `native` token → `default`; semgrep impact × likelihood matrix; bandit default severities for ruff's `S###` re-implementations; band rules for vulture confidence, lizard CCN and jscpd clone length; secret downgrades |
| `categories.yaml` | `237db25afbdaf507…` | Per tool and rule: the finding category; `emits` (the categories each tool can report, for per-category availability); the uncounted categories |
| `cwe.yaml` | `e54545b6cf54b234…` | Curated (tool, rule) → CWE, used only when the tool reports none (MITRE CWE 4.x); 77 bandit tests, 24 ESLint rules, plus pylint, ruff, hadolint, gitleaks and detect-secrets |
| `rule_equivalence.yaml` | `0451c96f5529e572…` | 27 cross-tool equivalence classes for deduplication |

The hashes in the table are the first 16 hex digits at the time of writing. The authoritative values are in each plan's `mapping_hashes` and `GET /api/analysis/image/`.

**Severity** is an impact level (`critical`, `high`, `medium`, `low`, `info`), never a tool's configuration level (ESLint's 2/1 says how a rule was configured, so ESLint maps by rule id). Examples: bandit and semgrep native tokens (`HIGH` → high, semgrep `ERROR` → high, `WARNING` → medium, `INFO` → low); when a semgrep rule declares both impact and likelihood the matrix wins (HIGH × HIGH or HIGH × MEDIUM → high, …); lizard CCN ≥ 41 → medium, ≥ 21 → low, else info; vulture confidence ≥ 90 → low, else info. A ruff `S###` rule takes the default severity of the equivalent bandit test (`severity_basis: bandit_default`), except where ruff's message states the context bandit uses (S602 "seems safe" → low, "identified" → high). A `secrets` finding on an example or template file (`*.example`, `.env.sample`, …) or with a placeholder value (`changeme`, `your_api_key`, `example`, `dummy`, …) is downgraded to `low`. The AI reviewer's own severity label is kept.

**Categories**: `security`, `secrets`, `bug`, `type`, `maintainability`, `dead_code`, `duplication`, `complexity`, `style`, `container`, `dependency`, `other`. By default `style` is uncounted (counted with `outcomes.count_style`), and `complexity` and `duplication` findings are uncounted (counted with `outcomes.count_metric_findings`) because the measurement reports those as metrics and counting their threshold crossings as findings would count them twice.

**CWE** comes from the tool when it reports one (bandit's `issue_cwe`, semgrep's `metadata.cwe`), else from `cwe.yaml`, normalized to sorted unique `CWE-n` ids.

**Counted.** A static finding is counted when it is in the measured scope, is not a parse error and is of a counted category. An AI finding is counted only by consensus (below), under the same scope and category rules.

## Deduplication

Several tools report the same defect (ruff `S` mirrors bandit, semgrep covers much of bandit, gitleaks and detect-secrets flag the same key, ruff, pylint and vulture all report one unused import). `dedup-v2` clusters findings into **issues**, each identified by an `issue_key`:

1. Findings are clustered separately per namespace: counted static, uncounted static, and AI. Counted and uncounted findings never share an issue.
2. Candidate pairs (same file):
   - different tools: line distance ≤ 2 (overlapping ranges have distance 0) **and** a shared CWE or a shared equivalence class (`rule_equivalence.yaml`, `tool:glob` members, e.g. `unused-import` = ruff F401, flake8 F401, pylint W0611, vulture "unused import"; `sql-injection` = bandit B608, ruff S608, semgrep `*sql-injection*`, …);
   - the same tool: the same line and the same rule, CWE or class (one problem under several rules); a tool never merges across lines.
3. Union-find over the pairs, sorted by (distance, canonical order), with a cannot-link constraint: a cluster holds at most one line per tool, so chains across neighbouring lines stop. The result does not depend on the input order.
4. `issue_key` = sha256 over the namespace and the members' fingerprints and lines.

An issue's severity is its most severe member's, except that a ruff proxy severity (`bandit_default`) does not decide it when the issue also contains a bandit finding, whose severity bandit derived from the code. An issue's category and CWE come from the same member.

A finding's **fingerprint** is sha256 of the tool, the rule family, the path and the whitespace-normalized source line (no line number), so it is stable when code moves.

**AI vs static overlap.** Counted AI consensus issues and counted static issues are matched one-to-one, closest first, when they are on the same file within 3 lines and share a CWE or the category. Reported as `matched`, `jaccard` = matched / (static + AI − matched), `ai_confirmed_share` and `static_found_by_ai_share`.

## Outcomes

Each run's `measurement.outcomes()` gives one value per outcome, or `None`. Densities are per 1000 measured SLOC and `None` below `outcomes.min_sloc_for_density` (default 50).

| Key | Definition | NA when |
| --- | --- | --- |
| `static_findings_per_kloc` | Counted static findings (all tools, before dedup) / measured KLOC | static unavailable |
| `static_issues_per_kloc` | Distinct counted static issues / measured KLOC (the default primary outcome) | static unavailable |
| `security_issues_per_kloc` | Distinct counted static issues of category security or secrets / measured KLOC | security or secrets category unavailable |
| `high_plus_issues_per_kloc` | Distinct counted static issues of severity high or critical / measured KLOC | static unavailable |
| `has_high_security` | At least one counted high/critical security or secrets issue (binary) | none found and the security categories unavailable |
| `ai_consensus_per_kloc` | Grounded, in-scope AI consensus issues / measured KLOC | no review, review failed, or the reviewer did not see all measured code |
| `complexity_max`, `complexity_mean` | Highest and mean cyclomatic complexity of the functions in the measured scope (lizard; radon when lizard is not planned) | that tool not OK |
| `duplication_pct` | Share of duplicated lines in the measured files (jscpd, recomputed per (file, line)) | jscpd not OK |
| `functional_pass_rate` | Hidden conformance suite pass rate | not evaluated, not applicable or infrastructure error |
| `measured_sloc` | The density denominator | sample unavailable |
| `suppression_markers_per_kloc` | Suppression markers on measured lines / measured KLOC | sample unavailable |
| `static_issues`, `security_issues`, `high_plus_issues` | The counts behind the densities | as above |
| `weighted_per_kloc` | Issues weighted by `outcomes.severity_weights` (critical 10, high 5, medium 2, low 1, info 0) / measured KLOC; a secondary leaderboard score | static unavailable |
| `ai_grounding_rate` | Share of AI findings (all replicates) whose evidence was found (exact or relocated) | no successful review, or no AI findings |
| `ai_mean_pairwise_jaccard` | Agreement between replicates (below) | no successful review, or fewer than 2 successful replicates |

**Availability.** A tool is OK when it succeeded or was not applicable. Static outcomes are unavailable when a required static tool is not OK; when an optional tool is not OK that could have reported counted findings on the measured code (it emits a counted category and covers a measured file's language); when no static tool ran OK; when more than `outcomes.max_unparseable_share` (default 0.1) of the measured SLOC is in files some static tool could not parse; or when an integrity check fails (`attribution_mismatch`, `mapping_drift`). A category is unavailable when a tool that can emit it is not OK. Two runs of one plan therefore never compare a count made with a tool against one made without it.

## AI reviewer: grounding, consensus and agreement

**Instrument.** The prompt `review-v1` and the JSON schema `findings-v1` (`data/review/`) are frozen into the plan by sha256. A finding has `file`, `start_line`, `end_line`, `evidence` (at most 3 verbatim lines), `category`, `cwe` (`CWE-n` or null), `severity`, `confidence` (0..1), `title`, `rationale` and `fix`. The model sees in-scope files with line numbers, in a seeded per-replicate order; a `modified` file is shown whole, and each finding is attributed to the origin of its line.

**Grounding** of a finding, in order:

1. the file is not in the sample, or was not shown to the model → `invalid_file`;
2. not `1 ≤ start ≤ end ≤ lines` → `out_of_range`;
3. the whitespace-normalized evidence occurs within the cited range widened by 3 lines → `exact`;
4. it occurs elsewhere in the file → `relocated` (lines moved to the occurrence nearest the cited start);
5. otherwise → `ungrounded`.

Matching is a substring test over the file's non-blank lines with whitespace collapsed, on identifier boundaries. Evidence shorter than 12 significant characters never grounds by relocation. Line-number prefixes copied from the listing are tolerated when every evidence line carries one. Only `exact` and `relocated` findings can be counted.

**Consensus.** Let k be the planned replicates and m the successful ones. Findings of the m replicates are clustered by anchor (not transitively): same file, same place (the lines the evidence occupies, within 3 lines; or the same cited range), and same weakness (the same CWE when both have one, else the same category). A point wider than 7 lines is a region and only clusters with a finding citing the same range. A finding prefers a cluster its replicate is not yet in. A cluster is **consensus** when at least `k // 2 + 1` replicates reported it (a strict majority of the *planned* k, so a failed replicate votes for nothing). The **quorum** for a successful review is the same number of successful replicates. One representative per consensus cluster is counted, if it is grounded, in the measured scope and of a counted category; it carries the most frequent severity among the cluster's grounded in-scope members (ties go to the more severe).

**Replacement.** A replicate that failed for a sample-level reason (truncated, invalid or empty output, incomplete review, transient API error) is replaced, up to `max_replacements` (default k), by a new replicate with the next index and its own seed. The review aims at k successful replicates. Fewer than k successful replicates mark the count a lower bound (`replicates_complete: false`).

**Agreement.** Per run, the mean pairwise **Jaccard index** of the replicates' cluster sets (J(∅, ∅) = 1). Across a study, **Krippendorff's α** (nominal) over units (target, file, category), where each successful replicate of a run with at least 2 successful replicates codes presence or absence, and failed replicates are missing values.

**Spend.** Usage and cost come from the provider (OpenRouter) or the gateway's call log (agent engine). Unknown spend is `None`; a partly known sum is a lower bound (`cost_complete: false`); local models cost a known 0.0.

## Statistics (`stats-v1`)

Pure Python, deterministic (seeded bootstrap), and tested against `scipy.stats` 1.15 reference values. `None`, NaN and non-numeric entries are missing data and are dropped before computing, with `n_missing` reported. Undefined results (too few observations, every value tied) are `None`, never 0 or 1.

| Quantity | Definition |
| --- | --- |
| Descriptives | n, n_missing, mean, sample SD (n − 1; None for n < 2), median, Q1, Q3, IQR (Hyndman–Fan type 7 quantiles), min, max |
| Bootstrap CI | Percentile bootstrap of the mean, `bootstrap_resamples` draws (default 10 000) from `random.Random(seed)`; the cluster bootstrap resamples whole blocks (apps). None for fewer than 2 observations |
| Proportion CI | Wilson score interval, no continuity correction |
| Kruskal–Wallis | H with tie correction; χ² p-value with g − 1 df; effect ε² = H / (n − 1) |
| Friedman | χ² over complete blocks with tie correction; effect Kendall's W = χ² / (n (k − 1)) |
| Mann–Whitney U | Exact null distribution when a sample has ≤ 8 observations and there are no ties, else the tie-corrected normal approximation with continuity correction |
| Wilcoxon signed-rank | Zero differences dropped (`wilcox`), exact distribution for n ≤ 50 without ties or zeros, exact sign-flip permutation for n ≤ 13 with ties, else normal approximation |
| Cliff's δ | P(X > Y) − P(X < Y); magnitude negligible < 0.147 ≤ small < 0.33 ≤ medium < 0.474 ≤ large; percentile bootstrap CI (paired designs resample pairs) |
| Vargha–Delaney Â12 | P(X > Y) + 0.5 P(X = Y) |
| Matched-pairs rank-biserial | (R⁺ − R⁻) / (R⁺ + R⁻) |
| Holm | Family-wise error control: confirmatory family and pairwise comparisons within an outcome |
| Benjamini–Hochberg | False discovery rate: exploratory family |
| Krippendorff's α (nominal) | 1 − D_o / D_e; None when nothing is pairable or only one category occurs |
| Spearman ρ | With the t-approximation p-value; None for fewer than 3 pairs or a constant variable |

**Unit of analysis.** Replicates of one app are correlated, so the report aggregates them into one value per (group, block) cell (`cell_aggregate`: mean or median) before testing. With a complete blocked design (every group in every block) it uses Friedman and Wilcoxon on the cells; otherwise Kruskal–Wallis and Mann–Whitney. A run without a known block is its own unit and is never paired.

## References

- Benjamini, Y., & Hochberg, Y. (1995). Controlling the false discovery rate: a practical and powerful approach to multiple testing. *Journal of the Royal Statistical Society B*, 57(1), 289–300.
- Cliff, N. (1993). Dominance statistics: ordinal analyses to answer ordinal questions. *Psychological Bulletin*, 114(3), 494–509.
- Efron, B., & Tibshirani, R. J. (1993). *An Introduction to the Bootstrap*. Chapman & Hall.
- Friedman, M. (1937). The use of ranks to avoid the assumption of normality implicit in the analysis of variance. *Journal of the American Statistical Association*, 32(200), 675–701.
- Holm, S. (1979). A simple sequentially rejective multiple test procedure. *Scandinavian Journal of Statistics*, 6(2), 65–70.
- Hyndman, R. J., & Fan, Y. (1996). Sample quantiles in statistical packages. *The American Statistician*, 50(4), 361–365.
- Jaccard, P. (1912). The distribution of the flora in the alpine zone. *New Phytologist*, 11(2), 37–50.
- Kendall, M. G., & Babington Smith, B. (1939). The problem of m rankings. *Annals of Mathematical Statistics*, 10(3), 275–287.
- Kerby, D. S. (2014). The simple difference formula: an approach to teaching nonparametric correlation. *Comprehensive Psychology*, 3, 11.IT.3.1.
- Krippendorff, K. (2011). Computing Krippendorff's alpha-reliability. Annenberg School for Communication, University of Pennsylvania.
- Kruskal, W. H., & Wallis, W. A. (1952). Use of ranks in one-criterion variance analysis. *Journal of the American Statistical Association*, 47(260), 583–621.
- Mann, H. B., & Whitney, D. R. (1947). On a test of whether one of two random variables is stochastically larger than the other. *Annals of Mathematical Statistics*, 18(1), 50–60.
- Romano, J., Kromrey, J. D., Coraggio, J., & Skowronek, J. (2006). Appropriate statistics for ordinal level data: should we really be using t-test and Cohen's d for evaluating group differences on the NSSE and other surveys? Annual meeting of the Florida Association of Institutional Research.
- Tomczak, M., & Tomczak, E. (2014). The need to report effect size estimates revisited. *Trends in Sport Sciences*, 1(21), 19–25.
- Vargha, A., & Delaney, H. D. (2000). A critique and improvement of the CL common language effect size statistics of McGraw and Wong. *Journal of Educational and Behavioral Statistics*, 25(2), 101–132.
- Wilcoxon, F. (1945). Individual comparisons by ranking methods. *Biometrics Bulletin*, 1(6), 80–83.
- Wilson, E. B. (1927). Probable inference, the law of succession, and statistical inference. *Journal of the American Statistical Association*, 22(158), 209–212.
- MITRE. *Common Weakness Enumeration (CWE)*, version 4.x. https://cwe.mitre.org/
