import { apiFetch } from "./core";

export interface ApiTokenSummary {
  id: string;
  name: string;
  prefix: string;
  scopes: string[];
  expires_at: string | null;
  last_used_at: string | null;
  last_used_ip: string;
  revoked_at: string | null;
  created_at: string;
}

export interface ApiTokenCreatedResponse extends ApiTokenSummary {
  token: string;
}

export interface CreateApiTokenPayload {
  name: string;
  scopes?: string[];
  expires_at?: string | null;
}

export async function createApiToken(
  payload: CreateApiTokenPayload,
): Promise<ApiTokenCreatedResponse> {
  const res = await apiFetch("/tokens/", {
    method: "POST",
    body: JSON.stringify(payload),
  });
  return res.json();
}

export async function listApiTokens(): Promise<ApiTokenSummary[]> {
  const res = await apiFetch("/tokens/");
  return res.json();
}

export async function revokeApiToken(id: string): Promise<void> {
  await apiFetch(`/tokens/${id}/`, { method: "DELETE" });
}
