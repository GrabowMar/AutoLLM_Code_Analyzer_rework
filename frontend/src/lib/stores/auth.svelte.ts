const ALLAUTH_BASE = '/_allauth/browser/v1';

function getCsrfToken(): string {
	if (typeof document === 'undefined') return '';
	const match = document.cookie.match(/(?:^|;\s*)csrftoken=([^;]*)/);
	return match ? decodeURIComponent(match[1]) : '';
}

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
}

function createAuth() {
	let isAuthenticated = $state(false);
	let isLoading = $state(true);
	let user = $state<AuthUser | null>(null);

	async function checkSession() {
		isLoading = true;
		try {
			const controller = new AbortController();
			const timeoutId = setTimeout(() => controller.abort(), 10000);
			const res = await fetch(`${ALLAUTH_BASE}/auth/session`, {
				credentials: 'include',
				signal: controller.signal,
			});
			clearTimeout(timeoutId);
			const body = await res.json();
			if (res.ok && body.data?.user) {
				isAuthenticated = true;
				user = body.data.user;
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
		const res = await fetch(`${ALLAUTH_BASE}/auth/login`, {
			method: 'POST',
			headers: {
				'Content-Type': 'application/json',
				'X-CSRFToken': getCsrfToken(),
			},
			credentials: 'include',
			body: JSON.stringify({ email, password }),
		});
		const body = await res.json();

		if (res.ok && body.data?.user) {
			isAuthenticated = true;
			user = body.data.user;
			return { ok: true };
		}

		// Check for pending flows (e.g. MFA)
		if (body.data?.flows) {
			const pending = body.data.flows.find(
				(f: { id: string; is_pending?: boolean }) => f.is_pending
			);
			if (pending) {
				return { ok: false, pendingFlow: pending.id };
			}
		}

		const errors = body.errors as Array<{ message: string }> | undefined;
		const errorMsg = errors?.map((e) => e.message).join('. ');
		return { ok: false, error: errorMsg };
	}

	async function signup(
		email: string,
		password: string,
		_password2: string
	): Promise<SignupResult> {
		const res = await fetch(`${ALLAUTH_BASE}/auth/signup`, {
			method: 'POST',
			headers: {
				'Content-Type': 'application/json',
				'X-CSRFToken': getCsrfToken(),
			},
			credentials: 'include',
			body: JSON.stringify({ email, password }),
		});
		const body = await res.json();

		if (res.ok || res.status === 401) {
			// 401 with email_verification_sent flow is expected
			return { ok: true };
		}

		const errors = body.errors as Array<{ message: string }> | undefined;
		const errorMsg = errors?.map((e) => e.message).join('. ');
		return { ok: false, error: errorMsg };
	}

	async function logout(): Promise<void> {
		try {
			await fetch(`${ALLAUTH_BASE}/auth/session`, {
				method: 'DELETE',
				headers: {
					'X-CSRFToken': getCsrfToken(),
				},
				credentials: 'include',
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
