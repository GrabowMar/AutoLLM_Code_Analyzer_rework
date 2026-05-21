<script lang="ts">
	import { onMount } from 'svelte';
	import { goto } from '$app/navigation';
	import { formatApiError } from '$lib/api/core';
	import { getAuth } from '$lib/stores/auth.svelte';
	import { Input } from '$lib/components/ui/input';
	import { Label } from '$lib/components/ui/label';
	import { Button } from '$lib/components/ui/button';
	import { Alert, AlertDescription } from '$lib/components/ui/alert';

	const auth = getAuth();

	const REMEMBER_KEY = 'llm_lab_stay_logged_in';

	let email = $state('');
	let password = $state('');
	let stayLoggedIn = $state(true);
	let error = $state('');
	let submitting = $state(false);

	onMount(() => {
		try {
			const saved = localStorage.getItem(REMEMBER_KEY);
			if (saved !== null) {
				stayLoggedIn = saved === '1';
			}
		} catch {
			// private browsing / blocked storage
		}
	});

	function persistRememberPreference() {
		try {
			localStorage.setItem(REMEMBER_KEY, stayLoggedIn ? '1' : '0');
		} catch {
			// ignore
		}
	}

	$effect(() => {
		if (!auth.isLoading && auth.isAuthenticated) {
			goto('/');
		}
	});

	async function handleSubmit(e: Event) {
		e.preventDefault();
		error = '';
		submitting = true;

		try {
			persistRememberPreference();
			const res = await auth.login(email, password, stayLoggedIn);
			if (!res.ok && res.pendingFlow) {
				if (res.pendingFlow === 'mfa_authenticate') {
					goto('/auth/2fa');
					return;
				}
				if (res.pendingFlow === 'verify_email') {
					error =
						'Please verify your email before signing in. Check Mailpit at http://localhost:8025 for the verification link.';
					return;
				}
			}
			if (!res.ok) {
				error = res.error || 'Login failed. Please check your credentials.';
				return;
			}
			goto('/');
		} catch (err: unknown) {
			error = formatApiError(err, 'Login failed. Please check your credentials.');
		} finally {
			submitting = false;
		}
	}
</script>

<svelte:head>
	<title>Log In - LLM Lab</title>
</svelte:head>

<!-- Mobile: borderless inline form. Desktop: bordered card -->
<div class="w-full sm:rounded-md sm:border sm:border-border sm:bg-card sm:py-6 sm:shadow-sm">
		<div class="space-y-1.5 sm:px-6">
			<h2 class="text-xl font-semibold leading-none tracking-tight sm:text-2xl">Log In</h2>
			<p class="text-xs text-muted-foreground sm:text-sm">Enter your email and password to sign in.</p>
		</div>
		<div class="pt-4 sm:px-6">
			{#if error}
				<Alert variant="destructive" class="mb-4">
					<AlertDescription>{error}</AlertDescription>
				</Alert>
			{/if}
			<form onsubmit={handleSubmit} class="space-y-4">
				<div class="space-y-2">
					<Label for="email">Email</Label>
					<Input
						id="email"
						type="email"
						placeholder="you@example.com"
						bind:value={email}
						required
						autocomplete="email"
						class="h-11 text-base sm:h-9 sm:text-sm"
					/>
				</div>
				<div class="space-y-2">
					<Label for="password">Password</Label>
					<Input
						id="password"
						type="password"
						bind:value={password}
						required
						autocomplete="current-password"
						class="h-11 text-base sm:h-9 sm:text-sm"
					/>
				</div>
				<label
					class="flex items-center gap-2.5 text-sm cursor-pointer select-none text-muted-foreground hover:text-foreground"
				>
					<input
						type="checkbox"
						bind:checked={stayLoggedIn}
						onchange={persistRememberPreference}
						class="h-4 w-4 rounded border-input accent-primary"
					/>
					Stay logged in
				</label>
				<Button type="submit" class="h-11 w-full text-base sm:h-9 sm:text-sm" disabled={submitting}>
					{submitting ? 'Signing in...' : 'Sign In'}
				</Button>
			</form>
		</div>
		<div class="flex flex-col space-y-2 pt-4 text-center text-sm sm:px-6">
			<a href="/auth/password/reset" class="text-muted-foreground hover:text-foreground underline">
				Forgot your password?
			</a>
			<p class="text-muted-foreground">
				Don't have an account?
				<a href="/auth/signup" class="underline hover:text-foreground">Sign up</a>
			</p>
		</div>
	</div>
