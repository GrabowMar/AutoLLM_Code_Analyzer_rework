<script lang="ts">
	import FlaskConical from '@lucide/svelte/icons/flask-conical';
	import ArrowLeft from '@lucide/svelte/icons/arrow-left';
	import ThemeToggle from '$lib/components/ThemeToggle.svelte';

	let { children } = $props();
</script>

<div class="flex min-h-screen flex-col items-center justify-center bg-background px-4 py-8 pb-20 sm:p-4">
	<div class="w-full max-w-md space-y-6">
		<div class="flex flex-col items-center space-y-3 text-center">
			<div class="flex size-11 items-center justify-center rounded-md bg-primary text-primary-foreground sm:size-12">
				<FlaskConical class="size-5 sm:size-6" />
			</div>
			<div>
				<h1 class="font-display text-xl font-bold tracking-tight sm:text-2xl">
					LLM<span class="text-primary">_</span>Lab
				</h1>
				<p class="font-mono text-xs text-muted-foreground sm:text-sm">// research platform for llm evaluation</p>
			</div>
		</div>

		<!-- Mobile: borderless inline form. Desktop: bordered card -->
		<main class="w-full sm:rounded-md sm:border sm:border-border sm:bg-card sm:p-6 sm:shadow-sm">
			{@render children()}
		</main>

		<div class="flex items-center justify-between">
			<a href="/" class="back-link sm:text-sm">
				<ArrowLeft class="size-3 sm:size-3.5" aria-hidden="true" />
				Back to home
			</a>
			<ThemeToggle />
		</div>
	</div>
</div>
