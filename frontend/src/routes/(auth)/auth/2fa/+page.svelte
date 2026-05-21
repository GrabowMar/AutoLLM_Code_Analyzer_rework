<script lang="ts">
	import { goto } from '$app/navigation';
	import { authenticate2FA } from '$lib/api/client';
	import { Input } from '$lib/components/ui/input';
	import { Label } from '$lib/components/ui/label';
	import { Button } from '$lib/components/ui/button';
	import { Alert, AlertDescription } from '$lib/components/ui/alert';

	let code = $state('');
	let error = $state('');
	let submitting = $state(false);

	async function handleSubmit(e: Event) {
		e.preventDefault();
		error = '';
		submitting = true;

		try {
			await authenticate2FA(code);
			goto('/');
		} catch (err: unknown) {
			const e = err as { errors?: Array<{ message: string }> };
			if (e.errors?.length) {
				error = e.errors.map((e) => e.message).join('. ');
			} else {
				error = 'Invalid code. Please try again.';
			}
		} finally {
			submitting = false;
		}
	}
</script>

<svelte:head>
	<title>Two-Factor Authentication - LLM Lab</title>
</svelte:head>

<div class="flex min-h-[50vh] items-center justify-center sm:min-h-[60vh]">
	<div class="w-full max-w-md sm:rounded-md sm:border sm:border-border sm:bg-card sm:py-6 sm:shadow-sm">
		<div class="space-y-1.5 sm:px-6">
			<h2 class="text-xl font-semibold leading-none tracking-tight sm:text-2xl">Two-Factor Authentication</h2>
			<p class="text-xs text-muted-foreground sm:text-sm">Enter the code from your authenticator app.</p>
		</div>
		<div class="pt-4 sm:px-6">
			{#if error}
				<Alert variant="destructive" class="mb-4">
					<AlertDescription>{error}</AlertDescription>
				</Alert>
			{/if}
			<form onsubmit={handleSubmit} class="space-y-4">
				<div class="space-y-2">
					<Label for="code">Authentication Code</Label>
					<Input
						id="code"
						type="text"
						inputmode="numeric"
						pattern="[0-9]*"
						placeholder="000000"
						bind:value={code}
						required
						autocomplete="one-time-code"
						class="h-11 text-center text-lg tracking-[0.3em] sm:h-9 sm:text-base sm:tracking-normal sm:text-left"
					/>
				</div>
				<Button type="submit" class="h-11 w-full text-base sm:h-9 sm:text-sm" disabled={submitting}>
					{submitting ? 'Verifying...' : 'Verify'}
				</Button>
			</form>
		</div>
	</div>
</div>
