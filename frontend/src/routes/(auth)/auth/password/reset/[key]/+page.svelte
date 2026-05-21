<script lang="ts">
	import { page } from '$app/state';
	import { goto } from '$app/navigation';
	import { ensureCsrfCookie, formatApiError } from '$lib/api/core';
	import { resetPassword, validatePasswordResetKey } from '$lib/api/client';
	import { Input } from '$lib/components/ui/input';
	import { Label } from '$lib/components/ui/label';
	import { Button } from '$lib/components/ui/button';
	import { Alert, AlertDescription } from '$lib/components/ui/alert';
	import { onMount } from 'svelte';

	let password = $state('');
	let password2 = $state('');
	let error = $state('');
	let success = $state(false);
	let submitting = $state(false);
	let validating = $state(true);
	let linkValid = $state(false);

	function resetKeyFromUrl(): string {
		const raw = page.params.key ?? '';
		try {
			return decodeURIComponent(raw);
		} catch {
			return raw;
		}
	}

	onMount(async () => {
		const key = resetKeyFromUrl();
		if (!key) {
			error = 'Password reset link is missing or invalid.';
			validating = false;
			return;
		}
		try {
			await ensureCsrfCookie();
			await validatePasswordResetKey(key);
			linkValid = true;
		} catch (err: unknown) {
			error =
				formatApiError(
					err,
					'This password reset link is expired or invalid.',
				) +
				' Request a new link and open only the newest email (older links stop working).';
		} finally {
			validating = false;
		}
	});

	async function handleSubmit(e: Event) {
		e.preventDefault();
		error = '';

		if (password !== password2) {
			error = 'Passwords do not match.';
			return;
		}
		if (password.length < 8) {
			error = 'Password must be at least 8 characters.';
			return;
		}

		submitting = true;
		const key = resetKeyFromUrl();

		try {
			await resetPassword(key, password);
			success = true;
		} catch (err: unknown) {
			error =
				formatApiError(
					err,
					'Password reset failed. The link may be expired or invalid.',
				) +
				' If you already submitted once, try logging in, or request a fresh link.';
		} finally {
			submitting = false;
		}
	}
</script>

<svelte:head>
	<title>Set New Password - LLM Lab</title>
</svelte:head>

<div class="flex min-h-[50vh] items-center justify-center sm:min-h-[60vh]">
	<div class="w-full max-w-md sm:rounded-md sm:border sm:border-border sm:bg-card sm:py-6 sm:shadow-sm">
		<div class="space-y-1.5 sm:px-6">
			<h2 class="text-xl font-semibold leading-none tracking-tight sm:text-2xl">Set New Password</h2>
			<p class="text-xs text-muted-foreground sm:text-sm">Choose a new password for your account.</p>
		</div>
		<div class="pt-4 sm:px-6">
			{#if validating}
				<p class="text-muted-foreground">Checking reset link...</p>
			{:else if success}
				<Alert class="mb-4">
					<AlertDescription>Your password has been reset successfully!</AlertDescription>
				</Alert>
				<Button class="h-11 w-full text-base sm:h-9 sm:text-sm" onclick={() => goto('/auth/login')}>Continue to Login</Button>
			{:else if !linkValid}
				<Alert variant="destructive" class="mb-4">
					<AlertDescription>{error}</AlertDescription>
				</Alert>
				<Button variant="outline" class="h-11 w-full text-base sm:h-9 sm:text-sm" onclick={() => goto('/auth/password/reset')}>
					Request a new reset link
				</Button>
			{:else}
				{#if error}
					<Alert variant="destructive" class="mb-4">
						<AlertDescription>{error}</AlertDescription>
					</Alert>
				{/if}
				<form onsubmit={handleSubmit} class="space-y-4">
					<div class="space-y-2">
						<Label for="password">New Password</Label>
						<Input
							id="password"
							type="password"
							bind:value={password}
							required
							minlength={8}
							autocomplete="new-password"
							class="h-11 text-base sm:h-9 sm:text-sm"
						/>
						<p class="text-xs text-muted-foreground">At least 8 characters.</p>
					</div>
					<div class="space-y-2">
						<Label for="password2">Confirm New Password</Label>
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
						{submitting ? 'Resetting...' : 'Reset Password'}
					</Button>
				</form>
			{/if}
		</div>
	</div>
</div>
