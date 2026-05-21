import { allauthFetch, apiFetch } from "./core";

export interface ApiUser {
  email: string;
  name: string;
  url: string;
  is_staff: boolean;
}

export async function authenticate2FA(code: string): Promise<void> {
  const res = await allauthFetch("/auth/2fa/authenticate", {
    method: "POST",
    body: JSON.stringify({ code }),
  });
  if (!res.ok) {
    const body = await res.json().catch(() => ({}));
    throw body;
  }
}

export async function changePassword(data: {
  current_password: string;
  new_password: string;
}): Promise<void> {
  const res = await allauthFetch("/account/password/change", {
    method: "POST",
    body: JSON.stringify(data),
  });
  if (!res.ok) {
    const body = await res.json().catch(() => ({}));
    throw body;
  }
}

export async function getMe(): Promise<ApiUser> {
  const res = await apiFetch("/users/me/");
  return res.json();
}

export async function getUser(pk: number): Promise<ApiUser> {
  const res = await apiFetch(`/users/${pk}/`);
  return res.json();
}

export async function logout(): Promise<void> {
  const res = await allauthFetch("/auth/session", {
    method: "DELETE",
  });
  if (!res.ok) {
    const body = await res.json().catch(() => ({}));
    throw body;
  }
}

export async function requestPasswordReset(email: string): Promise<void> {
  const res = await allauthFetch("/auth/password/request", {
    method: "POST",
    body: JSON.stringify({ email }),
  });
  if (!res.ok) {
    const body = await res.json().catch(() => ({}));
    throw body;
  }
}

export async function resetPassword(
  key: string,
  password: string,
): Promise<void> {
  const res = await allauthFetch("/auth/password/reset", {
    method: "POST",
    body: JSON.stringify({ key, password }),
  });
  if (!res.ok) {
    const body = await res.json().catch(() => ({}));
    throw body;
  }
}

export async function updateMe(data: { name: string }): Promise<ApiUser> {
  const res = await apiFetch("/users/me/", {
    method: "PATCH",
    body: JSON.stringify(data),
  });
  return res.json();
}

export async function verifyEmail(key: string): Promise<void> {
  const res = await allauthFetch("/auth/email/verify", {
    method: "POST",
    body: JSON.stringify({ key }),
  });
  if (!res.ok) {
    const body = await res.json().catch(() => ({}));
    throw body;
  }
}
