<script lang="ts">
	/**
	 * Model detail header: identity (provider tile, serif name, badges, copyable ID), actions, and a
	 * figure strip — the catalog facts on the left, this platform's own measurements on the right
	 * (skeleton while they load). The section tabs sit along the bottom edge (`nav`).
	 * Owns the page <h1> and browser title, like PageHeader.
	 */
	import { Button } from '$lib/components/ui/button';
	import ArrowLeft from '@lucide/svelte/icons/arrow-left';
	import Globe from '@lucide/svelte/icons/globe';
	import GitCompareArrows from '@lucide/svelte/icons/git-compare-arrows';
	import RefreshCw from '@lucide/svelte/icons/refresh-cw';
	import Copy from '@lucide/svelte/icons/copy';
	import Sparkles from '@lucide/svelte/icons/sparkles';
	import Download from '@lucide/svelte/icons/download';
	import Link2 from '@lucide/svelte/icons/link-2';
	import ChevronRight from '@lucide/svelte/icons/chevron-right';
	import ProviderIcon from '$lib/components/ProviderIcon.svelte';
	import { pageTitle } from '$lib/stores/page-title.svelte';
	import type { Snippet } from 'svelte';
	import type { LLMModelDetail, ModelInsights } from '$lib/api/client';
	import { formatPrice, formatTokens, copyToClipboard, getCaps, isoDay, fmtParams, fmtRate, fmtCI, knownTokenLimit, metadataView } from './helpers';
	import { shortModelName, modelNamePrefix } from '$lib/components/table';

	interface Props {
		model: LLMModelDetail;
		slug: string;
		refreshing: boolean;
		insights: ModelInsights | null;
		insightsError?: string;
		/** Staff-only metadata refresh; the button is hidden when omitted. */
		onRefresh?: () => void;
		/** Opens a section (the platform figures link to Results). */
		onOpenTab?: (tab: string) => void;
		/** Section tabs for the bottom strip. */
		nav?: Snippet;
	}

	let { model, slug, refreshing, insights, insightsError = '', onRefresh, onOpenTab, nav }: Props = $props();

	const title = $derived(shortModelName(model.model_name));
	$effect(() => {
		const t = title;
		pageTitle.current = t;
		return () => {
			if (pageTitle.current === t) pageTitle.current = '';
		};
	});

	function exportJson() {
		const blob = new Blob([JSON.stringify(model, null, 2)], { type: 'application/json' });
		const url = URL.createObjectURL(blob);
		const a = document.createElement('a');
		a.href = url;
		a.download = `${slug}.json`;
		document.body.appendChild(a);
		a.click();
		document.body.removeChild(a);
		URL.revokeObjectURL(url);
	}

	const providerLabel = $derived(model.is_local ? (model.endpoint?.name ?? model.provider) : modelNamePrefix(model.model_name) || model.provider);
	const lastUpdated = $derived(model.updated_at ? new Date(model.updated_at).toISOString().slice(0, 10) : null);

	const view = $derived(metadataView(model));
	const md = $derived(view.md);
	const hf = $derived(view.hf);
	const params = $derived(view.params);
	const caps = $derived(getCaps(model));
	const released = $derived(md?.release_date || isoDay(view.caps.created ?? caps.created));
	const knowledge = $derived(md?.knowledge || isoDay(view.caps.knowledge_cutoff ?? caps.knowledge_cutoff));
	const expiration = $derived(isoDay(caps.expiration_date));
	const openWeights = $derived(md?.open_weights ?? null);

	const contextWindow = $derived(knownTokenLimit(model.context_window));
	const maxOutput = $derived(knownTokenLimit(model.max_output_tokens));

	interface Figure {
		label: string;
		value: string;
		sub?: string;
		title?: string;
		muted?: boolean;
	}

	const catalogFigures = $derived.by<Figure[]>(() => {
		const out: Figure[] = [];
		out.push(
			contextWindow != null
				? { label: 'Context', value: model.context_window_display, sub: maxOutput != null ? `${formatTokens(maxOutput)} max out` : 'max out not reported' }
				: { label: 'Context', value: '—', sub: 'not reported', muted: true, title: 'Context window not reported by the source' },
		);
		if (model.is_local) {
			out.push({ label: 'Price / 1M', value: '—', sub: 'local endpoint', muted: true, title: 'Served by a local endpoint: no per-token price' });
		} else if (model.is_free) {
			out.push({ label: 'Price / 1M', value: 'Free', sub: 'no token cost' });
		} else {
			out.push({
				label: 'Price / 1M',
				value: `${formatPrice(model.input_price_per_million)}`,
				sub: `in · ${formatPrice(model.output_price_per_million)} out`,
			});
		}
		if (params?.active != null && params.is_moe) {
			const total = params.reported_total ?? params.checkpoint_total;
			out.push({
				label: 'Parameters',
				value: `${params.active_source === 'config_estimate' ? '~' : ''}${fmtParams(params.active)}`,
				sub: `active · ${fmtParams(total)} total (MoE)`,
				title: params.active_note ?? undefined,
			});
		} else if (hf?.parameters) {
			out.push({ label: 'Parameters', value: fmtParams(hf.parameters), sub: 'checkpoint tensors', title: 'Every tensor stored in the Hugging Face repo' });
		} else if (model.parameter_size) {
			out.push({ label: 'Parameters', value: model.parameter_size, sub: 'endpoint size label' });
		} else {
			out.push({ label: 'Parameters', value: '—', sub: 'not disclosed', muted: true });
		}
		out.push(
			released
				? { label: 'Released', value: released, sub: knowledge ? `knowledge ${knowledge}` : undefined }
				: { label: 'Released', value: '—', sub: 'unknown', muted: true },
		);
		return out;
	});

	const gen = $derived(insights?.generation);

	let headerEl = $state<HTMLElement | null>(null);
	let condensed = $state(false);
	$effect(() => {
		if (!headerEl) return;
		// 3rem top bar + the sticky bar itself: the header counts as gone once its figures pass under them.
		const io = new IntersectionObserver(([e]) => (condensed = !e.isIntersecting), { rootMargin: '-96px 0px 0px 0px', threshold: 0 });
		io.observe(headerEl);
		return () => io.disconnect();
	});
</script>

<svelte:head>
	<title>{model.model_name} · LLM Lab</title>
</svelte:head>

<header class="mh" class:has-nav={!!nav} bind:this={headerEl}>
	<div class="mh-top">
		<a href="/models" class="page-header-back" aria-label="Back to Models" title="Back to Models">
			<ArrowLeft class="size-4" aria-hidden="true" />
		</a>
		<span class="mh-tile" aria-hidden="true"><ProviderIcon provider={model.provider} size={22} /></span>
		<div class="mh-id">
			<div class="mh-titlebar">
				<h1 class="mh-title">{title}</h1>
				{#if model.is_local}<span class="mh-badge mh-badge-local" title="Served by the local endpoint {model.endpoint?.name ?? model.provider}">Local</span>{/if}
				{#if model.is_free && !model.is_local}<span class="mh-badge mh-badge-free">Free</span>{/if}
				{#if openWeights === true}<span class="mh-badge mh-badge-open" title="Open weights, per models.dev">Open weights</span>{/if}
				{#if model.supports_reasoning}<span class="mh-badge">Reasoning</span>{/if}
				{#if expiration}<span class="mh-badge mh-badge-warn" title="OpenRouter retires this model on {expiration}">Retires {expiration}</span>{/if}
			</div>
			<div class="mh-sub">
				<span class="text-foreground">{providerLabel}</span>
				<span aria-hidden="true" class="opacity-40">/</span>
				<button type="button" class="mh-copy" title="Copy model ID" onclick={() => copyToClipboard(model.model_id)}>
					<span class="truncate">{model.model_id}</span>
					<Copy class="size-3 shrink-0" aria-hidden="true" />
					<span class="sr-only">Copy model ID</span>
				</button>
				{#if view.inheritedFrom}
					<span
						class="mh-chip"
						title="External metadata (models.dev, Hugging Face, Epoch, LMArena) comes from the catalog twin {view.inheritedFrom}, assumed to be the same weights. This model's own runs are never pooled with the twin's."
					>
						<Link2 class="size-3" aria-hidden="true" />twin
						{#if view.inheritedSlug}<a href="/models/{view.inheritedSlug}" class="font-mono hover:text-primary">{view.inheritedFrom}</a>{:else}<span class="font-mono">{view.inheritedFrom}</span>{/if}
					</span>
				{/if}
			</div>
		</div>
		<div class="mh-actions">
			<div class="flex items-center gap-0.5">
				{#if !model.is_local}
					<Button variant="ghost" size="sm" class="size-8 p-0" href="https://openrouter.ai/models/{model.model_id}" target="_blank" rel="noopener noreferrer" title="View on OpenRouter" aria-label="View on OpenRouter">
						<Globe />
					</Button>
				{/if}
				<Button variant="ghost" size="sm" class="size-8 p-0" onclick={exportJson} title="Export model as JSON{lastUpdated ? ` (metadata from ${lastUpdated})` : ''}" aria-label="Export model as JSON">
					<Download />
				</Button>
				{#if onRefresh}
					<Button variant="ghost" size="sm" class="size-8 p-0" onclick={onRefresh} title="Refresh metadata from OpenRouter" aria-label="Refresh metadata from OpenRouter" disabled={refreshing}>
						<RefreshCw class={refreshing ? 'animate-spin' : ''} />
					</Button>
				{/if}
			</div>
			<Button variant="outline" size="sm" href="/models/compare?models={slug}" title="Compare with other models">
				<GitCompareArrows class="h-3.5 w-3.5" />Compare
			</Button>
			<Button size="sm" href="/generate?model={encodeURIComponent(slug)}" title="Generate an app with this model">
				<Sparkles class="h-3.5 w-3.5" />Generate app
			</Button>
		</div>
	</div>

	<div class="mh-figs">
		<div class="mh-group" aria-label="Catalog facts">
			{#each catalogFigures as f (f.label)}
				<div class="mh-fig" class:is-unknown={f.muted} title={f.title}>
					<span class="mh-fig-label">{f.label}</span>
					<span class="mh-fig-value {f.muted ? 'is-muted' : ''}">{f.value}</span>
					{#if f.sub}<span class="mh-fig-sub">{f.sub}</span>{/if}
				</div>
			{/each}
		</div>
		<!-- This platform's measurements: the reason the page exists, so they get their own tinted block. -->
		<button type="button" class="mh-platform" onclick={() => onOpenTab?.('results')} aria-label="On this platform — open Results">
			<span class="mh-platform-tag">On this platform <ChevronRight class="size-3" aria-hidden="true" /></span>
			<span class="mh-platform-figs">
			{#if insightsError}
				<div class="mh-fig"><span class="mh-fig-label">Apps</span><span class="mh-fig-value is-muted">—</span><span class="mh-fig-sub">could not load</span></div>
			{:else if !gen}
				{#each [0, 1] as i (i)}
					<div class="mh-fig" aria-hidden="true"><span class="mh-fig-label skeleton w-12"></span><span class="mh-skel-value skeleton"></span><span class="mh-fig-sub skeleton w-20"></span></div>
				{/each}
			{:else if gen.runs_total === 0}
				<div class="mh-fig">
					<span class="mh-fig-label">Apps</span>
					<span class="mh-fig-value is-muted">0</span>
					<span class="mh-fig-sub">not generated yet</span>
				</div>
			{:else}
				<div class="mh-fig">
					<span class="mh-fig-label">Apps</span>
					<span class="mh-fig-value">{gen.runs_total.toLocaleString()}</span>
					<span class="mh-fig-sub">{fmtRate(gen.success.rate)} build success</span>
				</div>
				<div class="mh-fig" title="Share of evaluated apps that passed the hidden API conformance suite (Wilson 95% interval)">
					<span class="mh-fig-label">Conformance</span>
					<span class="mh-fig-value">{fmtRate(gen.functional.rate)}</span>
					<span class="mh-fig-sub">{gen.functional.n ? `${fmtCI(gen.functional.ci_low, gen.functional.ci_high)} · n=${gen.functional.n}` : 'not evaluated'}</span>
				</div>
			{/if}
			</span>
		</button>
	</div>

</header>

{#if nav}
	<div class="mh-sticky" class:is-condensed={condensed}>
		<div class="mh-mini" aria-hidden={!condensed}>
			<span class="mh-mini-tile"><ProviderIcon provider={model.provider} size={14} /></span>
			<span class="mh-mini-title">{title}</span>
		</div>
		<div class="mh-nav">{@render nav()}</div>
		<div class="mh-mini-actions" aria-hidden={!condensed}>
			<Button variant="outline" size="sm" class="h-7 px-2.5 text-xs" href="/models/compare?models={slug}" tabindex={condensed ? 0 : -1}>
				<GitCompareArrows class="h-3.5 w-3.5" />Compare
			</Button>
			<Button size="sm" class="h-7 px-2.5 text-xs" href="/generate?model={encodeURIComponent(slug)}" tabindex={condensed ? 0 : -1}>
				<Sparkles class="h-3.5 w-3.5" />Generate
			</Button>
		</div>
	</div>
{/if}

<style>
	.mh {
		position: relative;
		border: 1px solid var(--border);
		border-radius: var(--radius);
		background: var(--card);
		box-shadow: var(--shadow-card);
		overflow: hidden;
	}
	/* With tabs, the header and the sticky tab bar read as one card until the bar sticks. */
	.mh.has-nav {
		border-bottom-left-radius: 0;
		border-bottom-right-radius: 0;
		/* The page's space-y puts a bottom margin on the header; the tab bar must sit flush under it. */
		margin-bottom: 0 !important;
	}
	.mh-top {
		display: flex;
		flex-wrap: wrap;
		align-items: center;
		gap: 0.75rem 1rem;
		padding: 1rem 1.25rem 0.875rem;
	}
	.mh-tile {
		display: inline-flex;
		width: 2.75rem;
		height: 2.75rem;
		flex-shrink: 0;
		align-items: center;
		justify-content: center;
		border-radius: calc(var(--radius) + 0.25rem);
		border: 1px solid var(--border);
		background: color-mix(in oklab, var(--muted) 60%, var(--card));
	}
	.mh-id {
		display: flex;
		min-width: 0;
		flex: 1 1 20rem;
		flex-direction: column;
		gap: 0.25rem;
	}
	.mh-titlebar {
		display: flex;
		flex-wrap: wrap;
		align-items: center;
		gap: 0.375rem 0.5rem;
	}
	.mh-title {
		font-family: var(--font-display);
		font-size: clamp(1.375rem, 1.1rem + 0.9vw, 1.75rem);
		font-weight: 600;
		line-height: 1.15;
		letter-spacing: -0.015em;
		margin-right: 0.25rem;
		overflow-wrap: anywhere;
	}
	.mh-badge {
		display: inline-flex;
		align-items: center;
		height: 1.25rem;
		border-radius: 9999px;
		border: 1px solid var(--border);
		padding-inline: 0.5rem;
		font-size: 0.6875rem;
		font-weight: 500;
		color: var(--muted-foreground);
		white-space: nowrap;
	}
	.mh-badge-local {
		color: var(--info-fg);
		border-color: color-mix(in oklab, var(--info) 35%, transparent);
		background: color-mix(in oklab, var(--info) 10%, transparent);
	}
	.mh-badge-free {
		color: var(--success-fg);
		border-color: color-mix(in oklab, var(--success) 35%, transparent);
		background: color-mix(in oklab, var(--success) 10%, transparent);
	}
	.mh-badge-open {
		color: var(--foreground);
		border-color: color-mix(in oklab, var(--chart-2, #0d9488) 45%, transparent);
		background: color-mix(in oklab, var(--chart-2, #0d9488) 12%, transparent);
	}
	.mh-badge-warn {
		color: var(--warning-fg);
		border-color: color-mix(in oklab, var(--warning) 40%, transparent);
		background: color-mix(in oklab, var(--warning) 10%, transparent);
	}
	.mh-sub {
		display: flex;
		min-width: 0;
		flex-wrap: wrap;
		align-items: center;
		gap: 0.25rem 0.5rem;
		font-size: 0.75rem;
		color: var(--muted-foreground);
	}
	.mh-copy {
		display: inline-flex;
		min-width: 0;
		cursor: pointer;
		align-items: center;
		gap: 0.375rem;
		border-radius: 0.25rem;
		font-family: var(--font-mono);
		transition: color 150ms;
	}
	.mh-copy :global(svg) {
		opacity: 0.55;
	}
	.mh-copy:hover {
		color: var(--foreground);
	}
	.mh-copy:hover :global(svg) {
		opacity: 1;
	}
	.mh-chip {
		display: inline-flex;
		min-width: 0;
		align-items: center;
		gap: 0.3rem;
		border-radius: 9999px;
		border: 1px dashed var(--border);
		padding: 0 0.5rem;
		line-height: 1.25rem;
	}
	.mh-actions {
		display: flex;
		flex-shrink: 0;
		flex-wrap: wrap;
		align-items: center;
		gap: 0.5rem;
		margin-left: auto;
	}

	/* Figure strip */
	.mh-figs {
		display: grid;
		grid-template-columns: minmax(0, 4fr) minmax(0, 2.4fr);
		border-top: 1px solid var(--border);
	}
	.mh-group {
		display: flex;
		min-width: 0;
	}
	.mh-group > .mh-fig {
		flex: 1 1 0;
	}
	.mh-group > .mh-fig.is-unknown {
		flex: 0.6 1 0;
	}
	.mh-fig {
		display: flex;
		min-width: 0;
		flex-direction: column;
		gap: 0.2rem;
		padding: 0.75rem 1.25rem 0.875rem;
		border-right: 1px solid var(--border);
		text-align: left;
	}
	.mh-group > .mh-fig:last-child {
		border-right: 0;
	}
	.mh-fig-label {
		font-family: var(--font-mono);
		font-size: 0.6875rem;
		text-transform: uppercase;
		letter-spacing: 0.1em;
		color: var(--muted-foreground);
	}
	.mh-fig-value {
		font-family: var(--font-mono);
		font-size: 1.375rem;
		font-weight: 500;
		line-height: 1.1;
		letter-spacing: -0.02em;
		font-variant-numeric: tabular-nums;
		white-space: nowrap;
		overflow: hidden;
		text-overflow: ellipsis;
	}
	.mh-fig-value.is-muted {
		color: var(--muted-foreground);
	}
	.mh-fig-sub {
		font-size: 0.6875rem;
		color: var(--muted-foreground);
		white-space: nowrap;
		overflow: hidden;
		text-overflow: ellipsis;
	}

	.mh-platform {
		position: relative;
		display: flex;
		min-width: 0;
		cursor: pointer;
		flex-direction: column;
		border-left: 1px solid var(--border);
		background: color-mix(in oklab, var(--primary) 6%, var(--card));
		text-align: left;
		transition: background-color 150ms;
	}
	.mh-platform-figs {
		display: flex;
		flex: 1;
		min-width: 0;
	}
	.mh-platform-figs > .mh-fig {
		flex: 1 1 0;
		padding-top: 0.25rem;
	}
	.mh-platform-figs > .mh-fig:last-child {
		border-right: 0;
	}
	.mh-platform:hover {
		background: color-mix(in oklab, var(--primary) 11%, var(--card));
	}
	.mh-platform:focus-visible {
		outline: 2px solid var(--ring);
		outline-offset: -2px;
	}
	.mh-platform::before {
		content: '';
		position: absolute;
		inset: 0 auto 0 0;
		width: 2px;
		background: var(--primary);
	}
	.mh-platform-tag {
		display: inline-flex;
		align-self: flex-start;
		margin: 0.625rem 1.25rem 0;
		align-items: center;
		gap: 0.125rem;
		font-size: 0.625rem;
		font-weight: 600;
		text-transform: uppercase;
		letter-spacing: 0.08em;
		color: var(--primary);
	}
	.skeleton {
		display: block;
		border-radius: 0.25rem;
		background: color-mix(in oklab, var(--muted-foreground) 18%, transparent);
		animation: mh-pulse 1.4s ease-in-out infinite;
	}
	.mh-fig-label.skeleton,
	.mh-fig-sub.skeleton {
		height: 0.625rem;
	}
	.mh-skel-value {
		width: 3.5rem;
		height: 1.375rem;
	}
	@keyframes mh-pulse {
		50% {
			opacity: 0.45;
		}
	}
	@media (prefers-reduced-motion: reduce) {
		.skeleton {
			animation: none;
		}
	}

	/* Sticky tab bar: under the 3rem app top bar; gains a condensed identity once the header scrolls away. */
	.mh-sticky {
		position: sticky;
		top: 3rem;
		z-index: 20;
		display: flex;
		align-items: center;
		gap: 0.75rem;
		margin-top: 0 !important;
		border: 1px solid var(--border);
		border-top-color: color-mix(in oklab, var(--border) 60%, transparent);
		border-radius: 0 0 var(--radius) var(--radius);
		background: color-mix(in oklab, var(--card) 92%, transparent);
		backdrop-filter: blur(8px);
		padding-inline: 0.5rem;
		transition: box-shadow 200ms, border-radius 200ms;
	}
	.mh-sticky.is-condensed {
		border-radius: 0 0 var(--radius) var(--radius);
		border-top-color: var(--border);
		box-shadow: var(--shadow-pop);
	}
	.mh-mini,
	.mh-mini-actions {
		display: flex;
		flex-shrink: 0;
		align-items: center;
		gap: 0.5rem;
		max-width: 0;
		overflow: hidden;
		opacity: 0;
		transition: max-width 220ms ease-out, opacity 160ms ease-out;
	}
	.mh-sticky.is-condensed .mh-mini {
		max-width: 16rem;
		opacity: 1;
		padding-left: 0.5rem;
	}
	.mh-sticky.is-condensed .mh-mini-actions {
		max-width: 16rem;
		opacity: 1;
		margin-left: auto;
		padding-right: 0.25rem;
	}
	/* Condensed: icons and counts go so the tabs fit beside the identity and actions. */
	.mh-sticky.is-condensed :global([role='tab'] svg),
	.mh-sticky.is-condensed :global([role='tab'] .tab-count) {
		display: none;
	}
	.mh-sticky.is-condensed :global([role='tab']) {
		padding-inline: 0.625rem;
	}
	.mh-mini-tile {
		display: inline-flex;
		width: 1.5rem;
		height: 1.5rem;
		flex-shrink: 0;
		align-items: center;
		justify-content: center;
		border-radius: 0.375rem;
		border: 1px solid var(--border);
	}
	.mh-mini-title {
		overflow: hidden;
		text-overflow: ellipsis;
		white-space: nowrap;
		font-family: var(--font-display);
		font-size: 0.9375rem;
		font-weight: 600;
	}
	.mh-nav {
		display: flex;
		min-width: 0;
		flex: 1 1 auto;
		overflow-x: auto;
		scrollbar-width: none;
	}
	@media (prefers-reduced-motion: reduce) {
		.mh-mini,
		.mh-mini-actions,
		.mh-sticky {
			transition: none;
		}
	}
	.mh-nav :global([role='tablist']) {
		border-bottom: 0;
	}

	@media (max-width: 1100px) {
		.mh-figs {
			grid-template-columns: minmax(0, 1fr);
		}
		.mh-mini-actions {
			display: none;
		}
		.mh-platform {
			border-left: 0;
			border-top: 1px solid var(--border);
		}
	}
	@media (max-width: 640px) {
		.mh-top {
			padding: 0.875rem 1rem 0.75rem;
		}
		.mh-fig {
			padding-inline: 1rem;
		}
		.mh-group {
			flex-wrap: wrap;
		}
		.mh-group > .mh-fig,
		.mh-group > .mh-fig.is-unknown {
			flex: 1 1 45%;
			border-bottom: 1px solid var(--border);
		}
		.mh-actions {
			margin-left: 0;
			width: 100%;
		}
	}
</style>
