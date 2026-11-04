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
	import type { Snippet } from 'svelte';
	import type { LLMModelDetail } from '$lib/api/client';
	import { formatPrice, formatTokens, copyToClipboard, getCaps, isoDay, fmtParams, knownTokenLimit, metadataView } from './helpers';
	import { shortModelName, modelNamePrefix } from '$lib/components/table';

	interface Props {
		model: LLMModelDetail;
		slug: string;
		efficiency: { grade: string; color: string };
		refreshing: boolean;
		applicationCount?: number;
		/** Staff-only metadata refresh; the button is hidden when omitted. */
		onRefresh?: () => void;
		/** Section tabs for the header's nav strip. */
		nav?: Snippet;
	}

	let { model, slug, efficiency, refreshing, applicationCount, onRefresh, nav: navSnippet }: Props = $props();

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

	const lastUpdated = $derived(model.updated_at ? new Date(model.updated_at).toISOString().slice(0, 10) : null);
	const providerLabel = $derived(modelNamePrefix(model.model_name) || model.provider);

	const view = $derived(metadataView(model));
	const md = $derived(view.md);
	const hf = $derived(view.hf);
	const params = $derived(view.params);
	const inherited = $derived(view.inheritedFrom ? ` (inherited from ${view.inheritedFrom}, assumed same weights)` : '');
	const released = $derived(md?.release_date || isoDay(getCaps(model).created));
	const knowledge = $derived(md?.knowledge || isoDay(getCaps(model).knowledge_cutoff));

	interface HeaderStat {
		label: string;
		value: string | number;
		title?: string;
		/** Unknown value: the "—" renders muted. */
		muted?: boolean;
	}

	const contextWindow = $derived(knownTokenLimit(model.context_window));
	const maxOutput = $derived(knownTokenLimit(model.max_output_tokens));

	const stats = $derived(
		(
		[
			contextWindow != null
				? { label: 'context', value: model.context_window_display }
				: { label: 'context', value: '—', muted: true, title: 'Context window not reported by the source' },
			maxOutput != null ? { label: 'max out', value: formatTokens(maxOutput) } : null,
			{
				label: model.is_free ? 'no token cost' : '$/1M in · out',
				value: model.is_free ? 'Free' : `${formatPrice(model.input_price_per_million)} · ${formatPrice(model.output_price_per_million)}`,
			},
			params?.active != null && params.is_moe
				? {
						label: 'active / total params',
						value: `${params.active_source === 'config_estimate' ? '~' : ''}${fmtParams(params.active)} / ${fmtParams(params.reported_total ?? params.checkpoint_total)}`,
						title: `${params.active_note ?? ''}${params.reported_total ? '' : ' Total = checkpoint tensor count.'}${inherited}`,
					}
				: hf?.parameters
					? { label: 'checkpoint params', value: fmtParams(hf.parameters), title: `Every tensor stored in the Hugging Face repo${inherited}` }
					: null,
			released ? { label: 'released', value: released } : null,
			knowledge ? { label: 'knowledge', value: knowledge } : null,
			{ label: 'value', value: efficiency.grade, title: `Cost efficiency ${(model.cost_efficiency * 100).toFixed(0)}/100 — context per dollar, A+ best` },
			{ label: applicationCount === 1 ? 'app here' : 'apps here', value: applicationCount ?? '—' },
		] as (HeaderStat | null)[]
		).filter((s): s is HeaderStat => s !== null)
	);
</script>

<PageHeader title={shortModelName(model.model_name)} documentTitle={model.model_name} back={{ href: '/models', label: 'Models' }} nav={navSnippet}>
	{#snippet meta()}
		{#if model.is_free}
			<Badge variant="outline" class="border-success/30 bg-success/10 text-xs text-success-fg">
				<Gift class="mr-1 h-3 w-3" /> Free
			</Badge>
		{/if}
	{/snippet}
	<div class="flex min-w-0 flex-wrap items-center gap-x-3 gap-y-1 text-xs text-muted-foreground">
		<span class="inline-flex items-center gap-1.5 text-foreground">
			<ProviderIcon provider={model.provider} size={16} />
			{providerLabel}
		</span>
		<span aria-hidden="true" class="opacity-50">·</span>
		<button
			type="button"
			class="group/id inline-flex min-w-0 cursor-pointer items-center gap-1.5 rounded font-mono hover:text-foreground"
			title="Copy model ID"
			onclick={() => copyToClipboard(model.model_id)}
		>
			<span class="truncate">{model.model_id}</span>
			<Copy class="size-3 shrink-0 opacity-60 group-hover/id:opacity-100" aria-hidden="true" />
			<span class="sr-only">Copy model ID</span>
		</button>
		{#if lastUpdated}
			<span aria-hidden="true" class="opacity-50">·</span>
			<span>metadata from <time class="font-mono" datetime={model.updated_at}>{lastUpdated}</time></span>
		{/if}
		{#if view.inheritedFrom}
			<span aria-hidden="true" class="opacity-50">·</span>
			<span title="Same name on the serving provider; assumed same weights. This model's own runs are never pooled with the twin's.">
				external metadata inherited from
				{#if view.inheritedSlug}<a href="/models/{view.inheritedSlug}" class="font-mono text-primary hover:underline">{view.inheritedFrom}</a>{:else}<span class="font-mono">{view.inheritedFrom}</span>{/if}
			</span>
		{/if}
	</div>
	<p class="header-stats mt-1.5">
		{#each stats as s (s.label)}
			<span class="header-stat" title={s.title}><b class={s.muted ? 'font-normal text-muted-foreground' : undefined}>{s.value}</b> {s.label}</span>
		{/each}
	</p>
	{#snippet actions()}
		<div class="flex items-center gap-0.5">
			<Button variant="ghost" size="sm" class="size-8 p-0" href="https://openrouter.ai/models/{model.model_id}" target="_blank" rel="noopener noreferrer" title="View on OpenRouter" aria-label="View on OpenRouter">
				<Globe />
			</Button>
			<Button variant="ghost" size="sm" class="size-8 p-0" onclick={exportJson} title="Export model as JSON" aria-label="Export model as JSON">
				<Download />
			</Button>
			{#if onRefresh}
				<Button variant="ghost" size="sm" class="size-8 p-0" onclick={onRefresh} title="Refresh metadata from OpenRouter" aria-label="Refresh metadata from OpenRouter" disabled={refreshing}>
					<RefreshCw class={refreshing ? 'animate-spin' : ''} />
				</Button>
			{/if}
		</div>
		<Button variant="outline" size="sm" href="/models/compare?models={slug}" title="Compare with other models">
			<GitCompareArrows class="h-3.5 w-3.5" />Compare
		</Button>
		<Button size="sm" href="/generate?model={encodeURIComponent(slug)}" title="Generate an app with this model">
			<Sparkles class="h-3.5 w-3.5" />Generate app
		</Button>
	{/snippet}
</PageHeader>
