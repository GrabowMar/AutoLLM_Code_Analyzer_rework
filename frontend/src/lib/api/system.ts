import { apiFetch } from "./core";

export interface DocNode {
  slug: string;
  title: string;
  category?: string;
  description?: string;
  children: DocNode[];
}

export interface DocPage {
  slug: string;
  title: string;
  category: string;
  raw: string;
  last_modified: number;
}

export interface DocSearchResult {
  slug: string;
  title: string;
  category?: string;
  section?: string;
  snippet: string;
  score: number;
}

export interface SystemSnapshot {
  host: Record<string, unknown>;
  containers: Record<string, unknown>[];
  redis: Record<string, unknown>;
  celery: Record<string, unknown>;
  db: Record<string, unknown>;
  app_stats: Record<string, unknown>;
}

export async function clearCaches(): Promise<{
  success: boolean;
  message?: string;
  error?: string;
}> {
  const res = await apiFetch("/system/maintenance/clear-caches", {
    method: "POST",
  });
  return res.json();
}

export async function clearStuckAnalysis(
  olderThanMinutes = 60,
): Promise<{ updated: number }> {
  const res = await apiFetch(
    `/system/maintenance/clear-stuck-analysis?older_than_minutes=${olderThanMinutes}`,
    { method: "POST" },
  );
  return res.json();
}

export async function clearStuckGeneration(
  olderThanMinutes = 60,
): Promise<{ updated: number }> {
  const res = await apiFetch(
    `/system/maintenance/clear-stuck-generation?older_than_minutes=${olderThanMinutes}`,
    { method: "POST" },
  );
  return res.json();
}

export async function getDoc(slug: string): Promise<DocPage | null> {
  const res = await apiFetch(`/docs/page?slug=${encodeURIComponent(slug)}`);
  if (res.status === 404) return null;
  return res.json();
}

export async function getDocsTree(): Promise<DocNode[]> {
  const res = await apiFetch("/docs/tree");
  return res.json();
}

export async function getDocsCategories(): Promise<string[]> {
  const res = await apiFetch("/docs/categories");
  return res.json();
}

export async function getSystemAppStats(): Promise<Record<string, unknown>> {
  const res = await apiFetch("/system/app-stats");
  return res.json();
}

export async function getSystemCelery(): Promise<Record<string, unknown>> {
  const res = await apiFetch("/system/celery");
  return res.json();
}

export async function getSystemContainers(): Promise<
  Record<string, unknown>[]
> {
  const res = await apiFetch("/system/containers");
  return res.json();
}

export async function getSystemDb(): Promise<Record<string, unknown>> {
  const res = await apiFetch("/system/db");
  return res.json();
}

export async function getSystemHost(): Promise<Record<string, unknown>> {
  const res = await apiFetch("/system/host");
  return res.json();
}

export async function getSystemRedis(): Promise<Record<string, unknown>> {
  const res = await apiFetch("/system/redis");
  return res.json();
}

export async function getSystemSnapshot(): Promise<SystemSnapshot> {
  const res = await apiFetch("/system/");
  return res.json();
}

export async function purgeOrphanContainers(): Promise<{ purged: number }> {
  const res = await apiFetch("/system/maintenance/purge-orphan-containers", {
    method: "POST",
  });
  return res.json();
}

export async function searchDocs(q: string): Promise<DocSearchResult[]> {
  const res = await apiFetch(`/docs/search?q=${encodeURIComponent(q)}`);
  return res.json();
}

// ---------------------------------------------------------------------------
// App footprint (preview apps on the apps daemon)
// ---------------------------------------------------------------------------

export interface FootprintSettings {
  idle_stop_enabled: boolean;
  idle_stop_minutes: number;
  max_running_apps: number;
  app_mem_limit_mb: number;
  auto_prune_enabled: boolean;
  auto_prune_interval_hours: number;
  last_auto_prune_at?: string | null;
  last_sweep_at?: string | null;
  last_sweep_result?: Record<string, unknown>;
  updated_at?: string;
}

export interface FootprintApp {
  id: string;
  name: string;
  status: string;
  owner: string;
  run_id: string | null;
  created_at: string;
  last_used_at: string | null;
  idle_seconds: number | null;
  mem_usage: number | null;
  mem_limit: number | null;
  busy: boolean;
  human_eval: boolean;
}

export interface FootprintDaemonDisk {
  images_count: number;
  images_bytes: number;
  images_unused_count: number;
  images_unused_bytes: number;
  build_cache_bytes: number;
  volumes_bytes: number;
  containers_count: number;
  containers_bytes: number;
}

export interface FootprintOverview {
  settings: FootprintSettings;
  counts: Record<string, number>;
  apps: FootprintApp[];
  daemon: {
    reachable: boolean;
    error?: string;
    mem_budget: string;
    disk?: FootprintDaemonDisk;
    apps_mem_bytes?: number;
    other_mem_bytes?: number;
    other_running?: { name: string; kind: string; mem_usage: number | null }[];
  };
}

export interface PruneResult {
  removed_images: number;
  removed_containers: number;
  in_use: number;
  reclaimed_bytes: number;
  sample_cache_removed?: number;
  build_cache_reclaimed?: number;
  aggressive: boolean;
  errors: string[];
}

export async function getFootprint(): Promise<FootprintOverview> {
  const res = await apiFetch("/system/footprint");
  return res.json();
}

export async function updateFootprintSettings(
  changes: Partial<FootprintSettings>,
): Promise<FootprintSettings> {
  const res = await apiFetch("/system/footprint/settings", {
    method: "PATCH",
    body: JSON.stringify(changes),
  });
  return res.json();
}

export async function stopAllApps(): Promise<{
  stopping: number;
  skipped: number;
}> {
  const res = await apiFetch("/system/footprint/stop-all", { method: "POST" });
  return res.json();
}

export async function removeApps(
  includeRunning = false,
): Promise<{ removing: number; skipped: number }> {
  const res = await apiFetch(
    `/system/footprint/remove?include_running=${includeRunning}`,
    { method: "POST" },
  );
  return res.json();
}

export async function stopIdleApps(
  idleMinutes: number,
): Promise<{ idle: number; over_cap: number; stopped: string[] }> {
  const res = await apiFetch(
    `/system/footprint/stop-idle?idle_minutes=${idleMinutes}`,
    { method: "POST" },
  );
  return res.json();
}

export async function applyAppMemLimit(): Promise<{
  limit_mb: number;
  updated: number;
  failed: { name: string; error: string }[];
}> {
  const res = await apiFetch("/system/footprint/apply-mem-limit", {
    method: "POST",
  });
  return res.json();
}

export async function pruneAppsDaemon(
  aggressive = false,
): Promise<PruneResult> {
  const res = await apiFetch(
    `/system/footprint/prune?aggressive=${aggressive}`,
    { method: "POST" },
  );
  return res.json();
}
