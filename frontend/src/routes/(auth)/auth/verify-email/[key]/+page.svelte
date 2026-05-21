<script lang="ts">
	import { page } from '$app/state';
	import { goto } from '$app/navigation';
	import { verifyEmail } from '$lib/api/client';
	import { Button } from '$lib/components/ui/button';
	import { Alert, AlertDescription } from '$lib/components/ui/alert';
	import { onMount } from 'svelte';

	let verifying = $state(true);
	let success = $state(false);
	let error = $state('');

	onMount(async () => {
		const key = page.params.key ?? '';
		try {
			await verifyEmail(key);
			success = true;
		} catch (err: unknown) {
			const e = err as { errors?: Array<{ message: string }> };
			if (e.errors?.length) {
				error = e.errors.map((e) => e.message).join('. ');
			} else {
				error = 'Email verification failed. The link may be expired or invalid.';
			}
		} finally {
			verifying = false;
		}
	});
</script>

<svelte:head>
	<title>Email Verification - LLM Lab</title>
</svelte:head>

<div class="flex min-h-[50vh] items-center justify-center sm:min-h-[60vh]">
	<div class="w-full max-w-md sm:rounded-md sm:border sm:border-border sm:bg-card sm:py-6 sm:shadow-sm">
		<div class="space-y-1.5 sm:px-6">
			<h2 class="text-xl font-semibold leading-none tracking-tight sm:text-2xl">Email Verification</h2>
		</div>
		<div class="pt-4 sm:px-6">
			{#if verifying}
				<p class="text-muted-foreground">Verifying your email...</p>
			{:else if success}
				<Alert class="mb-4">
					<AlertDescription>Your email has been verified successfully!</AlertDescription>
				</Alert>
				<Button class="h-11 w-full text-base sm:h-9 sm:text-sm" onclick={() => goto('/auth/login')}>Continue to Login</Button>
			{:else}
				<Alert variant="destructive" class="mb-4">
					<AlertDescription>{error}</AlertDescription>
				</Alert>
				<Button variant="outline" class="h-11 w-full text-base sm:h-9 sm:text-sm" onclick={() => goto('/auth/signup')}>
					Back to Sign Up
				</Button>
			{/if}
		</div>
	</div>
</div>
