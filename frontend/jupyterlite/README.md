# Report notebooks

Create an **Experiment report** from an existing study in Reports, open its
**Notebook** tab, then choose **Run all**. This analyzes the report's saved data;
it does not start model generations, rerun scanners, or collect human labels.
Generate a fresh report to incorporate newer observations or newly available
per-group tool measurements.

The workflow produces:

- Normalized scorecards, static-tool heatmaps, source size versus issue density,
  normalized-severity stacks, functional pass-rate distributions, and task matrices.
- Outcome intervals, tool overlap, correlation results with recorded corrections,
  TOPSIS/WSM and weight/data sensitivity, declared paired comparisons, and
  exploratory paired comparisons within a completed experiment without a baseline plan.
- PNG/SVG figures, full-precision CSV/JSON data, the source snapshot, and a manifest
  with dataset hash, package versions, completed/skipped/error steps and evidence gaps.

Use **Results ZIP** to download the bundle. **.ipynb** downloads the open notebook,
including edits and executed outputs; before Jupyter starts it downloads the generated
version with an embedded snapshot. Edits persist in this browser's storage. **Reset**
replaces edits with the current generated workflow. The optional live-data helper is
not called by Run all, so the snapshot remains reproducible.

Missing observations remain missing. Repeats are averaged within each app before
app-level summaries. Functional pass rates measure recorded functional tests, not
human agreement with an AI evaluator. Human validation, scanner false-positive
adjudication and paired repair-effect cohorts are explicitly unavailable when absent.
Completed execution does not mean these evidence requirements have been satisfied.
Task-specific reference validation is listed per stack and evaluation suite; general
probe controls do not validate every task. Single-run cohorts cannot establish
within-model repeat variability. Subscription or zero quoted prices do not establish
free generation or economic superiority.
Raw tool findings are not cross-tool-deduplicated; coverage accompanies their heatmap.

## Runtime

JupyterLite runs Python in the browser using Pyodide. The first kernel start downloads
Python and scientific packages from the configured CDN; network access is needed.
Both frontend Docker builds include JupyterLite. The local entrypoint seeds the static
site when a source bind mount hides it. To build manually:

```sh
frontend/jupyterlite/build.sh
```

For desktop Jupyter, install `numpy pandas matplotlib ipython`, open the downloaded
notebook and run all cells. A matching `report.json` next to the notebook takes
precedence over its embedded snapshot. Optional live API access is browser-only.

## Verification

Study reports can select an explicit evaluation suite in the creation form or with
`study_report --eval-suite SLUG@VERSION#HASH`. The report then uses the latest final
non-retest result from that instrument for every selected run. Missing results stay
missing; the original planned instrument and source cohort remain recorded.
Prompt-tagged sensitivity separates stated and unstated check subsets without
changing the strict instrument score. These annotations do not establish complete
prompt compliance or interactive UI correctness.

Completed source-bound experiments can be imported with `study_report --evidence
PATH.json`. Supported blocks are `lock_materialization` and `feature_lifecycle`;
their frozen protocols, complete selections, raw outcomes, controls and source
hashes are validated before import. Summaries are recomputed from observations.
The notebook exports these supplemental observations and plots alongside the
primary analysis. Docker builds and runtime experiments execute outside the
browser; the notebook analyzes the resulting immutable evidence.

```sh
# From frontend, using the project's Node dependencies:
npm test -- src/lib/components/reports/notebook.test.ts src/lib/components/reports/jupyter.test.ts src/lib/components/reports/interactive.test.ts
npm run check

# From the repository root, in a Python environment with numpy/pandas/matplotlib:
python scripts/tests/test_report_workflow.py

# In the configured Django test environment:
pytest backend/reports/tests
```

The portable workflow tests execute real plotting and ZIP export with complete,
missing and repeated observations; they do not need the Django database.
