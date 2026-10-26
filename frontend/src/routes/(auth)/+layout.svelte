<script lang="ts">
	import ArrowLeft from '@lucide/svelte/icons/arrow-left';
	import ThemeToggle from '$lib/components/ThemeToggle.svelte';
	import BrandMark from '$lib/components/BrandMark.svelte';

	let { children } = $props();

	const pillars = [
		{ n: '01', title: 'Generate', text: 'Build applications with any model, harness and sampling preset.' },
		{ n: '02', title: 'Evaluate', text: 'Static, dynamic and runtime analysis of every generated app.' },
		{ n: '03', title: 'Compare', text: 'Pre-registered experiments, paired deltas and confidence intervals.' },
	];
</script>

<div class="grid min-h-screen lg:grid-cols-[minmax(0,1fr)_minmax(0,1.1fr)]">
	<!-- Editorial panel (desktop only) -->
	<aside class="relative hidden overflow-hidden border-r border-sidebar-border bg-sidebar text-sidebar-foreground lg:flex lg:flex-col lg:justify-between lg:p-12">
		<BrandMark tagline size="lg" />

		<div class="max-w-md space-y-8">
			<h2 class="font-display text-4xl font-semibold leading-[1.1] tracking-tight text-sidebar-accent-foreground">
				A research platform for evaluating <span class="italic text-sidebar-primary">LLM-generated</span> code.
			</h2>
			<ol class="space-y-5">
				{#each pillars as p (p.n)}
					<li class="grid grid-cols-[2.5rem_1fr] gap-x-2">
						<span class="font-mono text-xs text-sidebar-primary tabular-nums">{p.n}</span>
						<div>
							<p class="text-sm font-semibold text-sidebar-accent-foreground">{p.title}</p>
							<p class="text-sm text-sidebar-foreground/75">{p.text}</p>
						</div>
					</li>
				{/each}
			</ol>
		</div>

		<p class="font-mono text-[11px] uppercase tracking-[0.14em] text-sidebar-foreground/50">
			LLM Lab · {new Date().getFullYear()}
		</p>
	</aside>

	<div class="lab-canvas flex flex-col items-center justify-center px-4 py-8 pb-20 sm:p-8">
		<div class="w-full max-w-md space-y-6">
			<div class="flex flex-col items-center space-y-3 text-center lg:hidden">
				<BrandMark tagline size="lg" />
			</div>

			<!-- Mobile: borderless inline form. Desktop: bordered card -->
			<main class="w-full sm:rounded-md sm:border sm:border-border sm:bg-card sm:p-7 sm:shadow-[var(--shadow-pop)]">
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
</div>
