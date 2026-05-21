import {
  ALLAUTH_BASE,
  ensureCsrfCookie,
  formatApiError,
  getCsrfToken,
  parseAllauthAuthResponse,
  parseAllauthJson,
} from "$lib/api/core";

interface AuthUser {
  id: number;
  email: string;
  display?: string;
  name?: string;
}

interface LoginResult {
  ok: boolean;
  error?: string;
  pendingFlow?: string;
}

interface SignupResult {
  ok: boolean;
  error?: string;
  pendingFlow?: string;
}

function mapUser(raw: Record<string, unknown> | undefined): AuthUser | null {
  if (!raw || typeof raw.email !== "string") return null;
  return {
    id: typeof raw.id === "number" ? raw.id : Number(raw.id),
    email: raw.email,
    display: typeof raw.display === "string" ? raw.display : undefined,
    name: typeof raw.name === "string" ? raw.name : undefined,
  };
}

function createAuth() {
  let isAuthenticated = $state(false);
  let isLoading = $state(true);
  let user = $state<AuthUser | null>(null);

  async function checkSession() {
    isLoading = true;
    try {
      await ensureCsrfCookie();
      const controller = new AbortController();
      const timeoutId = setTimeout(() => controller.abort(), 10000);
      const res = await fetch(`${ALLAUTH_BASE}/auth/session`, {
        credentials: "include",
        signal: controller.signal,
      });
      clearTimeout(timeoutId);
      const body = await parseAllauthJson(res);
      const parsed = parseAllauthAuthResponse(res, body, "");
      const mapped = mapUser(parsed.user);
      if (parsed.ok && mapped) {
        isAuthenticated = true;
        user = mapped;
      } else {
        isAuthenticated = false;
        user = null;
      }
    } catch {
      isAuthenticated = false;
      user = null;
    } finally {
      isLoading = false;
    }
  }

  async function login(email: string, password: string): Promise<LoginResult> {
    await ensureCsrfCookie();
    const res = await fetch(`${ALLAUTH_BASE}/auth/login`, {
      method: "POST",
      headers: {
        "Content-Type": "application/json",
        "X-CSRFToken": getCsrfToken(),
      },
      credentials: "include",
      body: JSON.stringify({ email, password }),
    });
    const body = await parseAllauthJson(res);
    const parsed = parseAllauthAuthResponse(
      res,
      body,
      `Login failed (HTTP ${res.status}).`,
    );

    if (parsed.ok) {
      const mapped = mapUser(parsed.user);
      if (mapped) {
        isAuthenticated = true;
        user = mapped;
        return { ok: true };
      }
    }

    if (parsed.pendingFlow) {
      if (import.meta.env.DEV) {
        console.error("login pending flow", res.status, body);
      }
      return { ok: false, pendingFlow: parsed.pendingFlow };
    }

    if (import.meta.env.DEV) {
      console.error("login failed", res.status, body);
    }
    return {
      ok: false,
      error:
        parsed.error ||
        "Login failed. Please check your credentials.",
    };
  }

  async function signup(
    email: string,
    password: string,
    _password2: string,
  ): Promise<SignupResult> {
    await ensureCsrfCookie();
    const res = await fetch(`${ALLAUTH_BASE}/auth/signup`, {
      method: "POST",
      headers: {
        "Content-Type": "application/json",
        "X-CSRFToken": getCsrfToken(),
      },
      credentials: "include",
      body: JSON.stringify({ email, password }),
    });
    const body = await parseAllauthJson(res);
    const parsed = parseAllauthAuthResponse(
      res,
      body,
      `Sign up failed (HTTP ${res.status}).`,
    );

    // Signup success: 200 with user, or 401 with verify_email pending
    if (parsed.ok) {
      return { ok: true };
    }
    if (parsed.pendingFlow === "verify_email") {
      return { ok: true, pendingFlow: "verify_email" };
    }
    if (res.status === 401 && parsed.pendingFlow) {
      return { ok: true, pendingFlow: parsed.pendingFlow };
    }

    if (import.meta.env.DEV) {
      console.error("signup failed", res.status, body);
    }
    return {
      ok: false,
      error:
        parsed.error ||
        "Sign up failed. Please try again.",
    };
  }

  async function logout(): Promise<void> {
    try {
      await ensureCsrfCookie();
      await fetch(`${ALLAUTH_BASE}/auth/session`, {
        method: "DELETE",
        headers: {
          "X-CSRFToken": getCsrfToken(),
        },
        credentials: "include",
      });
    } finally {
      isAuthenticated = false;
      user = null;
    }
  }

  return {
    get isAuthenticated() {
      return isAuthenticated;
    },
    get isLoading() {
      return isLoading;
    },
    get user() {
      return user;
    },
    checkSession,
    login,
    signup,
    logout,
  };
}

let authInstance: ReturnType<typeof createAuth> | null = null;

export function getAuth() {
  if (!authInstance) {
    authInstance = createAuth();
  }
  return authInstance;
}
