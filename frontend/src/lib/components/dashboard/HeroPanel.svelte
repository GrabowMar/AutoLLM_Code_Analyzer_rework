<script lang="ts">
	/**
	 * Dashboard masthead — the lab's "front page": an editorial header on graph paper with the
	 * dateline, a live-status strip, the serif headline, and four numbered headline figures
	 * (models · apps · analyses · findings), the analyses figure carrying a 30-day sparkline.
	 * Built with ui-ux-pro-max (Swiss Modernism 2.0, dense dashboard; one animated element —
	 * the sparkline draw-in — and none under prefers-reduced-motion).
	 */
	import { onMount, type Component, type Snippet } from 'svelte';
	import { DropdownMenu } from 'bits-ui';
	import { goto } from '$app/navigation';
	import { Button } from '$lib/components/ui/button';
	import Plus from '@lucide/svelte/icons/plus';
	import Wand2 from '@lucide/svelte/icons/wand-2';
	import RefreshCw from '@lucide/svelte/icons/refresh-cw';
	import Loader2 from '@lucide/svelte/icons/loader-2';
	import Wrench from '@lucide/svelte/icons/wrench';
	import CloudDownload from '@lucide/svelte/icons/cloud-download';
	import Eraser from '@lucide/svelte/icons/eraser';
	import Activity from '@lucide/svelte/icons/activity';
	import Cpu from '@lucide/svelte/icons/cpu';
	import ArrowUpRight from '@lucide/svelte/icons/arrow-up-right';
	import { timeAgo } from '$lib/utils/dashboard';
	import AsciiField from './AsciiField.svelte';
	import { getMe } from '$lib/api/users';
	import { pageTitle } from '$lib/stores/page-title.svelte';
	import type { AnalysisTrends, AnalyzerHealth, StatisticsOverview } from '$lib/api/client';

	interface Props {
		isRefreshing: boolean;
		lastRefreshed: Date | null;
		onRefresh: () => void;
		/** Running maintenance action ('sync' | 'caches' | 'stuck'), or null. */
		pendingAction?: string | null;
		onSyncModels?: () => void;
		onClearCaches?: () => void;
		onClearStuck?: () => void;
		overview?: StatisticsOverview | null;
		totalAnalyses?: number | null;
		completedAnalyses?: number | null;
		runningAnalyses?: number | null;
		trends?: AnalysisTrends | null;
		health?: AnalyzerHealth | null;
	}

	let {
		isRefreshing,
		lastRefreshed,
		onRefresh,
		pendingAction = null,
		onSyncModels,
		onClearCaches,
		onClearStuck,
		overview = null,
		totalAnalyses = null,
		completedAnalyses = null,
		runningAnalyses = null,
		trends = null,
		health = null,
	}: Props = $props();

	// The masthead owns the page <h1>, so it publishes the breadcrumb title like PageHeader does.
	$effect(() => {
		pageTitle.current = 'Dashboard';
		return () => {
			if (pageTitle.current === 'Dashboard') pageTitle.current = '';
		};
	});

	// Every maintenance action is staff-only on the API, so non-staff never see the menu
	// (same check as the sidebar's staffOnly sections).
	let isStaff = $state(false);
	let firstName = $state('');
	onMount(async () => {
		try {
			const me = await getMe();
			isStaff = me.is_staff ?? false;
			firstName = (me.name || '').trim().split(/\s+/)[0] || me.email.split('@')[0] || '';
		} catch {
			isStaff = false;
		}
	});

	// Dateline: re-evaluated on every refresh so a tab left open overnight rolls over.
	const now = $derived(lastRefreshed ?? new Date());
	const greeting = $derived.by(() => {
		const h = now.getHours();
		return h < 5 ? 'Working late' : h < 12 ? 'Good morning' : h < 18 ? 'Good afternoon' : 'Good evening';
	});
	const dateline = $derived(
		now.toLocaleDateString('en-GB', { weekday: 'long', day: 'numeric', month: 'long', year: 'numeric' })
	);
	const isoWeek = $derived.by(() => {
		const d = new Date(Date.UTC(now.getFullYear(), now.getMonth(), now.getDate()));
		const day = d.getUTCDay() || 7;
		d.setUTCDate(d.getUTCDate() + 4 - day);
		const yearStart = new Date(Date.UTC(d.getUTCFullYear(), 0, 1));
		return Math.ceil(((d.getTime() - yearStart.getTime()) / 86_400_000 + 1) / 7);
	});

	// ── 30-day sparkline (analyses per day) ──────────────────────────
	const SPARK_W = 120;
	const SPARK_H = 28;
	const spark = $derived.by(() => {
		const pts = trends?.series ?? [];
		if (pts.length < 4 || !trends?.total) return null;
		const max = Math.max(...pts.map((p) => p.total), 1);
		const step = SPARK_W / (pts.length - 1);
		const xy = pts.map((p, i) => [i * step, SPARK_H - 2 - (p.total / max) * (SPARK_H - 4)] as const);
		const line = xy.map(([x, y], i) => `${i ? 'L' : 'M'}${x.toFixed(1)} ${y.toFixed(1)}`).join(' ');
		const area = `${line} L${SPARK_W} ${SPARK_H} L0 ${SPARK_H} Z`;
		const last = xy[xy.length - 1];
		const peak = pts.reduce((a, b) => (b.total > a.total ? b : a));
		return { line, area, last, peak, days: trends.days, total: trends.total };
	});

	const fmt = (n: number | null | undefined) => (n == null ? '—' : n.toLocaleString());

	const itemClass =
		'flex cursor-pointer select-none items-center gap-2 rounded-sm px-2 py-1.5 text-sm outline-none transition-colors duration-150 data-[highlighted]:bg-surface-2 data-[disabled]:pointer-events-none data-[disabled]:opacity-50 [&_svg]:size-3.5 [&_svg]:shrink-0 [&_svg]:text-muted-foreground';
</script>

<svelte:head>
	<title>Dashboard · LLM Lab</title>
</svelte:head>

{#snippet actionIcon(key: string, Icon: Component)}
	{#if pendingAction === key}<Loader2 class="animate-spin motion-reduce:animate-none" />{:else}<Icon />{/if}
{/snippet}

{#snippet figure(index: string, href: string, label: string, value: string, title: string | undefined, sub: Snippet, extra?: Snippet)}
	<a {href} class="mast-fig group" {title}>
		<span class="mast-fig-label"><span class="mast-fig-index">{index}</span><span class="mast-fig-name">{label}</span><ArrowUpRight class="mast-fig-arrow" aria-hidden="true" /></span>
		<span class="mast-fig-row">
			<span class="mast-fig-value">{value}</span>
			{@render extra?.()}
		</span>
		<span class="mast-fig-sub">{@render sub()}</span>
	</a>
{/snippet}

<header class="masthead">
	<!-- Registration marks: print-shop crop corners framing the sheet. -->
	<span class="mast-crop mast-crop-tl" aria-hidden="true"></span>
	<span class="mast-crop mast-crop-tr" aria-hidden="true"></span>

	<!-- Hero area: ASCII field behind the dateline + headline (figures stay on clean paper). -->
	<div class="mast-hero">
	<AsciiField fadeFrom={0.32} />

	<!-- Dateline strip -->
	<div class="mast-dateline">
		<span class="page-eyebrow"><span class="mast-brand-dot" aria-hidden="true"></span>LLM Lab · Field report · Wk {isoWeek}</span>
		<span class="mast-dateline-rule" aria-hidden="true"></span>
		<span class="flex flex-wrap items-center gap-x-3 gap-y-1 font-mono text-[11px] text-muted-foreground">
			{#if health}
				<a href="/analyzers" class="mast-status hover:text-foreground" title="Analyzer services reachable right now">
					<span class="status-dot h-1.5 w-1.5 {health.offline ? 'text-warning' : 'text-success'}"></span>
					{health.online}/{health.total} analyzers online
				</a>
			{/if}
			{#if lastRefreshed}
				<span class="mast-status" aria-live="polite">
					<span class="status-dot status-dot-live h-1.5 w-1.5 text-success"></span>
					live · {timeAgo(lastRefreshed.toISOString())}
				</span>
			{/if}
		</span>
	</div>

	<!-- Headline + actions -->
	<div class="mast-main">
		<div class="min-w-0">
			<p class="mast-greeting">
				{greeting}{#if firstName}, <span class="text-foreground">{firstName}</span>{/if}<span class="max-sm:hidden"><span aria-hidden="true">&nbsp;·&nbsp;</span>{dateline}</span>
			</p>
			<h1 class="mast-title">
				<span class="sr-only">Dashboard: </span>The lab, <em>at a glance</em>.
			</h1>
			<p class="mast-lead">Generation, analysis and evaluation activity across every model under study.</p>
		</div>
		<div class="mast-actions">
			<Button onclick={onRefresh} size="sm" variant="outline" disabled={isRefreshing} aria-label="Refresh dashboard" title="Refresh dashboard">
				{#if isRefreshing}
					<Loader2 class="h-3.5 w-3.5 animate-spin" />
				{:else}
					<RefreshCw class="h-3.5 w-3.5" />
				{/if}
			</Button>
			<!-- Maintenance shortcuts; staff only. -->
			{#if isStaff}
				<DropdownMenu.Root>
					<DropdownMenu.Trigger>
						{#snippet child({ props })}
							<Button {...props} size="sm" variant="outline" aria-label="Maintenance actions" title="Maintenance">
								{#if pendingAction}<Loader2 class="h-3.5 w-3.5 animate-spin" />{:else}<Wrench class="h-3.5 w-3.5" />{/if}
							</Button>
						{/snippet}
					</DropdownMenu.Trigger>
					<DropdownMenu.Portal>
						<DropdownMenu.Content
							align="end"
							sideOffset={6}
							collisionPadding={12}
							class="z-50 min-w-48 rounded-md border border-border bg-popover p-1 text-popover-foreground shadow-md"
						>
							<DropdownMenu.Item class={itemClass} disabled={pendingAction !== null} onSelect={() => onSyncModels?.()}>
								{@render actionIcon('sync', CloudDownload)}Sync models
							</DropdownMenu.Item>
							<DropdownMenu.Item class={itemClass} disabled={pendingAction !== null} onSelect={() => onClearCaches?.()}>
								{@render actionIcon('caches', Eraser)}Clear caches
							</DropdownMenu.Item>
							<DropdownMenu.Item class={itemClass} disabled={pendingAction !== null} onSelect={() => onClearStuck?.()}>
								{@render actionIcon('stuck', Activity)}Clear stuck runs
							</DropdownMenu.Item>
							<DropdownMenu.Separator class="-mx-1 my-1 h-px bg-border" />
							<DropdownMenu.Item class={itemClass} onSelect={() => goto('/system')}>
								<Cpu />Open system panel
							</DropdownMenu.Item>
						</DropdownMenu.Content>
					</DropdownMenu.Portal>
				</DropdownMenu.Root>
			{/if}
			<Button href="/analysis/create" size="sm" variant="outline" class="gap-1.5">
				<Plus class="h-3.5 w-3.5" />
				New analysis
			</Button>
			<Button href="/generate" size="sm" class="gap-1.5">
				<Wand2 class="h-3.5 w-3.5" />
				Generate app
			</Button>
		</div>
	</div>

	</div>

	<!-- Headline figures -->
	<div class="mast-figs">
		{#snippet modelsSub()}{#if overview}of {overview.total_models.toLocaleString()} catalogued{:else}&nbsp;{/if}{/snippet}
		{@render figure('01', '/models', 'Models in use', fmt(overview?.models_in_use), undefined, modelsSub)}

		{#snippet appsSub()}
			{#if overview}{overview.apps_completed.toLocaleString()} completed{#if overview.apps_success_rate != null}<span class="mast-fig-sep">·</span>{overview.apps_success_rate}% success{/if}{:else}&nbsp;{/if}
		{/snippet}
		{@render figure(
			'02',
			'/applications',
			'Apps generated',
			fmt(overview?.total_apps),
			overview
				? overview.apps_success_rate == null
					? 'No generation finished yet'
					: `${overview.apps_success_rate}% of finished generations completed`
				: undefined,
			appsSub
		)}

		{#snippet analysesSub()}
			{#if completedAnalyses != null}{completedAnalyses.toLocaleString()} completed{/if}{#if runningAnalyses}<span class="mast-fig-sep">·</span><span class="text-info-fg">{runningAnalyses} running</span>{/if}{#if completedAnalyses == null && !runningAnalyses}&nbsp;{/if}
		{/snippet}
		{#snippet analysesSpark()}
			{#if spark}
				<svg
					class="mast-spark"
					viewBox="0 0 {SPARK_W} {SPARK_H}"
					role="img"
					aria-label="{spark.total} analyses in the last {spark.days} days; busiest day {spark.peak.label} with {spark.peak.total}"
				>
					<title>{spark.total} runs · last {spark.days} days · peak {spark.peak.total} on {spark.peak.label}</title>
					<path d={spark.area} class="mast-spark-area" />
					<path d={spark.line} class="mast-spark-line" pathLength="1" />
					<circle cx={spark.last[0]} cy={spark.last[1]} r="2.2" class="mast-spark-dot" />
				</svg>
			{/if}
		{/snippet}
		{@render figure('03', '/analysis', 'Analyses', fmt(totalAnalyses), spark ? `${spark.total} in the last ${spark.days} days` : undefined, analysesSub, analysesSpark)}

		{#snippet findingsSub()}
			{#if overview}{overview.total_apps ? (overview.total_findings / overview.total_apps).toFixed(1) : '—'} per app avg{:else}&nbsp;{/if}
		{/snippet}
		{@render figure(
			'04',
			'/statistics',
			'Findings',
			fmt(overview?.total_findings),
			overview
				? `Every tool's findings over all analysis runs (dependency advisories included); ${overview.analyses_completed} analyses completed`
				: undefined,
			findingsSub
		)}
	</div>
</header>

<style>
	.masthead {
		position: relative;
		overflow: hidden;
		border: 1px solid var(--border);
		border-radius: var(--radius);
		background-color: var(--card);
		box-shadow: var(--shadow-card);
		/* Graph paper, fading out toward the figures so numbers sit on clean paper. */
		background-image:
			linear-gradient(var(--grid-line-major) 1px, transparent 1px),
			linear-gradient(90deg, var(--grid-line-major) 1px, transparent 1px),
			linear-gradient(var(--grid-line) 1px, transparent 1px),
			linear-gradient(90deg, var(--grid-line) 1px, transparent 1px);
		background-size:
			40px 40px,
			40px 40px,
			8px 8px,
			8px 8px;
		background-position: -1px -1px;
	}
	/* A thin indigo → ochre → teal "spectrum" rule along the top edge: the lab's three accents. */
	.masthead::before {
		content: '';
		position: absolute;
		inset: 0 0 auto 0;
		height: 3px;
		background: linear-gradient(
			90deg,
			var(--primary) 0 60%,
			var(--chart-3, #c2850c) 60% 80%,
			var(--chart-2, #0d9488) 80% 100%
		);
	}

	.mast-crop {
		position: absolute;
		width: 10px;
		height: 10px;
		border-color: color-mix(in oklab, var(--foreground) 35%, transparent);
		pointer-events: none;
	}
	.mast-crop-tl { top: 10px; left: 10px; border-top: 1px solid; border-left: 1px solid; }
	.mast-crop-tr { top: 10px; right: 10px; border-top: 1px solid; border-right: 1px solid; }

	.mast-hero {
		position: relative;
	}
	.mast-dateline,
	.mast-main {
		position: relative;
		z-index: 1;
	}
	.mast-dateline {
		display: flex;
		flex-wrap: wrap;
		align-items: center;
		gap: 0.5rem 0.75rem;
		padding: 1rem 1.75rem 0;
	}
	.mast-dateline-rule {
		flex: 1 1 2rem;
		height: 1px;
		background: var(--border);
	}
	.mast-brand-dot {
		width: 6px;
		height: 6px;
		border-radius: 9999px;
		background: var(--chart-3, #c2850c);
	}
	.mast-status {
		display: inline-flex;
		align-items: center;
		min-height: 1.5rem;
		gap: 0.375rem;
		white-space: nowrap;
		transition: color 150ms;
	}

	.mast-main {
		display: flex;
		flex-wrap: wrap;
		align-items: flex-end;
		justify-content: space-between;
		gap: 1rem 1.5rem;
		padding: 1.25rem 1.75rem 1.5rem;
	}
	.mast-greeting {
		font-family: var(--font-mono);
		font-size: 0.75rem;
		color: var(--muted-foreground);
		margin-bottom: 0.375rem;
	}
	.mast-title {
		font-family: var(--font-display);
		font-weight: 600;
		font-size: clamp(1.75rem, 1.2rem + 2vw, 2.625rem);
		line-height: 1.05;
		letter-spacing: -0.02em;
		color: var(--foreground);
		text-wrap: balance;
	}
	.mast-title em {
		font-style: italic;
		font-weight: 500;
		color: var(--primary);
	}
	.mast-lead {
		margin-top: 0.5rem;
		max-width: 38rem;
		font-size: 0.875rem;
		color: var(--muted-foreground);
	}
	.mast-actions {
		display: flex;
		flex-wrap: wrap;
		align-items: center;
		gap: 0.5rem;
		/* Solid backing so the ASCII field never shows through the outline buttons. */
		padding: 0.375rem;
		margin: -0.375rem;
		border-radius: calc(var(--radius) + 0.25rem);
		background: var(--card);
		box-shadow: 0 0 0 1px color-mix(in oklab, var(--border) 60%, transparent);
	}

	.mast-figs {
		display: grid;
		grid-template-columns: repeat(4, minmax(0, 1fr));
		border-top: 1px solid var(--border);
		background: color-mix(in oklab, var(--card) 88%, transparent);
	}
	.masthead { container: masthead / inline-size; }
	@container masthead (max-width: 820px) {
		.mast-figs { grid-template-columns: repeat(2, minmax(0, 1fr)); }
		.mast-fig:nth-child(2n) { border-right: 0; }
		.mast-fig:nth-child(-n + 2) { border-bottom: 1px solid var(--border); }
	}
	@media (max-width: 640px) {
		.mast-dateline, .mast-main { padding-inline: 1rem; }
		.mast-fig { padding-inline: 1rem; }
		.mast-fig::before { left: 1rem; }
		.mast-fig-label { letter-spacing: 0.04em; gap: 0.375rem; }
		.mast-fig-index { display: none; }
		.mast-fig :global(.mast-fig-arrow) { display: none; }
	}

	.mast-fig {
		position: relative;
		display: flex;
		flex-direction: column;
		gap: 0.25rem;
		min-width: 0;
		padding: 0.875rem 1.75rem 1rem;
		border-right: 1px solid var(--border);
		color: inherit;
		text-decoration: none;
		transition: background-color 150ms ease-out;
	}
	.mast-fig:last-child { border-right: 0; }
	/* Accent tick on top of each cell; grows on hover/focus. */
	.mast-fig::before {
		content: '';
		position: absolute;
		top: -1px;
		left: 1.75rem;
		width: 1.25rem;
		height: 2px;
		background: var(--primary);
		transition: width 200ms ease-out;
	}
	.mast-fig:nth-child(2)::before { background: var(--chart-2, #0d9488); }
	.mast-fig:nth-child(3)::before { background: var(--chart-3, #c2850c); }
	.mast-fig:nth-child(4)::before { background: var(--muted-foreground); }
	.mast-fig:hover,
	.mast-fig:focus-visible { background-color: var(--accent); }
	.mast-fig:hover::before,
	.mast-fig:focus-visible::before { width: 3rem; }
	.mast-fig:focus-visible {
		outline: 2px solid var(--ring);
		outline-offset: -2px;
	}

	.mast-fig-label {
		display: flex;
		align-items: center;
		gap: 0.5rem;
		font-family: var(--font-mono);
		font-size: 0.6875rem;
		text-transform: uppercase;
		letter-spacing: 0.12em;
		color: var(--muted-foreground);
	}
	.mast-fig-name { min-width: 0; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
	.mast-fig-index { color: color-mix(in oklab, var(--muted-foreground) 70%, transparent); }
	:global(.mast-fig-arrow) {
		width: 0.875rem;
		height: 0.875rem;
		margin-left: auto;
		opacity: 0;
		transform: translate(-3px, 3px);
		transition: opacity 150ms, transform 150ms ease-out;
	}
	.mast-fig:hover :global(.mast-fig-arrow),
	.mast-fig:focus-visible :global(.mast-fig-arrow) {
		opacity: 1;
		transform: none;
		color: var(--primary);
	}
	.mast-fig-row {
		display: flex;
		align-items: flex-end;
		justify-content: space-between;
		gap: 0.75rem;
		min-height: 2.5rem;
	}
	.mast-fig-value {
		font-family: var(--font-mono);
		font-weight: 500;
		font-size: clamp(1.5rem, 1.1rem + 1.2vw, 2.125rem);
		line-height: 1;
		letter-spacing: -0.03em;
		font-variant-numeric: tabular-nums;
		color: var(--foreground);
	}
	.mast-fig-sub {
		font-size: 0.75rem;
		color: var(--muted-foreground);
		white-space: nowrap;
		overflow: hidden;
		text-overflow: ellipsis;
	}
	.mast-fig-sep { margin-inline: 0.375rem; opacity: 0.6; }

	.mast-spark {
		width: min(120px, 45%);
		height: 28px;
		flex-shrink: 1;
		overflow: visible;
	}
	.mast-spark-area { fill: color-mix(in oklab, var(--chart-3, #c2850c) 14%, transparent); }
	.mast-spark-line {
		fill: none;
		stroke: var(--chart-3, #c2850c);
		stroke-width: 1.5;
		stroke-linejoin: round;
		stroke-linecap: round;
		stroke-dasharray: 1;
		stroke-dashoffset: 1;
		animation: mast-draw 900ms 200ms cubic-bezier(0.22, 1, 0.36, 1) forwards;
	}
	.mast-spark-dot { fill: var(--chart-3, #c2850c); }
	@keyframes mast-draw {
		to { stroke-dashoffset: 0; }
	}
	@media (prefers-reduced-motion: reduce) {
		.mast-spark-line { animation: none; stroke-dashoffset: 0; }
		.mast-fig, .mast-fig::before, :global(.mast-fig-arrow) { transition: none; }
	}
</style>
