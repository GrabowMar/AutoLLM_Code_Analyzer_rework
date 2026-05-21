<script lang="ts">
	import { page } from '$app/state';
	import { goto } from '$app/navigation';
	import { resetPassword } from '$lib/api/client';
	import { Input } from '$lib/components/ui/input';
	import { Label } from '$lib/components/ui/label';
	import { Button } from '$lib/components/ui/button';
	import { Alert, AlertDescription } from '$lib/components/ui/alert';

	let password = $state('');
	let password2 = $state('');
	let error = $state('');
	let success = $state(false);
	let submitting = $state(false);

	async function handleSubmit(e: Event) {
		e.preventDefault();
		error = '';

		if (password !== password2) {
			error = 'Passwords do not match.';
			return;
		}

		submitting = true;
		const key = page.params.key ?? '';

		try {
			await resetPassword(key, password);
			success = true;
		} catch (err: unknown) {
			const e = err as { errors?: Array<{ message: string }> };
			if (e.errors?.length) {
				error = e.errors.map((e) => e.message).join('. ');
			} else {
				error = 'Password reset failed. The link may be expired or invalid.';
			}
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
			{#if success}
				<Alert class="mb-4">
					<AlertDescription>Your password has been reset successfully!</AlertDescription>
				</Alert>
				<Button class="h-11 w-full text-base sm:h-9 sm:text-sm" onclick={() => goto('/auth/login')}>Continue to Login</Button>
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
							autocomplete="new-password"
							class="h-11 text-base sm:h-9 sm:text-sm"
						/>
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
