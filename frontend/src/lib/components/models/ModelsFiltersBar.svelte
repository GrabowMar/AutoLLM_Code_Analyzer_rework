<script lang="ts">
	/**
	 * Models filter toolbar — the top strip of the models table card.
	 *   Row 1: [search……] Source · Provider · Capabilities · Input price · Context · [More filters n] · Clear
	 *   Row 2 (More): Released · Weights · Input · Tokenizer · Max output · Research data · Used in apps · Hide variants
	 * Every menu button shows its own value, so there is no separate "active filters" row. Row 2 opens on
	 * its own when one of its filters is set (e.g. from a shared link). Sorting is on the column headers.
	 */
	import FilterMenu, { type FilterMenuOption } from '$lib/components/FilterMenu.svelte';
	import type { ModelFacets } from '$lib/api/models';
	import { SECONDARY, countActive, type ModelFilters } from './filters';
	import Search from '@lucide/svelte/icons/search';
	import X from '@lucide/svelte/icons/x';
	import Eye from '@lucide/svelte/icons/eye';
	import Wrench from '@lucide/svelte/icons/wrench';
	import Radio from '@lucide/svelte/icons/radio';
	import Braces from '@lucide/svelte/icons/braces';
	import Brain from '@lucide/svelte/icons/brain';
	import Dices from '@lucide/svelte/icons/dices';
	import ListTree from '@lucide/svelte/icons/list-tree';
	import Sigma from '@lucide/svelte/icons/sigma';
	import Building2 from '@lucide/svelte/icons/building-2';
	import Sparkles from '@lucide/svelte/icons/sparkles';
	import CircleDollarSign from '@lucide/svelte/icons/circle-dollar-sign';
	import Ruler from '@lucide/svelte/icons/ruler';
	import AppWindow from '@lucide/svelte/icons/app-window';
	import Server from '@lucide/svelte/icons/server';
	import Cloud from '@lucide/svelte/icons/cloud';
	import Layers from '@lucide/svelte/icons/layers';
	import CalendarClock from '@lucide/svelte/icons/calendar-clock';
	import Scale from '@lucide/svelte/icons/scale';
	import Image from '@lucide/svelte/icons/image';
	import FileText from '@lucide/svelte/icons/file-text';
	import AudioLines from '@lucide/svelte/icons/audio-lines';
	import Video from '@lucide/svelte/icons/video';
	import Type from '@lucide/svelte/icons/type';
	import ArrowUpToLine from '@lucide/svelte/icons/arrow-up-to-line';
	import Database from '@lucide/svelte/icons/database';
	import EyeOff from '@lucide/svelte/icons/eye-off';
	import SlidersHorizontal from '@lucide/svelte/icons/sliders-horizontal';

	interface Props {
		filters: ModelFilters;
		facets: ModelFacets | null;
		/** Search text changed (the page debounces the reload). */
		onSearch: (value: string) => void;
		onChange: (patch: Partial<ModelFilters>) => void;
		onClear: () => void;
	}

	let { filters, facets, onSearch, onChange, onClear }: Props = $props();

	const SOURCES: FilterMenuOption[] = [
		{ value: 'openrouter', label: 'OpenRouter catalog', icon: Cloud },
		{ value: 'local', label: 'Local endpoints', icon: Server },
	];
	const CAPABILITIES: FilterMenuOption[] = [
		{ value: 'function_calling', label: 'Tool calling', icon: Wrench },
		{ value: 'reasoning', label: 'Reasoning', icon: Brain },
		{ value: 'vision', label: 'Vision', icon: Eye },
		{ value: 'structured_outputs', label: 'Structured outputs', short: 'Structured', icon: ListTree },
		{ value: 'json_mode', label: 'JSON mode', icon: Braces },
		{ value: 'seed', label: 'Seed (reproducible sampling)', short: 'Seed', icon: Dices },
		{ value: 'logprobs', label: 'Log-probabilities', short: 'Logprobs', icon: Sigma },
		{ value: 'streaming', label: 'Streaming', icon: Radio },
	];
	const PRICES: FilterMenuOption[] = [
		{ value: 'free', label: 'Free' },
		{ value: 'low', label: 'Under $1', hint: '/1M' },
		{ value: 'medium', label: '$1 – $10', hint: '/1M' },
		{ value: 'high', label: 'Over $10', hint: '/1M' },
	];
	const TOKEN_BUCKETS: FilterMenuOption[] = [
		{ value: 'small', label: 'Under 8K' },
		{ value: 'medium', label: '8K – 32K' },
		{ value: 'large', label: '32K – 128K' },
		{ value: 'xlarge', label: '128K and up' },
	];
	const RELEASED: FilterMenuOption[] = [
		{ value: '30d', label: 'Last 30 days' },
		{ value: '90d', label: 'Last 3 months' },
		{ value: '180d', label: 'Last 6 months' },
		{ value: '365d', label: 'Last 12 months' },
		{ value: 'older', label: 'Over a year ago' },
	];
	const WEIGHTS: FilterMenuOption[] = [
		{ value: 'open', label: 'Open weights' },
		{ value: 'closed', label: 'Closed (API only)' },
	];
	const MODALITIES: FilterMenuOption[] = [
		{ value: 'image', label: 'Images', icon: Image },
		{ value: 'file', label: 'Files / PDFs', icon: FileText },
		{ value: 'audio', label: 'Audio', icon: AudioLines },
		{ value: 'video', label: 'Video', icon: Video },
	];
	const COVERAGE: FilterMenuOption[] = [
		{ value: 'lmarena', label: 'LMArena scores' },
		{ value: 'epoch', label: 'Epoch AI (compute, params)', short: 'Epoch AI' },
		{ value: 'huggingface', label: 'Hugging Face card' },
	];

	const providerOptions = $derived(
		(facets?.providers ?? []).map((p) => ({ value: p.value, label: p.value, hint: String(p.count) })),
	);
	const tokenizerOptions = $derived(
		(facets?.tokenizers ?? []).map((t) => ({ value: t.value, label: t.value, hint: String(t.count) })),
	);

	const total = $derived(countActive(filters));
	const secondaryCount = $derived(countActive(filters, SECONDARY));
	let moreOpen = $state(false);
	// A shared link with secondary filters opens the row; closing it by hand still works afterwards.
	let autoOpened = false;
	$effect(() => {
		if (!autoOpened && secondaryCount > 0) {
			moreOpen = true;
			autoOpened = true;
		}
	});

	let input: HTMLInputElement;
	function onKey(e: KeyboardEvent) {
		const t = e.target as HTMLElement;
		if (e.key !== '/' || e.metaKey || e.ctrlKey || t.isContentEditable) return;
		if (['INPUT', 'TEXTAREA', 'SELECT'].includes(t.tagName)) return;
		e.preventDefault();
		input?.focus();
	}
</script>

<svelte:window onkeydown={onKey} />

<div class="mf-bar" role="search" aria-label="Search and filter models">
	<div class="mf-row">
		<div class="mf-search">
			<Search class="mf-search-icon" aria-hidden="true" />
			<input
				bind:this={input}
				value={filters.search}
				oninput={(e) => onSearch((e.target as HTMLInputElement).value)}
				type="search"
				placeholder="Search models…"
				aria-label="Search models"
				autocomplete="off"
				spellcheck="false"
			/>
			{#if filters.search}
				<button class="mf-search-clear" onclick={() => { onSearch(''); input?.focus(); }} aria-label="Clear search"><X class="size-3" /></button>
			{:else}
				<kbd aria-hidden="true">/</kbd>
			{/if}
		</div>

		<div class="mf-menus">
			<FilterMenu label="Source" icon={Layers} value={filters.source} options={SOURCES} onChange={(v) => onChange({ source: v })} anyLabel="All sources" />
			<FilterMenu
				label="Provider"
				icon={Building2}
				value={filters.provider}
				options={providerOptions}
				onChange={(v) => onChange({ provider: v })}
				searchable
				anyLabel="All providers"
			/>
			<FilterMenu
				label="Capabilities"
				icon={Sparkles}
				value={filters.capability}
				options={CAPABILITIES}
				onChange={(v) => onChange({ capability: v })}
				multiple
				caption="Models that support all selected"
			/>
			<FilterMenu
				label="Input price"
				icon={CircleDollarSign}
				value={filters.price}
				options={PRICES}
				onChange={(v) => onChange({ price: v })}
				caption="USD per million input tokens"
			/>
			<FilterMenu
				label="Context"
				icon={Ruler}
				value={filters.context}
				options={TOKEN_BUCKETS}
				onChange={(v) => onChange({ context: v })}
				caption="Context window, tokens"
			/>
			<button
				class="fm-trigger {moreOpen ? 'mf-more-open' : ''} {secondaryCount ? 'fm-trigger-on' : ''}"
				aria-expanded={moreOpen}
				aria-controls="mf-more"
				onclick={() => (moreOpen = !moreOpen)}
			>
				<SlidersHorizontal class="size-3.5" aria-hidden="true" />More filters
				{#if secondaryCount}<span class="mf-badge">{secondaryCount}</span>{/if}
			</button>
			{#if total > 0}
				<button class="mf-clear" onclick={onClear}>
					<X class="size-3" aria-hidden="true" />Clear{total > 1 ? ` ${total}` : ''}
				</button>
			{/if}
		</div>
	</div>

	{#if moreOpen}
		<div class="mf-row mf-more" id="mf-more">
			<FilterMenu label="Released" icon={CalendarClock} value={filters.released} options={RELEASED} onChange={(v) => onChange({ released: v })} anyLabel="Any time" caption="When the model was added to OpenRouter" />
			<FilterMenu label="Weights" icon={Scale} value={filters.weights} options={WEIGHTS} onChange={(v) => onChange({ weights: v })} anyLabel="Open or closed" caption="Per models.dev; uncovered models match neither" />
			<FilterMenu label="Input" icon={Image} value={filters.modality} options={MODALITIES} onChange={(v) => onChange({ modality: v })} multiple caption="Accepts all selected, besides text" />
			<FilterMenu label="Tokenizer" icon={Type} value={filters.tokenizer} options={tokenizerOptions} onChange={(v) => onChange({ tokenizer: v })} searchable anyLabel="Any tokenizer" />
			<FilterMenu label="Max output" icon={ArrowUpToLine} value={filters.maxOutput} options={TOKEN_BUCKETS} onChange={(v) => onChange({ maxOutput: v })} caption="Max output tokens per response" />
			<FilterMenu label="Research data" icon={Database} value={filters.coverage} options={COVERAGE} onChange={(v) => onChange({ coverage: v })} multiple caption="External data attached to the model" />
			<span class="mf-divider" aria-hidden="true"></span>
			<button
				class="fm-trigger {filters.usedInApps ? 'fm-trigger-on' : ''}"
				aria-pressed={filters.usedInApps}
				onclick={() => onChange({ usedInApps: !filters.usedInApps })}
				title="Only models that generated at least one app"
			>
				<AppWindow class="size-3.5" aria-hidden="true" />Used in apps
			</button>
			<button
				class="fm-trigger {filters.hideVariants ? 'fm-trigger-on' : ''}"
				aria-pressed={filters.hideVariants}
				onclick={() => onChange({ hideVariants: !filters.hideVariants })}
				title='Hide "(batch)" price variants and router pseudo-models'
			>
				<EyeOff class="size-3.5" aria-hidden="true" />Hide batch &amp; routers
			</button>
		</div>
	{/if}
</div>

<style>
	.mf-bar {
		border-bottom: 1px solid var(--border);
	}
	.mf-row {
		display: flex;
		flex-wrap: wrap;
		align-items: center;
		gap: 0.5rem;
		padding: 0.5rem;
	}
	.mf-more {
		gap: 0.375rem;
		padding-top: 0.5rem;
		border-top: 1px dashed color-mix(in oklab, var(--border) 80%, transparent);
		background: color-mix(in oklab, var(--muted) 30%, transparent);
	}
	.mf-divider {
		width: 1px;
		height: 1.25rem;
		margin-inline: 0.25rem;
		background: var(--border);
	}
	.mf-search {
		position: relative;
		flex: 1 1 14rem;
		min-width: 0;
	}
	.mf-search :global(.mf-search-icon) {
		position: absolute;
		left: 0.625rem;
		top: 50%;
		width: 0.875rem;
		height: 0.875rem;
		transform: translateY(-50%);
		color: var(--muted-foreground);
		pointer-events: none;
	}
	.mf-search input {
		height: 2.25rem;
		width: 100%;
		border-radius: calc(var(--radius) + 0.125rem);
		border: 1px solid var(--input);
		background: var(--surface-1, var(--card));
		padding: 0 2rem 0 2.125rem;
		font-size: 0.875rem;
		transition: border-color 150ms;
	}
	.mf-search input::placeholder {
		color: color-mix(in oklab, var(--muted-foreground) 80%, transparent);
	}
	.mf-search input:hover {
		border-color: color-mix(in oklab, var(--primary) 40%, var(--input));
	}
	.mf-search input:focus-visible {
		outline: none;
		border-color: var(--ring);
		box-shadow: 0 0 0 3px color-mix(in oklab, var(--ring) 25%, transparent);
	}
	.mf-search input::-webkit-search-cancel-button {
		display: none;
	}
	.mf-search kbd,
	.mf-search-clear {
		position: absolute;
		right: 0.5rem;
		top: 50%;
		transform: translateY(-50%);
	}
	.mf-search kbd {
		border: 1px solid var(--border);
		border-radius: 0.25rem;
		background: var(--muted);
		padding: 0 0.3rem;
		font-family: var(--font-mono);
		font-size: 0.6875rem;
		color: var(--muted-foreground);
		pointer-events: none;
	}
	.mf-search-clear {
		display: inline-flex;
		border-radius: 0.25rem;
		padding: 0.25rem;
		color: var(--muted-foreground);
	}
	.mf-search-clear:hover {
		color: var(--foreground);
	}
	.mf-menus {
		display: flex;
		flex-wrap: wrap;
		align-items: center;
		gap: 0.375rem;
	}
	.mf-more-open:not(:global(.fm-trigger-on)) {
		color: var(--foreground);
		background: var(--muted);
	}
	.mf-badge {
		display: inline-flex;
		min-width: 1rem;
		height: 1rem;
		align-items: center;
		justify-content: center;
		border-radius: 9999px;
		background: var(--primary);
		padding-inline: 0.25rem;
		font-size: 0.6875rem;
		font-weight: 600;
		color: var(--primary-foreground);
	}
	.mf-clear {
		display: inline-flex;
		height: 2.25rem;
		align-items: center;
		gap: 0.25rem;
		border-radius: calc(var(--radius) + 0.125rem);
		padding-inline: 0.5rem;
		font-size: 0.75rem;
		color: var(--muted-foreground);
		transition: color 150ms;
	}
	.mf-clear:hover {
		color: var(--foreground);
	}
	.mf-clear:focus-visible,
	.mf-search-clear:focus-visible {
		outline: 2px solid var(--ring);
		outline-offset: 1px;
	}
	@media (max-width: 640px) {
		.mf-search {
			flex-basis: 100%;
		}
		.mf-divider {
			display: none;
		}
		/* Phones: each filter row scrolls sideways instead of stacking five lines of buttons. */
		.mf-menus,
		.mf-more {
			flex-wrap: nowrap;
			overflow-x: auto;
			scrollbar-width: none;
		}
		.mf-menus {
			width: 100%;
		}
	}
</style>
