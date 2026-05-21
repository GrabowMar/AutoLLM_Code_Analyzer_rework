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
  analyzers: {
    name: string;
    type: string;
    display_name: string;
    available: boolean;
    availability_message: string;
  }[];
}

export interface CodeGenerationStats {
  total_apps: number;
  completed: number;
  failed: number;
  running: number;
  success_rate: number;
  avg_duration_seconds: number;
  total_tokens: number;
  total_cost_usd: number;
  total_lines_of_code: number;
  by_provider: { provider: string; apps: number }[];
}

export interface ModelComparisonRow {
  model_id: string;
  name: string;
  provider: string;
  apps: number;
  apps_completed: number;
  success_rate: number;
  avg_duration_seconds: number;
  cost_efficiency: number;
  security: number;
  quality: number;
  performance: number;
  mss: number;
  findings: {
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

export interface SeverityDistribution {
  total: number;
  distribution: SeverityBucket[];
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
  total_apps: number;
  apps_completed: number;
  apps_failed: number;
  apps_running: number;
  apps_success_rate: number;
  total_analyses: number;
  analyses_completed: number;
  analyses_failed: number;
  analyses_running: number;
  analyses_success_rate: number;
  total_findings: number;
  avg_findings_per_app: number;
  models_in_use: number;
  avg_analysis_seconds: number;
}

export interface ToolEffectivenessRow {
  name: string;
  type: string;
  scans: number;
  findings: number;
  avg_per_scan: number;
  top_rule: string;
}

export interface TopFindingRow {
  title: string;
  severity: string;
  rule_id: string;
  count: number;
}

export interface TrendPoint {
  date: string;
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
