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
	 *
	 * Search matches name, provider and id. Rows show tools / reasoning / context /
	 * price badges. Keyboard: Tab into the list, ↑ ↓ Home End move, Space toggles.
	 */
	import { onMount } from 'svelte';
	import type { LLMModelSummary } from '$lib/api/models';
	import { formatApiError } from '$lib/api/core';
	import { formatPrice } from '$lib/utils/formatters';
	import { cn } from '$lib/utils';
	import { Button } from '$lib/components/ui/button';
	import { Checkbox } from '$lib/components/ui/checkbox';
	import { Input } from '$lib/components/ui/input';
	import EmptyState from '$lib/components/EmptyState.svelte';
	import ErrorAlert from '$lib/components/ErrorAlert.svelte';
	import LoadingState from '$lib/components/LoadingState.svelte';
	import ProviderIcon from '$lib/components/ProviderIcon.svelte';
	import { loadModelCatalog, modelIneligibility } from './catalog';
	import { navigateList } from './list-keyboard';
	import Brain from '@lucide/svelte/icons/brain';
	import Check from '@lucide/svelte/icons/check';
	import ChevronRight from '@lucide/svelte/icons/chevron-right';
	import Search from '@lucide/svelte/icons/search';
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
	}: Props = $props();

	// Bulk "select shown" is offered only for small, deliberate result sets.
	const SELECT_SHOWN_LIMIT = 50;
	// Badge look as a <span>: rows are <button>s, which may only hold phrasing content.
	const chip = 'inline-flex items-center gap-1 rounded-full border px-2 py-0.5 text-[11px] font-medium leading-4 tabular-nums';

	let models = $state<LLMModelSummary[]>([]);
	let loading = $state(true);
	let error = $state('');
	let search = $state('');
	let freeOnly = $state(false);
	let expanded = $state(new Set<string>());

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
	const hiddenCount = $derived(models.length - eligible.length);
	const query = $derived(search.trim().toLowerCase());
	const filtered = $derived(
		eligible.filter((m) => {
			if (freeOnly && !m.is_free) return false;
			if (!query) return true;
			return (
				m.model_name.toLowerCase().includes(query) ||
				m.provider.toLowerCase().includes(query) ||
				m.model_id.toLowerCase().includes(query) ||
				m.canonical_slug.toLowerCase().includes(query)
			);
		})
	);
	const groups = $derived.by(() => {
		const map = new Map<string, LLMModelSummary[]>();
		for (const m of filtered) {
			const list = map.get(m.provider);
			if (list) list.push(m);
			else map.set(m.provider, [m]);
		}
		return [...map.entries()]
			.sort(([a], [b]) => a.localeCompare(b))
			.map(([provider, list]) => ({
				provider,
				models: list.sort((a, b) => a.model_name.localeCompare(b.model_name)),
				selectedCount: list.filter((m) => selectedSet.has(m.id)).length,
			}));
	});
	const filtering = $derived(query.length > 0 || freeOnly);
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
			(hiddenCount > 0 ? ` · ${hiddenCount} hidden (no tool calling)` : '')
	);

	async function load(force = false) {
		loading = true;
		error = '';
		try {
			const list = await loadModelCatalog({ force });
			models = list;
			// Open the groups that already hold a selection.
			const ids = new Set(mode === 'multi' ? selectedIds : selectedId != null ? [selectedId] : []);
			expanded = new Set(list.filter((m) => ids.has(m.id)).map((m) => m.provider));
		} catch (e) {
			error = formatApiError(e, 'Could not load the model catalog.');
		} finally {
			loading = false;
		}
	}

	onMount(() => {
		void load();
	});

	function isExpanded(provider: string): boolean {
		return query.length > 0 || expanded.has(provider);
	}

	function toggleGroup(provider: string) {
		const next = new Set(expanded);
		if (next.has(provider)) next.delete(provider);
		else next.add(provider);
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
		<label class="flex shrink-0 cursor-pointer items-center gap-1.5 text-xs text-muted-foreground">
			<Checkbox bind:checked={freeOnly} {disabled} />
			Free only
		</label>
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
				? 'Sync models from OpenRouter on the Models page.'
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
			{#each groups as group (group.provider)}
				{@const open = isExpanded(group.provider)}
				<div class="border-b border-border/60 last:border-b-0">
					<button
						type="button"
						data-nav-item
						class={cn(
							'sticky top-0 z-[1] flex w-full cursor-pointer items-center gap-2 bg-surface-2 px-3 py-1.5 text-left text-xs transition-colors duration-150 hover:bg-surface-3 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-inset focus-visible:ring-ring/40',
							group.selectedCount > 0 ? 'font-semibold text-foreground' : 'font-medium text-muted-foreground'
						)}
						aria-expanded={open}
						onclick={() => toggleGroup(group.provider)}
						onkeydown={navigateList}
					>
						<ChevronRight
							class={cn('size-3.5 shrink-0 transition-transform duration-150', open && 'rotate-90')}
							aria-hidden="true"
						/>
						<ProviderIcon provider={group.provider} size={14} />
						<span class="min-w-0 flex-1 truncate">{group.provider}</span>
						{#if group.selectedCount > 0}
							<span class={cn(chip, 'border-transparent bg-primary/15 text-primary')}>{group.selectedCount}/{group.models.length}</span>
						{:else}
							<span class="tabular-nums text-[11px] text-muted-foreground">{group.models.length}</span>
						{/if}
					</button>
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
											<span class="truncate font-mono text-[11px] text-muted-foreground">{m.model_id}</span>
										</span>
										<span class="mt-1 flex flex-wrap items-center gap-1">
											{#if m.supports_function_calling}
												<span class={cn(chip, 'border-border bg-muted text-muted-foreground')} title="Native tool calling"><Wrench class="size-3" aria-hidden="true" />tools</span>
											{/if}
											{#if m.supports_reasoning}
												<span class={cn(chip, 'border-border bg-muted text-muted-foreground')} title="Accepts reasoning parameters"><Brain class="size-3" aria-hidden="true" />reasoning</span>
											{/if}
											<span class={cn(chip, 'border-border text-foreground')} title="Context window">{m.context_window_display} ctx</span>
											<span
												class={cn(chip, m.is_free ? 'border-transparent bg-primary/15 text-primary' : 'border-border text-foreground')}
												title="Input / output price per 1M tokens">{priceLabel(m)}</span
											>
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
