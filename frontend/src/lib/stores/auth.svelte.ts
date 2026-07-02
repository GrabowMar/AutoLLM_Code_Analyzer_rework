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

interface BootstrapStatus {
  requiresBootstrap: boolean;
  defaultEmail: string;
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
  let sessionCheckGeneration = 0;

  async function checkSession() {
    const generation = ++sessionCheckGeneration;
    isLoading = true;
    try {
      const res = await fetch(`${ALLAUTH_BASE}/auth/session`, {
        credentials: "include",
      });
      if (generation !== sessionCheckGeneration) return;

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
      if (generation !== sessionCheckGeneration) return;
      isAuthenticated = false;
      user = null;
    } finally {
      if (generation === sessionCheckGeneration) {
        isLoading = false;
      }
    }
  }

  async function login(
    email: string,
    password: string,
    remember = true,
  ): Promise<LoginResult> {
    // Ignore in-flight checkSession so it cannot clear state after a successful login.
    sessionCheckGeneration++;
    await ensureCsrfCookie();
    const res = await fetch(`${ALLAUTH_BASE}/auth/login`, {
      method: "POST",
      headers: {
        "Content-Type": "application/json",
        "X-CSRFToken": getCsrfToken(),
      },
      credentials: "include",
      body: JSON.stringify({ email, password, remember }),
    });
    const body = await parseAllauthJson(res);

    // Already signed in (session cookie present) — treat as success.
    if (res.status === 409) {
      await checkSession();
      if (isAuthenticated && user) {
        return { ok: true };
      }
      return {
        ok: false,
        error:
          "You already have an active session. Open the home page or sign out from the user menu, then try again.",
      };
    }

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
      error: parsed.error || "Login failed. Please check your credentials.",
    };
  }

  async function signup(
    email: string,
    password: string,
    _password2: string,
    remember = true,
  ): Promise<SignupResult> {
    sessionCheckGeneration++;
    await ensureCsrfCookie();
    const res = await fetch(`${ALLAUTH_BASE}/auth/signup`, {
      method: "POST",
      headers: {
        "Content-Type": "application/json",
        "X-CSRFToken": getCsrfToken(),
      },
      credentials: "include",
      body: JSON.stringify({ email, password, remember }),
    });
    const body = await parseAllauthJson(res);
    const parsed = parseAllauthAuthResponse(
      res,
      body,
      `Sign up failed (HTTP ${res.status}).`,
    );

    // Signup success: 200 with user logged in immediately
    if (parsed.ok) {
      const mapped = mapUser(parsed.user);
      if (mapped) {
        isAuthenticated = true;
        user = mapped;
      }
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
      error: parsed.error || "Sign up failed. Please try again.",
    };
  }

  async function getBootstrapStatus(): Promise<BootstrapStatus> {
    const res = await fetch("/api/users/bootstrap-status/", {
      credentials: "include",
    });
    const body = await parseAllauthJson(res);
    if (!res.ok) {
      throw body;
    }
    return {
      requiresBootstrap: body.requires_bootstrap === true,
      defaultEmail:
        typeof body.default_email === "string" ? body.default_email : "",
    };
  }

  async function bootstrapAdminSignup(
    name: string,
    password: string,
    remember = true,
  ): Promise<SignupResult> {
    sessionCheckGeneration++;
    await ensureCsrfCookie();
    const res = await fetch("/api/users/bootstrap-admin/", {
      method: "POST",
      headers: {
        "Content-Type": "application/json",
        "X-CSRFToken": getCsrfToken(),
      },
      credentials: "include",
      body: JSON.stringify({ name, password, remember }),
    });
    const body = await parseAllauthJson(res);
    if (!res.ok) {
      return {
        ok: false,
        error: formatApiError(body, "Initial admin setup failed."),
      };
    }
    await checkSession();
    if (isAuthenticated && user) {
      return { ok: true };
    }
    return {
      ok: false,
      error:
        "Initial admin setup succeeded, but the new session could not be loaded.",
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
    bootstrapAdminSignup,
    checkSession,
    getBootstrapStatus,
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
