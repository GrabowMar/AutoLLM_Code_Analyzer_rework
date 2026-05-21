<script lang="ts">
	import { page } from '$app/state';
	import { Alert, AlertDescription } from '$lib/components/ui/alert';

	const created = $derived(page.url.searchParams.get('created') === '1');
</script>

<svelte:head>
	<title>Verify Your Email - LLM Lab</title>
</svelte:head>

<div class="flex min-h-[50vh] items-center justify-center sm:min-h-[60vh]">
	<div class="w-full max-w-md sm:rounded-md sm:border sm:border-border sm:bg-card sm:py-6 sm:shadow-sm">
		<div class="space-y-1.5 sm:px-6">
			<h2 class="text-xl font-semibold leading-none tracking-tight sm:text-2xl">Check Your Email</h2>
		</div>
		<div class="pt-4 sm:px-6">
			<Alert>
				<AlertDescription>
					{#if created}
						Your account was created. Email verification is required before you can sign in.
					{:else}
						Please verify your email address before signing in.
					{/if}
					In local development, open
					<a href="http://localhost:8025" class="underline" target="_blank" rel="noreferrer">Mailpit</a>
					and click the latest verification link for your address.
				</AlertDescription>
			</Alert>
			<p class="mt-4 text-xs text-muted-foreground sm:text-sm">
				After verifying, go to
				<a href="/auth/login" class="underline hover:text-foreground">Log in</a>.
				Didn't receive the email? Check spam, or
				<a href="/auth/signup" class="underline hover:text-foreground">sign up again</a> with a new address.
			</p>
		</div>
	</div>
</div>
