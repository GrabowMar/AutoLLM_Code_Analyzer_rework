/**
 * The notebook a report opens in JupyterLite (the report page's Notebook tab): a Jupyter
 * notebook (nbformat 4) plus `report.json` written next to it. The notebook loads the
 * report's curated tables as pandas DataFrames ("-" → NaN, numeric columns converted),
 * shows each table with a quick chart where one makes sense, keeps the raw `report_data`,
 * offers a re-ranking cell for study reports, and ends with a cell that re-fetches the
 * report live from the API (same-origin `pyfetch`, the browser session authenticates it).
 */
import type { ReportDetail, ReportDocument } from "$lib/api/reports";
import { chartSpec } from "./chart";
import { planTable, reportAccession } from "./format";
import { rankingData } from "./ranking";

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
  cells: Cell[];
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
  const dir = `reports/${reportAccession(report.report_id)}`;
  return { dir, notebook: `${dir}/report.ipynb`, data: `${dir}/report.json` };
}

/** `report.json`: what the notebook loads (tables as the page shows them, plus the raw data). */
export function notebookData(report: ReportDetail, doc: ReportDocument) {
  return {
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
        `Python runs in your browser (Pyodide); edits are saved in this browser only. [Back to the report](${origin}/reports/${report.report_id})`,
      ].join("\n"),
    ),
  );
  cells.push(
    code(
      [
        "import json, re",
        "import pandas as pd",
        "import matplotlib.pyplot as plt",
        "",
        "pd.set_option('display.max_columns', 40)",
        "with open('report.json') as f:",
        "    report = json.load(f)",
        "",
        "_NUM = re.compile(r'^[+\\-\\u2212]?(\\d{1,3}(,\\d{3})+|\\d+)?(\\.\\d+)?(e[+-]?\\d+)?%?$', re.I)",
        "",
        "def frame(t):",
        '    """One report table as a DataFrame: \'-\' -> NaN, all-numeric columns -> numbers (\'%\' kept as a fraction of 100)."""',
        "    df = pd.DataFrame(t['rows'], columns=t['columns']).replace({'-': pd.NA, '': pd.NA})",
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

  doc.tables.forEach((t, i) => {
    const n = i + 1;
    const plan = planTable(t);
    const spec = chartSpec(t, plan);
    cells.push(
      md(`## Table ${n} · ${t.title}${t.note ? `\n\n${t.note}` : ""}`),
    );
    if (t.rows.length === 0) {
      cells.push(code(`T[${n}]  # no rows in this report's scope`));
      return;
    }
    const lines = [`T[${n}]`];
    if (spec) {
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
          "w = np.array([max(weights[k], 0) for k in weights], float); w = w / w.sum()",
          "v = rows.to_numpy(float) / np.where(np.linalg.norm(rows.to_numpy(float), axis=0) == 0, 1, np.linalg.norm(rows.to_numpy(float), axis=0)) * w",
          "benefit = np.array([crit[k]['direction'] == 'benefit' for k in weights])",
          "ideal = np.where(benefit, v.max(0), v.min(0)); anti = np.where(benefit, v.min(0), v.max(0))",
          "dp, dm = np.linalg.norm(v - ideal, axis=1), np.linalg.norm(v - anti, axis=1)",
          "score = np.where(dp + dm > 0, dm / (dp + dm), 0.5)",
          "out = pd.DataFrame({'score': score, 'report_rank': pd.DataFrame(rk['ranking']).set_index('model').loc[rows.index, 'topsis_rank']}, index=rows.index)",
          "out['rank'] = out['score'].rank(ascending=False, method='min').astype(int)",
          "out.sort_values('rank')",
        ].join("\n"),
      ),
    );
  }

  cells.push(
    md(
      "## Live data\n\nRe-fetch this report from the LLM Lab API (your browser session authenticates the request). Any read endpoint works the same way, e.g. `/api/reports/` for every report.",
    ),
  );
  cells.push(
    code(
      [
        "from pyodide.http import pyfetch",
        `resp = await pyfetch(${py(`/api/reports/${report.report_id}/tables/`)})`,
        "live = await resp.json()",
        "[(t['title'], len(t['rows'])) for t in live['tables']]",
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
        generated_from: report.completed_at,
      },
    },
    cells,
  };
}
