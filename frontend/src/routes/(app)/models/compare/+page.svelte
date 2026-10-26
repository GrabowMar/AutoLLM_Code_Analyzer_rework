<script lang="ts">
	import { formatPrice, formatTokens } from '$lib/utils/formatters';
	import { page } from '$app/stores';
	import { getModelComparison, type LLMModelDetail, type ModelComparisonResult } from '$lib/api/client';
	import * as Card from '$lib/components/ui/card';
	import { Button } from '$lib/components/ui/button';
	import { toast } from 'svelte-sonner';
	import AlertTriangle from '@lucide/svelte/icons/alert-triangle';
	import Check from '@lucide/svelte/icons/check';
	import Cpu from '@lucide/svelte/icons/cpu';
	import Link from '@lucide/svelte/icons/link';
	import PageHeader from '$lib/components/PageHeader.svelte';
	import LoadingState from '$lib/components/LoadingState.svelte';
	import ErrorAlert from '$lib/components/ErrorAlert.svelte';
	import EmptyState from '$lib/components/EmptyState.svelte';
	import X from '@lucide/svelte/icons/x';
	import { tableFrame, shortModelName, modelNamePrefix } from '$lib/components/table';

	type BaselineMode = 'first' | 'average' | 'median';

	const queryModels = $derived(
		($page.url.searchParams.get('models') ?? '')
			.split(',')
			.map((value) => value.trim())
			.filter(Boolean),
	);

	let comparison = $state<ModelComparisonResult | null>(null);
	let loading = $state(true);
	let error = $state('');
	let baseline = $state<BaselineMode>('first');
	let highlightSlug = $state('');

	const comparedModels = $derived(comparison?.items ?? []);
	const missingModels = $derived(comparison?.missing ?? []);
	const capabilityUnion = $derived([...new Set(comparedModels.flatMap((model) => model.capabilities))]);
	const baselineLabel = $derived(
		baseline === 'first'
			? (comparedModels[0]?.model_name ?? 'first selected model')
			: baseline === 'average'
				? 'average price'
				: 'median price',
	);
	const baselineInputPrice = $derived(resolveBaseline(comparedModels.map((model) => model.input_price_per_million), baseline));
	const baselineOutputPrice = $derived(resolveBaseline(comparedModels.map((model) => model.output_price_per_million), baseline));

	function resolveBaseline(values: number[], mode: BaselineMode): number {
		if (values.length === 0) return 0;
		if (mode === 'average') {
			return values.reduce((sum, value) => sum + value, 0) / values.length;
		}
		if (mode === 'median') {
			const sorted = [...values].sort((left, right) => left - right);
			const middle = Math.floor(sorted.length / 2);
			if (sorted.length % 2 === 0) {
				return (sorted[middle - 1] + sorted[middle]) / 2;
			}
			return sorted[middle];
		}
		return values[0] ?? 0;
	}

	function formatDelta(value: number): string {
		if (value === 0) return '0.00';
		return `${value > 0 ? '+' : ''}${value.toFixed(2)}`;
	}

	function efficiencyGrade(score: number): string {
		if (score >= 0.9) return 'A+';
		if (score >= 0.8) return 'A';
		if (score >= 0.7) return 'A-';
		if (score >= 0.6) return 'B+';
		if (score >= 0.5) return 'B';
		if (score >= 0.4) return 'B-';
		if (score >= 0.3) return 'C+';
		if (score >= 0.2) return 'C';
		return 'D';
	}

	/**
	 * Index set of the models holding the best value of a row, or empty when
	 * there is no clear leader (all tied, or more than two share the best).
	 * A 0 counts as missing for 'max' rows (unknown size) but as free for 'min' (prices).
	 */
	function leaders(values: (number | null | undefined)[], better: 'max' | 'min', fmt: (v: number) => string = String): Set<number> {
		const nums = values.map((v) => (typeof v === 'number' && Number.isFinite(v) && (better === 'min' ? v >= 0 : v > 0) ? v : null));
		const present = nums.filter((v): v is number => v != null);
		if (present.length < 2) return new Set();
		const best = better === 'max' ? Math.max(...present) : Math.min(...present);
		// Values that read the same once formatted (1M vs 1.05M → "1M") are a tie.
		const idx = nums.flatMap((v, i) => (v != null && fmt(v) === fmt(best) ? [i] : []));
		return idx.length <= 2 && idx.length < values.length ? new Set(idx) : new Set();
	}

	const best = $derived({
		context: leaders(comparedModels.map((m) => m.context_window), 'max', formatTokens),
		maxOut: leaders(comparedModels.map((m) => m.max_output_tokens), 'max', formatTokens),
		input: leaders(comparedModels.map((m) => m.input_price_per_million), 'min', formatPrice),
		output: leaders(comparedModels.map((m) => m.output_price_per_million), 'min', formatPrice),
		efficiency: leaders(comparedModels.map((m) => m.cost_efficiency), 'max', (v) => v.toFixed(3)),
	});

	/** Faint tint on the column picked under "Highlight". */
	function colTint(model: LLMModelDetail): string {
		return highlightSlug === model.canonical_slug ? 'bg-primary/[0.04]' : '';
	}

	function supportsCapability(model: LLMModelDetail, capability: string): boolean {
		return model.capabilities.includes(capability);
	}

	async function copyShareLink() {
		await navigator.clipboard.writeText(window.location.href);
		toast.success('Comparison link copied.');
	}

	async function loadComparison() {
		loading = true;
		error = '';
		comparison = null;

		if (queryModels.length === 0) {
			loading = false;
			return;
		}

		try {
			comparison = await getModelComparison(queryModels);
			if (comparison.items.length === 0) {
				error = 'No matching models were found.';
			}
			if (!highlightSlug && comparison.items.length > 0) {
				highlightSlug = comparison.items[0].canonical_slug;
			}
		} catch {
			error = 'Failed to load model comparison.';
		} finally {
			loading = false;
		}
	}

	$effect(() => {
		const nextSelection = queryModels;
		if (nextSelection.length > 0) {
			highlightSlug = nextSelection[0] ?? '';
		}
		void loadComparison();
	});
</script>

<!-- Baseline / highlight and missing models: the header's footer strip, not a card. -->
{#snippet settingsStrip()}
	<div class="flex flex-wrap items-center gap-x-4 gap-y-1.5">
		{#if comparedModels.length > 0}
			<span class="header-stat text-muted-foreground"><b>{comparedModels.length}</b> models</span>
			<div class="flex items-center gap-1.5">
				<label class="text-muted-foreground" for="baseline">Baseline</label>
				<select id="baseline" class="h-7 cursor-pointer rounded-md border border-input bg-surface-1 px-2 text-xs hover:border-primary/40 focus-visible:outline-none" bind:value={baseline}>
					<option value="first">First Selected</option>
					<option value="average">Average</option>
					<option value="median">Median</option>
				</select>
			</div>
			<div class="flex items-center gap-1.5">
				<label class="text-muted-foreground" for="highlight">Highlight</label>
				<select id="highlight" class="h-7 cursor-pointer rounded-md border border-input bg-surface-1 px-2 text-xs hover:border-primary/40 focus-visible:outline-none" bind:value={highlightSlug}>
					{#each comparedModels as model}
						<option value={model.canonical_slug}>{model.model_name}</option>
					{/each}
				</select>
			</div>
		{/if}
		{#if missingModels.length > 0}
			<span class="inline-flex min-w-0 items-center gap-1.5 text-warning-fg" role="status">
				<AlertTriangle class="size-3.5 shrink-0" aria-hidden="true" />
				<span class="truncate" title={missingModels.join(', ')}>Not found: {missingModels.join(', ')}</span>
			</span>
		{/if}
	</div>
{/snippet}

<div class="space-y-4">
	<PageHeader
		title="Model Comparison"
		description="Side-by-side comparison for the models selected in the models list or detail view."
		back={{ href: '/models', label: 'Models' }}
		children={!loading && (comparedModels.length > 0 || missingModels.length > 0) ? settingsStrip : undefined}
	>
		{#snippet actions()}
			{#if comparedModels.length > 0}
				<Button variant="outline" size="sm" onclick={copyShareLink}>
					<Link class="mr-2 h-3.5 w-3.5" />
					Share Link
				</Button>
			{/if}
		{/snippet}
	</PageHeader>

	{#if queryModels.length === 0}
		<Card.Root>
			<Card.Content>
				<EmptyState icon={Cpu} title="No models selected" description="Choose one or more models from the models page to open a comparison.">
					{#snippet action()}
						<Button href="/models" size="sm">Go to Models</Button>
					{/snippet}
				</EmptyState>
			</Card.Content>
		</Card.Root>
	{:else if loading}
		<LoadingState label="loading comparison" />
	{:else}
		{#if error}
			<ErrorAlert message={error} onretry={loadComparison} />
		{/if}

		{#if comparedModels.length > 0}
			<Card.Root class="gap-0 overflow-clip py-0">
				<Card.Header class="border-b border-border py-3">
					<Card.Title>Core Metrics</Card.Title>
					<Card.Description>The best value in a row is marked when one or two models clearly lead it.</Card.Description>
				</Card.Header>
				<Card.Content class="p-0">
					<div class="table-frame" {@attach tableFrame}>
						<table class="min-w-[720px]">
							<thead>
								{@render modelHead('Metric')}
							</thead>
							<tbody>
								<tr>
									<td class="col-sticky font-medium">Provider</td>
									{#each comparedModels as model (model.canonical_slug)}
										<td class={colTint(model)}>{model.provider}</td>
									{/each}
								</tr>
								<tr>
									<td class="col-sticky font-medium">Context window</td>
									{#each comparedModels as model, i (model.canonical_slug)}
										<td class="num {colTint(model)}">{@render bestVal(model.context_window_display || '—', best.context.has(i), !model.context_window)}</td>
									{/each}
								</tr>
								<tr>
									<td class="col-sticky font-medium">Max output</td>
									{#each comparedModels as model, i (model.canonical_slug)}
										<td class="num {colTint(model)}">{@render bestVal(model.max_output_tokens ? formatTokens(model.max_output_tokens) : '—', best.maxOut.has(i), !model.max_output_tokens)}</td>
									{/each}
								</tr>
								<tr>
									<td class="col-sticky font-medium">Input $/1M tokens</td>
									{#each comparedModels as model, i (model.canonical_slug)}
										<td class="num {colTint(model)}">{@render bestVal(formatPrice(model.input_price_per_million), best.input.has(i))}</td>
									{/each}
								</tr>
								<tr>
									<td class="col-sticky font-medium">Output $/1M tokens</td>
									{#each comparedModels as model, i (model.canonical_slug)}
										<td class="num {colTint(model)}">{@render bestVal(formatPrice(model.output_price_per_million), best.output.has(i))}</td>
									{/each}
								</tr>
								<tr>
									<td class="col-sticky font-medium">Cost efficiency</td>
									{#each comparedModels as model, i (model.canonical_slug)}
										<td class="num {colTint(model)}">
											<span class={best.efficiency.has(i) ? 'font-semibold text-primary' : 'font-semibold'}>{efficiencyGrade(model.cost_efficiency)}</span>
											<span class="ml-1.5 text-xs text-muted-foreground">{model.cost_efficiency.toFixed(3)}</span>
										</td>
									{/each}
								</tr>
								<tr>
									<td class="col-sticky font-medium">Free tier</td>
									{#each comparedModels as model (model.canonical_slug)}
										<td class="num {colTint(model)}">
											{#if model.is_free}
												<Check class="ml-auto h-4 w-4 text-foreground" aria-label="Yes" />
											{:else}
												<X class="ml-auto h-4 w-4 text-muted-foreground/60" aria-label="No" />
											{/if}
										</td>
									{/each}
								</tr>
							</tbody>
						</table>
					</div>
				</Card.Content>
			</Card.Root>

			<Card.Root class="gap-0 overflow-clip py-0">
				<Card.Header class="border-b border-border py-3">
					<Card.Title>Pricing Deltas</Card.Title>
					<Card.Description>Difference in $/1M tokens compared to {baselineLabel}; negative is cheaper.</Card.Description>
				</Card.Header>
				<Card.Content class="p-0">
					<div class="table-frame" {@attach tableFrame}>
						<table class="min-w-[720px]">
							<thead>
								{@render modelHead('Type')}
							</thead>
							<tbody>
								<tr>
									<td class="col-sticky font-medium">Input delta</td>
									{#each comparedModels as model (model.canonical_slug)}
										{@const delta = model.input_price_per_million - baselineInputPrice}
										<td class="num {colTint(model)} {delta === 0 ? 'text-muted-foreground' : ''}">{formatDelta(delta)}</td>
									{/each}
								</tr>
								<tr>
									<td class="col-sticky font-medium">Output delta</td>
									{#each comparedModels as model (model.canonical_slug)}
										{@const delta = model.output_price_per_million - baselineOutputPrice}
										<td class="num {colTint(model)} {delta === 0 ? 'text-muted-foreground' : ''}">{formatDelta(delta)}</td>
									{/each}
								</tr>
							</tbody>
						</table>
					</div>
				</Card.Content>
			</Card.Root>

			<Card.Root class="gap-0 overflow-clip py-0">
				<Card.Header class="border-b border-border py-3">
					<Card.Title>Capabilities Comparison</Card.Title>
				</Card.Header>
				<Card.Content class="p-0">
					<div class="table-frame" {@attach tableFrame}>
						<table class="min-w-[720px]">
							<thead>
								{@render modelHead('Capability', true)}
							</thead>
							<tbody>
								{#each capabilityUnion as capability (capability)}
									<tr>
										<td class="col-sticky font-medium">{capability}</td>
										{#each comparedModels as model (model.canonical_slug)}
											<td class="text-center {colTint(model)}">
												{#if supportsCapability(model, capability)}
													<Check class="inline h-4 w-4 text-foreground" aria-label="Yes" />
												{:else}
													<X class="inline h-4 w-4 text-muted-foreground/60" aria-label="No" />
												{/if}
											</td>
										{/each}
									</tr>
								{/each}
							</tbody>
						</table>
					</div>
				</Card.Content>
			</Card.Root>
		{/if}
	{/if}
</div>

{#snippet modelHead(label: string, center = false)}
	<tr>
		<th class="col-sticky min-w-40">{label}</th>
		{#each comparedModels as model (model.canonical_slug)}
			{@const prefix = modelNamePrefix(model.model_name)}
			<th class="{center ? 'text-center' : 'num'} normal-case tracking-normal {highlightSlug === model.canonical_slug ? 'text-primary' : ''}" title={model.model_name}>
				<a href="/models/{model.canonical_slug}" class="font-sans text-xs font-semibold hover:text-primary">{shortModelName(model.model_name)}</a>
				{#if prefix}<span class="block font-sans text-[11px] font-normal text-muted-foreground">{prefix}</span>{/if}
			</th>
		{/each}
	</tr>
{/snippet}

{#snippet bestVal(text: string, isBest: boolean, missing = false)}
	{#if missing}
		<span class="text-muted-foreground">—</span>
	{:else if isBest}
		<span class="font-semibold text-primary" title="Best in this row">{text}</span>
	{:else}
		{text}
	{/if}
{/snippet}
