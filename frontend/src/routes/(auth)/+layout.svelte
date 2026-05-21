<script lang="ts">
	import FlaskConical from '@lucide/svelte/icons/flask-conical';
	import ArrowLeft from '@lucide/svelte/icons/arrow-left';
	import ThemeToggle from '$lib/components/ThemeToggle.svelte';

	let { children } = $props();
</script>

<div class="relative flex min-h-screen flex-col items-center justify-center px-4 py-8 pb-20 sm:p-4 bg-background overflow-hidden">
	<!-- Grid background (IDE feel) -->
	<div
		class="pointer-events-none absolute inset-0 opacity-[0.04]"
		style="background-image: linear-gradient(var(--foreground) 1px, transparent 1px), linear-gradient(90deg, var(--foreground) 1px, transparent 1px); background-size: 32px 32px;"
		aria-hidden="true"
	></div>
	<!-- Glow accent -->
	<div
		class="pointer-events-none absolute -top-40 left-1/2 -translate-x-1/2 h-[500px] w-[500px] rounded-full opacity-20 blur-3xl"
		style="background: radial-gradient(circle, var(--primary), transparent 70%);"
		aria-hidden="true"
	></div>

	<div class="relative w-full max-w-md space-y-5 sm:space-y-6">
		<div class="flex flex-col items-center space-y-2 text-center sm:space-y-3">
			<div
				class="flex size-11 items-center justify-center rounded-md bg-primary text-primary-foreground shadow-lg shadow-primary/30 sm:size-12"
			>
				<FlaskConical class="size-5 sm:size-6" />
			</div>
			<div>
				<h1 class="text-xl font-bold tracking-tight sm:text-2xl" style="font-family: var(--font-display);">
					LLM<span class="text-primary">_</span>Lab
				</h1>
				<p class="text-xs text-muted-foreground sm:text-sm" style="font-family: var(--font-mono);">// research platform for llm evaluation</p>
			</div>
		</div>

		{@render children()}

		<div class="flex items-center justify-between">
			<a href="/" class="inline-flex items-center gap-1 text-xs text-muted-foreground transition-colors hover:text-primary sm:text-sm">
				<ArrowLeft class="size-3 sm:size-3.5" />
				Back to home
			</a>
			<ThemeToggle />
		</div>
	</div>
</div>
