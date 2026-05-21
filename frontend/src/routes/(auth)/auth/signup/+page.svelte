<script lang="ts">
	import { goto } from '$app/navigation';
	import { formatApiError } from '$lib/api/core';
	import { getAuth } from '$lib/stores/auth.svelte';
	import { Input } from '$lib/components/ui/input';
	import { Label } from '$lib/components/ui/label';
	import { Button } from '$lib/components/ui/button';
	import { Alert, AlertDescription } from '$lib/components/ui/alert';

	const auth = getAuth();

	let email = $state('');
	let password = $state('');
	let password2 = $state('');
	let error = $state('');
	let submitting = $state(false);

	async function handleSubmit(e: Event) {
		e.preventDefault();
		error = '';

		if (password !== password2) {
			error = 'Passwords do not match.';
			return;
		}

		submitting = true;
		try {
			const res = await auth.signup(email, password, password2);
			if (!res.ok) {
				error = res.error || 'Sign up failed. Please try again.';
				return;
			}
			goto('/auth/verify-email?created=1');
		} catch (err: unknown) {
			error = formatApiError(err, 'Sign up failed. Please try again.');
		} finally {
			submitting = false;
		}
	}
</script>

<svelte:head>
	<title>Sign Up - LLM Lab</title>
</svelte:head>

<!-- Mobile: borderless inline form. Desktop: bordered card -->
<div class="w-full sm:rounded-md sm:border sm:border-border sm:bg-card sm:py-6 sm:shadow-sm">
		<div class="space-y-1.5 sm:px-6">
			<h2 class="text-xl font-semibold leading-none tracking-tight sm:text-2xl">Create Account</h2>
			<p class="text-xs text-muted-foreground sm:text-sm">Enter your email and choose a password.</p>
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
						autocomplete="new-password"
						class="h-11 text-base sm:h-9 sm:text-sm"
					/>
				</div>
				<div class="space-y-2">
					<Label for="password2">Confirm Password</Label>
					<Input
						id="password2"
						type="password"
						bind:value={password2}
						required
						autocomplete="new-password"
						class="h-11 text-base sm:h-9 sm:text-sm"
					/>
				</div>
				<Button type="submit" class="h-11 w-full text-base sm:h-9 sm:text-sm" disabled={submitting}>
					{submitting ? 'Creating account...' : 'Sign Up'}
				</Button>
			</form>
		</div>
		<div class="pt-4 text-center text-sm sm:px-6">
			<p class="text-muted-foreground">
				Already have an account?
				<a href="/auth/login" class="underline hover:text-foreground">Log in</a>
			</p>
		</div>
	</div>
