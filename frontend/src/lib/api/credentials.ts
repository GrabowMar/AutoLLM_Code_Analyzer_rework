import { apiFetch } from "./core";

export type CredentialProvider = "openrouter" | "huggingface";

export interface CredentialStatus {
  provider: CredentialProvider;
  configured: boolean;
  key_prefix: string;
  last_validation_status: string;
  last_validation_message: string;
  last_validated_at: string | null;
  global_fallback_available: boolean;
  using_global_fallback: boolean;
}

export interface ValidationResult {
  status: string;
  message: string;
  is_valid: boolean;
}

// Legacy alias used elsewhere in the UI.
export type OpenRouterCredentialStatus = CredentialStatus;

export async function listCredentials(): Promise<CredentialStatus[]> {
  const res = await apiFetch("/credentials/");
  return res.json();
}

export async function getCredentialStatus(
  provider: CredentialProvider,
): Promise<CredentialStatus> {
  const res = await apiFetch(`/credentials/${provider}/`);
  return res.json();
}

export async function setCredentialKey(
  provider: CredentialProvider,
  apiKey: string,
): Promise<CredentialStatus> {
  const res = await apiFetch(`/credentials/${provider}/`, {
    method: "PUT",
    body: JSON.stringify({ api_key: apiKey }),
  });
  return res.json();
}

export async function deleteCredentialKey(
  provider: CredentialProvider,
): Promise<CredentialStatus> {
  const res = await apiFetch(`/credentials/${provider}/`, { method: "DELETE" });
  return res.json();
}

export async function testCredentialKey(
  provider: CredentialProvider,
): Promise<ValidationResult> {
  const res = await apiFetch(`/credentials/${provider}/test/`, {
    method: "POST",
  });
  return res.json();
}

// Back-compat aliases for the prior OpenRouter-only API.
export const getOpenRouterCredentialStatus = () =>
  getCredentialStatus("openrouter");
export const setOpenRouterKey = (k: string) =>
  setCredentialKey("openrouter", k);
export const deleteOpenRouterKey = () => deleteCredentialKey("openrouter");
export const testOpenRouterKey = () => testCredentialKey("openrouter");
