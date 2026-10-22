<script lang="ts">
	/**
	 * AppSpecPicker — multi-select of app specs (the graded tasks).
	 *
	 * Props:
	 * - `selectedIds` (bindable `Set<number>`): spec pks. Replaced, never mutated.
	 * - `specs`: preloaded list (otherwise `loadAppSpecCatalog()`, cached per page).
	 * - `maxHeight`: Tailwind max-height class of the list (default 'max-h-72').
	 * - `label` (default 'App specs'; '' hides it — then pass `aria-label`),
	 *   `aria-label`, `idPrefix` (default 'app-spec-picker'), `disabled`.
	 * - `onchange()` after every user change to the selection.
	 *
	 * Search (name, slug, category, description), category chips, difficulty
	 * filter, "Select shown" / "Clear", and counts. Keyboard: ↑ ↓ Home End in the list.
	 */
	import { onMount } from 'svelte';
	import type { AppRequirementTemplate } from '$lib/api/generation';
	import { formatApiError } from '$lib/api/core';
	import { cn } from '$lib/utils';
	import { Button } from '$lib/components/ui/button';
	import { Input } from '$lib/components/ui/input';
	import { Segmented } from '$lib/components/ui/segmented';
	import EmptyState from '$lib/components/EmptyState.svelte';
	import ErrorAlert from '$lib/components/ErrorAlert.svelte';
	import LoadingState from '$lib/components/LoadingState.svelte';
	import { loadAppSpecCatalog } from './catalog';
	import { navigateList } from './list-keyboard';
	import Check from '@lucide/svelte/icons/check';
	import Search from '@lucide/svelte/icons/search';
	import TriangleAlert from '@lucide/svelte/icons/triangle-alert';

	interface Props {
		selectedIds?: Set<number>;
		specs?: AppRequirementTemplate[];
		maxHeight?: string;
		label?: string;
		'aria-label'?: string;
		idPrefix?: string;
		disabled?: boolean;
		onchange?: () => void;
	}

	let {
		selectedIds = $bindable(new Set<number>()),
		specs: providedSpecs,
		maxHeight = 'max-h-72',
		label = 'App specs',
		'aria-label': ariaLabel,
		idPrefix = 'app-spec-picker',
		disabled = false,
		onchange,
	}: Props = $props();

	const DIFFICULTY_ORDER = ['basic', 'standard', 'advanced'];

	let fetched = $state<AppRequirementTemplate[]>([]);
	let loading = $state(false);
	let error = $state('');
	let search = $state('');
	let category = $state('');
	let difficulty = $state('all');

	const specs = $derived(providedSpecs ?? fetched);
	const byId = $derived(new Map(specs.map((s) => [s.id, s])));
	const missingIds = $derived(loading || error ? [] : [...selectedIds].filter((id) => !byId.has(id)));

	const categories = $derived.by(() => {
		const counts = new Map<string, number>();
		for (const s of specs) {
			const key = s.category || 'other';
			counts.set(key, (counts.get(key) ?? 0) + 1);
		}
		return [...counts.entries()].sort(([a], [b]) => a.localeCompare(b));
	});
	const difficulties = $derived(
		DIFFICULTY_ORDER.filter((d) => specs.some((s) => s.difficulty === d))
	);
	const difficultyItems = $derived([
		{ value: 'all', label: 'Any difficulty' },
		...difficulties.map((d) => ({ value: d, label: d[0].toUpperCase() + d.slice(1) })),
	]);

	const query = $derived(search.trim().toLowerCase());
	const filtered = $derived(
		specs
			.filter((s) => {
				if (category && (s.category || 'other') !== category) return false;
				if (difficulty !== 'all' && s.difficulty !== difficulty) return false;
				if (!query) return true;
				return (
					s.name.toLowerCase().includes(query) ||
					s.slug.toLowerCase().includes(query) ||
					(s.category ?? '').toLowerCase().includes(query) ||
					(s.description ?? '').toLowerCase().includes(query)
				);
			})
			.sort((a, b) => a.name.localeCompare(b.name))
	);
	const filtering = $derived(query.length > 0 || category !== '' || difficulty !== 'all');
	const selectedCount = $derived([...selectedIds].filter((id) => byId.has(id)).length);
	const hiddenSelected = $derived(
		filtering ? selectedCount - filtered.filter((s) => selectedIds.has(s.id)).length : 0
	);
	const unselectedShown = $derived(filtered.filter((s) => !selectedIds.has(s.id)).length);
	const headingId = $derived(`${idPrefix}-heading`);
	const countText = $derived(
		`${selectedCount} of ${specs.length} selected` +
			(hiddenSelected > 0 ? ` (${hiddenSelected} hidden by filters)` : '')
	);

	async function load() {
		const retry = !!error;
		loading = true;
		error = '';
		try {
			fetched = await loadAppSpecCatalog({ force: retry });
		} catch (e) {
			error = formatApiError(e, 'Could not load app specs.');
		} finally {
			loading = false;
		}
	}

	onMount(() => {
		if (!providedSpecs) void load();
	});

	function commit(next: Set<number>) {
		selectedIds = next;
		onchange?.();
	}

	function toggle(id: number) {
		if (disabled) return;
		const next = new Set(selectedIds);
		if (next.has(id)) next.delete(id);
		else next.add(id);
		commit(next);
	}

	function selectShown() {
		const next = new Set(selectedIds);
		for (const s of filtered) next.add(s.id);
		commit(next);
	}

	function clearAll() {
		commit(new Set());
	}

	function removeMissing() {
		const next = new Set(selectedIds);
		for (const id of missingIds) next.delete(id);
		commit(next);
	}

	const chip = 'rounded-full border px-1.5 text-[11px] font-medium leading-4';
</script>

<div class="space-y-2" data-slot="app-spec-picker">
	<div class="flex flex-wrap items-center justify-between gap-2">
		{#if label}<span id={headingId} class="text-sm font-medium">{label}</span>{/if}
		<div class="ml-auto flex items-center gap-2 text-xs text-muted-foreground">
			<span class="tabular-nums" aria-live="polite">{countText}</span>
			{#if unselectedShown > 0}
				<Button variant="ghost" size="sm" class="h-7 px-2" onclick={selectShown} {disabled}>
					{filtering ? `Select ${filtered.length} shown` : 'Select all'}
				</Button>
			{/if}
			{#if selectedIds.size > 0}
				<Button variant="ghost" size="sm" class="h-7 px-2" onclick={clearAll} {disabled}>Clear</Button>
			{/if}
		</div>
	</div>

	{#if missingIds.length > 0}
		<div class="flex flex-wrap items-center gap-2 rounded-md border border-warning/30 bg-warning/10 px-3 py-2 text-sm" role="alert">
			<TriangleAlert class="size-4 shrink-0 text-warning-fg" aria-hidden="true" />
			<span class="min-w-0 flex-1 text-warning-fg">
				{missingIds.length === 1 ? '1 selected spec no longer exists' : `${missingIds.length} selected specs no longer exist`}
			</span>
			<Button variant="outline" size="sm" onclick={removeMissing} {disabled}>Remove</Button>
		</div>
	{/if}

	<div class="flex flex-wrap items-center gap-2">
		<div class="relative min-w-[12rem] flex-1">
			<Search class="pointer-events-none absolute left-2.5 top-2.5 size-3.5 text-muted-foreground" aria-hidden="true" />
			<Input
				bind:value={search}
				type="search"
				placeholder="Search specs…"
				aria-label="Search app specs"
				class="h-8 pl-8 text-xs"
				{disabled}
			/>
		</div>
		{#if difficulties.length > 0}
			<Segmented aria-label="Difficulty" size="sm" bind:value={difficulty} items={difficultyItems} {disabled} />
		{/if}
	</div>

	{#if categories.length > 1}
		<div class="flex flex-wrap gap-1.5" role="group" aria-label="Filter by category">
			<button
				type="button"
				class={cn(
					'cursor-pointer rounded-full border px-2.5 py-0.5 text-xs font-medium transition-colors duration-150 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring/40',
					category === '' ? 'border-primary/40 bg-primary/10 text-primary' : 'border-border text-muted-foreground hover:text-foreground'
				)}
				aria-pressed={category === ''}
				{disabled}
				onclick={() => (category = '')}
			>
				All <span class="tabular-nums opacity-70">{specs.length}</span>
			</button>
			{#each categories as [name, count] (name)}
				<button
					type="button"
					class={cn(
						'cursor-pointer rounded-full border px-2.5 py-0.5 text-xs font-medium transition-colors duration-150 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring/40',
						category === name ? 'border-primary/40 bg-primary/10 text-primary' : 'border-border text-muted-foreground hover:text-foreground'
					)}
					aria-pressed={category === name}
					{disabled}
					onclick={() => (category = category === name ? '' : name)}
				>
					{name} <span class="tabular-nums opacity-70">{count}</span>
				</button>
			{/each}
		</div>
	{/if}

	{#if loading}
		<LoadingState label="loading app specs" class="rounded-md border border-border py-8" />
	{:else if error}
		<ErrorAlert message={error} onretry={load} />
	{:else if filtered.length === 0}
		<EmptyState
			compact
			class="rounded-md border border-border"
			title={specs.length === 0 ? 'No app specs yet' : 'No specs match'}
			description={specs.length === 0
				? 'Create app specs in the Library.'
				: 'Clear the search or the category / difficulty filters.'}
		/>
	{:else}
		<div
			class={cn('divide-y divide-border/50 overflow-y-auto rounded-md border border-border', maxHeight)}
			role="group"
			data-nav-list
			aria-labelledby={label ? headingId : undefined}
			aria-label={label ? undefined : (ariaLabel ?? 'App specs')}
		>
			{#each filtered as spec (spec.id)}
				{@const checked = selectedIds.has(spec.id)}
				<button
					type="button"
					role="checkbox"
					aria-checked={checked}
					data-nav-item
					{disabled}
					class={cn(
						'flex w-full cursor-pointer items-start gap-2.5 px-3 py-2 text-left transition-colors duration-150 hover:bg-surface-2 focus-visible:bg-surface-2 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-inset focus-visible:ring-ring/40 disabled:cursor-not-allowed disabled:opacity-60',
						checked && 'bg-primary/5'
					)}
					onclick={() => toggle(spec.id)}
					onkeydown={navigateList}
				>
					<span
						class={cn(
							'mt-0.5 flex size-4 shrink-0 items-center justify-center rounded-[4px] border transition-colors duration-150',
							checked ? 'border-primary bg-primary text-primary-foreground' : 'border-muted-foreground/60 bg-surface-1'
						)}
						aria-hidden="true"
					>
						{#if checked}<Check class="size-3" strokeWidth={3} />{/if}
					</span>
					<span class="min-w-0 flex-1">
						<span class="flex flex-wrap items-center gap-1.5">
							<span class="text-sm font-medium text-foreground">{spec.name}</span>
							{#if spec.category}
								<span class={cn(chip, 'border-border bg-muted text-muted-foreground')}>{spec.category}</span>
							{/if}
							{#if spec.difficulty}
								<span class={cn(chip, 'border-border text-muted-foreground')}>{spec.difficulty}</span>
							{/if}
							{#if !spec.is_default}
								<span class={cn(chip, 'border-border text-muted-foreground')}>yours</span>
							{/if}
						</span>
						{#if spec.description}
							<span class="mt-0.5 line-clamp-1 text-xs text-muted-foreground">{spec.description}</span>
						{/if}
					</span>
				</button>
			{/each}
		</div>
	{/if}
</div>
