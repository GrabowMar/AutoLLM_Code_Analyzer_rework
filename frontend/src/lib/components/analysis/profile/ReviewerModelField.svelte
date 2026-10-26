<script lang="ts" module>
	import type { LLMModelSummary } from '$lib/api/models';

	export interface ReviewerModelReasoning {
		/** Efforts the model accepts on OpenRouter (catalog order); empty = not listed. */
		efforts: string[];
		defaultEffort: string | null;
		/** Reasoning cannot be switched off. */
		mandatory: boolean;
	}

	export interface ReviewerModelInfo {
		/** Catalog load state. */
		state: 'loading' | 'ready' | 'error';
		/** The catalog row of the configured model; null when it is not in the catalog. */
		model: LLMModelSummary | null;
		/** Reasoning capabilities; null while loading or when unknown. */
		reasoning: ReviewerModelReasoning | null;
	}
</script>

<script lang="ts">
	/**
	 * ReviewerModelField — the AI reviewer's model: a summary of the chosen
	 * catalog model plus the generator's ModelPicker (single mode) to change it.
	 * The config stores the OpenRouter id (`model_id`); the picker works on
	 * catalog pks, so this maps between the two.
	 *
	 * Props: `id`, `model` (OpenRouter id), `defaultModel`, `requireTools`
	 * (an agent harness needs tool calling), `readonly`, `error`,
	 * `onchange(modelId)`, `info` (bindable output: catalog row + reasoning
	 * vocabulary of the model, read from its catalog detail).
	 */
	import { onMount, untrack } from 'svelte';
	import { getModel } from '$lib/api/models';
	import { formatApiError } from '$lib/api/core';
	import { formatPrice, formatTokens } from '$lib/utils/formatters';
	import { cn } from '$lib/utils';
	import { Button } from '$lib/components/ui/button';
	import ErrorAlert from '$lib/components/ErrorAlert.svelte';
	import ProviderIcon from '$lib/components/ProviderIcon.svelte';
	import ModelPicker from '$lib/components/generator/ModelPicker.svelte';
	import { findModel, loadModelCatalog } from '$lib/components/generator/catalog';
	import Brain from '@lucide/svelte/icons/brain';
	import LoaderCircle from '@lucide/svelte/icons/loader-circle';
	import RotateCcw from '@lucide/svelte/icons/rotate-ccw';
	import TriangleAlert from '@lucide/svelte/icons/triangle-alert';
	import Wrench from '@lucide/svelte/icons/wrench';

	interface Props {
		id: string;
		model: string;
		defaultModel: string;
		requireTools?: boolean;
		readonly?: boolean;
		error?: string;
		onchange?: (modelId: string) => void;
		info?: ReviewerModelInfo;
	}

	let {
		id,
		model,
		defaultModel,
		requireTools = false,
		readonly = false,
		error = '',
		onchange,
		info = $bindable({ state: 'loading', model: null, reasoning: null })
	}: Props = $props();

	const chip =
		'inline-flex items-center gap-1 rounded-full border px-2 py-0.5 text-[11px] font-medium leading-4 tabular-nums';

	let models = $state<LLMModelSummary[]>([]);
	let catalogState = $state<'loading' | 'ready' | 'error'>('loading');
	let catalogError = $state('');
	let pickerOpen = $state(false);
	let pickerId = $state<number | null>(null);

	const selected = $derived(catalogState === 'ready' ? (findModel(models, model) ?? null) : null);

	// Reasoning vocabularies per canonical slug (catalog detail), fetched once per
	// model; null = capabilities unknown (the editor then offers every effort and
	// the plan freeze decides).
	let reasoningBySlug = $state<Record<string, ReviewerModelReasoning | null>>({});
	const inflight = new Set<string>();
	const reasoning = $derived(selected ? (reasoningBySlug[selected.canonical_slug] ?? null) : null);
	const reasoningLoading = $derived(!!selected && !(selected.canonical_slug in reasoningBySlug));

	$effect(() => {
		info = { state: catalogState, model: selected, reasoning };
	});

	$effect(() => {
		pickerId = selected?.id ?? null;
	});

	function parseReasoning(raw: unknown): ReviewerModelReasoning {
		const r = raw && typeof raw === 'object' ? (raw as Record<string, unknown>) : {};
		return {
			efforts: Array.isArray(r.supported_efforts)
				? r.supported_efforts.filter((e): e is string => typeof e === 'string')
				: [],
			defaultEffort: typeof r.default_effort === 'string' ? r.default_effort : null,
			mandatory: r.mandatory === true
		};
	}

	$effect(() => {
		const slug = selected?.canonical_slug;
		if (!slug || inflight.has(slug) || untrack(() => slug in reasoningBySlug)) return;
		inflight.add(slug);
		getModel(slug)
			.then((detail) => {
				reasoningBySlug = {
					...reasoningBySlug,
					[slug]: parseReasoning(detail.capabilities_json?.reasoning)
				};
			})
			.catch(() => {
				reasoningBySlug = { ...reasoningBySlug, [slug]: null };
			})
			.finally(() => inflight.delete(slug));
	});

	async function loadCatalog(force = false) {
		catalogState = 'loading';
		catalogError = '';
		try {
			models = await loadModelCatalog({ force });
			catalogState = 'ready';
		} catch (e) {
			catalogError = formatApiError(e, 'Could not load the model catalog.');
			catalogState = 'error';
		}
	}

	onMount(() => {
		void loadCatalog();
	});

	function onPick() {
		const picked = models.find((m) => m.id === pickerId);
		if (!picked || picked.model_id === model) return;
		onchange?.(picked.model_id);
		pickerOpen = false;
	}

	function priceLabel(m: LLMModelSummary): string {
		if (m.is_free) return 'Free';
		return `${formatPrice(m.input_price_per_million)} / ${formatPrice(m.output_price_per_million)} per 1M`;
	}
</script>

<div class="space-y-2" data-slot="reviewer-model">
	<div
		class={cn(
			'flex flex-col gap-3 rounded-md border bg-card px-3 py-2.5 sm:flex-row sm:items-center',
			error ? 'border-destructive/50' : 'border-border'
		)}
	>
		<div class="flex min-w-0 flex-1 items-start gap-2.5">
			{#if selected}
				<ProviderIcon provider={selected.provider} size={20} />
			{/if}
			<div class="min-w-0 flex-1">
				<div class="flex flex-wrap items-baseline gap-x-2 gap-y-0.5">
					<span id="{id}-name" class="text-sm font-medium">{selected?.model_name ?? model}</span>
					<span class="break-all font-mono text-[11px] text-muted-foreground">{model}</span>
					{#if model === defaultModel}
						<span class={cn(chip, 'border-transparent bg-primary/15 text-primary')}>default</span>
					{/if}
				</div>
				{#if selected}
					<div class="mt-1 flex flex-wrap items-center gap-1">
						{#if selected.supports_function_calling}
							<span
								class={cn(chip, 'border-border bg-muted text-muted-foreground')}
								title="Native tool calling"><Wrench class="size-3" aria-hidden="true" />tools</span
							>
						{/if}
						{#if selected.supports_reasoning || (reasoning && reasoning.efforts.length > 0)}
							<span
								class={cn(chip, 'border-border bg-muted text-muted-foreground')}
								title="Reasoning model"><Brain class="size-3" aria-hidden="true" />reasoning</span
							>
						{/if}
						<span class={cn(chip, 'border-border text-foreground')} title="Context window"
							>{selected.context_window_display} ctx</span
						>
						{#if selected.max_output_tokens > 0}
							<span class={cn(chip, 'border-border text-foreground')} title="Model output limit"
								>{formatTokens(selected.max_output_tokens)} out</span
							>
						{/if}
						<span
							class={cn(chip, 'border-border text-foreground')}
							title="Catalog input / output price per 1M tokens">{priceLabel(selected)}</span
						>
						{#if reasoningLoading}
							<LoaderCircle
								class="size-3.5 animate-spin text-muted-foreground motion-reduce:animate-none"
								aria-label="Loading model capabilities"
							/>
						{/if}
					</div>
				{:else if catalogState === 'loading'}
					<p class="mt-1 flex items-center gap-1.5 text-[11px] text-muted-foreground" role="status">
						<LoaderCircle
							class="size-3 animate-spin motion-reduce:animate-none"
							aria-hidden="true"
						/>checking the model catalog
					</p>
				{/if}
			</div>
		</div>
		{#if !readonly}
			<div class="flex shrink-0 flex-wrap gap-1.5">
				{#if model !== defaultModel}
					<Button
						variant="ghost"
						size="sm"
						class="h-7 px-2"
						onclick={() => onchange?.(defaultModel)}
						title="Use {defaultModel}"
					>
						<RotateCcw />Default
					</Button>
				{/if}
				<Button
					variant="outline"
					size="sm"
					class="h-7 px-2"
					aria-expanded={pickerOpen}
					aria-controls="{id}-picker"
					onclick={() => (pickerOpen = !pickerOpen)}
				>
					{pickerOpen ? 'Close picker' : 'Change model'}
				</Button>
			</div>
		{/if}
	</div>

	{#if error}
		<p class="text-[11px] text-danger-fg" role="alert">{error}</p>
	{/if}

	{#if catalogState === 'error'}
		<ErrorAlert message={catalogError} onretry={() => loadCatalog(true)} />
	{:else if catalogState === 'ready' && !selected}
		<div
			class="flex items-start gap-2 rounded-md border border-warning/30 bg-warning/10 px-3 py-2 text-xs"
			role="alert"
		>
			<TriangleAlert class="mt-0.5 size-3.5 shrink-0 text-warning-fg" aria-hidden="true" />
			<p>
				<span class="font-medium text-warning-fg">Not in the model catalog.</span>
				<span class="text-muted-foreground">
					Freezing a plan resolves the model (pricing, capabilities) from the catalog, so runs and
					studies with this profile cannot start. Sync models on the Models page or pick another
					model.
				</span>
			</p>
		</div>
	{:else if catalogState === 'ready' && selected && requireTools && !selected.supports_function_calling}
		<div
			class="flex items-start gap-2 rounded-md border border-warning/30 bg-warning/10 px-3 py-2 text-xs"
			role="alert"
		>
			<TriangleAlert class="mt-0.5 size-3.5 shrink-0 text-warning-fg" aria-hidden="true" />
			<p>
				<span class="font-medium text-warning-fg">No tool calling.</span>
				<span class="text-muted-foreground"
					>The selected harness explores the app with tools; pick a model that supports them.</span
				>
			</p>
		</div>
	{/if}

	{#if pickerOpen && !readonly}
		<div id="{id}-picker" class="rounded-md border border-border bg-muted/20 p-3">
			<ModelPicker
				mode="single"
				bind:selectedId={pickerId}
				{requireTools}
				label=""
				aria-label="Reviewer model"
				idPrefix="{id}-picker"
				maxHeight="max-h-72"
				onchange={onPick}
			/>
		</div>
	{/if}
</div>
