<script lang="ts">
	/**
	 * ModelsStep — the `model` factor as its own step: the catalog picker always open (grouped by endpoint,
	 * tool calling required in app mode) beside the chosen models, each with where it runs, its price,
	 * its expected cost in this experiment (from the fresh plan) and a rate-limit warning for `:free`
	 * models. Levels are `LLMModel.model_id`s; levels the catalog no longer lists are kept and flagged.
	 *
	 * The picker's selection is re-derived from `value` on every change, so removing a chosen model on
	 * the right also unticks it in the list (and a later tick cannot bring it back).
	 *
	 * Props: `value` (levels), `mode`, `costByLevel` (model id → expected USD, fresh plans only),
	 * `disabled`, `onchange(levels)`.
	 */
	import { onMount } from 'svelte';
	import type { LLMModelSummary } from '$lib/api/models';
	import type { StudyMode } from '$lib/api/v3/types';
	import { Button } from '$lib/components/ui/button';
	import { toast } from 'svelte-sonner';
	import ProviderIcon from '$lib/components/ProviderIcon.svelte';
	import ModelPicker from '../../shared/ModelPicker.svelte';
	import { isLocalModel, loadModelCatalog, watchCatalog } from '../../shared/catalog';
	import { formatUsd } from '../../format';
	import Server from '@lucide/svelte/icons/server';
	import TriangleAlert from '@lucide/svelte/icons/triangle-alert';
	import X from '@lucide/svelte/icons/x';

	interface Props {
		value: string[];
		mode: StudyMode;
		costByLevel?: Record<string, number> | null;
		disabled?: boolean;
		onchange: (levels: string[]) => void;
	}

	let { value, mode, costByLevel = null, disabled = false, onchange }: Props = $props();

	let catalog = $state<LLMModelSummary[]>([]);
	let loaded = $state(false);
	const byModelId = $derived(new Map(catalog.map((m) => [m.model_id, m])));

	onMount(() => {
		void loadModelCatalog()
			.then((list) => (catalog = list))
			.catch(() => {})
			.finally(() => (loaded = true));
		return watchCatalog('models', (list) => (catalog = list));
	});

	// The picker works on pks; keep it in step with `value` (the source of truth).
	let pickerIds = $state(new Set<number>());
	$effect(() => {
		pickerIds = new Set(value.map((id) => byModelId.get(id)?.id).filter((id): id is number => id !== undefined));
	});

	function fromPicker() {
		const byPk = new Map(catalog.map((m) => [m.id, m.model_id]));
		// Keep levels the catalog does not list (e.g. a model hidden since), in their order.
		const unknown = value.filter((id) => !byModelId.has(id));
		const kept = value.filter((id) => byModelId.has(id) && pickerIds.has(byModelId.get(id)!.id));
		const added = [...pickerIds].map((pk) => byPk.get(pk)).filter((id): id is string => !!id && !value.includes(id));
		onchange([...unknown, ...kept, ...added]);
	}

	function remove(level: string) {
		onchange(value.filter((v) => v !== level));
	}

	// Bulk clear is undoable (a toast with Undo) rather than confirmed up front.
	function clearAll() {
		const before = [...value];
		onchange([]);
		toast(`Cleared ${before.length} model${before.length === 1 ? '' : 's'}`, {
			action: { label: 'Undo', onClick: () => onchange(before) },
		});
	}

	const isFree = (id: string) => id.endsWith(':free');
	const totalCost = $derived(costByLevel ? value.reduce((s, id) => s + (costByLevel?.[id] ?? 0), 0) : null);
	const localCount = $derived(value.filter((id) => {
		const m = byModelId.get(id);
		return m ? isLocalModel(m) : false;
	}).length);

	function where(m: LLMModelSummary | undefined): string {
		if (!m) return '';
		if (isLocalModel(m)) return m.endpoint?.name || m.endpoint?.slug || 'local endpoint';
		return 'OpenRouter';
	}
	const detail = (m: LLMModelSummary | undefined) => [where(m), price(m)].filter(Boolean).join(' · ');
	function price(m: LLMModelSummary | undefined): string {
		if (!m || isLocalModel(m)) return m ? 'no API cost' : '';
		if (m.is_free) return 'free tier';
		return `$${m.input_price_per_million.toFixed(2)} in · $${m.output_price_per_million.toFixed(2)} out /Mtok`;
	}
</script>

<div class="ms">
	<!-- The chosen models: a full-width tray above the catalog, so the catalog gets the whole width. -->
	<section class="ms-chosen" aria-labelledby="ms-chosen-title">
		<div class="ms-chosen-head">
			<h3 id="ms-chosen-title" class="text-sm font-semibold">
				Chosen <span class="font-mono text-muted-foreground tabular-nums">{value.length}</span>
			</h3>
			{#if value.length > 0}
				<p class="text-[11px] text-muted-foreground">
					{localCount} local · {value.length - localCount} OpenRouter{#if totalCost !== null}{' · ≈ '}<span class="font-mono tabular-nums">{formatUsd(totalCost)}</span> expected{/if}
				</p>
			{/if}
			<span class="flex-1"></span>
			{#if value.length > 0 && !disabled}
				<Button variant="ghost" size="sm" class="h-7 text-xs" onclick={clearAll}>Clear</Button>
			{/if}
		</div>
		{#if value.length === 0}
			<p class="ms-empty">
				<Server class="size-4 shrink-0" aria-hidden="true" />
				Tick models below, or “Select all” on an endpoint. Each one becomes a level of the comparison.
			</p>
		{:else}
			<ul class="ms-list" aria-label="Chosen models">
				{#each value as level (level)}
					{@const m = byModelId.get(level)}
					<li class="ms-card" data-warn={isFree(level) || (loaded && !m)}>
						<ProviderIcon provider={m?.provider ?? level.split('/')[0]} size={16} class="mt-0.5 shrink-0" decorative />
						<div class="min-w-0 flex-1">
							<p class="truncate text-[13px] font-medium" title={level}>{m?.model_name ?? level}</p>
							<p class="truncate text-[11px] text-muted-foreground" title={detail(m)}>
								{detail(m)}{#if costByLevel?.[level] !== undefined}{' · ≈ '}<span class="font-mono tabular-nums">{formatUsd(costByLevel[level])}</span>{/if}
							</p>
							{#if loaded && !m}
								<p class="text-[11px] text-warning-fg">Not in the catalog any more.</p>
							{:else if isFree(level)}
								<p class="flex items-center gap-1 text-[11px] text-warning-fg" title="Free tier is rate-limited (429s): runs may queue for hours">
									<TriangleAlert class="size-3 shrink-0" aria-hidden="true" />Free tier: rate-limited
								</p>
							{/if}
						</div>
						{#if !disabled}
							<button type="button" class="ms-remove" aria-label="Remove {m?.model_name ?? level}" onclick={() => remove(level)}>
								<X class="size-3.5" />
							</button>
						{/if}
					</li>
				{/each}
			</ul>
		{/if}
	</section>

	<div class="min-w-0">
		<ModelPicker
			mode="multi"
			bind:selectedIds={pickerIds}
			requireTools={mode === 'app'}
			groupBy="endpoint"
			showEndpointStatus
			maxHeight="max-h-[32rem]"
			label=""
			aria-label="Model catalog"
			idPrefix="step-models"
			showSelection={false}
			onchange={fromPicker}
			{disabled}
		/>
		{#if mode === 'app'}
			<p class="mt-1 text-[11px] text-muted-foreground">
				Agentic runs need tool calling: models without it are hidden. Mark tool support on an endpoint’s Models tab to list a local model.
			</p>
		{/if}
	</div>
</div>

<style>
	.ms {
		display: flex;
		flex-direction: column;
		gap: 1rem;
	}
	.ms-chosen {
		border: 1px solid var(--border);
		border-radius: var(--radius);
		background: color-mix(in oklab, var(--muted) 22%, var(--card));
	}
	.ms-chosen-head {
		display: flex;
		flex-wrap: wrap;
		align-items: center;
		gap: 0.25rem 0.75rem;
		min-height: 2.5rem;
		padding: 0.25rem 0.5rem 0.25rem 0.75rem;
	}
	.ms-empty {
		display: flex;
		align-items: center;
		gap: 0.5rem;
		padding: 0 0.75rem 0.75rem;
		font-size: 0.75rem;
		color: var(--muted-foreground);
	}
	.ms-list {
		display: grid;
		grid-template-columns: repeat(auto-fill, minmax(17rem, 1fr));
		gap: 0.5rem;
		max-height: 16rem;
		overflow-y: auto;
		padding: 0 0.75rem 0.75rem;
	}
	.ms-card {
		display: flex;
		align-items: flex-start;
		gap: 0.5rem;
		padding: 0.4375rem 0.375rem 0.4375rem 0.625rem;
		border: 1px solid var(--border);
		border-left: 2px solid var(--primary);
		border-radius: calc(var(--radius) - 2px);
		background: var(--card);
	}
	.ms-card[data-warn='true'] {
		border-left-color: var(--warning);
	}
	.ms-remove {
		position: relative;
		display: flex;
		width: 1.5rem;
		height: 1.5rem;
		flex-shrink: 0;
		cursor: pointer;
		align-items: center;
		justify-content: center;
		border-radius: calc(var(--radius) - 2px);
		color: var(--muted-foreground);
		transition:
			background-color 150ms,
			color 150ms;
	}
	.ms-remove:hover {
		background: var(--muted);
		color: var(--foreground);
	}
	.ms-remove:focus-visible {
		outline: 2px solid var(--ring);
	}
</style>
