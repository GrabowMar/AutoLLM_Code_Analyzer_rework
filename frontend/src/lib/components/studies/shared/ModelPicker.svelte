<script lang="ts">
	/**
	 * ModelPicker — choose LLMs from the whole catalog (every page, loaded once
	 * per page load via `loadModelCatalog`).
	 *
	 * Props:
	 * - `mode`: 'multi' (default) | 'single'.
	 * - `selectedIds` (bindable `Set<number>`, multi): model pks. Replaced, never mutated.
	 * - `selectedId` (bindable `number | null`, single).
	 * - `requireTools`: models without tool calling are ineligible. They are hidden
	 *   from the list; ineligible models that are still selected stay pinned on top
	 *   with a warning and a "Remove N ineligible" action.
	 * - `maxHeight`: Tailwind max-height class of the scrolling list (default 'max-h-80').
	 * - `label`: heading text (default 'Models'; '' hides it — then pass `aria-label`).
	 * - `aria-label`, `idPrefix` (default 'model-picker'), `disabled`.
	 * - `onchange()`: called after every user change to the selection.
	 * - `groupBy`: 'provider' (default; vendor groups) | 'endpoint' (one group per
	 *   local endpoint, listed first, then OpenRouter by vendor). Endpoint headers
	 *   have "Select all"; the bulk-select limit rises to 100.
	 * - `showEndpointStatus`: endpoint headers show status, relay/direct, slots
	 *   and loaded models (fetched from /inference/endpoints/, refreshed every 30 s).
	 * - `endpointFilter` (bindable): 'all' | 'local' | 'openrouter'. With
	 *   `groupBy="endpoint"` and both kinds in the catalog, a switch is shown.
	 * - `showSelection` (default true): chips of the selected models above the search.
	 * - `showUnavailable`: load hidden / no-longer-served local models too, so a
	 *   selected one is named with its reason instead of "not in the catalog".
	 *
	 * Finding models: the search matches every word anywhere (name, vendor, id, endpoint: "qwen 235b")
	 * and highlights it; capability chips (reasoning, vision, open weights, free, new) narrow with live
	 * counts; the sort switches from vendor groups to one flat list (cheapest, longest context, newest).
	 * Rows are one line — name, id, capability icons, then context and price in aligned columns — so
	 * models can be compared at a glance. A broad search opens only as many groups as fit a row budget
	 * (the rest stay as headers with counts) and a flat list renders in pages, so the list never mounts
	 * hundreds of rows at once.
	 * Keyboard: Tab into the list, ↑ ↓ Home End move, Space toggles; Escape in the search clears it.
	 */
	import { onMount } from 'svelte';
	import type { LLMModelSummary } from '$lib/api/models';
	import { listEndpoints } from '$lib/api/inference';
	import type { EndpointSummary } from '$lib/api/types/inference';
	import { formatApiError } from '$lib/api/core';
	import { formatPrice } from '$lib/utils/formatters';
	import { cn } from '$lib/utils';
	import { Button } from '$lib/components/ui/button';
	import { Input } from '$lib/components/ui/input';
	import { Segmented } from '$lib/components/ui/segmented';
	import * as Select from '$lib/components/ui/select';
	import EmptyState from '$lib/components/EmptyState.svelte';
	import ErrorAlert from '$lib/components/ErrorAlert.svelte';
	import ProviderIcon from '$lib/components/ProviderIcon.svelte';
	import Highlight from '$lib/components/Highlight.svelte';
	import { shortModelName } from '$lib/components/table';
	import {
		MODEL_FACETS,
		MODEL_SORTS,
		applyFacets,
		paginateGroups,
		facetCounts,
		filterByEndpointScope,
		groupModels,
		hasFacet,
		hasVariablePrice,
		isLocalModel,
		loadModelCatalog,
		matchesTokens,
		modelIneligibility,
		searchTokens,
		sortModels,
		watchCatalog,
		type ModelEndpointScope,
		type ModelFacet,
		type ModelGroup,
		type ModelGroupBy,
		type ModelSort,
	} from './catalog';
	import { navigateList } from './list-keyboard';
	import Brain from '@lucide/svelte/icons/brain';
	import Check from '@lucide/svelte/icons/check';
	import Eye from '@lucide/svelte/icons/eye';
	import Search from '@lucide/svelte/icons/search';
	import Server from '@lucide/svelte/icons/server';
	import TriangleAlert from '@lucide/svelte/icons/triangle-alert';
	import Unlock from '@lucide/svelte/icons/lock-open';
	import Wrench from '@lucide/svelte/icons/wrench';
	import X from '@lucide/svelte/icons/x';

	interface Props {
		mode?: 'single' | 'multi';
		selectedIds?: Set<number>;
		selectedId?: number | null;
		requireTools?: boolean;
		maxHeight?: string;
		label?: string;
		'aria-label'?: string;
		idPrefix?: string;
		disabled?: boolean;
		onchange?: () => void;
		groupBy?: ModelGroupBy;
		showEndpointStatus?: boolean;
		endpointFilter?: ModelEndpointScope;
		showUnavailable?: boolean;
		/** Show the chips of the selected models above the search (off when the page lists them itself). */
		showSelection?: boolean;
	}

	let {
		mode = 'multi',
		selectedIds = $bindable(new Set<number>()),
		selectedId = $bindable(null),
		requireTools = false,
		maxHeight = 'max-h-80',
		label = 'Models',
		'aria-label': ariaLabel,
		idPrefix = 'model-picker',
		disabled = false,
		onchange,
		groupBy = 'provider',
		showEndpointStatus = false,
		endpointFilter = $bindable('all'),
		showUnavailable = false,
		showSelection = true,
	}: Props = $props();

	// Bulk "select shown" is offered only for small, deliberate result sets
	// (grouped by endpoint, whole endpoints are picked, so allow more).
	const SELECT_SHOWN_LIMIT = $derived(groupBy === 'endpoint' ? 100 : 50);
	const ENDPOINT_REFRESH_MS = 30_000;
	/** Rows rendered per page (grouped or flat): a broad list never mounts hundreds of rows. */
	const PAGE = 80;

	let models = $state<LLMModelSummary[]>([]);
	let loading = $state(true);
	let error = $state('');
	let search = $state('');
	let facets = $state(new Set<ModelFacet>());
	let sort = $state<ModelSort>('vendor');
	let limit = $state(PAGE);
	let endpointInfo = $state(new Map<number, EndpointSummary>());

	const rules = $derived({ requireTools });
	const byId = $derived(new Map(models.map((m) => [m.id, m])));
	const selection = $derived(mode === 'multi' ? [...selectedIds] : selectedId != null ? [selectedId] : []);
	const selectedSet = $derived(new Set(selection));

	const ineligibleSelected = $derived.by(() => {
		if (loading || error) return [];
		return selection
			.map((id) => ({ id, model: byId.get(id), reason: modelIneligibility(byId.get(id), rules) }))
			.filter((entry): entry is { id: number; model: LLMModelSummary | undefined; reason: string } => entry.reason !== null);
	});
	const eligibleSelected = $derived(
		selection.map((id) => byId.get(id)).filter((m): m is LLMModelSummary => !!m && modelIneligibility(m, rules) === null),
	);

	const eligible = $derived(models.filter((m) => modelIneligibility(m, rules) === null));
	const hasLocal = $derived(models.some(isLocalModel));
	const hasRemote = $derived(models.some((m) => !isLocalModel(m)));
	const showScope = $derived(groupBy === 'endpoint' && hasLocal && hasRemote);
	const scoped = $derived(filterByEndpointScope(eligible, showScope ? endpointFilter : 'all'));
	const hiddenCount = $derived(models.length - eligible.length);
	const hiddenReason = $derived(requireTools ? 'no tool calling or unavailable' : 'no longer available');
	const tokens = $derived(searchTokens(search));
	const queried = $derived(scoped.filter((m) => matchesTokens(m, tokens)));
	const counts = $derived(facetCounts(queried, facets));
	const filtered = $derived(applyFacets(queried, facets));
	const facetItems = $derived(MODEL_FACETS.filter((f) => facets.has(f.key) || scoped.some((m) => hasFacet(m, f.key))));
	const filtering = $derived(tokens.length > 0 || facets.size > 0 || (showScope && endpointFilter !== 'all'));

	// Sections (sort = vendor: local endpoints, then vendors, each under a light sticky label) or one
	// flat sorted list; both paged.
	const flat = $derived(sort !== 'vendor');
	const page = $derived(flat ? null : paginateGroups(groupModels(filtered, groupBy, selectedSet), limit));
	const sortedFlat = $derived(flat ? sortModels(filtered, sort) : []);
	const flatShown = $derived(sortedFlat.slice(0, limit));
	const remaining = $derived(flat ? sortedFlat.length - flatShown.length : (page?.remaining ?? 0));

	// A new query, filter or sort starts from the first page.
	const viewKey = $derived(`${tokens.join(' ')}|${[...facets].sort().join(',')}|${sort}|${endpointFilter}`);
	let lastViewKey = '';
	$effect(() => {
		const key = viewKey;
		if (key === lastViewKey) return;
		lastViewKey = key;
		limit = PAGE;
	});

	const canSelectShown = $derived(
		mode === 'multi' && filtering && filtered.length > 0 && filtered.length <= SELECT_SHOWN_LIMIT && filtered.some((m) => !selectedSet.has(m.id)),
	);
	const headingId = $derived(`${idPrefix}-heading`);
	const footerText = $derived(
		`${filtered.length} of ${eligible.length} models match` + (hiddenCount > 0 ? ` · ${hiddenCount} hidden (${hiddenReason})` : ''),
	);

	async function load(force = false) {
		loading = true;
		error = '';
		try {
			models = await loadModelCatalog({ force, includeUnavailable: showUnavailable });
		} catch (e) {
			error = formatApiError(e, 'Could not load the model catalog.');
		} finally {
			loading = false;
		}
	}

	onMount(() => {
		void load();
		// Models added or retired by a catalog sync since the list was cached show up here.
		return watchCatalog(
			'models',
			(list) => {
				models = list;
				error = '';
			},
			{ includeUnavailable: showUnavailable },
		);
	});

	async function loadEndpointInfo() {
		try {
			const list = await listEndpoints();
			endpointInfo = new Map(list.map((e) => [e.id, e]));
		} catch {
			// Headers fall back to the catalog's online flag.
		}
	}

	$effect(() => {
		if (!showEndpointStatus || groupBy !== 'endpoint') return;
		void loadEndpointInfo();
		const timer = setInterval(loadEndpointInfo, ENDPOINT_REFRESH_MS);
		return () => clearInterval(timer);
	});

	const loadedByEndpoint = $derived(new Map([...endpointInfo.values()].map((e) => [e.id, new Set(e.live.loaded.map((l) => l.name))])));

	function isLoaded(m: LLMModelSummary): boolean {
		if (!m.endpoint || !m.served_model_name) return false;
		return loadedByEndpoint.get(m.endpoint.id)?.has(m.served_model_name) ?? false;
	}

	function endpointStatus(group: ModelGroup): { label: string; tone: 'success' | 'warning' | 'danger' | 'neutral' } {
		const info = group.endpoint ? endpointInfo.get(group.endpoint.id) : undefined;
		const status = info?.status ?? (group.endpoint?.online ? 'online' : 'offline');
		const tone = status === 'online' ? 'success' : status === 'degraded' ? 'warning' : status === 'offline' ? 'danger' : 'neutral';
		return { label: status, tone };
	}

	function endpointMeta(group: ModelGroup): string {
		const info = group.endpoint ? endpointInfo.get(group.endpoint.id) : undefined;
		const kind = group.endpoint?.kind === 'openai_compatible' ? 'OpenAI-compatible' : 'Ollama';
		const parts = [kind];
		if (info) {
			parts.push(info.connection);
			parts.push(`${info.max_concurrency} ${info.max_concurrency === 1 ? 'slot' : 'slots'}`);
		}
		return parts.join(' · ');
	}

	function selectGroup(group: ModelGroup) {
		if (disabled || mode !== 'multi') return;
		const next = new Set(selectedIds);
		const all = group.models.every((m) => next.has(m.id));
		for (const m of group.models) {
			if (all) next.delete(m.id);
			else next.add(m.id);
		}
		selectedIds = next;
		changed();
	}

	function toggleFacet(key: ModelFacet) {
		const next = new Set(facets);
		if (next.has(key)) next.delete(key);
		else next.add(key);
		facets = next;
	}

	function clearFilters() {
		search = '';
		facets = new Set();
		endpointFilter = 'all';
	}

	function changed() {
		onchange?.();
	}

	function toggle(id: number) {
		if (disabled) return;
		if (mode === 'single') {
			if (selectedId === id) return;
			selectedId = id;
		} else {
			const next = new Set(selectedIds);
			if (next.has(id)) next.delete(id);
			else next.add(id);
			selectedIds = next;
		}
		changed();
	}

	function remove(ids: number[]) {
		if (mode === 'single') {
			if (selectedId != null && ids.includes(selectedId)) selectedId = null;
		} else {
			const next = new Set(selectedIds);
			for (const id of ids) next.delete(id);
			selectedIds = next;
		}
		changed();
	}

	function selectShown() {
		const next = new Set(selectedIds);
		for (const m of filtered) next.add(m.id);
		selectedIds = next;
		changed();
	}

	function clearAll() {
		if (mode === 'single') selectedId = null;
		else selectedIds = new Set();
		changed();
	}

	function priceText(m: LLMModelSummary): string {
		if (isLocalModel(m)) return 'local';
		if (m.is_free) return 'free';
		if (hasVariablePrice(m)) return 'varies';
		return `${formatPrice(m.input_price_per_million)} / ${formatPrice(m.output_price_per_million)}`;
	}

	function searchKeydown(e: KeyboardEvent) {
		if (e.key === 'Escape' && search) {
			e.preventDefault();
			e.stopPropagation();
			search = '';
		}
	}
</script>

{#snippet row(m: LLMModelSummary, showSource: boolean)}
	{@const checked = selectedSet.has(m.id)}
	{@const local = isLocalModel(m)}
	{@const id = local && m.served_model_name ? m.served_model_name : m.model_id}
	{@const name = showSource ? m.model_name : shortModelName(m.model_name)}
	{@const sub = id.toLowerCase() === m.model_name.toLowerCase() ? [m.parameter_size, m.quantization].filter(Boolean).join(' · ') : id}
	<button
		type="button"
		data-nav-item
		role={mode === 'multi' ? 'checkbox' : 'radio'}
		aria-checked={checked}
		{disabled}
		class="mp-row mp-grid"
		data-checked={checked}
		title="{m.model_name} — {id}"
		onclick={() => toggle(m.id)}
		onkeydown={navigateList}
	>
		<span class={cn('mp-box', mode === 'multi' ? 'rounded-[4px]' : 'rounded-full')} aria-hidden="true">
			{#if checked}
				{#if mode === 'multi'}<Check class="size-3" strokeWidth={3} />{:else}<span class="size-1.5 rounded-full bg-current"></span>{/if}
			{/if}
		</span>
		<span class="mp-name">
			<span class="mp-title">
				{#if showSource}
					{#if local}<Server class="size-3.5 shrink-0 text-muted-foreground" aria-hidden="true" />{:else}<ProviderIcon provider={m.provider} size={14} decorative class="shrink-0" />{/if}
				{/if}
				<span class="truncate"><Highlight text={name} {tokens} /></span>
				{#if hasFacet(m, 'new')}<span class="mp-tag" title="Released {m.released_at}"><span class="sr-only">{', '}</span>new</span>{/if}
				{#if local && isLoaded(m)}<span class="mp-tag mp-tag-ok" title="Loaded in memory now: no warm-up"><span class="sr-only">{', '}</span>loaded</span>{/if}
			</span>
			{#if sub || (showSource && local)}
				<span class="mp-id"><span class="sr-only">{', id '}</span><Highlight text={sub} {tokens} />{#if showSource && local}{sub ? ' · ' : ''}{m.endpoint?.name ?? ''}{/if}</span>
			{/if}
		</span>
		<span class="mp-caps">
			{#if !requireTools}
				<span class="mp-cap" data-on={m.supports_function_calling} title={m.supports_function_calling ? 'Tool calling' : 'No tool calling'}>
					<Wrench class="size-3.5" aria-hidden="true" />{#if m.supports_function_calling}<span class="sr-only">{', tools'}</span>{/if}
				</span>
			{/if}
			<span class="mp-cap" data-on={m.supports_reasoning === true} title={m.supports_reasoning ? 'Reasoning' : 'No reasoning parameters'}>
				<Brain class="size-3.5" aria-hidden="true" />{#if m.supports_reasoning}<span class="sr-only">{', reasoning'}</span>{/if}
			</span>
			<span class="mp-cap" data-on={hasFacet(m, 'vision')} title={hasFacet(m, 'vision') ? 'Image input' : 'Text only'}>
				<Eye class="size-3.5" aria-hidden="true" />{#if hasFacet(m, 'vision')}<span class="sr-only">{', vision'}</span>{/if}
			</span>
			<span class="mp-cap" data-on={hasFacet(m, 'open_weights')} title={hasFacet(m, 'open_weights') ? 'Open weights' : 'Closed weights'}>
				<Unlock class="size-3.5" aria-hidden="true" />{#if hasFacet(m, 'open_weights')}<span class="sr-only">{', open weights'}</span>{/if}
			</span>
		</span>
		<span class="mp-num mp-ctx" title={local ? "Context window (capped by the endpoint's num_ctx)" : 'Context window'}>
			<span class="sr-only">{', '}</span>{#if (m.context_window ?? 0) > 0}{m.context_window_display}<span class="sr-only">{' context'}</span>{:else}—{/if}
		</span>
		<span
			class={cn('mp-num mp-price', (m.is_free || local || hasVariablePrice(m)) && 'text-muted-foreground')}
			title={local ? [m.parameter_size, m.quantization].filter(Boolean).join(' · ') || 'Runs on your endpoint: no API cost' : 'Input / output price per 1M tokens'}
		>
			<span class="sr-only">{', '}</span>{#if local && (m.parameter_size || m.quantization)}{[m.parameter_size, m.quantization].filter(Boolean).join(' ')}{:else}{priceText(m)}{/if}
		</span>
	</button>
{/snippet}

<div class="@container space-y-2">
	<div class="flex flex-wrap items-center justify-between gap-2">
		{#if label}
			<span id={headingId} class="text-sm font-medium">{label}</span>
		{/if}
		<div class="ml-auto flex items-center gap-2 text-xs text-muted-foreground">
			{#if showSelection}<span class="tabular-nums" role="status" aria-atomic="true">{selection.length} selected<span class="sr-only"> {selection.length === 1 ? 'model' : 'models'}</span></span>{/if}
			{#if canSelectShown}
				<Button variant="ghost" size="sm" class="h-7 px-2" onclick={selectShown} {disabled}>
					Select {filtered.length} shown
				</Button>
			{/if}
			{#if showSelection && selection.length > 0}
				<Button variant="ghost" size="sm" class="h-7 px-2" onclick={clearAll} {disabled}>Clear</Button>
			{/if}
		</div>
	</div>

	{#if ineligibleSelected.length > 0}
		<div class="rounded-md border border-warning/30 bg-warning/10 px-3 py-2 text-sm" role="alert">
			<div class="flex flex-wrap items-start gap-2">
				<TriangleAlert class="mt-0.5 size-4 shrink-0 text-warning-fg" aria-hidden="true" />
				<div class="min-w-0 flex-1">
					<p class="font-medium text-warning-fg">
						{ineligibleSelected.length === 1
							? '1 selected model can’t run with this setup'
							: `${ineligibleSelected.length} selected models can’t run with this setup`}
					</p>
					<ul class="mt-1 space-y-0.5 text-xs text-muted-foreground">
						{#each ineligibleSelected as entry (entry.id)}
							<li class="flex flex-wrap items-center gap-x-2">
								<span class="font-medium text-foreground">{entry.model?.model_name ?? `Model #${entry.id}`}</span>
								<span>— {entry.reason}</span>
							</li>
						{/each}
					</ul>
				</div>
				<Button variant="outline" size="sm" class="shrink-0" {disabled} onclick={() => remove(ineligibleSelected.map((e) => e.id))}>
					Remove {ineligibleSelected.length} ineligible
				</Button>
			</div>
		</div>
	{/if}

	{#if showSelection && eligibleSelected.length > 0}
		<ul class="flex max-h-24 flex-wrap gap-1.5 overflow-y-auto" aria-label="Selected models">
			{#each eligibleSelected as m (m.id)}
				<li>
					<span class="inline-flex max-w-full items-center gap-1 rounded-full border border-primary/30 bg-primary/10 py-0.5 pr-1 pl-2 text-xs">
						<span class="truncate">{m.model_name}</span>
						<button
							type="button"
							class="relative flex size-4 shrink-0 cursor-pointer items-center justify-center rounded-full text-muted-foreground transition-colors after:absolute after:-inset-1 after:content-[''] hover:bg-primary/20 hover:text-foreground focus-visible:ring-2 focus-visible:ring-ring/40 focus-visible:outline-none"
							aria-label="Remove {m.model_name}"
							{disabled}
							onclick={() => remove([m.id])}
						>
							<X class="size-3" />
						</button>
					</span>
				</li>
			{/each}
		</ul>
	{/if}

	<div class="flex flex-wrap items-center gap-x-3 gap-y-2">
		<div class="relative min-w-[12rem] flex-1">
			<Search class="pointer-events-none absolute top-2.5 left-2.5 size-3.5 text-muted-foreground" aria-hidden="true" />
			<Input
				bind:value={search}
				type="search"
				placeholder="Search name, vendor, id or endpoint…"
				aria-label="Search models"
				aria-describedby="{idPrefix}-count"
				class="h-8 pl-8 text-xs"
				onkeydown={searchKeydown}
				{disabled}
			/>
		</div>
		{#if showScope}
			<Segmented
				aria-label="Model source"
				size="sm"
				bind:value={() => endpointFilter, (v) => (endpointFilter = v as ModelEndpointScope)}
				items={[
					{ value: 'all', label: 'All' },
					{ value: 'local', label: 'Local' },
					{ value: 'openrouter', label: 'OpenRouter' },
				]}
				{disabled}
			/>
		{/if}
	</div>

	<div class="flex flex-wrap items-center gap-x-2 gap-y-1.5">
		<div class="flex flex-wrap gap-1" role="group" aria-label="Filter by capability">
			{#each facetItems as f (f.key)}
				{@const on = facets.has(f.key)}
				<button
					type="button"
					class="mp-facet"
					aria-pressed={on}
					title={f.title}
					disabled={disabled || (!on && counts[f.key] === 0)}
					onclick={() => toggleFacet(f.key)}
				>
					{#if on}<Check class="size-3" aria-hidden="true" />{/if}{f.label}
					<span class="mp-facet-n">{counts[f.key]}</span>
				</button>
			{/each}
		</div>
		<div class="ml-auto w-44">
			<Select.Simple
				id="{idPrefix}-sort"
				size="sm"
				aria-label="Sort models"
				value={sort}
				items={MODEL_SORTS}
				onValueChange={(v) => (sort = v as ModelSort)}
				{disabled}
			/>
		</div>
	</div>

	{#if loading}
		<div class={cn('overflow-hidden rounded-md border border-border', maxHeight)} aria-busy="true" aria-label="Loading models">
			{#each Array.from({ length: 7 }) as _, i (i)}
				<div class="mp-skel-row" aria-hidden="true">
					<span class="mp-skel size-4"></span>
					<span class="mp-skel h-3 flex-1" style="max-width: {40 + ((i * 17) % 35)}%"></span>
					<span class="mp-skel h-3 w-10"></span>
					<span class="mp-skel h-3 w-20"></span>
				</div>
			{/each}
		</div>
	{:else if error}
		<ErrorAlert message={error} onretry={() => load(true)} />
	{:else if filtered.length === 0}
		<EmptyState
			compact
			class="rounded-md border border-border"
			title={models.length === 0 ? 'No models in the catalog' : 'No models match'}
			description={models.length === 0
				? 'Sync models from OpenRouter on the Models page, or add a local endpoint.'
				: 'Try fewer words, or remove a capability filter.'}
		>
			{#snippet action()}
				{#if models.length > 0 && filtering}
					<Button variant="outline" size="sm" onclick={clearFilters}>Clear search and filters</Button>
				{/if}
			{/snippet}
		</EmptyState>
	{:else}
		<div
			class={cn('mp-list overflow-y-auto overscroll-contain rounded-md border border-border bg-card', maxHeight)}
			role="group"
			data-nav-list
			aria-labelledby={label ? headingId : undefined}
			aria-label={label ? undefined : (ariaLabel ?? 'Models')}
		>
			<div class="mp-grid mp-head" aria-hidden="true">
				<span></span>
				<span>Model</span>
				<span class="text-center">Abilities</span>
				<span class="text-right">Context</span>
				<span class="text-right" title="Input / output price per 1M tokens">Price / 1M</span>
			</div>
			{#if flat}
				{#each flatShown as m (m.id)}
					{@render row(m, true)}
				{/each}
			{:else if page}
				{#each page.groups as group (group.key)}
					{@const isEndpoint = group.type === 'endpoint'}
					{@const status = isEndpoint && showEndpointStatus ? endpointStatus(group) : null}
					{@const allOn = group.models.every((m) => selectedSet.has(m.id))}
					<div class="mp-section" data-endpoint={isEndpoint} role="presentation">
						{#if isEndpoint}
							<Server class="size-3.5 shrink-0" aria-hidden="true" />
						{:else}
							<ProviderIcon provider={group.provider} size={14} decorative class="shrink-0" />
						{/if}
						<span class="mp-section-name" title={isEndpoint ? `${group.label} · ${endpointMeta(group)}` : group.label}>{group.label}</span>
						{#if status}
							<span
								class={cn(
									'inline-flex shrink-0 items-center gap-1',
									status.tone === 'success' && 'text-success-fg',
									status.tone === 'warning' && 'text-warning-fg',
									status.tone === 'danger' && 'text-danger-fg',
								)}
							>
								<span class="status-dot h-1.5 w-1.5" aria-hidden="true"></span>{status.label}
							</span>
							<span class="hidden min-w-0 truncate @lg:inline">{endpointMeta(group)}</span>
						{/if}
						<span class="flex-1"></span>
						<span class="mp-section-count">
							{#if group.selectedCount > 0}<b>{group.selectedCount}</b>/{/if}{group.models.length}
						</span>
						{#if isEndpoint && mode === 'multi'}
							<button
								type="button"
								class="mp-section-all"
								{disabled}
								onclick={() => selectGroup(group)}
								aria-label="{allOn ? 'Deselect' : 'Select'} all models on {group.label}"
							>
								{allOn ? 'Deselect all' : 'Select all'}
							</button>
						{/if}
					</div>
					{#each group.shown as m (m.id)}
						{@render row(m, false)}
					{/each}
				{/each}
			{/if}
			{#if remaining > 0}
				<button type="button" class="mp-more" onclick={() => (limit += PAGE)}>
					Show {Math.min(PAGE, remaining)} more <span class="text-muted-foreground">· {remaining} not shown</span>
				</button>
			{/if}
		</div>
		<p id="{idPrefix}-count" class="text-[11px] text-muted-foreground">{footerText}</p>
	{/if}
</div>

<style>
	/* One grid for the column header and every row, so the numbers line up. */
	.mp-grid {
		display: grid;
		grid-template-columns: 1rem minmax(0, 1fr) 5rem 3.5rem 7.75rem;
		align-items: center;
		column-gap: 0.75rem;
		padding-inline: 0.75rem;
	}
	.mp-head {
		position: sticky;
		top: 0;
		z-index: 2;
		height: 1.875rem;
		border-bottom: 1px solid var(--border);
		background: var(--card);
		font-family: var(--font-mono);
		font-size: 0.6875rem;
		letter-spacing: 0.06em;
		text-transform: uppercase;
		color: var(--muted-foreground);
	}
	.mp-section {
		position: sticky;
		top: 1.875rem;
		z-index: 1;
		display: flex;
		align-items: center;
		gap: 0.5rem;
		min-height: 1.875rem;
		padding: 0.25rem 0.75rem;
		border-bottom: 1px solid color-mix(in oklab, var(--border) 60%, transparent);
		background: color-mix(in oklab, var(--muted) 35%, var(--card));
		font-size: 0.75rem;
		color: var(--muted-foreground);
	}
	.mp-section-name {
		min-width: 0;
		overflow: hidden;
		text-overflow: ellipsis;
		white-space: nowrap;
		font-weight: 600;
		color: var(--foreground);
	}
	.mp-section[data-endpoint='true'] {
		background: color-mix(in oklab, var(--primary) 6%, var(--card));
	}
	.mp-section-count {
		font-family: var(--font-mono);
		font-size: 0.6875rem;
		font-variant-numeric: tabular-nums;
	}
	.mp-section-count b {
		font-weight: 600;
		color: var(--primary);
	}
	.mp-section-all {
		flex-shrink: 0;
		min-height: 1.5rem;
		min-width: 1.5rem;
		white-space: nowrap;
		cursor: pointer;
		border-radius: calc(var(--radius) - 2px);
		padding-inline: 0.5rem;
		font-size: 0.6875rem;
		font-weight: 500;
		color: var(--primary);
		transition: background-color 150ms;
	}
	.mp-section-all:hover {
		background: color-mix(in oklab, var(--primary) 10%, transparent);
	}
	.mp-section-all:focus-visible {
		outline: 2px solid var(--ring);
	}
	.mp-row {
		position: relative;
		width: 100%;
		min-height: 2.875rem;
		cursor: pointer;
		padding-block: 0.375rem;
		text-align: left;
		font-size: 0.8125rem;
		border-bottom: 1px solid color-mix(in oklab, var(--border) 35%, transparent);
		transition: background-color 120ms;
	}
	.mp-row:hover {
		background: color-mix(in oklab, var(--muted) 45%, transparent);
	}
	.mp-row:focus-visible {
		outline: none;
		background: color-mix(in oklab, var(--muted) 45%, transparent);
		box-shadow: inset 0 0 0 2px color-mix(in oklab, var(--ring) 50%, transparent);
	}
	.mp-row[data-checked='true'] {
		background: color-mix(in oklab, var(--primary) 7%, transparent);
	}
	/* Selected: an accent bar as well as the tint and the check (never colour alone). */
	.mp-row[data-checked='true']::before {
		content: '';
		position: absolute;
		inset-block: 0;
		left: 0;
		width: 2px;
		background: var(--primary);
	}
	.mp-row:disabled {
		cursor: not-allowed;
		opacity: 0.6;
	}
	.mp-box {
		display: flex;
		width: 1rem;
		height: 1rem;
		align-items: center;
		justify-content: center;
		border: 1px solid color-mix(in oklab, var(--muted-foreground) 55%, transparent);
		background: var(--surface-1);
		transition:
			background-color 120ms,
			border-color 120ms;
	}
	.mp-row:hover .mp-box {
		border-color: color-mix(in oklab, var(--primary) 60%, transparent);
	}
	.mp-row[data-checked='true'] .mp-box {
		border-color: var(--primary);
		background: var(--primary);
		color: var(--primary-foreground);
	}
	.mp-name {
		display: flex;
		min-width: 0;
		flex-direction: column;
		gap: 0.0625rem;
	}
	.mp-title {
		display: flex;
		min-width: 0;
		align-items: center;
		gap: 0.375rem;
		font-weight: 500;
		color: var(--foreground);
	}
	.mp-id {
		min-width: 0;
		overflow: hidden;
		text-overflow: ellipsis;
		white-space: nowrap;
		font-family: var(--font-mono);
		font-size: 0.6875rem;
		color: var(--muted-foreground);
	}
	.mp-tag {
		flex-shrink: 0;
		border-radius: 3px;
		padding: 0 0.25rem;
		font-family: var(--font-mono);
		font-size: 0.625rem;
		font-weight: 500;
		line-height: 0.9375rem;
		letter-spacing: 0.04em;
		text-transform: uppercase;
		color: var(--primary);
		box-shadow: inset 0 0 0 1px color-mix(in oklab, var(--primary) 40%, transparent);
	}
	.mp-tag-ok {
		color: var(--success-fg);
		box-shadow: inset 0 0 0 1px color-mix(in oklab, var(--success) 45%, transparent);
	}
	.mp-caps {
		display: flex;
		justify-content: center;
		gap: 0.375rem;
	}
	.mp-cap {
		display: inline-flex;
		color: var(--foreground);
	}
	/* Absent abilities stay as faint placeholders so the column reads as a table, not a scatter. */
	.mp-cap[data-on='false'] {
		color: color-mix(in oklab, var(--muted-foreground) 30%, transparent);
	}
	.mp-num {
		text-align: right;
		font-family: var(--font-mono);
		font-size: 0.75rem;
		font-variant-numeric: tabular-nums;
		white-space: nowrap;
	}
	.mp-ctx {
		color: var(--muted-foreground);
	}
	/* Narrow containers: drop the abilities and context columns (still in the row's name for AT). */
	@container (max-width: 30rem) {
		.mp-grid {
			grid-template-columns: 1rem minmax(0, 1fr) auto;
		}
		.mp-caps,
		.mp-ctx,
		.mp-head > :nth-child(3),
		.mp-head > :nth-child(4) {
			display: none;
		}
	}
	.mp-facet {
		display: inline-flex;
		height: 1.75rem;
		cursor: pointer;
		align-items: center;
		gap: 0.3125rem;
		border: 1px solid var(--border);
		border-radius: 9999px;
		padding-inline: 0.625rem;
		font-size: 0.75rem;
		color: var(--muted-foreground);
		transition:
			border-color 150ms,
			background-color 150ms,
			color 150ms;
	}
	.mp-facet:hover:not(:disabled) {
		border-color: color-mix(in oklab, var(--primary) 40%, var(--border));
		color: var(--foreground);
	}
	.mp-facet[aria-pressed='true'] {
		border-color: var(--primary);
		background: color-mix(in oklab, var(--primary) 10%, transparent);
		color: var(--foreground);
	}
	.mp-facet:disabled {
		cursor: not-allowed;
		opacity: 0.5;
	}
	.mp-facet:focus-visible {
		outline: 2px solid var(--ring);
		outline-offset: 1px;
	}
	.mp-facet-n {
		font-family: var(--font-mono);
		font-size: 0.6875rem;
		font-variant-numeric: tabular-nums;
		color: var(--muted-foreground);
	}
	.mp-more {
		display: flex;
		width: 100%;
		min-height: 2.25rem;
		cursor: pointer;
		align-items: center;
		justify-content: center;
		gap: 0.25rem;
		font-size: 0.75rem;
		font-weight: 500;
		color: var(--primary);
		transition: background-color 150ms;
	}
	.mp-more:hover {
		background: var(--surface-2);
	}
	.mp-more:focus-visible {
		outline: 2px solid var(--ring);
		outline-offset: -2px;
	}
	.mp-skel-row {
		display: flex;
		align-items: center;
		gap: 0.75rem;
		height: 2.875rem;
		padding-inline: 0.75rem;
		border-bottom: 1px solid color-mix(in oklab, var(--border) 40%, transparent);
	}
	.mp-skel {
		display: block;
		border-radius: 3px;
		background: color-mix(in oklab, var(--muted-foreground) 16%, transparent);
		animation: mp-pulse 1.4s ease-in-out infinite;
	}
	@keyframes mp-pulse {
		50% {
			opacity: 0.45;
		}
	}
	@media (prefers-reduced-motion: reduce) {
		.mp-skel {
			animation: none;
		}
		.mp-row,
		.mp-facet,
		.mp-box {
			transition: none;
		}
	}
</style>
