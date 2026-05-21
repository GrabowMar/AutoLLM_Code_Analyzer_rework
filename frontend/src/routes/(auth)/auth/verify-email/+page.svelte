<script lang="ts">
	import { Alert, AlertDescription } from '$lib/components/ui/alert';
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
					We've sent a verification link to your email address. Please check your inbox and click
					the link to verify your account.
				</AlertDescription>
			</Alert>
			<p class="mt-4 text-xs text-muted-foreground sm:text-sm">
				Didn't receive the email? Check your spam folder, or
				<a href="/auth/signup" class="underline hover:text-foreground">try signing up again</a>.
			</p>
		</div>
	</div>
</div>
