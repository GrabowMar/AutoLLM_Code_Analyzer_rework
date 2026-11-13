/**
 * Lazy shiki tokenizer for the code panes (sample files, slot picker, diffs).
 *
 * One highlighter per page load (browser only); grammars load on first use. Tokens use the dual
 * `github-light` / `github-dark` themes with `defaultColor: false`, so each token carries `--shiki-light` and
 * `--shiki-dark` and `CodeViewer` picks one with CSS. Files above `MAX_HIGHLIGHT_BYTES` stay plain text.
 */
import type { Highlighter, ThemedToken } from "shiki";

export const MAX_HIGHLIGHT_BYTES = 400_000;

/** Server languages (`RunFile.language`, symbol index) → shiki grammar ids. */
const LANG_MAP: Record<string, string> = {
  python: "python",
  py: "python",
  javascript: "javascript",
  js: "javascript",
  jsx: "jsx",
  typescript: "typescript",
  ts: "typescript",
  tsx: "tsx",
  go: "go",
  java: "java",
  html: "html",
  css: "css",
  scss: "scss",
  vue: "vue",
  svelte: "svelte",
  json: "json",
  yaml: "yaml",
  yml: "yaml",
  toml: "toml",
  markdown: "markdown",
  md: "markdown",
  dockerfile: "dockerfile",
  docker: "dockerfile",
  sql: "sql",
  shell: "bash",
  bash: "bash",
  sh: "bash",
  ini: "ini",
  xml: "xml",
  diff: "diff",
};

const EXT_MAP: Record<string, string> = {
  py: "python",
  js: "javascript",
  mjs: "javascript",
  cjs: "javascript",
  jsx: "jsx",
  ts: "typescript",
  tsx: "tsx",
  go: "go",
  java: "java",
  html: "html",
  htm: "html",
  css: "css",
  scss: "scss",
  vue: "vue",
  svelte: "svelte",
  json: "json",
  yaml: "yaml",
  yml: "yaml",
  toml: "toml",
  md: "markdown",
  sql: "sql",
  sh: "bash",
  ini: "ini",
  xml: "xml",
  diff: "diff",
  patch: "diff",
};

/** The shiki grammar for a file, from its server language or its extension; `"text"` when unknown. */
export function shikiLang(
  language: string | null | undefined,
  path?: string,
): string {
  const fromLanguage = language ? LANG_MAP[language.toLowerCase()] : undefined;
  if (fromLanguage) return fromLanguage;
  if (path) {
    const base = path.split("/").pop() ?? "";
    if (/^dockerfile/i.test(base)) return "dockerfile";
    const ext = base.includes(".") ? base.split(".").pop()!.toLowerCase() : "";
    if (EXT_MAP[ext]) return EXT_MAP[ext];
  }
  return "text";
}

let highlighterPromise: Promise<Highlighter> | null = null;

function getHighlighter(): Promise<Highlighter> {
  if (!highlighterPromise) {
    highlighterPromise = import("shiki").then((shiki) =>
      shiki.createHighlighter({
        themes: ["github-light", "github-dark"],
        langs: [],
      }),
    );
    highlighterPromise.catch(() => {
      highlighterPromise = null;
    });
  }
  return highlighterPromise;
}

/** An outline chip at a line of `CodeViewer` (a symbol starting there). */
export interface LineMarker {
  id: string;
  label: string;
  title?: string;
}

export interface TokenLine {
  tokens: { content: string; style: string }[];
}

function styleOf(token: ThemedToken): string {
  const style = token.htmlStyle ?? {};
  return Object.entries(style)
    .map(([k, v]) => `${k}:${v}`)
    .join(";");
}

/** Plain lines (no colours): the fallback while loading, for big files and unknown grammars. */
export function plainLines(code: string): TokenLine[] {
  return code.split("\n").map((line) => ({
    tokens: [{ content: line.replace(/\r$/, ""), style: "" }],
  }));
}

/** Tokenized lines of `code`; resolves to plain lines when highlighting is unavailable. */
export async function tokenize(
  code: string,
  lang: string,
): Promise<TokenLine[]> {
  if (
    typeof window === "undefined" ||
    lang === "text" ||
    code.length > MAX_HIGHLIGHT_BYTES
  )
    return plainLines(code);
  try {
    const highlighter = await getHighlighter();
    if (!highlighter.getLoadedLanguages().includes(lang)) {
      await highlighter.loadLanguage(
        lang as Parameters<Highlighter["loadLanguage"]>[0],
      );
    }
    const result = highlighter.codeToTokens(code, {
      lang: lang as Parameters<Highlighter["codeToTokens"]>[1]["lang"],
      themes: { light: "github-light", dark: "github-dark" },
      defaultColor: false,
    });
    return result.tokens.map((line) => ({
      tokens: line.map((t) => ({ content: t.content, style: styleOf(t) })),
    }));
  } catch {
    return plainLines(code);
  }
}
