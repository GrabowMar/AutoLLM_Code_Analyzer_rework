/**
 * Portable report notebook and snapshot for embedded JupyterLite or desktop Jupyter.
 * Experiment reports run the complete analysis workflow from unrounded observations.
 * Curated tables remain available as DataFrames; live API access is an optional helper.
 */
import type { ReportDetail, ReportDocument } from "$lib/api/reports";
import analysisWorkflow from "./analysis_workflow.py?raw";
import { chartSpec } from "./chart";
import { planTable, reportAccession } from "./format";
import { rankingData } from "./ranking";

export const NOTEBOOK_WORKFLOW_VERSION = 4;

/** Saved notebooks retain edits; callers offer an explicit reset for older generated workflows. */
export function notebookNeedsUpdate(notebook: unknown): boolean {
  const version = (
    notebook as { metadata?: { llmlab?: { workflow_version?: unknown } } }
  )?.metadata?.llmlab?.workflow_version;
  return typeof version !== "number" || version < NOTEBOOK_WORKFLOW_VERSION;
}

type Cell =
  | { cell_type: "markdown"; metadata: Record<string, unknown>; source: string }
  | {
      cell_type: "code";
      metadata: Record<string, unknown>;
      source: string;
      execution_count: null;
      outputs: never[];
    };

export interface Notebook {
  nbformat: 4;
  nbformat_minor: 5;
  metadata: Record<string, unknown>;
  cells: (Cell & { id: string })[];
}

const md = (source: string): Cell => ({
  cell_type: "markdown",
  metadata: {},
  source,
});
const code = (source: string): Cell => ({
  cell_type: "code",
  metadata: {},
  source,
  execution_count: null,
  outputs: [],
});

/** A Python string literal (JSON strings are valid Python string literals). */
const py = (s: string) => JSON.stringify(s);

/** Folder and file names of a report's notebook in the JupyterLite drive. */
export function notebookPaths(report: Pick<ReportDetail, "report_id">) {
  const dir = `reports/${reportAccession(report.report_id)}-${encodeURIComponent(report.report_id)}`;
  return { dir, notebook: `${dir}/report.ipynb`, data: `${dir}/report.json` };
}

/** `report.json`: what the notebook loads (tables as the page shows them, plus the raw data). */
export function notebookData(report: ReportDetail, doc: ReportDocument) {
  return {
    workflow_version: NOTEBOOK_WORKFLOW_VERSION,
    report_id: report.report_id,
    report_type: report.report_type,
    title: report.title,
    description: report.description,
    config: report.config,
    summary: report.summary,
    created_at: report.created_at,
    completed_at: report.completed_at,
    tables: doc.tables.map((t) => ({
      title: t.title,
      columns: t.columns,
      rows: t.rows,
      note: t.note,
      total_rows: t.totalRows,
    })),
    report_data: report.report_data,
  };
}

export function buildNotebook(
  report: ReportDetail,
  doc: ReportDocument,
  origin = "",
): Notebook {
  const acc = reportAccession(report.report_id);
  const cells: Cell[] = [];
  cells.push(
    md(
      [
        `# ${report.title}`,
        "",
        `**${acc}** · ${report.report_type.replaceAll("_", " ")} · generated ${report.completed_at ?? report.created_at}`,
        report.description ? `\n> ${report.description}` : "",
        "",
        `The report's ${doc.tables.length} tables are loaded below as pandas DataFrames (\`T[1]\` … \`T[${doc.tables.length}]\`, numbered as on the report page). ` +
          "Cells keep the report's formatting until converted: `-` means *not measured* and becomes `NaN`, never 0. " +
          "The raw payload behind them is `report['report_data']`.",
        "",
        report.report_type === "study_report"
          ? "Choose **Run all** to create the figures, CSV tables, statistical results, evidence checks and `report-output.zip`. Missing measurements are listed as unavailable. This analyzes the saved report; it does not launch model generations or collect new human labels."
          : "Choose **Run all** to explore the report tables. Create an Experiment report for the complete statistical and figure workflow.",
        "",
        `Python runs in your browser (Pyodide); edits are saved in this browser only. [Back to the report](${origin}/reports/${report.report_id})`,
      ].join("\n"),
    ),
  );
  cells.push(
    code(
      [
        "import json, re",
        "from pathlib import Path",
        "from IPython.display import display",
        "import pandas as pd",
        "import matplotlib.pyplot as plt",
        "",
        "pd.set_option('display.max_columns', 40)",
        "if Path('report.json').exists():",
        "    with open('report.json') as f:",
        "        report = json.load(f)",
        "else:",
        `    report = json.loads(${py(JSON.stringify(notebookData(report, doc)))})`,
        "",
        `if report.get('report_id') != ${py(report.report_id)}:`,
        "    raise ValueError('report.json belongs to a different report; use the matching snapshot')",
        "",
        "_NUM = re.compile(r'^[+\\-\\u2212]?(\\d{1,3}(,\\d{3})+|\\d+)?(\\.\\d+)?(e[+-]?\\d+)?%?$', re.I)",
        "",
        "def frame(t):",
        '    """One report table as a DataFrame: \'-\' -> NaN; numeric columns -> numbers (\'25%\' -> 25, percentage points)."""',
        "    df = pd.DataFrame(t['rows'], columns=t['columns']).replace({'-': pd.NA, '': pd.NA})",
        "    used, columns = set(), []",
        "    for i, c in enumerate(df.columns):",
        "        name = c",
        "        while name in used:",
        "            name += f' [{i + 1}]'",
        "        used.add(name); columns.append(name)",
        "    df.columns = columns",
        "    for c in df.columns:",
        "        vals = df[c].dropna().astype(str)",
        "        if len(vals) and vals.map(lambda v: bool(_NUM.match(v))).all():",
        "            df[c] = pd.to_numeric(df[c].astype('string').str.replace(',', '').str.replace('\\u2212', '-').str.rstrip('%'), errors='coerce')",
        "    df.attrs['title'], df.attrs['note'] = t['title'], t['note']",
        "    return df",
        "",
        "T = {i + 1: frame(t) for i, t in enumerate(report['tables'])}",
        "pd.DataFrame({'table': [t['title'] for t in report['tables']], 'rows': [t['total_rows'] for t in report['tables']]}, index=list(T))",
      ].join("\n"),
    ),
  );

  if (report.report_type === "study_report") {
    cells.push(
      md(
        [
          "## Reproducible analysis workflow",
          "",
          "Uses complete, unrounded report data rather than the preview tables below. Repeats are averaged within app units; missing measurements remain missing. Statistical tests, correction families and ranking sensitivity come from the report's recorded analysis. Additional descriptive plots are exploratory.",
          "",
          "The output includes PNG/SVG figures, CSV tables, the source snapshot and a manifest with skipped analyses and evidence gaps. Functional pass rate is an observed test outcome, not a human-validated AI assessment. Re-running replaces generated outputs in `report-output`.",
        ].join("\n"),
      ),
    );
    cells.push(code(analysisWorkflow));
    cells.push(
      code(
        [
          "workflow = run_report_workflow(report, output_dir='report-output')",
          "from IPython.display import Image, FileLink",
          "display(pd.DataFrame(workflow['manifest']['steps'])[['name', 'status', 'reason']])",
          "display(pd.DataFrame(workflow['manifest'].get('evidence_checks', [])))",
          "for filename in workflow['manifest']['files']:",
          "    if filename.endswith('.png'):",
          "        display(Image(filename=str(Path(workflow['output_dir']) / filename)))",
          "display(FileLink(workflow['archive']))",
          "if workflow['manifest']['status'] == 'error':",
          "    raise RuntimeError('Some analysis steps failed. Inspect the status table and manifest in the output ZIP.')",
        ].join("\n"),
      ),
    );
  }

  doc.tables.forEach((t, i) => {
    const n = i + 1;
    const plan = planTable(t);
    const spec =
      new Set(t.columns).size === t.columns.length ? chartSpec(t, plan) : null;
    cells.push(
      md(`## Table ${n} · ${t.title}${t.note ? `\n\n${t.note}` : ""}`),
    );
    if (t.rows.length === 0) {
      cells.push(code(`T[${n}]  # no rows in this report's scope`));
      return;
    }
    const lines = [`T[${n}]`];
    if (spec && report.report_type !== "study_report") {
      const label = t.columns[spec.labelIndex];
      const metric = spec.metrics[0];
      lines[0] = `df = T[${n}]`;
      lines.push(
        `col = ${py(metric.header)}`,
        "if pd.api.types.is_numeric_dtype(df[col]):",
        `    ax = df.set_index(${py(label)})[col].head(40).iloc[::-1].plot.barh(figsize=(7, max(2, 0.35 * min(len(df), 40))), color='#3b5bdb')`,
        `    ax.set_xlabel(col); ax.set_ylabel(''); ax.set_title(${py(t.title)}, loc='left')`,
        "    plt.tight_layout(); plt.show()",
        "df",
      );
    }
    cells.push(code(lines.join("\n")));
  });

  const rk = rankingData(report.report_data);
  if (rk) {
    const weights = Object.fromEntries(
      rk.criteria.map((c) => [c.key, c.weight]),
    );
    cells.push(
      md(
        "## What if: re-rank with your own weights\n\nTOPSIS as the report computes it (vector normalisation, ideal and anti-ideal per criterion). Change the weights and run the cell; they are renormalised to sum to 1.",
      ),
    );
    cells.push(
      code(
        [
          "import numpy as np",
          `weights = ${JSON.stringify(weights, null, 1).replace(/\n\s*/g, " ")}`,
          "rk = report['report_data']['ranking']",
          "crit = {c['key']: c for c in rk['criteria']}",
          "rows = pd.DataFrame(rk['ranking']).set_index('model')[list(weights)].dropna()",
          "w = np.array([weights[k] for k in weights], float)",
          "if not np.isfinite(w).all() or (w < 0).any() or w.sum() <= 0:",
          "    raise ValueError('Weights must be finite, nonnegative, with a positive sum')",
          "w = w / w.sum()",
          "if rows.empty:",
          "    raise ValueError('No group has every selected ranking criterion')",
          "v = rows.to_numpy(float) / np.where(np.linalg.norm(rows.to_numpy(float), axis=0) == 0, 1, np.linalg.norm(rows.to_numpy(float), axis=0)) * w",
          "benefit = np.array([crit[k]['direction'] == 'benefit' for k in weights])",
          "ideal = np.where(benefit, v.max(0), v.min(0)); anti = np.where(benefit, v.min(0), v.max(0))",
          "dp, dm = np.linalg.norm(v - ideal, axis=1), np.linalg.norm(v - anti, axis=1)",
          "score = np.divide(dm, dp + dm, out=np.full_like(dm, 0.5), where=dp + dm > 0)",
          "out = pd.DataFrame({'score': score, 'report_rank': pd.DataFrame(rk['ranking']).set_index('model').loc[rows.index, 'topsis_rank']}, index=rows.index)",
          "order = sorted(out.index, key=lambda name: (-out.loc[name, 'score'], name))",
          "group_score, group_rank = None, 0",
          "ranks = {}",
          "for position, name in enumerate(order, 1):",
          "    value = out.loc[name, 'score']",
          "    if group_score is None or abs(value - group_score) > 1e-12:",
          "        group_score, group_rank = value, position",
          "    ranks[name] = group_rank",
          "out['rank'] = pd.Series(ranks).astype(int)",
          "out.sort_values('rank')",
        ].join("\n"),
      ),
    );
  }

  cells.push(
    md(
      "## Optional live data\n\nThis defines a helper; Run all keeps the saved snapshot. In JupyterLite, explicitly call `await fetch_live_tables()` to read the current API tables using your browser session. Generate a new report to analyze newly collected runs.",
    ),
  );
  cells.push(
    code(
      [
        "async def fetch_live_tables():",
        "    from pyodide.http import pyfetch",
        `    resp = await pyfetch(${py(`/api/reports/${encodeURIComponent(report.report_id)}/tables/`)})`,
        "    if not resp.ok:",
        "        raise RuntimeError(f'Report request failed: HTTP {resp.status}')",
        "    return await resp.json()",
      ].join("\n"),
    ),
  );
  cells.push(md("## Scratch\n\nYour own analysis goes here."));
  cells.push(code(""));

  return {
    nbformat: 4,
    nbformat_minor: 5,
    metadata: {
      kernelspec: {
        name: "python",
        display_name: "Python (Pyodide)",
        language: "python",
      },
      language_info: { name: "python" },
      llmlab: {
        report_id: report.report_id,
        workflow_version: NOTEBOOK_WORKFLOW_VERSION,
        generated_from: report.completed_at,
      },
    },
    cells: cells.map((cell, i) => ({ ...cell, id: `report-cell-${i + 1}` })),
  };
}
