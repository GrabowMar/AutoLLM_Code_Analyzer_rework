/**
 * /api/integrations/ client: MCP server config, one-click LLM providers,
 * GitHub export and outbound notification webhooks.
 *
 * Secrets (provider API keys, webhook URLs / signing secrets / bot tokens) are
 * write-only: responses carry masked hints at most. The GitHub token itself is
 * stored through the credentials API (`setCredentialKey("github", …)`).
 *
 * Bad GitHub / provider keys come back as 400 (never 401): `apiFetch` turns a
 * 401 into a redirect to the login page.
 */
import { apiFetch } from "./core";

function post(body: unknown): RequestInit {
  return { method: "POST", body: JSON.stringify(body) };
}

// ── MCP server ────────────────────────────────────────────────────────

export type McpClientAuth = "header" | "url";

export interface McpClientSnippet {
  id: string;
  name: string;
  auth: McpClientAuth;
  /** shell | json | toml | python | text */
  language: string;
  /** Where the snippet goes (file, settings path, terminal). */
  location: string;
  /** Contains the literal placeholder `<TOKEN>` (see MCP_TOKEN_PLACEHOLDER). */
  snippet: string;
  docs_url: string;
}

export interface McpTool {
  name: string;
  description: string;
  read_only: boolean;
}

export interface McpConfig {
  server_url: string;
  url_token_template: string;
  protocol_versions: string[];
  tool_count: number;
  tools: McpTool[];
  prompts: string[];
  clients: McpClientSnippet[];
}

export const MCP_TOKEN_PLACEHOLDER = "<TOKEN>";

export async function getMcpConfig(): Promise<McpConfig> {
  const res = await apiFetch("/integrations/mcp/config/");
  return res.json();
}

/** Replace every `<TOKEN>` placeholder; returns the text unchanged without a token. */
export function fillToken(
  text: string,
  token: string | null | undefined,
): string {
  return token ? text.split(MCP_TOKEN_PLACEHOLDER).join(token) : text;
}

// ── LLM providers ─────────────────────────────────────────────────────

export interface ProviderConnectedEndpoint {
  id: number;
  slug: string;
  name: string;
  enabled: boolean;
  /** Available (not hidden) models. */
  model_count: number;
  last_error: string;
}

export interface ProviderPreset {
  id: string;
  name: string;
  description: string;
  base_url: string;
  docs_url: string;
  keys_url: string;
  max_concurrency: number;
  request_timeout_s: number;
  /** Direct endpoints are staff-only. */
  can_connect: boolean;
  connected_endpoints: ProviderConnectedEndpoint[];
}

export interface ProviderKeyTest {
  ok: boolean;
  message: string;
  /** Chat/code models the preset keeps. */
  model_count: number;
  /** Everything the provider's /models listed. */
  total_models: number;
  sample_model_ids: string[];
}

export interface ProviderConnectResult {
  endpoint_id: number;
  slug: string;
  name: string;
  model_count: number;
  hidden_count: number;
  total_models: number;
  inventory_error: string;
}

export async function listProviders(): Promise<ProviderPreset[]> {
  const res = await apiFetch("/integrations/providers/");
  return res.json();
}

/** `ok: false` (bad key, provider down) is still HTTP 200. */
export async function testProviderKey(
  providerId: string,
  apiKey: string,
): Promise<ProviderKeyTest> {
  const res = await apiFetch(
    `/integrations/providers/${encodeURIComponent(providerId)}/test/`,
    post({ api_key: apiKey }),
  );
  return res.json();
}

export async function connectProvider(
  providerId: string,
  payload: { api_key: string; name?: string },
): Promise<ProviderConnectResult> {
  const res = await apiFetch(
    `/integrations/providers/${encodeURIComponent(providerId)}/connect/`,
    post(payload),
  );
  return res.json();
}

// ── GitHub ────────────────────────────────────────────────────────────

export interface GitHubStatus {
  configured: boolean;
  connected: boolean;
  login: string | null;
  name: string | null;
  html_url: string | null;
  avatar_url: string | null;
  scopes: string[];
  token_type: string | null;
  message: string;
}

export type GitHubExportTarget = "repo" | "gist";

export interface GitHubExportPayload {
  /** Generation job UUID. */
  application_id: string;
  target: GitHubExportTarget;
  /** Required for target=repo. */
  repo_name?: string;
  /** Repo: private repository; gist: secret gist. */
  private: boolean;
  description?: string;
  upload_sarif: boolean;
  /** Repo only: include the stack skeleton so the app builds. */
  include_scaffold: boolean;
}

export interface GitHubExportResult {
  target: GitHubExportTarget;
  url: string;
  repo_full_name: string | null;
  commit_sha: string | null;
  files_count: number;
  skipped: string[];
  sarif: { uploaded: boolean; message: string; id: string | null };
}

export async function getGitHubStatus(): Promise<GitHubStatus> {
  const res = await apiFetch("/integrations/github/status/");
  return res.json();
}

export async function exportToGitHub(
  payload: GitHubExportPayload,
): Promise<GitHubExportResult> {
  const res = await apiFetch("/integrations/github/export/", post(payload));
  return res.json();
}

// ── Notification webhooks ─────────────────────────────────────────────

export type WebhookKind =
  | "slack"
  | "discord"
  | "teams"
  | "ntfy"
  | "telegram"
  | "generic";

export interface WebhookSubscription {
  id: number;
  name: string;
  kind: WebhookKind;
  url_hint: string;
  has_url: boolean;
  secret_hint: string;
  has_secret: boolean;
  telegram_chat_id: string;
  events: string[];
  include_experiment_jobs: boolean;
  enabled: boolean;
  last_success_at: string | null;
  last_error: string;
  last_error_at: string | null;
  created_at: string;
  updated_at: string;
}

export interface WebhookCreatePayload {
  name: string;
  kind: WebhookKind;
  /** All kinds except telegram. Write-only. */
  url?: string;
  /** generic: HMAC signing secret; telegram: bot token (required); ntfy: access token. */
  secret?: string;
  telegram_chat_id?: string;
  events: string[];
  include_experiment_jobs?: boolean;
  enabled?: boolean;
}

export interface WebhookPatchPayload {
  name?: string;
  url?: string;
  /** `""` clears the secret (not allowed for telegram). */
  secret?: string;
  telegram_chat_id?: string;
  events?: string[];
  include_experiment_jobs?: boolean;
  enabled?: boolean;
}

export type WebhookDeliveryStatus = "pending" | "sent" | "failed";

export interface WebhookDelivery {
  id: number;
  event_type: string;
  event_key: string;
  status: WebhookDeliveryStatus;
  attempts: number;
  response_code: number | null;
  error: string;
  payload: Record<string, unknown>;
  created_at: string;
  updated_at: string;
  sent_at: string | null;
}

export interface WebhookTestResult {
  ok: boolean;
  status_code: number | null;
  error: string;
  delivery: WebhookDelivery;
}

export interface WebhookEventType {
  id: string;
  description: string;
}

const hooks = "/integrations/webhooks";

export async function listWebhookEventTypes(): Promise<WebhookEventType[]> {
  const res = await apiFetch(`${hooks}/event-types/`);
  return res.json();
}

export async function listWebhooks(): Promise<WebhookSubscription[]> {
  const res = await apiFetch(`${hooks}/`);
  return res.json();
}

export async function createWebhook(
  payload: WebhookCreatePayload,
): Promise<WebhookSubscription> {
  const res = await apiFetch(`${hooks}/`, post(payload));
  return res.json();
}

export async function updateWebhook(
  id: number,
  payload: WebhookPatchPayload,
): Promise<WebhookSubscription> {
  const res = await apiFetch(`${hooks}/${id}/`, {
    method: "PATCH",
    body: JSON.stringify(payload),
  });
  return res.json();
}

export async function deleteWebhook(id: number): Promise<void> {
  await apiFetch(`${hooks}/${id}/`, { method: "DELETE" });
}

/** Sends a sample `test` event now (one attempt, 10 s timeout). */
export async function testWebhook(id: number): Promise<WebhookTestResult> {
  const res = await apiFetch(`${hooks}/${id}/test/`, { method: "POST" });
  return res.json();
}

/** The 50 most recent deliveries, newest first. */
export async function listWebhookDeliveries(
  id: number,
): Promise<WebhookDelivery[]> {
  const res = await apiFetch(`${hooks}/${id}/deliveries/`);
  return res.json();
}
