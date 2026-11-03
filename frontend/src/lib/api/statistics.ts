import { apiFetch } from "./core";

export interface AnalysisTrends {
  days: number;
  total: number;
  series: TrendPoint[];
}

export interface AnalyzerHealth {
  total: number;
  online: number;
  offline: number;
  by_type: Record<string, Record<string, number>>;
  analyzers: {
    name: string;
    type: string;
    display_name: string;
    available: boolean;
    message: string;
  }[];
}

export interface CodeGenerationStats {
  total_apps: number;
  completed: number;
  failed: number;
  running: number;
  pending?: number;
  cancelled?: number;
  /** completed / (completed + failed), percent; null until something finished */
  success_rate: number | null;
  /** mean over completed generations; null until one completed */
  avg_duration_seconds: number | null;
  total_tokens: number;
  total_cost_usd: number;
  /** source lines of code (the study tables' LOC denominator) */
  total_lines_of_code: number;
  unique_templates: number;
}

export interface ModelComparisonRow {
  model_id: string;
  name: string;
  provider: string;
  apps: number;
  apps_completed: number;
  apps_failed?: number;
  /** over finished generations (completed + failed), percent; null when none finished */
  success_rate: number | null;
  /** mean over completed generations; null when none completed */
  avg_duration_seconds: number | null;
  // Shared rankings scoring — all 0..1 scales.
  mss: number;
  empirical_quality: number | null;
  composite: number;
  /** Evaluation (EvalSuite) functional pass rate; null when no app of the model was evaluated. */
  pass_rate: number | null;
  n_trials: number;
  /** Code issues the empirical score counts, by normalized level; null when no app of the model was measured. */
  findings: {
    critical: number;
    high: number;
    medium: number;
    low: number;
    info: number;
  } | null;
  ai_findings: {
    critical: number;
    high: number;
    medium: number;
    low: number;
    info: number;
  };
}

export interface RecentActivityItem {
  kind: string;
  id: string;
  title: string;
  status: string;
  created_at: string;
}

export interface SeverityBucket {
  severity: string;
  count: number;
  percent: number;
}

/**
 * Findings by the platform's *normalized* severity (a heuristic, also for
 * tools that print no level): aggregate statistics only, always labelled
 * "normalized (heuristic)", never shown as a finding's level.
 */
export interface SeverityDistribution {
  total: number;
  distribution: SeverityBucket[];
  by_source?: {
    static: Record<string, number>;
    ai: Record<string, number>;
  } | null;
}

export interface StatisticsDashboard {
  overview: StatisticsOverview;
  severity: SeverityDistribution;
  trends: AnalysisTrends;
  models: ModelComparisonRow[];
  tools: ToolEffectivenessRow[];
  top_findings: TopFindingRow[];
  code_generation: CodeGenerationStats;
  analyzer_health: AnalyzerHealth;
  recent_activity: RecentActivityItem[];
}

export interface StatisticsOverview {
  total_models: number;
  models_in_use: number;
  total_apps: number;
  apps_completed: number;
  apps_failed: number;
  apps_running: number;
  apps_pending: number;
  apps_cancelled?: number;
  /** completed / (completed + failed), percent; null until something finished */
  apps_success_rate: number | null;
  total_analyses: number;
  analyses_completed: number;
  analyses_partial?: number;
  analyses_failed: number;
  analyses_running: number;
  analyses_pending: number;
  analyses_cancelled?: number;
  /** completed / (completed + partial + failed), percent; null until something finished */
  analyses_success_rate: number | null;
  /** mean over completed analyses; null until one completed */
  avg_analysis_duration_seconds: number | null;
  total_findings: number;
}

export interface ToolEffectivenessRow {
  name: string;
  type: string;
  /** executions that measured the app (completed, or nothing to read) */
  scans: number;
  /** failed, cancelled or app-not-running executions: they measured nothing */
  not_measured?: number;
  findings: number;
  /** findings / scans; null when no execution measured anything */
  avg_per_scan: number | null;
  top_rule: string;
}

/** One row per (title, tool, the tool's own label). */
export interface TopFindingRow {
  title: string;
  /** Tool slug. */
  tool: string;
  /** The level exactly as the tool printed it; "" = the tool rates nothing (show "unrated"). */
  tool_severity: string;
  count: number;
  /** A known-vulnerability advisory about a package version, not a code issue. */
  dependency_advisory: boolean;
}

export interface TrendPoint {
  date: string;
  label: string;
  total: number;
  completed: number;
  failed: number;
}

export async function getStatisticsAnalyzerHealth(): Promise<AnalyzerHealth> {
  const res = await apiFetch("/statistics/analyzer-health/");
  return res.json();
}

export async function getStatisticsCodeGeneration(): Promise<CodeGenerationStats> {
  const res = await apiFetch("/statistics/code-generation/");
  return res.json();
}

export async function getStatisticsDashboard(): Promise<StatisticsDashboard> {
  const res = await apiFetch("/statistics/dashboard/");
  return res.json();
}

export async function getStatisticsModels(
  limit = 25,
): Promise<ModelComparisonRow[]> {
  const res = await apiFetch(`/statistics/models/?limit=${limit}`);
  return res.json();
}

export async function getStatisticsOverview(): Promise<StatisticsOverview> {
  const res = await apiFetch("/statistics/overview/");
  return res.json();
}

export async function getStatisticsRecentActivity(
  limit = 20,
): Promise<RecentActivityItem[]> {
  const res = await apiFetch(`/statistics/recent-activity/?limit=${limit}`);
  return res.json();
}

export async function getStatisticsSeverity(): Promise<SeverityDistribution> {
  const res = await apiFetch("/statistics/severity/");
  return res.json();
}

export async function getStatisticsTools(): Promise<ToolEffectivenessRow[]> {
  const res = await apiFetch("/statistics/tools/");
  return res.json();
}

export async function getStatisticsTopFindings(
  limit = 10,
): Promise<TopFindingRow[]> {
  const res = await apiFetch(`/statistics/top-findings/?limit=${limit}`);
  return res.json();
}

export async function getStatisticsTrends(days = 14): Promise<AnalysisTrends> {
  const res = await apiFetch(`/statistics/trends/?days=${days}`);
  return res.json();
}
