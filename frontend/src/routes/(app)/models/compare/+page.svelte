<script lang="ts">
	import { formatPrice, formatTokens } from '$lib/utils/formatters';
	import { page } from '$app/stores';
	import { getModelComparison, type LLMModelDetail, type ModelComparisonResult } from '$lib/api/client';
	import * as Card from '$lib/components/ui/card';
	import { Badge } from '$lib/components/ui/badge';
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
	import Settings from '@lucide/svelte/icons/settings';
	import X from '@lucide/svelte/icons/x';

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

<div class="space-y-6">
	<PageHeader
		title="Model Comparison"
		description="Side-by-side comparison for the models selected in the models list or detail view."
		back={{ href: '/models', label: 'Models' }}
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

		{#if missingModels.length > 0}
			<div class="rounded-md border border-warning/30 bg-warning/10 p-4 text-sm text-warning-fg">
				<div class="flex items-start gap-2">
					<AlertTriangle class="mt-0.5 h-4 w-4 shrink-0" />
					<div>
						<p class="font-medium">Some requested models were not found.</p>
						<p class="mt-1">Missing: {missingModels.join(', ')}</p>
					</div>
				</div>
			</div>
		{/if}

		{#if comparedModels.length > 0}
			<Card.Root>
				<Card.Header>
					<div class="flex items-center gap-2">
						<Settings class="h-4 w-4 text-muted-foreground" />
						<Card.Title>Comparison Settings</Card.Title>
					</div>
				</Card.Header>
				<Card.Content class="space-y-3">
					<div class="flex flex-col gap-3 sm:flex-row sm:flex-wrap sm:items-center sm:gap-4">
						<div class="flex items-center gap-2">
							<label class="text-sm font-medium" for="baseline">Baseline:</label>
							<select id="baseline" class="h-8 rounded-md border border-input bg-surface-1 px-2 text-sm hover:border-primary/40 focus-visible:outline-none cursor-pointer" bind:value={baseline}>
								<option value="first">First Selected</option>
								<option value="average">Average</option>
								<option value="median">Median</option>
							</select>
						</div>
						<div class="flex items-center gap-2">
							<label class="text-sm font-medium" for="highlight">Highlight:</label>
							<select id="highlight" class="h-8 rounded-md border border-input bg-surface-1 px-2 text-sm hover:border-primary/40 focus-visible:outline-none cursor-pointer" bind:value={highlightSlug}>
								{#each comparedModels as model}
									<option value={model.canonical_slug}>{model.model_name}</option>
								{/each}
							</select>
						</div>
					</div>
					<div class="flex flex-wrap gap-1.5">
						{#each comparedModels as model (model.canonical_slug)}
							<Badge variant="secondary" class="gap-1.5 {highlightSlug === model.canonical_slug ? 'border border-primary/30 bg-primary/10 text-primary' : ''}">
								<Cpu class="h-3 w-3" />
								{model.model_name}
							</Badge>
						{/each}
					</div>
				</Card.Content>
			</Card.Root>

			<Card.Root>
				<Card.Header>
					<Card.Title>Core Metrics</Card.Title>
				</Card.Header>
				<Card.Content class="p-0">
					<div class="overflow-x-auto">
						<table class="w-full min-w-[720px]">
							<thead>
								<tr class="border-b bg-muted/30">
									<th class="px-4 py-3 text-left text-xs font-medium text-muted-foreground">Metric</th>
									{#each comparedModels as model (model.canonical_slug)}
										<th class="px-4 py-3 text-left text-xs font-medium {highlightSlug === model.canonical_slug ? 'text-primary' : 'text-muted-foreground'}">{model.model_name}</th>
									{/each}
								</tr>
							</thead>
							<tbody class="divide-y">
								<tr class="hover:bg-muted/30">
									<td class="px-4 py-2.5 text-sm font-medium">Provider</td>
									{#each comparedModels as model}
										<td class="px-4 py-2.5 text-sm">{model.provider}</td>
									{/each}
								</tr>
								<tr class="hover:bg-muted/30">
									<td class="px-4 py-2.5 text-sm font-medium">Context Window</td>
									{#each comparedModels as model}
										<td class="px-4 py-2.5 text-sm font-mono">{model.context_window_display}</td>
									{/each}
								</tr>
								<tr class="hover:bg-muted/30">
									<td class="px-4 py-2.5 text-sm font-medium">Max Output</td>
									{#each comparedModels as model}
										<td class="px-4 py-2.5 text-sm font-mono">{model.max_output_tokens ? formatTokens(model.max_output_tokens) : '—'}</td>
									{/each}
								</tr>
								<tr class="hover:bg-muted/30">
									<td class="px-4 py-2.5 text-sm font-medium">Input $/1M Tokens</td>
									{#each comparedModels as model}
										<td class="px-4 py-2.5 text-sm font-mono">{formatPrice(model.input_price_per_million)}</td>
									{/each}
								</tr>
								<tr class="hover:bg-muted/30">
									<td class="px-4 py-2.5 text-sm font-medium">Output $/1M Tokens</td>
									{#each comparedModels as model}
										<td class="px-4 py-2.5 text-sm font-mono">{formatPrice(model.output_price_per_million)}</td>
									{/each}
								</tr>
								<tr class="hover:bg-muted/30">
									<td class="px-4 py-2.5 text-sm font-medium">Cost Efficiency</td>
									{#each comparedModels as model}
										<td class="px-4 py-2.5 text-sm">
											<div class="flex items-center gap-2">
												<span class="font-semibold">{efficiencyGrade(model.cost_efficiency)}</span>
												<span class="font-mono text-xs text-muted-foreground">{model.cost_efficiency.toFixed(3)}</span>
											</div>
										</td>
									{/each}
								</tr>
								<tr class="hover:bg-muted/30">
									<td class="px-4 py-2.5 text-sm font-medium">Free Tier</td>
									{#each comparedModels as model}
										<td class="px-4 py-2.5 text-sm">
											{#if model.is_free}
												<Check class="h-4 w-4 text-success-fg" aria-label="Yes" />
											{:else}
												<X class="h-4 w-4 text-muted-foreground" aria-label="No" />
											{/if}
										</td>
									{/each}
								</tr>
							</tbody>
						</table>
					</div>
				</Card.Content>
			</Card.Root>

			<Card.Root>
				<Card.Header>
					<Card.Title>Pricing Deltas</Card.Title>
					<Card.Description>Difference compared to {baselineLabel}.</Card.Description>
				</Card.Header>
				<Card.Content class="p-0">
					<div class="overflow-x-auto">
						<table class="w-full min-w-[720px]">
							<thead>
								<tr class="border-b bg-muted/30">
									<th class="px-4 py-3 text-left text-xs font-medium text-muted-foreground">Type</th>
									{#each comparedModels as model (model.canonical_slug)}
										<th class="px-4 py-3 text-left text-xs font-medium text-muted-foreground">{model.model_name}</th>
									{/each}
								</tr>
							</thead>
							<tbody class="divide-y">
								<tr class="hover:bg-muted/30">
									<td class="px-4 py-2.5 text-sm font-medium">Input Delta</td>
									{#each comparedModels as model}
										{@const delta = model.input_price_per_million - baselineInputPrice}
										<td class="px-4 py-2.5 text-sm font-mono {delta > 0 ? 'text-danger-fg' : delta < 0 ? 'text-success-fg' : 'text-muted-foreground'}">
											{formatDelta(delta)}
										</td>
									{/each}
								</tr>
								<tr class="hover:bg-muted/30">
									<td class="px-4 py-2.5 text-sm font-medium">Output Delta</td>
									{#each comparedModels as model}
										{@const delta = model.output_price_per_million - baselineOutputPrice}
										<td class="px-4 py-2.5 text-sm font-mono {delta > 0 ? 'text-danger-fg' : delta < 0 ? 'text-success-fg' : 'text-muted-foreground'}">
											{formatDelta(delta)}
										</td>
									{/each}
								</tr>
							</tbody>
						</table>
					</div>
				</Card.Content>
			</Card.Root>

			<Card.Root>
				<Card.Header>
					<Card.Title>Capabilities Comparison</Card.Title>
				</Card.Header>
				<Card.Content class="p-0">
					<div class="overflow-x-auto">
						<table class="w-full min-w-[720px]">
							<thead>
								<tr class="border-b bg-muted/30">
									<th class="px-4 py-3 text-left text-xs font-medium text-muted-foreground">Capability</th>
									{#each comparedModels as model (model.canonical_slug)}
										<th class="px-4 py-3 text-center text-xs font-medium text-muted-foreground">{model.model_name}</th>
									{/each}
								</tr>
							</thead>
							<tbody class="divide-y">
								{#each capabilityUnion as capability (capability)}
									<tr class="hover:bg-muted/30">
										<td class="px-4 py-2.5 text-sm font-medium">{capability}</td>
										{#each comparedModels as model}
											<td class="px-4 py-2.5 text-center">
												{#if supportsCapability(model, capability)}
													<Check class="inline h-4 w-4 text-success-fg" aria-label="Yes" />
												{:else}
													<X class="inline h-4 w-4 text-muted-foreground" aria-label="No" />
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
