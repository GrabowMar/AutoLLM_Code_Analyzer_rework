<script lang="ts">
	import { formatPrice } from '$lib/utils/formatters';
	import { Badge } from '$lib/components/ui/badge';
	import { Input } from '$lib/components/ui/input';
	import { Label } from '$lib/components/ui/label';
	import type { LLMModelSummary } from '$lib/api/client';
	import Search from '@lucide/svelte/icons/search';
	import Check from '@lucide/svelte/icons/check';
	import ChevronRight from '@lucide/svelte/icons/chevron-right';
	import LoaderCircle from '@lucide/svelte/icons/loader-circle';

	interface Props {
		models: LLMModelSummary[];
		loading?: boolean;
		mode: 'single' | 'multi';
		/** Single-select: selected model DB id, or '' for none */
		selectedId?: number | '';
		/** Multi-select: set of model DB ids */
		selectedIds?: Set<number>;
		allowEmpty?: boolean;
		emptyLabel?: string;
		maxHeight?: string;
		onSelectId?: (id: number | '') => void;
		onToggleId?: (id: number) => void;
		onSelectAll?: () => void;
		onClearAll?: () => void;
	}

	let {
		models,
		loading = false,
		mode,
		selectedId = $bindable<number | ''>(''),
		selectedIds = $bindable(new Set<number>()),
		allowEmpty = false,
		emptyLabel = 'Auto-select (recommended)',
		maxHeight = 'max-h-72',
		onSelectId,
		onToggleId,
		onSelectAll,
		onClearAll,
	}: Props = $props();

	let search = $state('');
	let freeOnly = $state(false);
	// Which provider accordions are open (multi-mode only)
	let expandedProviders = $state<Set<string>>(new Set());

	// Models grouped by provider, filtered by search + freeOnly
	const providerGroups = $derived.by(() => {
		const q = search.toLowerCase();
		const groups = new Map<string, LLMModelSummary[]>();
		for (const m of models) {
			if (freeOnly && !m.is_free) continue;
			if (q) {
				const hit =
					m.model_name.toLowerCase().includes(q) ||
					m.provider.toLowerCase().includes(q) ||
					m.model_id.toLowerCase().includes(q);
				if (!hit) continue;
			}
			if (!groups.has(m.provider)) groups.set(m.provider, []);
			groups.get(m.provider)!.push(m);
		}
		// Alphabetical provider order
		return new Map([...groups.entries()].sort(([a], [b]) => a.localeCompare(b)));
	});

	// How many selected models each provider has (multi-mode)
	const selectedPerProvider = $derived.by(() => {
		const counts = new Map<string, number>();
		if (mode !== 'multi') return counts;
		for (const m of models) {
			if (selectedIds.has(m.id))
				counts.set(m.provider, (counts.get(m.provider) ?? 0) + 1);
		}
		return counts;
	});

	// A provider is visible-expanded when searching (all groups shown) or explicitly toggled
	function isExpanded(provider: string): boolean {
		return search.length > 0 || expandedProviders.has(provider);
	}

	function toggleProvider(provider: string) {
		const next = new Set(expandedProviders);
		if (next.has(provider)) next.delete(provider);
		else next.add(provider);
		expandedProviders = next;
	}

	function selectSingle(id: number | '') {
		selectedId = id;
		onSelectId?.(id);
	}

	function toggleMulti(id: number, provider?: string) {
		const s = new Set(selectedIds);
		if (s.has(id)) {
			s.delete(id);
		} else {
			s.add(id);
			// Auto-expand the provider group when a model inside it is selected
			if (provider && !expandedProviders.has(provider)) {
				const next = new Set(expandedProviders);
				next.add(provider);
				expandedProviders = next;
			}
		}
		selectedIds = s;
		onToggleId?.(id);
	}

	$effect(() => {
		if (mode === 'single' && selectedId && models.length > 0) {
			const activeModel = models.find(m => m.id === selectedId);
			if (activeModel && !expandedProviders.has(activeModel.provider)) {
				const next = new Set(expandedProviders);
				next.add(activeModel.provider);
				expandedProviders = next;
			}
		}
	});
</script>

<div class="space-y-2">
	<div class="flex flex-wrap items-center justify-between gap-2">
		<Label class="text-xs uppercase tracking-wide text-muted-foreground">Models</Label>
		{#if mode === 'multi' && onSelectAll && onClearAll}
			<div class="flex gap-1 text-[10px]">
				<button type="button" class="text-primary hover:underline cursor-pointer" onclick={onSelectAll}>Select all</button>
				<span class="text-muted-foreground">|</span>
				<button type="button" class="text-muted-foreground hover:underline cursor-pointer" onclick={onClearAll}>Clear</button>
				<span class="ml-1 text-muted-foreground">({selectedIds.size})</span>
			</div>
		{/if}
	</div>

	<!-- Search + free-only in one row -->
	<div class="flex items-center gap-2">
		<div class="relative flex-1">
			<Search class="absolute left-2.5 top-2 h-3.5 w-3.5 text-muted-foreground" />
			<Input bind:value={search} placeholder="Search models…" class="h-8 pl-8 text-xs transition-all hover:border-primary/45 focus-visible:ring-primary/20" />
		</div>
		<label class="flex shrink-0 cursor-pointer items-center gap-1.5 text-[10px] text-muted-foreground">
			<input type="checkbox" bind:checked={freeOnly} class="rounded border-input cursor-pointer" />
			Free only
		</label>
	</div>

	{#if loading}
		<div class="flex items-center justify-center rounded-md border py-8 text-sm text-muted-foreground">
			<LoaderCircle class="mr-2 h-4 w-4 animate-spin text-primary" /> Loading models…
		</div>
	{:else if providerGroups.size === 0}
		<div class="rounded-md border py-6 text-center text-xs text-muted-foreground">
			No models match your filters.
		</div>
	{:else if mode === 'multi'}
		<!-- Accordion grouped by provider (Multi-Select) -->
		<div class="overflow-y-auto rounded-md border divide-y divide-border/50 {maxHeight}">
			{#each [...providerGroups.entries()] as [provider, providerModels]}
				{@const selCount = selectedPerProvider.get(provider) ?? 0}
				{@const expanded = isExpanded(provider)}
				<div>
					<!-- Provider header — click to expand/collapse -->
					<button
						type="button"
						class="flex w-full items-center gap-2 bg-muted/20 px-3 py-1.5 text-left text-xs font-medium transition-colors hover:bg-muted/40 cursor-pointer {selCount > 0 ? 'text-foreground font-semibold bg-primary/[0.02]' : 'text-muted-foreground'}"
						onclick={() => toggleProvider(provider)}
					>
						<ChevronRight class="h-3 w-3 shrink-0 transition-transform duration-150 {expanded ? 'rotate-90' : ''}" />
						<span class="min-w-0 flex-1 truncate">{provider}</span>
						{#if selCount > 0}
							<Badge variant="secondary" class="shrink-0 text-[9px] px-1.5 py-0 bg-primary/10 text-primary border-primary/20">{selCount}/{providerModels.length}</Badge>
						{:else}
							<span class="shrink-0 tabular-nums text-[10px] opacity-40">{providerModels.length}</span>
						{/if}
					</button>

					<!-- Models in this group -->
					{#if expanded}
						<div class="divide-y divide-border/30">
							{#each providerModels as m}
								<button
									type="button"
									class="flex w-full items-start gap-2 pl-7 pr-3 py-2 text-left text-xs transition-colors hover:bg-muted/40 cursor-pointer {selectedIds.has(m.id) ? 'bg-primary/5 text-foreground font-semibold' : 'text-muted-foreground'}"
									onclick={() => toggleMulti(m.id, provider)}
								>
									<!-- Checkbox indicator -->
									<div class="mt-0.5 flex h-3.5 w-3.5 shrink-0 items-center justify-center rounded border transition-colors {selectedIds.has(m.id) ? 'bg-primary border-primary text-primary-foreground' : 'border-muted-foreground/30'}">
										{#if selectedIds.has(m.id)}<Check class="h-2.5 w-2.5" />{/if}
									</div>
									<div class="min-w-0 flex-1">
										<div class="flex flex-wrap items-center gap-1.5">
											<span class="font-medium text-foreground">{m.model_name}</span>
											{#if m.is_free}
												<Badge variant="secondary" class="text-[9px] px-1 py-0 scale-90">Free</Badge>
											{/if}
										</div>
										<div class="mt-0.5 text-[9px] text-muted-foreground font-mono">
											{m.context_window_display} · in {formatPrice(m.input_price_per_million)} / out {formatPrice(m.output_price_per_million)} per 1M
										</div>
									</div>
								</button>
							{/each}
						</div>
					{/if}
				</div>
			{/each}
		</div>
	{:else}
		<!-- Accordion grouped by provider (Single-Select) -->
		<div class="overflow-y-auto rounded-md border divide-y divide-border/50 {maxHeight}">
			{#if allowEmpty}
				<button
					type="button"
					class="flex w-full items-center gap-2 border-b px-3 py-2 text-left text-xs transition-colors hover:bg-muted/40 cursor-pointer {selectedId === '' ? 'bg-primary/5 text-foreground font-semibold' : 'text-muted-foreground'}"
					onclick={() => selectSingle('')}
				>
					<div class="flex h-3.5 w-3.5 shrink-0 items-center justify-center rounded-full border transition-colors {selectedId === '' ? 'bg-primary border-primary text-primary-foreground' : 'border-muted-foreground/30'}">
						{#if selectedId === ''}<div class="h-1.5 w-1.5 rounded-full bg-current"></div>{/if}
					</div>
					<span class="italic font-medium">{emptyLabel}</span>
				</button>
			{/if}
			{#each [...providerGroups.entries()] as [provider, providerModels]}
				{@const hasActive = providerModels.some(m => m.id === selectedId)}
				{@const expanded = isExpanded(provider)}
				<div>
					<!-- Provider header — click to expand/collapse -->
					<button
						type="button"
						class="flex w-full items-center gap-2 bg-muted/20 px-3 py-1.5 text-left text-xs font-medium transition-colors hover:bg-muted/40 cursor-pointer {hasActive ? 'text-foreground font-semibold bg-primary/[0.02]' : 'text-muted-foreground'}"
						onclick={() => toggleProvider(provider)}
					>
						<ChevronRight class="h-3 w-3 shrink-0 transition-transform duration-150 {expanded ? 'rotate-90' : ''}" />
						<span class="min-w-0 flex-1 truncate">{provider}</span>
						{#if hasActive}
							<Badge variant="secondary" class="shrink-0 text-[9px] px-1.5 py-0 bg-primary/10 text-primary border-primary/20">Selected</Badge>
						{:else}
							<span class="shrink-0 tabular-nums text-[10px] opacity-40">{providerModels.length}</span>
						{/if}
					</button>

					<!-- Models in this group -->
					{#if expanded}
						<div class="divide-y divide-border/30">
							{#each providerModels as m}
								<button
									type="button"
									class="flex w-full items-start gap-2 pl-7 pr-3 py-2 text-left text-xs transition-colors hover:bg-muted/40 cursor-pointer {selectedId === m.id ? 'bg-primary/5 text-foreground font-semibold' : 'text-muted-foreground'}"
									onclick={() => selectSingle(m.id)}
								>
									<!-- Circular radio indicator -->
									<div class="mt-0.5 flex h-3.5 w-3.5 shrink-0 items-center justify-center rounded-full border transition-colors {selectedId === m.id ? 'bg-primary border-primary text-primary-foreground' : 'border-muted-foreground/30'}">
										{#if selectedId === m.id}
											<div class="h-1.5 w-1.5 rounded-full bg-current"></div>
										{/if}
									</div>
									<div class="min-w-0 flex-1">
										<div class="flex flex-wrap items-center gap-1.5">
											<span class="font-medium text-foreground">{m.model_name}</span>
											{#if m.is_free}
												<Badge variant="secondary" class="text-[9px] px-1 py-0 scale-90">Free</Badge>
											{/if}
										</div>
										<div class="mt-0.5 text-[9px] text-muted-foreground font-mono">
											{m.context_window_display} · in {formatPrice(m.input_price_per_million)} / out {formatPrice(m.output_price_per_million)} per 1M
										</div>
									</div>
								</button>
							{/each}
						</div>
					{/if}
				</div>
			{/each}
		</div>
	{/if}
</div>
