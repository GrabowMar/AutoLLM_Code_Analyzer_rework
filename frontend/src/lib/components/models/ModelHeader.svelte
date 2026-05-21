<script lang="ts">
	import * as Card from '$lib/components/ui/card';
	import { Badge } from '$lib/components/ui/badge';
	import { Button } from '$lib/components/ui/button';
	import Cpu from '@lucide/svelte/icons/cpu';
	import Globe from '@lucide/svelte/icons/globe';
	import GitCompareArrows from '@lucide/svelte/icons/git-compare-arrows';
	import RefreshCw from '@lucide/svelte/icons/refresh-cw';
	import Copy from '@lucide/svelte/icons/copy';
	import Gift from '@lucide/svelte/icons/gift';
	import Sparkles from '@lucide/svelte/icons/sparkles';
	import Download from '@lucide/svelte/icons/download';
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
</script>

<!-- Header Card -->
<Card.Root>
	<Card.Content class="p-5">
		<div class="flex flex-col gap-4 sm:flex-row sm:items-start sm:justify-between">
			<div class="flex items-start gap-4">
				<div class="flex h-12 w-12 items-center justify-center rounded-md bg-primary/10">
					<Cpu class="h-6 w-6 text-primary" />
				</div>
				<div>
					<h1 class="text-xl font-semibold">{model.model_name}</h1>
					<div class="flex flex-wrap items-center gap-2 mt-1.5">
						<Badge variant="outline" class="text-xs">{model.provider}</Badge>
						{#if model.is_free}
							<Badge variant="outline" class="text-xs bg-emerald-500/15 text-emerald-500 border-emerald-500/30">
								<Gift class="h-3 w-3 mr-1" /> Free
							</Badge>
						{/if}
						<Badge variant="outline" class="text-xs hidden sm:inline-flex">{model.context_window_display} ctx</Badge>
					</div>
					<p class="text-xs text-muted-foreground mt-1 font-mono truncate max-w-full">{model.model_id}</p>
				</div>
			</div>
			<div class="flex flex-wrap items-center gap-2 sm:shrink-0">
				<Button variant="outline" size="sm" onclick={() => copyToClipboard(model.model_id)} title="Copy Model ID">
					<Copy class="h-3.5 w-3.5 sm:mr-1.5" /><span class="hidden sm:inline">ID</span>
				</Button>
				<Button variant="outline" size="sm" href="https://openrouter.ai/models/{model.model_id}" target="_blank" rel="noopener noreferrer" title="View on OpenRouter">
					<Globe class="h-3.5 w-3.5 sm:mr-1.5" /><span class="hidden sm:inline">OpenRouter</span>
				</Button>
				<Button variant="outline" size="sm" href="/models/compare?models={slug}" title="Compare">
					<GitCompareArrows class="h-3.5 w-3.5 sm:mr-1.5" /><span class="hidden sm:inline">Compare</span>
				</Button>
				<Button variant="outline" size="sm" onclick={exportJson} title="Export model as JSON">
					<Download class="h-3.5 w-3.5 sm:mr-1.5" /><span class="hidden sm:inline">Export JSON</span>
				</Button>
				<Button variant="outline" size="sm" onclick={onRefresh} title="Refresh metadata from OpenRouter" disabled={refreshing}>
					<RefreshCw class="h-3.5 w-3.5 sm:mr-1.5 {refreshing ? 'animate-spin' : ''}" /><span class="hidden sm:inline">Refresh Data</span>
				</Button>
				<Button size="sm" href="/sample-generator?mode=custom&model={slug}" title="Generate an app with this model">
					<Sparkles class="h-3.5 w-3.5 sm:mr-1.5" /><span class="hidden sm:inline">Generate App</span>
				</Button>
			</div>
		</div>
	</Card.Content>
</Card.Root>

<!-- KPI Row -->
<div class="grid grid-cols-3 sm:grid-cols-4 md:grid-cols-7 gap-2 sm:gap-3">
	<div class="rounded-lg border bg-card p-2 sm:p-3 text-center">
		<div class="text-[10px] sm:text-xs text-muted-foreground mb-0.5 sm:mb-1">Applications</div>
		<div class="text-sm sm:text-lg font-semibold">{applicationCount ?? '—'}</div>
	</div>
	<div class="rounded-lg border bg-card p-2 sm:p-3 text-center">
		<div class="text-[10px] sm:text-xs text-muted-foreground mb-0.5 sm:mb-1">Context</div>
		<div class="text-sm sm:text-lg font-semibold">{model.context_window_display}</div>
	</div>
	<div class="rounded-lg border bg-card p-2 sm:p-3 text-center">
		<div class="text-[10px] sm:text-xs text-muted-foreground mb-0.5 sm:mb-1">Input $/1M</div>
		<div class="text-sm sm:text-lg font-semibold">{formatPrice(model.input_price_per_million)}</div>
	</div>
	<div class="rounded-lg border bg-card p-2 sm:p-3 text-center">
		<div class="text-[10px] sm:text-xs text-muted-foreground mb-0.5 sm:mb-1">Output $/1M</div>
		<div class="text-sm sm:text-lg font-semibold">{formatPrice(model.output_price_per_million)}</div>
	</div>
	<div class="rounded-lg border bg-card p-2 sm:p-3 text-center">
		<div class="text-[10px] sm:text-xs text-muted-foreground mb-0.5 sm:mb-1">Efficiency</div>
		<div class="text-sm sm:text-lg font-semibold {efficiency.color}">{efficiency.grade}</div>
	</div>
	<div class="rounded-lg border bg-card p-2 sm:p-3 text-center">
		<div class="text-[10px] sm:text-xs text-muted-foreground mb-0.5 sm:mb-1">Max Output</div>
		<div class="text-sm sm:text-lg font-semibold">{model.max_output_tokens ? formatTokens(model.max_output_tokens) : 'N/A'}</div>
	</div>
	<div class="rounded-lg border bg-card p-2 sm:p-3 text-center">
		<div class="text-[10px] sm:text-xs text-muted-foreground mb-0.5 sm:mb-1">Last Updated</div>
		<div class="text-xs sm:text-sm font-semibold font-mono">{lastUpdated}</div>
	</div>
</div>
