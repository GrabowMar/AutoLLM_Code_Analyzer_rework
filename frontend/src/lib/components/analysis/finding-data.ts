/**
 * Reading a finding's `tool_specific_data`: parsers store standardized keys
 * (rule_name, docs_url, references, cwe, owasp, package, installed_version,
 * fixed_versions, vuln_id, aliases, cvss_score, url, method, param, evidence,
 * duplicate_of, complexity, …) next to older tool-specific ones (trivy
 * `fixed_version`, osv `fix_versions`, jscpd `second_file`, …). These helpers
 * merge both so the detail view renders whichever a parser wrote.
 */
import type { RunFinding } from "$lib/api/runs";

type Data = Record<string, any>;

/**
 * Keys never shown: internal deduplication identifiers, and detect-secrets'
 * SHA-1 of the secret (reversible for weak secrets).
 */
const HIDDEN = new Set(["issue_key", "hashed_secret"]);

/** Keys the structured sections render (everything else goes to "Other data"). */
export const KNOWN_KEYS = new Set([
  "rule_name",
  "test_name",
  "message_id",
  "likelihood",
  "impact",
  "rank",
  "entropy",
  "docs_url",
  "references",
  "cwe",
  "owasp",
  "wstg",
  "tags",
  "end_line",
  "end_column",
  "fix",
  "tool_confidence",
  "package",
  "version",
  "installed_version",
  "fixed_version",
  "fix_versions",
  "fixed_versions",
  "vuln_id",
  "aliases",
  "cvss",
  "cvss_score",
  "cvss_vector",
  "ecosystem",
  "manifest",
  "direct",
  "url",
  "urls",
  "method",
  "param",
  "evidence",
  "attack",
  "instances",
  "other_info",
  "curl",
  "matcher",
  "secret_type",
  "complexity",
  "function",
  "nloc",
  "duplicate_of",
  "second_file",
  "second_start",
  "lines",
  "tokens",
  "module",
]);

/** Only http(s) URLs become links (tool output is untrusted). */
export function safeUrl(value: unknown): string | null {
  if (typeof value !== "string") return null;
  try {
    const u = new URL(value.trim());
    return u.protocol === "http:" || u.protocol === "https:" ? u.href : null;
  } catch {
    return null;
  }
}

/** Target URLs of dynamic tools point at the app's private container address: text, not links. */
export function isPrivateUrl(value: string): boolean {
  try {
    const host = new URL(value).hostname;
    return (
      /^(10|127)\./.test(host) ||
      /^172\.(1[6-9]|2\d|3[01])\./.test(host) ||
      /^192\.168\./.test(host) ||
      host === "localhost" ||
      !host.includes(".")
    );
  } catch {
    return true;
  }
}

function strings(value: unknown): string[] {
  if (Array.isArray(value))
    return value.filter((v) => typeof v === "string" && v.trim()).map(String);
  if (typeof value === "string" && value.trim()) return [value];
  return [];
}

function num(value: unknown): number | null {
  const n = typeof value === "string" ? Number.parseFloat(value) : value;
  return typeof n === "number" && Number.isFinite(n) ? n : null;
}

export function cweUrl(cwe: string): string | null {
  const m = /CWE-?(\d+)/i.exec(cwe);
  return m ? `https://cwe.mitre.org/data/definitions/${m[1]}.html` : null;
}

/** Advisory page for a vulnerability id (CVE → NVD, GHSA → GitHub, others → OSV). */
export function advisoryUrl(id: string): string | null {
  const v = id.trim();
  if (/^CVE-\d{4}-\d+$/i.test(v))
    return `https://nvd.nist.gov/vuln/detail/${v.toUpperCase()}`;
  if (/^GHSA(-[a-z0-9]{4}){3}$/i.test(v))
    return `https://github.com/advisories/${v}`;
  if (/^(PYSEC|RUSTSEC|GO|OSV|MAL|GSD|DSA|USN|RHSA|ALSA)-/i.test(v)) {
    return `https://osv.dev/vulnerability/${v}`;
  }
  return null;
}

export interface DependencyInfo {
  package: string;
  installed: string;
  fixedIn: string[];
  ecosystem: string;
  manifest: string;
  direct: boolean | null;
  ids: string[];
  cvssScore: number | null;
  cvssVector: string;
}

export function dependencyInfo(f: RunFinding): DependencyInfo | null {
  const d: Data = f.tool_specific_data ?? {};
  const pkg = d.package ?? d.module;
  if (!pkg) return null;
  const cvssRaw = d.cvss_score ?? d.cvss;
  const vector =
    typeof d.cvss_vector === "string"
      ? d.cvss_vector
      : typeof d.cvss === "string" && d.cvss.startsWith("CVSS:")
        ? d.cvss
        : "";
  const score = num(cvssRaw);
  const ids = [
    ...new Set([
      ...strings(d.vuln_id),
      ...(f.rule_id && advisoryUrl(f.rule_id) ? [f.rule_id] : []),
      ...strings(d.aliases),
    ]),
  ];
  return {
    package: String(pkg),
    installed: String(d.installed_version ?? d.version ?? ""),
    fixedIn: [
      ...new Set([
        ...strings(d.fixed_versions),
        ...strings(d.fix_versions),
        ...strings(d.fixed_version),
      ]),
    ],
    ecosystem: String(d.ecosystem ?? ""),
    manifest: String(d.manifest ?? ""),
    direct: typeof d.direct === "boolean" ? d.direct : null,
    ids,
    // A 0 score means "not scored" in npm audit: not a measurement.
    cvssScore: score && score > 0 ? score : null,
    cvssVector: vector,
  };
}

export interface HttpInfo {
  method: string;
  url: string;
  param: string;
  attack: string;
  evidence: string;
  instances: number | null;
  otherUrls: string[];
  otherInfo: string;
  curl: string;
  matcher: string;
}

export function httpInfo(f: RunFinding): HttpInfo | null {
  const d: Data = f.tool_specific_data ?? {};
  if (!d.url && !d.curl && !d.method) return null;
  const url = String(d.url ?? "");
  const evidence = String(d.evidence ?? "");
  return {
    method: String(d.method ?? ""),
    url,
    param: String(d.param ?? ""),
    attack: String(d.attack ?? ""),
    // ZAP/nuclei also store the evidence as the code snippet: show it once.
    evidence: evidence.trim() === (f.code_snippet ?? "").trim() ? "" : evidence,
    instances: num(d.instances),
    otherUrls: strings(d.urls).filter((u) => u !== url),
    otherInfo: String(d.other_info ?? ""),
    curl: String(d.curl ?? ""),
    matcher: String(d.matcher ?? ""),
  };
}

export interface DuplicateInfo {
  file: string;
  startLine: number | null;
  endLine: number | null;
  lines: number | null;
  tokens: number | null;
}

export function duplicateInfo(f: RunFinding): DuplicateInfo | null {
  const d: Data = f.tool_specific_data ?? {};
  const dup = (d.duplicate_of ?? {}) as Data;
  const file = String(dup.file ?? d.second_file ?? "");
  if (!file) return null;
  return {
    file,
    startLine: num(dup.start_line ?? d.second_start),
    endLine: num(dup.end_line),
    lines: num(d.lines),
    tokens: num(d.tokens),
  };
}

/** Rule-level facts: human name, docs link, references, CWE/OWASP/WSTG, tags. */
export function ruleInfo(f: RunFinding) {
  const d: Data = f.tool_specific_data ?? {};
  const cwe = strings(d.cwe);
  return {
    name: String(d.rule_name ?? d.test_name ?? ""),
    docsUrl: safeUrl(d.docs_url),
    references: [
      ...new Set(
        strings(d.references)
          .map(safeUrl)
          .filter((u): u is string => !!u),
      ),
    ],
    cwe,
    owasp: strings(d.owasp),
    wstg: strings(d.wstg),
    tags: strings(d.tags),
    messageId: String(d.message_id ?? ""),
    likelihood: String(d.likelihood ?? ""),
    impact: String(d.impact ?? ""),
    toolConfidence: String(d.tool_confidence ?? ""),
    fix: String(d.fix ?? ""),
    secretType: String(d.secret_type ?? ""),
    entropy: num(d.entropy),
  };
}

export function complexityInfo(f: RunFinding) {
  const d: Data = f.tool_specific_data ?? {};
  const complexity = num(d.complexity);
  if (complexity == null && !d.function && num(d.nloc) == null) return null;
  return {
    complexity,
    fn: String(d.function ?? ""),
    nloc: num(d.nloc),
    rank: String(d.rank ?? ""),
  };
}

/** `file:line:col–end` for display. */
export function locationText(f: RunFinding): string {
  if (!f.file_path) return "";
  const d: Data = f.tool_specific_data ?? {};
  let text = f.file_path;
  if (f.line_number != null) {
    text += `:${f.line_number}`;
    if (f.column_number != null) text += `:${f.column_number}`;
    const end = num(d.end_line);
    if (end != null && end !== f.line_number) text += `–${end}`;
  }
  return text;
}

/** Leftover tool data the structured sections don't cover, as `[key, display]`. */
export function otherData(f: RunFinding): [string, string][] {
  return Object.entries(f.tool_specific_data ?? {})
    .filter(
      ([k, v]) =>
        !KNOWN_KEYS.has(k) &&
        !HIDDEN.has(k) &&
        v !== null &&
        v !== "" &&
        !(Array.isArray(v) && v.length === 0),
    )
    .map(([k, v]) => [
      k.replaceAll("_", " "),
      typeof v === "object" ? JSON.stringify(v) : String(v),
    ]);
}
