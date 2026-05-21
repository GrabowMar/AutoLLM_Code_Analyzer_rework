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
