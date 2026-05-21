<script lang="ts">
	import { Badge } from '$lib/components/ui/badge';
	import { Input } from '$lib/components/ui/input';
	import { Label } from '$lib/components/ui/label';
	import type { LLMModelSummary } from '$lib/api/client';
	import Search from '@lucide/svelte/icons/search';
	import Check from '@lucide/svelte/icons/check';
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
		maxHeight = 'max-h-64',
		onSelectId,
		onToggleId,
		onSelectAll,
		onClearAll,
	}: Props = $props();

	let search = $state('');
	let providerFilter = $state('');
	let freeOnly = $state(false);

	const filtered = $derived(
		models.filter((m) => {
			if (freeOnly && !m.is_free) return false;
			if (providerFilter && m.provider !== providerFilter) return false;
			const q = search.toLowerCase();
			if (!q) return true;
			return (
				m.model_name.toLowerCase().includes(q) ||
				m.provider.toLowerCase().includes(q) ||
				m.model_id.toLowerCase().includes(q)
			);
		}),
	);

	const providers = $derived([...new Set(models.map((m) => m.provider))].sort());

	function formatPrice(n: number): string {
		if (n === 0) return 'Free';
		return `$${n.toFixed(2)}`;
	}

	function selectSingle(id: number | '') {
		selectedId = id;
		onSelectId?.(id);
	}

	function toggleMulti(id: number) {
		const s = new Set(selectedIds);
		if (s.has(id)) s.delete(id);
		else s.add(id);
		selectedIds = s;
		onToggleId?.(id);
	}
</script>

<div class="space-y-2">
	<div class="flex flex-wrap items-center justify-between gap-2">
		<Label class="text-sm">Models</Label>
		{#if mode === 'multi' && onSelectAll && onClearAll}
			<div class="flex gap-1 text-[10px]">
				<button type="button" class="text-primary hover:underline" onclick={onSelectAll}>Select all</button>
				<span class="text-muted-foreground">|</span>
				<button type="button" class="text-muted-foreground hover:underline" onclick={onClearAll}>Clear</button>
				<span class="ml-1 text-muted-foreground">({selectedIds.size})</span>
			</div>
		{/if}
	</div>

	<div class="relative">
		<Search class="absolute left-2.5 top-2.5 h-3.5 w-3.5 text-muted-foreground" />
		<Input bind:value={search} placeholder="Search models…" class="h-8 pl-8 text-xs" />
	</div>

	<div class="flex flex-wrap items-center gap-2">
		<button
			type="button"
			class="rounded-full px-2.5 py-0.5 text-[10px] font-medium transition-colors {!providerFilter ? 'bg-primary text-primary-foreground' : 'bg-muted text-muted-foreground hover:text-foreground'}"
			onclick={() => (providerFilter = '')}
		>
			All
		</button>
		{#each providers as p}
			<button
				type="button"
				class="rounded-full px-2.5 py-0.5 text-[10px] font-medium transition-colors {providerFilter === p ? 'bg-primary text-primary-foreground' : 'bg-muted text-muted-foreground hover:text-foreground'}"
				onclick={() => (providerFilter = providerFilter === p ? '' : p)}
			>
				{p}
			</button>
		{/each}
		<label class="ml-auto flex items-center gap-1.5 text-[10px] cursor-pointer text-muted-foreground">
			<input type="checkbox" bind:checked={freeOnly} class="rounded border-input" />
			Free only
		</label>
	</div>

	<div class="space-y-1 overflow-y-auto rounded-md border p-1 {maxHeight}">
		{#if loading}
			<div class="flex items-center justify-center py-8 text-sm text-muted-foreground">
				<LoaderCircle class="mr-2 h-4 w-4 animate-spin" /> Loading models…
			</div>
		{:else if mode === 'single' && allowEmpty}
			<button
				type="button"
				class="flex w-full items-center gap-2 rounded-md px-2.5 py-2 text-left text-sm transition-colors hover:bg-muted/50 {selectedId === '' ? 'bg-primary/5 ring-1 ring-primary/20' : ''}"
				onclick={() => selectSingle('')}
			>
				<div
					class="flex h-4 w-4 shrink-0 items-center justify-center rounded-full border {selectedId === '' ? 'bg-primary border-primary text-primary-foreground' : 'border-border'}"
				>
					{#if selectedId === ''}<Check class="h-3 w-3" />{/if}
				</div>
				<span class="text-muted-foreground italic">{emptyLabel}</span>
			</button>
		{/if}
		{#each filtered as m}
			{#if mode === 'single'}
				<button
					type="button"
					class="flex w-full items-start gap-2 rounded-md px-2.5 py-2 text-left text-sm transition-colors hover:bg-muted/50 {selectedId === m.id ? 'bg-primary/5 ring-1 ring-primary/20' : ''}"
					onclick={() => selectSingle(m.id)}
				>
					<div
						class="mt-0.5 flex h-4 w-4 shrink-0 items-center justify-center rounded-full border {selectedId === m.id ? 'bg-primary border-primary text-primary-foreground' : 'border-border'}"
					>
						{#if selectedId === m.id}<Check class="h-3 w-3" />{/if}
					</div>
					<div class="min-w-0 flex-1">
						<div class="flex flex-wrap items-center gap-1.5">
							<span class="font-medium">{m.model_name}</span>
							<Badge variant="outline" class="text-[10px]">{m.provider}</Badge>
							{#if m.is_free}
								<Badge variant="secondary" class="text-[10px]">Free</Badge>
							{/if}
						</div>
						<div class="mt-0.5 text-[10px] text-muted-foreground">
							{m.context_window_display} · in {formatPrice(m.input_price_per_million)} / out{' '}
							{formatPrice(m.output_price_per_million)} per 1M
						</div>
					</div>
				</button>
			{:else}
				<button
					type="button"
					class="flex w-full items-start gap-2 rounded-md px-2.5 py-2 text-left text-sm transition-colors hover:bg-muted/50 {selectedIds.has(m.id) ? 'bg-primary/5 ring-1 ring-primary/20' : ''}"
					onclick={() => toggleMulti(m.id)}
				>
					<div
						class="mt-0.5 flex h-4 w-4 shrink-0 items-center justify-center rounded border {selectedIds.has(m.id) ? 'bg-primary border-primary text-primary-foreground' : 'border-border'}"
					>
						{#if selectedIds.has(m.id)}<Check class="h-3 w-3" />{/if}
					</div>
					<div class="min-w-0 flex-1">
						<div class="flex flex-wrap items-center gap-1.5">
							<span class="font-medium">{m.model_name}</span>
							{#if m.is_free}
								<Badge variant="secondary" class="text-[10px]">Free</Badge>
							{:else}
								<span class="ml-auto font-mono text-[10px] text-muted-foreground"
									>${m.input_price_per_million}</span
								>
							{/if}
						</div>
					</div>
				</button>
			{/if}
		{/each}
		{#if !loading && filtered.length === 0}
			<p class="py-6 text-center text-xs text-muted-foreground">No models match your filters.</p>
		{/if}
	</div>
</div>
