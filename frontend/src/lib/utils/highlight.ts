/**
 * Shared Shiki highlighter: one lazily created instance per page load, used
 * by the docs Markdown renderer (`lib/docs/markdown.ts`) and the analysis
 * code viewer (`components/analysis/CodeViewer.svelte`).
 *
 * Output is dual-themed without a default color: every token carries the
 * `--shiki-light` / `--shiki-dark` CSS variables and the consumer's CSS picks
 * one per color mode, so switching themes never re-highlights.
 *
 * Shiki is imported dynamically: pages that never show code never load it.
 */

import type { BundledLanguage, Highlighter } from "shiki";

export const SHIKI_THEMES = {
  light: "github-light",
  dark: "github-dark",
} as const;

/** Grammars loaded up front (the docs' fenced-code vocabulary); others load on demand. */
export const SHIKI_LANGS = [
  "bash",
  "sh",
  "shell",
  "console",
  "python",
  "py",
  "javascript",
  "js",
  "typescript",
  "ts",
  "tsx",
  "jsx",
  "json",
  "jsonc",
  "yaml",
  "yml",
  "toml",
  "ini",
  "dockerfile",
  "docker",
  "html",
  "css",
  "scss",
  "svelte",
  "sql",
  "diff",
  "markdown",
  "md",
  "text",
  "plaintext",
];

/** Short or tool-specific language names → Shiki grammar ids. */
export const LANG_ALIASES: Record<string, string> = {
  py: "python",
  js: "javascript",
  ts: "typescript",
  yml: "yaml",
  docker: "dockerfile",
  sh: "bash",
  shell: "bash",
  console: "bash",
  plaintext: "text",
  md: "markdown",
  // Names the analysis sample resolver uses for manifest files.
  javascriptreact: "jsx",
  typescriptreact: "tsx",
  dotenv: "ini",
  env: "ini",
};

/** File extension → Shiki grammar id (fallback when no language is known). */
const EXTENSION_LANGS: Record<string, string> = {
  py: "python",
  pyi: "python",
  js: "javascript",
  mjs: "javascript",
  cjs: "javascript",
  jsx: "jsx",
  ts: "typescript",
  mts: "typescript",
  cts: "typescript",
  tsx: "tsx",
  vue: "vue",
  svelte: "svelte",
  json: "json",
  jsonc: "jsonc",
  html: "html",
  htm: "html",
  css: "css",
  scss: "scss",
  less: "less",
  md: "markdown",
  yml: "yaml",
  yaml: "yaml",
  toml: "toml",
  ini: "ini",
  cfg: "ini",
  sh: "bash",
  bash: "bash",
  sql: "sql",
  xml: "xml",
  svg: "xml",
  txt: "text",
};

/** Whole file names → Shiki grammar id. */
const FILENAME_LANGS: Record<string, string> = {
  dockerfile: "dockerfile",
  makefile: "make",
  ".env": "ini",
  ".gitignore": "text",
  ".dockerignore": "text",
};

let highlighterPromise: Promise<Highlighter> | null = null;

/**
 * The shared highlighter (themes {@link SHIKI_THEMES}, grammars
 * {@link SHIKI_LANGS}). A failed load is forgotten so a later call retries.
 */
export function getHighlighter(): Promise<Highlighter> {
  if (!highlighterPromise) {
    const promise = import("shiki").then((shiki) =>
      shiki.createHighlighter({
        themes: [SHIKI_THEMES.light, SHIKI_THEMES.dark],
        langs: SHIKI_LANGS,
      }),
    );
    highlighterPromise = promise;
    promise.catch(() => {
      if (highlighterPromise === promise) highlighterPromise = null;
    });
  }
  return highlighterPromise;
}

/** `lang` resolved through {@link LANG_ALIASES} if that grammar is loaded, else "text". */
export function resolveLanguage(
  lang: string,
  highlighter: Highlighter,
): string {
  const key = (lang || "text").toLowerCase();
  const aliased = LANG_ALIASES[key] ?? key;
  const loaded = new Set<string>(highlighter.getLoadedLanguages());
  return loaded.has(aliased) ? aliased : "text";
}

/**
 * Load a bundled grammar on demand (e.g. `vue`) and resolve to the id to
 * highlight with; unknown languages resolve to "text" (no highlighting).
 */
export async function ensureLanguage(lang: string): Promise<string> {
  const highlighter = await getHighlighter();
  const key = (lang || "text").toLowerCase();
  const id = LANG_ALIASES[key] ?? key;
  if (highlighter.getLoadedLanguages().includes(id)) return id;
  const { bundledLanguages } = await import("shiki");
  if (!(id in bundledLanguages)) return "text";
  try {
    await highlighter.loadLanguage(id as BundledLanguage);
    return id;
  } catch {
    return "text";
  }
}

/**
 * Grammar id for a file: the analysis manifest's `language` when given,
 * otherwise the file name or extension; "text" when nothing matches.
 */
export function languageForFile(
  path: string,
  language?: string | null,
): string {
  const known = (language ?? "").trim().toLowerCase();
  if (known && known !== "unknown" && known !== "other") {
    return LANG_ALIASES[known] ?? known;
  }
  const name = (path.split("/").pop() ?? "").toLowerCase();
  if (FILENAME_LANGS[name]) return FILENAME_LANGS[name];
  if (name.startsWith("dockerfile")) return "dockerfile";
  if (name.startsWith(".env")) return "ini";
  const dot = name.lastIndexOf(".");
  const ext = dot > 0 ? name.slice(dot + 1) : "";
  return EXTENSION_LANGS[ext] ?? "text";
}

/** One token: its text and the inline style carrying the theme variables. */
export interface HighlightToken {
  content: string;
  /** e.g. `--shiki-light:#D73A49;--shiki-dark:#F97583` ("" for plain text). */
  style: string;
}

/** One source line as tokens (an empty array for an empty line). */
export type HighlightedLine = HighlightToken[];

function styleString(style: Record<string, string> | undefined): string {
  if (!style) return "";
  return Object.entries(style)
    .map(([key, value]) => `${key}:${value}`)
    .join(";");
}

/**
 * Tokenize `code` into lines of dual-theme tokens. Lines are split on "\n"
 * exactly like Shiki does, so `result[n - 1]` is source line `n`.
 */
export async function highlightLines(
  code: string,
  lang: string,
): Promise<HighlightedLine[]> {
  const id = await ensureLanguage(lang);
  const highlighter = await getHighlighter();
  const { tokens } = highlighter.codeToTokens(code, {
    // `ensureLanguage` returned a loaded grammar id or "text".
    lang: id as BundledLanguage,
    themes: SHIKI_THEMES,
    defaultColor: false,
  });
  return tokens.map((line) =>
    line.map((token) => ({
      content: token.content,
      style: styleString(token.htmlStyle),
    })),
  );
}

/** Plain (unhighlighted) lines: the instant first paint and the failure fallback. */
export function plainLines(code: string): HighlightedLine[] {
  return code
    .split("\n")
    .map((line) => (line ? [{ content: line, style: "" }] : []));
}
