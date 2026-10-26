<script lang="ts">
	import { Button } from '$lib/components/ui/button';
	import Plus from '@lucide/svelte/icons/plus';
	import Wand2 from '@lucide/svelte/icons/wand-2';
	import RefreshCw from '@lucide/svelte/icons/refresh-cw';
	import Loader2 from '@lucide/svelte/icons/loader-2';
	import { greeting, timeAgo } from '$lib/utils/dashboard';

	interface Props {
		userName: string | null;
		isRefreshing: boolean;
		lastRefreshed: Date | null;
		onRefresh: () => void;
	}

	let { userName, isRefreshing, lastRefreshed, onRefresh }: Props = $props();

	const today = new Date().toLocaleDateString(undefined, {
		weekday: 'long',
		month: 'long',
		day: 'numeric',
		year: 'numeric',
	});
</script>

<section class="hero-panel" aria-labelledby="hero-title">
	<!-- Decorative figure: two fitted curves with a confidence band on a log-like grid -->
	<svg class="hero-figure" viewBox="0 0 480 200" preserveAspectRatio="xMaxYMid slice" aria-hidden="true">
		<g stroke="currentColor" stroke-opacity="0.10" stroke-width="1">
			{#each [40, 80, 120, 160] as y (y)}<line x1="0" x2="480" y1={y} y2={y} />{/each}
			{#each [60, 140, 220, 300, 380, 460] as x (x)}<line y1="0" y2="200" x1={x} x2={x} />{/each}
		</g>
		<path
			d="M0 170 C 80 160, 140 120, 220 100 S 360 60, 480 40 L 480 70 C 360 92, 300 118, 220 130 S 80 178, 0 190 Z"
			fill="currentColor"
			fill-opacity="0.08"
		/>
		<path d="M0 180 C 80 168, 140 126, 220 115 S 360 76, 480 55" fill="none" stroke="currentColor" stroke-opacity="0.55" stroke-width="2" />
		<path
			d="M0 186 C 90 182, 160 160, 240 150 S 380 120, 480 110"
			fill="none"
			stroke="var(--accent-2)"
			stroke-opacity="0.6"
			stroke-width="1.5"
			stroke-dasharray="5 4"
		/>
		<g fill="var(--card)" stroke="currentColor" stroke-opacity="0.7" stroke-width="1.5">
			{#each [[60, 170], [140, 135], [220, 114], [300, 94], [380, 74], [460, 58]] as [cx, cy] (cx)}
				<circle {cx} {cy} r="3.5" />
			{/each}
		</g>
	</svg>

	<div class="relative flex flex-col gap-5 p-5 sm:p-7">
		<p class="page-eyebrow">
			<span class="text-primary">Lab notebook</span>
			<span aria-hidden="true">·</span>
			<span class="normal-case tracking-normal">{today}</span>
		</p>

		<div class="max-w-2xl space-y-2">
			<h1 id="hero-title" class="display-xl">
				{greeting()}{#if userName}, <span class="italic text-primary">{userName}</span>{/if}.
			</h1>
			<p class="max-w-xl text-sm leading-relaxed text-muted-foreground sm:text-[15px]">
				Benchmark LLM-generated applications end to end — generation, static and dynamic analysis,
				and comparative statistics across models and harnesses.
			</p>
		</div>

		<div class="flex flex-wrap items-center gap-2">
			<Button href="/sample-generator" size="sm" class="gap-1.5">
				<Wand2 class="h-3.5 w-3.5" />
				Generate app
			</Button>
			<Button href="/analysis/create" size="sm" variant="outline" class="gap-1.5 bg-card">
				<Plus class="h-3.5 w-3.5" />
				New analysis
			</Button>
			<Button
				onclick={onRefresh}
				size="icon"
				variant="ghost"
				class="h-8 w-8"
				disabled={isRefreshing}
				aria-label="Refresh dashboard"
			>
				{#if isRefreshing}
					<Loader2 class="h-4 w-4 animate-spin" />
				{:else}
					<RefreshCw class="h-4 w-4" />
				{/if}
			</Button>
			{#if lastRefreshed}
				<span class="ml-1 flex items-center gap-1.5 font-mono text-[11px] text-muted-foreground">
					<span class="status-dot status-dot-live h-1.5 w-1.5 text-success"></span>
					live · updated {timeAgo(lastRefreshed.toISOString())}
				</span>
			{/if}
		</div>
	</div>
</section>
