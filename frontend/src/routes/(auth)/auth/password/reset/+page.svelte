<script lang="ts">
	import { requestPasswordReset } from '$lib/api/client';
	import { Input } from '$lib/components/ui/input';
	import { Label } from '$lib/components/ui/label';
	import { Button } from '$lib/components/ui/button';
	import { Alert, AlertDescription } from '$lib/components/ui/alert';

	let email = $state('');
	let error = $state('');
	let success = $state(false);
	let submitting = $state(false);

	async function handleSubmit(e: Event) {
		e.preventDefault();
		error = '';
		submitting = true;

		try {
			await requestPasswordReset(email);
			success = true;
		} catch (err: unknown) {
			const e = err as { errors?: Array<{ message: string }> };
			if (e.errors?.length) {
				error = e.errors.map((e) => e.message).join('. ');
			} else {
				error = 'Failed to send password reset email.';
			}
		} finally {
			submitting = false;
		}
	}
</script>

<svelte:head>
	<title>Reset Password - LLM Lab</title>
</svelte:head>

<div class="flex min-h-[50vh] items-center justify-center sm:min-h-[60vh]">
	<div class="w-full max-w-md sm:rounded-md sm:border sm:border-border sm:bg-card sm:py-6 sm:shadow-sm">
		<div class="space-y-1.5 sm:px-6">
			<h2 class="text-xl font-semibold leading-none tracking-tight sm:text-2xl">Reset Password</h2>
			<p class="text-xs text-muted-foreground sm:text-sm">Enter your email and we'll send you a reset link.</p>
		</div>
		<div class="pt-4 sm:px-6">
			{#if success}
				<Alert>
					<AlertDescription>
						If an account exists with that email, we've sent a password reset link.
						Open the <strong>latest</strong> message only — in local dev, see
						<a href="http://localhost:8025" class="underline" target="_blank" rel="noreferrer">Mailpit</a>.
						Older reset emails stop working after you request a new one.
					</AlertDescription>
				</Alert>
			{:else}
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
					<Button type="submit" class="h-11 w-full text-base sm:h-9 sm:text-sm" disabled={submitting}>
						{submitting ? 'Sending...' : 'Send Reset Link'}
					</Button>
				</form>
			{/if}
		</div>
		<div class="pt-4 text-center text-sm sm:px-6">
			<a href="/auth/login" class="text-muted-foreground underline hover:text-foreground">
				Back to login
			</a>
		</div>
	</div>
</div>
