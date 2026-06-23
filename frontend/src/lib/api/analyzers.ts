import { apiFetch } from "./core";

export interface ConfigField {
  name: string;
  type: "string" | "number" | "boolean" | "select" | "multiselect";
  label: string;
  description: string;
  default: unknown;
  options: { value: string; label: string }[];
  required: boolean;
  min: number | null;
  max: number | null;
  placeholder: string;
}

export interface AnalyzerTool {
  slug: string;
  name: string;
  description: string;
  category: string;
  kind: "container" | "ai";
  target_language: string;
  icon: string;
  version: string;
  config_schema: ConfigField[];
  default_config: Record<string, unknown>;
  run_timeout: number;
  is_enabled: boolean;
  installed: boolean;
  install_status: string;
  installed_version: string;
}

export interface Workspace {
  id: string;
  status: "absent" | "provisioning" | "ready" | "stopped" | "error";
  image: string;
  error_message: string;
  container_name: string;
  last_used_at: string | null;
  installed_count: number;
}

export interface InstalledTool {
  id: string;
  tool_slug: string;
  tool_name: string;
  category: string;
  status: "installing" | "installed" | "failed";
  installed_version: string;
  config: Record<string, unknown>;
  install_log: string;
}

export interface TestResult {
  available: boolean;
  message: string;
  findings: Record<string, unknown>[];
  raw_output: Record<string, unknown>;
}

// --- Catalog ----------------------------------------------------------------

export async function getToolCatalog(): Promise<AnalyzerTool[]> {
  const res = await apiFetch("/analyzers/tools/");
  return res.json();
}

export async function getTool(slug: string): Promise<AnalyzerTool> {
  const res = await apiFetch(`/analyzers/tools/${slug}/`);
  return res.json();
}

// --- Workspace --------------------------------------------------------------

export async function getWorkspace(): Promise<Workspace> {
  const res = await apiFetch("/analyzers/workspace/");
  return res.json();
}

export async function provisionWorkspace(): Promise<Workspace> {
  const res = await apiFetch("/analyzers/workspace/provision/", { method: "POST" });
  return res.json();
}

export async function startWorkspace(): Promise<Workspace> {
  const res = await apiFetch("/analyzers/workspace/start/", { method: "POST" });
  return res.json();
}

export async function stopWorkspace(): Promise<Workspace> {
  const res = await apiFetch("/analyzers/workspace/stop/", { method: "POST" });
  return res.json();
}

export async function deleteWorkspace(): Promise<Workspace> {
  const res = await apiFetch("/analyzers/workspace/", { method: "DELETE" });
  return res.json();
}

// --- Installed tools --------------------------------------------------------

export async function getInstalledTools(): Promise<InstalledTool[]> {
  const res = await apiFetch("/analyzers/workspace/tools/");
  return res.json();
}

export async function installTool(toolSlug: string): Promise<InstalledTool> {
  const res = await apiFetch("/analyzers/workspace/tools/", {
    method: "POST",
    body: JSON.stringify({ tool_slug: toolSlug }),
  });
  return res.json();
}

export async function uninstallTool(slug: string): Promise<void> {
  await apiFetch(`/analyzers/workspace/tools/${slug}/`, { method: "DELETE" });
}

export async function updateToolConfig(
  slug: string,
  config: Record<string, unknown>,
): Promise<InstalledTool> {
  const res = await apiFetch(`/analyzers/workspace/tools/${slug}/`, {
    method: "PUT",
    body: JSON.stringify({ config }),
  });
  return res.json();
}

export async function testTool(
  slug: string,
  config: Record<string, unknown> = {},
): Promise<TestResult> {
  const res = await apiFetch(`/analyzers/workspace/tools/${slug}/test/`, {
    method: "POST",
    body: JSON.stringify({ config }),
  });
  return res.json();
}
