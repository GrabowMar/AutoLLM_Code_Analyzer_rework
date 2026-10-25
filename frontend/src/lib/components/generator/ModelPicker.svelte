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
	 * - `showUnavailable`: load hidden / no-longer-served local models too, so a
	 *   selected one is named with its reason instead of "not in the catalog".
	 *
	 * Search matches name, provider, id and endpoint. OpenRouter rows show tools /
	 * reasoning / context / price badges; local rows show parameter size,
	 * quantization, context, tools and "loaded" instead of a price.
	 * Keyboard: Tab into the list, ↑ ↓ Home End move, Space toggles.
	 */
	import { onMount } from 'svelte';
	import type { LLMModelSummary } from '$lib/api/models';
	import { listEndpoints } from '$lib/api/inference';
	import type { EndpointSummary } from '$lib/api/types/inference';
	import { formatApiError } from '$lib/api/core';
	import { formatPrice } from '$lib/utils/formatters';
	import { cn } from '$lib/utils';
	import { Button } from '$lib/components/ui/button';
	import { Checkbox } from '$lib/components/ui/checkbox';
	import { Input } from '$lib/components/ui/input';
	import { Segmented } from '$lib/components/ui/segmented';
	import EmptyState from '$lib/components/EmptyState.svelte';
	import ErrorAlert from '$lib/components/ErrorAlert.svelte';
	import LoadingState from '$lib/components/LoadingState.svelte';
	import ProviderIcon from '$lib/components/ProviderIcon.svelte';
	import {
		filterByEndpointScope,
		groupModels,
		isLocalModel,
		loadModelCatalog,
		modelIneligibility,
		watchCatalog,
		type ModelEndpointScope,
		type ModelGroup,
		type ModelGroupBy,
	} from './catalog';
	import { navigateList } from './list-keyboard';
	import Brain from '@lucide/svelte/icons/brain';
	import Check from '@lucide/svelte/icons/check';
	import ChevronRight from '@lucide/svelte/icons/chevron-right';
	import Search from '@lucide/svelte/icons/search';
	import Server from '@lucide/svelte/icons/server';
	import TriangleAlert from '@lucide/svelte/icons/triangle-alert';
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
	}: Props = $props();

	// Bulk "select shown" is offered only for small, deliberate result sets
	// (grouped by endpoint, whole endpoints are picked, so allow more).
	const SELECT_SHOWN_LIMIT = $derived(groupBy === 'endpoint' ? 100 : 50);
	const ENDPOINT_REFRESH_MS = 30_000;
	// Badge look as a <span>: rows are <button>s, which may only hold phrasing content.
	const chip = 'inline-flex items-center gap-1 rounded-full border px-2 py-0.5 text-[11px] font-medium leading-4 tabular-nums';

	let models = $state<LLMModelSummary[]>([]);
	let loading = $state(true);
	let error = $state('');
	let search = $state('');
	let freeOnly = $state(false);
	let expanded = $state(new Set<string>());
	let endpointInfo = $state(new Map<number, EndpointSummary>());

	const rules = $derived({ requireTools });
	const byId = $derived(new Map(models.map((m) => [m.id, m])));
	const selection = $derived(
		mode === 'multi' ? [...selectedIds] : selectedId != null ? [selectedId] : []
	);
	const selectedSet = $derived(new Set(selection));

	const ineligibleSelected = $derived.by(() => {
		if (loading || error) return [];
		return selection
			.map((id) => ({ id, model: byId.get(id), reason: modelIneligibility(byId.get(id), rules) }))
			.filter((entry): entry is { id: number; model: LLMModelSummary | undefined; reason: string } =>
				entry.reason !== null
			);
	});
	const eligibleSelected = $derived(
		selection
			.map((id) => byId.get(id))
			.filter((m): m is LLMModelSummary => !!m && modelIneligibility(m, rules) === null)
	);

	const eligible = $derived(models.filter((m) => modelIneligibility(m, rules) === null));
	const hasLocal = $derived(models.some(isLocalModel));
	const hasRemote = $derived(models.some((m) => !isLocalModel(m)));
	const showScope = $derived(groupBy === 'endpoint' && hasLocal && hasRemote);
	const scoped = $derived(filterByEndpointScope(eligible, endpointFilter));
	const hiddenCount = $derived(models.length - eligible.length);
	const hiddenReason = $derived(
		requireTools ? 'no tool calling or unavailable' : 'no longer available'
	);
	const query = $derived(search.trim().toLowerCase());
	// The source switch replaces the "Free only" box, so a hidden box never filters.
	const freeFilter = $derived(freeOnly && !showScope);
	const filtered = $derived(
		scoped.filter((m) => {
			if (freeFilter && !m.is_free) return false;
			if (!query) return true;
			return (
				m.model_name.toLowerCase().includes(query) ||
				m.provider.toLowerCase().includes(query) ||
				m.model_id.toLowerCase().includes(query) ||
				m.canonical_slug.toLowerCase().includes(query) ||
				(m.served_model_name ?? '').toLowerCase().includes(query) ||
				(m.endpoint?.slug ?? '').toLowerCase().includes(query) ||
				(m.endpoint?.name ?? '').toLowerCase().includes(query)
			);
		})
	);
	const groups = $derived<ModelGroup[]>(groupModels(filtered, groupBy, selectedSet));
	const filtering = $derived(query.length > 0 || freeFilter || endpointFilter !== 'all');
	const canSelectShown = $derived(
		mode === 'multi' &&
			filtering &&
			filtered.length > 0 &&
			filtered.length <= SELECT_SHOWN_LIMIT &&
			filtered.some((m) => !selectedSet.has(m.id))
	);
	const headingId = $derived(`${idPrefix}-heading`);
	const footerText = $derived(
		`${filtered.length} of ${eligible.length} models shown` +
			(hiddenCount > 0 ? ` · ${hiddenCount} hidden (${hiddenReason})` : '')
	);

	async function load(force = false) {
		loading = true;
		error = '';
		try {
			const list = await loadModelCatalog({ force, includeUnavailable: showUnavailable });
			models = list;
			// Open the groups that already hold a selection.
			const ids = new Set(mode === 'multi' ? selectedIds : selectedId != null ? [selectedId] : []);
			expanded = new Set(
				groupModels(
					list.filter((m) => ids.has(m.id)),
					groupBy
				).map((g) => g.key)
			);
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
			{ includeUnavailable: showUnavailable }
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

	const loadedByEndpoint = $derived(
		new Map([...endpointInfo.values()].map((e) => [e.id, new Set(e.live.loaded.map((l) => l.name))]))
	);

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
		parts.push(`${group.models.length} ${group.models.length === 1 ? 'model' : 'models'}`);
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

	function isExpanded(key: string): boolean {
		return query.length > 0 || expanded.has(key);
	}

	function toggleGroup(key: string) {
		const next = new Set(expanded);
		if (next.has(key)) next.delete(key);
		else next.add(key);
		expanded = next;
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

	function priceLabel(m: LLMModelSummary): string {
		if (m.is_free) return 'Free';
		return `${formatPrice(m.input_price_per_million)} / ${formatPrice(m.output_price_per_million)} per 1M`;
	}
</script>

<div class="space-y-2">
	<div class="flex flex-wrap items-center justify-between gap-2">
		{#if label}
			<span id={headingId} class="text-sm font-medium">{label}</span>
		{/if}
		<div class="ml-auto flex items-center gap-2 text-xs text-muted-foreground">
			<span class="tabular-nums" aria-live="polite">{selection.length} selected</span>
			{#if canSelectShown}
				<Button variant="ghost" size="sm" class="h-7 px-2" onclick={selectShown} {disabled}>
					Select {filtered.length} shown
				</Button>
			{/if}
			{#if selection.length > 0}
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
				<Button
					variant="outline"
					size="sm"
					class="shrink-0"
					{disabled}
					onclick={() => remove(ineligibleSelected.map((e) => e.id))}
				>
					Remove {ineligibleSelected.length} ineligible
				</Button>
			</div>
		</div>
	{/if}

	{#if eligibleSelected.length > 0}
		<ul class="flex max-h-24 flex-wrap gap-1.5 overflow-y-auto" aria-label="Selected models">
			{#each eligibleSelected as m (m.id)}
				<li>
					<span class="inline-flex max-w-full items-center gap-1 rounded-full border border-primary/30 bg-primary/10 py-0.5 pl-2 pr-1 text-xs">
						<span class="truncate">{m.model_name}</span>
						<button
							type="button"
							class="flex size-4 shrink-0 cursor-pointer items-center justify-center rounded-full text-muted-foreground transition-colors hover:bg-primary/20 hover:text-foreground focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring/40"
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
			<Search class="pointer-events-none absolute left-2.5 top-2.5 size-3.5 text-muted-foreground" aria-hidden="true" />
			<Input
				bind:value={search}
				type="search"
				placeholder="Search name, provider or id…"
				aria-label="Search models"
				class="h-8 pl-8 text-xs"
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
		{:else}
			<label class="flex shrink-0 cursor-pointer items-center gap-1.5 text-xs text-muted-foreground">
				<Checkbox bind:checked={freeOnly} {disabled} />
				Free only
			</label>
		{/if}
	</div>

	{#if loading}
		<LoadingState label="loading models" class="rounded-md border border-border py-8" />
	{:else if error}
		<ErrorAlert message={error} onretry={() => load(true)} />
	{:else if groups.length === 0}
		<EmptyState
			compact
			class="rounded-md border border-border"
			title={models.length === 0 ? 'No models in the catalog' : 'No models match'}
			description={models.length === 0
				? 'Sync models from OpenRouter on the Models page, or add a local endpoint.'
				: endpointFilter !== 'all'
					? 'Clear the search or show all sources.'
					: 'Clear the search or the Free only filter.'}
		/>
	{:else}
		<div
			class={cn('overflow-y-auto rounded-md border border-border', maxHeight)}
			role="group"
			data-nav-list
			aria-labelledby={label ? headingId : undefined}
			aria-label={label ? undefined : (ariaLabel ?? 'Models')}
		>
			{#each groups as group (group.key)}
				{@const open = isExpanded(group.key)}
				{@const isEndpoint = group.type === 'endpoint'}
				{@const status = isEndpoint && showEndpointStatus ? endpointStatus(group) : null}
				<div class="border-b border-border/60 last:border-b-0">
					<div class="sticky top-0 z-[1] flex items-center bg-surface-2">
						<button
							type="button"
							data-nav-item
							class={cn(
								'flex min-w-0 flex-1 cursor-pointer items-center gap-2 px-3 py-1.5 text-left text-xs transition-colors duration-150 hover:bg-surface-3 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-inset focus-visible:ring-ring/40',
								group.selectedCount > 0 ? 'font-semibold text-foreground' : 'font-medium text-muted-foreground'
							)}
							aria-expanded={open}
							onclick={() => toggleGroup(group.key)}
							onkeydown={navigateList}
						>
							<ChevronRight
								class={cn('size-3.5 shrink-0 transition-transform duration-150', open && 'rotate-90')}
								aria-hidden="true"
							/>
							{#if isEndpoint}
								<Server class="size-3.5 shrink-0" aria-hidden="true" />
							{:else}
								<ProviderIcon provider={group.provider} size={14} />
							{/if}
							<span class="min-w-0 truncate">{group.label}</span>
							{#if isEndpoint}
								<span class="hidden min-w-0 truncate font-normal text-muted-foreground sm:inline">· {endpointMeta(group)}</span>
							{/if}
							{#if status}
								<span
									class={cn(
										'inline-flex shrink-0 items-center gap-1 font-normal',
										status.tone === 'success' && 'text-success-fg',
										status.tone === 'warning' && 'text-warning-fg',
										status.tone === 'danger' && 'text-danger-fg',
										status.tone === 'neutral' && 'text-muted-foreground'
									)}
								>
									<span class="status-dot h-1.5 w-1.5" aria-hidden="true"></span>{status.label}
								</span>
							{/if}
							<span class="flex-1"></span>
							{#if group.selectedCount > 0}
								<span class={cn(chip, 'border-transparent bg-primary/15 text-success-fg')}>{group.selectedCount}/{group.models.length}</span>
							{:else}
								<span class="tabular-nums text-[11px] text-muted-foreground">{group.models.length}</span>
							{/if}
						</button>
						{#if isEndpoint && mode === 'multi'}
							<Button
								variant="ghost"
								size="sm"
								class="mr-1 h-6 shrink-0 px-2 text-[11px]"
								{disabled}
								onclick={() => selectGroup(group)}
								aria-label="{group.models.every((m) => selectedSet.has(m.id)) ? 'Deselect' : 'Select'} all models on {group.label}"
							>
								{group.models.every((m) => selectedSet.has(m.id)) ? 'Deselect all' : 'Select all'}
							</Button>
						{/if}
					</div>
					{#if open}
						<div class="divide-y divide-border/40">
							{#each group.models as m (m.id)}
								{@const checked = selectedSet.has(m.id)}
								<button
									type="button"
									data-nav-item
									role={mode === 'multi' ? 'checkbox' : 'radio'}
									aria-checked={checked}
									{disabled}
									class={cn(
										'flex w-full cursor-pointer items-start gap-2.5 py-2 pl-8 pr-3 text-left transition-colors duration-150 hover:bg-surface-2 focus-visible:bg-surface-2 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-inset focus-visible:ring-ring/40 disabled:cursor-not-allowed disabled:opacity-60',
										checked && 'bg-primary/5'
									)}
									onclick={() => toggle(m.id)}
									onkeydown={navigateList}
								>
									<span
										class={cn(
											'mt-0.5 flex size-4 shrink-0 items-center justify-center border transition-colors duration-150',
											mode === 'multi' ? 'rounded-[4px]' : 'rounded-full',
											checked ? 'border-primary bg-primary text-primary-foreground' : 'border-muted-foreground/60 bg-surface-1'
										)}
										aria-hidden="true"
									>
										{#if checked}
											{#if mode === 'multi'}<Check class="size-3" strokeWidth={3} />{:else}<span class="size-1.5 rounded-full bg-current"></span>{/if}
										{/if}
									</span>
									<span class="min-w-0 flex-1">
										<span class="flex flex-wrap items-baseline gap-x-2">
											<span class="text-sm font-medium text-foreground">{m.model_name}</span>
											<span class="truncate font-mono text-[11px] text-muted-foreground">{isLocalModel(m) && m.served_model_name ? m.served_model_name : m.model_id}</span>
										</span>
										<span class="mt-1 flex flex-wrap items-center gap-1">
											{#if m.supports_function_calling}
												<span class={cn(chip, 'border-border bg-muted text-muted-foreground')} title={isLocalModel(m) ? 'Tool calling (reported by the endpoint)' : 'Native tool calling'}><Wrench class="size-3" aria-hidden="true" />tools</span>
											{/if}
											{#if m.supports_reasoning}
												<span class={cn(chip, 'border-border bg-muted text-muted-foreground')} title="Accepts reasoning parameters"><Brain class="size-3" aria-hidden="true" />reasoning</span>
											{/if}
											{#if isLocalModel(m)}
												{#if m.parameter_size}
													<span class={cn(chip, 'border-border text-foreground')} title="Parameter count">{m.parameter_size}</span>
												{/if}
												{#if m.quantization}
													<span class={cn(chip, 'border-border text-foreground')} title="Quantization">{m.quantization}</span>
												{/if}
												<span class={cn(chip, 'border-border text-foreground')} title="Context window (capped by the endpoint's num_ctx)">{m.context_window_display} ctx</span>
												{#if isLoaded(m)}
													<span class={cn(chip, 'border-transparent bg-primary/15 text-success-fg')} title="Loaded in memory now: no warm-up">
														<span class="status-dot h-1.5 w-1.5" aria-hidden="true"></span>loaded
													</span>
												{/if}
											{:else}
												<span class={cn(chip, 'border-border text-foreground')} title="Context window">{m.context_window_display} ctx</span>
												<span
													class={cn(chip, m.is_free ? 'border-transparent bg-primary/15 text-success-fg' : 'border-border text-foreground')}
													title="Input / output price per 1M tokens">{priceLabel(m)}</span
												>
											{/if}
										</span>
									</span>
								</button>
							{/each}
						</div>
					{/if}
				</div>
			{/each}
		</div>
		<p class="text-[11px] text-muted-foreground">{footerText}</p>
	{/if}
</div>
