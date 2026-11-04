<script lang="ts">
	/**
	 * LevelPicker — the levels of one factor (spec 10.2 step 3, props contract 10.8).
	 *
	 * - `model`: the catalog `ModelPicker` (grouped by endpoint, tool-calling required in app mode); each
	 *   chosen model shows its price per Mtok, the estimated $ per cell (from the plan) and a rate-limit
	 *   warning for `:free` models. Levels are `LLMModel.model_id`s.
	 * - library factors: `LibraryRefPicker` (pinned `slug@v#hash12` refs); `prompt_set` levels get an
	 *   "Edit prompt" action (`oneditprompt(level)`), which forks the prompt set for this study.
	 * - enum factors: one checkbox per value.
	 *
	 * Props: `factor`, `value` (levels), `mode`, `onchange(levels)`, `costByLevel` (level → expected $ per
	 * cell, optional), `oneditprompt`, `disabled`.
	 */
	import { onMount } from 'svelte';
	import type { FactorKey, LibraryItemOut, StudyMode } from '$lib/api/v3/types';
	import type { LLMModelSummary } from '$lib/api/models';
	import { Checkbox } from '$lib/components/ui/checkbox';
	import { Button } from '$lib/components/ui/button';
	import { LibraryRefPicker, PinnedRefBadge } from '$lib/components/library';
	import ModelPicker from '../shared/ModelPicker.svelte';
	import { loadModelCatalog, watchCatalog } from '../shared/catalog';
	import { formatUsd } from '../format';
	import { FACTOR_BY_KEY } from './design';
	import Pencil from '@lucide/svelte/icons/pencil';
	import TriangleAlert from '@lucide/svelte/icons/triangle-alert';
	import X from '@lucide/svelte/icons/x';

	interface Props {
		factor: FactorKey;
		value: string[];
		mode: StudyMode;
		onchange: (levels: string[]) => void;
		costByLevel?: Record<string, number> | null;
		oneditprompt?: (level: string) => void;
		disabled?: boolean;
	}

	let { factor, value, mode, onchange, costByLevel = null, oneditprompt, disabled = false }: Props = $props();

	const def = $derived(FACTOR_BY_KEY[factor]);

	// ── models ──
	let catalog = $state<LLMModelSummary[]>([]);
	let showPicker = $state(false);
	const byModelId = $derived(new Map(catalog.map((m) => [m.model_id, m])));
	let pickerIds = $state(new Set<number>());

	function openPicker() {
		if (!showPicker) {
			pickerIds = new Set(value.map((id) => byModelId.get(id)?.id).filter((id): id is number => id !== undefined));
		}
		showPicker = !showPicker;
	}

	onMount(() => {
		if (FACTOR_BY_KEY[factor]?.source.type !== 'model') return;
		void loadModelCatalog().then((list) => (catalog = list)).catch(() => {});
		return watchCatalog('models', (list) => (catalog = list));
	});

	function onModelIds(ids: Set<number>) {
		const byPk = new Map(catalog.map((m) => [m.id, m.model_id]));
		// Keep levels the catalog does not list (e.g. a model hidden since), in their order.
		const unknown = value.filter((id) => !byModelId.has(id));
		onchange([...unknown, ...[...ids].map((pk) => byPk.get(pk)).filter((id): id is string => !!id)]);
	}

	function remove(level: string) {
		onchange(value.filter((v) => v !== level));
	}

	function toggleEnum(level: string, on: boolean) {
		const values = def.source.type === 'enum' ? def.source.values.map((v) => v.value) : [];
		const set = new Set(value);
		if (on) set.add(level);
		else set.delete(level);
		onchange(values.filter((v) => set.has(v)));
	}

	const isFree = (id: string) => id.endsWith(':free');
	const price = (m: LLMModelSummary | undefined) =>
		m && !m.is_local ? `$${m.input_price_per_million.toFixed(2)} / $${m.output_price_per_million.toFixed(2)} per Mtok` : m?.is_local ? 'local endpoint' : '';
	const libraryFilter = (item: LibraryItemOut) => {
		if (factor === 'prompt_set') return (item.content as { mode?: string }).mode === mode;
		return true;
	};
</script>

{#if def.source.type === 'model'}
	<div class="space-y-1.5">
		{#if value.length > 0}
			<ul class="flex flex-wrap gap-1.5" aria-label="Chosen models">
				{#each value as level (level)}
					{@const m = byModelId.get(level)}
					<li class="inline-flex max-w-full items-center gap-1.5 rounded-md border border-border bg-card px-2 py-1 text-xs">
						<span class="min-w-0">
							<span class="block truncate font-medium" title={level}>{m?.model_name ?? level}</span>
							<span class="block truncate text-muted-foreground">
								{price(m)}{#if costByLevel?.[level] !== undefined} · ≈ {formatUsd(costByLevel[level])} in this experiment{/if}
								{#if !m && catalog.length > 0}<span class="text-warning-fg"> · not in the catalog</span>{/if}
							</span>
							{#if isFree(level)}
								<span class="flex items-center gap-1 text-warning-fg">
									<TriangleAlert class="size-3" aria-hidden="true" /> Free tier: rate-limited (429s), runs may queue for hours
								</span>
							{/if}
						</span>
						{#if !disabled}
							<button type="button" class="cursor-pointer rounded p-0.5 hover:bg-accent" aria-label="Remove {level}" onclick={() => remove(level)}>
								<X class="size-3.5" />
							</button>
						{/if}
					</li>
				{/each}
			</ul>
		{/if}
		{#if !disabled}
			<Button variant="outline" size="sm" aria-expanded={showPicker} onclick={openPicker}>
				{showPicker ? 'Done' : value.length ? 'Change models' : 'Choose models'}
			</Button>
		{/if}
		{#if showPicker}
			<ModelPicker
				mode="multi"
				bind:selectedIds={pickerIds}
				requireTools={mode === 'app'}
				groupBy="endpoint"
				showEndpointStatus
				label=""
				aria-label="Models"
				idPrefix="factor-model"
				onchange={() => onModelIds(pickerIds)}
				{disabled}
			/>
		{/if}
	</div>
{:else if def.source.type === 'library'}
	<div class="space-y-1.5">
		<LibraryRefPicker
			kind={def.source.kind}
			multiple
			{value}
			filter={libraryFilter}
			{onchange}
			{disabled}
			aria-label={def.label}
			placeholder="Default ({mode === 'app' ? 'resolver default' : 'slot default'})"
		/>
		{#if factor === 'prompt_set' && value.length > 0 && oneditprompt}
			<ul class="flex flex-wrap gap-1.5">
				{#each value as level (level)}
					<li class="inline-flex items-center gap-1 text-xs">
						<PinnedRefBadge ref={level} kind="prompt_set" />
						<button
							type="button"
							class="inline-flex cursor-pointer items-center gap-1 rounded px-1 text-primary hover:bg-accent disabled:cursor-not-allowed disabled:opacity-50"
							onclick={() => oneditprompt(level)}
							{disabled}
						>
							<Pencil class="size-3" aria-hidden="true" /> Edit prompt
						</button>
					</li>
				{/each}
			</ul>
		{/if}
	</div>
{:else}
	<fieldset class="flex flex-wrap gap-x-4 gap-y-1.5" aria-label={def.label}>
		{#each def.source.values as option (option.value)}
			<label class="flex items-center gap-2 text-xs">
				<Checkbox checked={value.includes(option.value)} {disabled} onCheckedChange={(v) => toggleEnum(option.value, v === true)} />
				{option.label}
			</label>
		{/each}
	</fieldset>
{/if}
