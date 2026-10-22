<script lang="ts">
	import { Badge } from '$lib/components/ui/badge';
	import { Button } from '$lib/components/ui/button';
	import Globe from '@lucide/svelte/icons/globe';
	import GitCompareArrows from '@lucide/svelte/icons/git-compare-arrows';
	import RefreshCw from '@lucide/svelte/icons/refresh-cw';
	import Copy from '@lucide/svelte/icons/copy';
	import Gift from '@lucide/svelte/icons/gift';
	import Sparkles from '@lucide/svelte/icons/sparkles';
	import Download from '@lucide/svelte/icons/download';
	import ProviderIcon from '$lib/components/ProviderIcon.svelte';
	import PageHeader from '$lib/components/PageHeader.svelte';
	import type { LLMModelDetail } from '$lib/api/client';
	import { formatPrice, formatTokens, copyToClipboard } from './helpers';

	interface Props {
		model: LLMModelDetail;
		slug: string;
		efficiency: { grade: string; color: string };
		refreshing: boolean;
		applicationCount?: number;
		onRefresh: () => void;
	}

	let { model, slug, efficiency, refreshing, applicationCount, onRefresh }: Props = $props();

	function exportJson() {
		const blob = new Blob([JSON.stringify(model, null, 2)], { type: 'application/json' });
		const url = URL.createObjectURL(blob);
		const a = document.createElement('a');
		a.href = url;
		a.download = `${slug}.json`;
		document.body.appendChild(a);
		a.click();
		document.body.removeChild(a);
		URL.revokeObjectURL(url);
	}

	const lastUpdated = $derived(
		model.updated_at ? new Date(model.updated_at).toISOString().slice(0, 16).replace('T', ' ') : 'N/A',
	);

	const kpis = $derived<{ label: string; value: string | number; class?: string }[]>([
		{ label: 'Applications', value: applicationCount ?? '—' },
		{ label: 'Context', value: model.context_window_display },
		{ label: 'Input $/1M', value: formatPrice(model.input_price_per_million) },
		{ label: 'Output $/1M', value: formatPrice(model.output_price_per_million) },
		{ label: 'Efficiency', value: efficiency.grade, class: `font-display text-lg ${efficiency.color}` },
		{ label: 'Max Output', value: model.max_output_tokens ? formatTokens(model.max_output_tokens) : 'N/A' },
		{ label: 'Last Updated', value: lastUpdated, class: 'font-mono text-sm' },
	]);
</script>

<PageHeader title={model.model_name} back={{ href: '/models', label: 'Models' }}>
	{#snippet meta()}
		<ProviderIcon provider={model.provider} size={20} />
		<Badge variant="outline" class="text-xs">{model.provider}</Badge>
		{#if model.is_free}
			<Badge variant="outline" class="text-xs border-success/30 bg-success/10 text-success-fg">
				<Gift class="h-3 w-3 mr-1" /> Free
			</Badge>
		{/if}
		<Badge variant="outline" class="text-xs hidden sm:inline-flex">{model.context_window_display} ctx</Badge>
	{/snippet}
	<p class="font-mono text-xs truncate max-w-full">{model.model_id}</p>
	{#snippet actions()}
		<Button variant="outline" size="sm" onclick={() => copyToClipboard(model.model_id)} title="Copy Model ID" aria-label="Copy model ID">
			<Copy class="h-3.5 w-3.5 sm:mr-1.5" /><span class="hidden sm:inline">ID</span>
		</Button>
		<Button variant="outline" size="sm" href="https://openrouter.ai/models/{model.model_id}" target="_blank" rel="noopener noreferrer" title="View on OpenRouter" aria-label="View on OpenRouter">
			<Globe class="h-3.5 w-3.5 sm:mr-1.5" /><span class="hidden sm:inline">OpenRouter</span>
		</Button>
		<Button variant="outline" size="sm" href="/models/compare?models={slug}" title="Compare" aria-label="Compare">
			<GitCompareArrows class="h-3.5 w-3.5 sm:mr-1.5" /><span class="hidden sm:inline">Compare</span>
		</Button>
		<Button variant="outline" size="sm" onclick={exportJson} title="Export model as JSON" aria-label="Export model as JSON">
			<Download class="h-3.5 w-3.5 sm:mr-1.5" /><span class="hidden sm:inline">Export JSON</span>
		</Button>
		<Button variant="outline" size="sm" onclick={onRefresh} title="Refresh metadata from OpenRouter" aria-label="Refresh metadata from OpenRouter" disabled={refreshing}>
			<RefreshCw class="h-3.5 w-3.5 sm:mr-1.5 {refreshing ? 'animate-spin' : ''}" /><span class="hidden sm:inline">Refresh Data</span>
		</Button>
		<Button size="sm" href="/sample-generator?mode=agent&model={encodeURIComponent(slug)}" title="Generate an app with this model" aria-label="Generate an app with this model">
			<Sparkles class="h-3.5 w-3.5 sm:mr-1.5" /><span class="hidden sm:inline">Generate App</span>
		</Button>
	{/snippet}
</PageHeader>

<!-- KPI Row -->
<div class="grid grid-cols-2 sm:grid-cols-4 lg:grid-cols-7 gap-3">
	{#each kpis as kpi (kpi.label)}
		<div class="kpi-card">
			<span class="kpi-label">{kpi.label}</span>
			<span class="font-semibold tabular-nums {kpi.class ?? 'font-display text-lg'}">{kpi.value}</span>
		</div>
	{/each}
</div>
