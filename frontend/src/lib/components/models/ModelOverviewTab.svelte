<script lang="ts">
	/**
	 * Overview tab: what the model is (description, model card facts from
	 * OpenRouter + models.dev + Hugging Face), how it did here (snapshot of the
	 * caller's measurements) and where it sits in the catalog.
	 */
	import * as Card from '$lib/components/ui/card';
	import { Badge } from '$lib/components/ui/badge';
	import ExternalLink from '@lucide/svelte/icons/external-link';
	import TriangleAlert from '@lucide/svelte/icons/triangle-alert';
	import Sparkles from '@lucide/svelte/icons/sparkles';
	import { Button } from '$lib/components/ui/button';
	import LoadingState from '$lib/components/LoadingState.svelte';
	import ErrorAlert from '$lib/components/ErrorAlert.svelte';
	import type { LLMModelDetail, LLMModelSummary, ModelInsights } from '$lib/api/client';
	import ModelComparisonChart from './ModelComparisonChart.svelte';
	import PercentileMeter from './PercentileMeter.svelte';
	import {
		compactNumber,
		fmtCI,
		fmtParams,
		fmtRate,
		formatPrice,
		formatTokens,
		getArchitecture,
		getCaps,
		getHuggingFace,
		getModelsDev,
		isoDay,
		stripMarkdown,
	} from './helpers';

	interface Props {
		model: LLMModelDetail;
		slug: string;
		insights: ModelInsights | null;
		insightsError: string;
		onRetryInsights: () => void;
		relatedModels: LLMModelSummary[];
		onOpenTab: (tab: string) => void;
	}

	let { model, slug, insights, insightsError, onRetryInsights, relatedModels, onOpenTab }: Props = $props();

	const caps = $derived(getCaps(model));
	const arch = $derived(getArchitecture(model));
	const md = $derived(getModelsDev(model));
	const hf = $derived(getHuggingFace(model));
	const hfId = $derived((caps.hugging_face_id as string | undefined) || hf?.id || '');
	const expiration = $derived(isoDay(caps.expiration_date));
	const aliasTarget = $derived((caps.alias_target as string | undefined) || '');
	const topProvider = $derived((caps.top_provider || {}) as Record<string, unknown>);
	const inputMods = $derived((arch.input_modalities as string[] | undefined) ?? md?.modalities?.input ?? []);
	const outputMods = $derived((arch.output_modalities as string[] | undefined) ?? md?.modalities?.output ?? []);
	const released = $derived(md?.release_date || isoDay(caps.created));
	const knowledge = $derived(md?.knowledge || isoDay(caps.knowledge_cutoff));

	interface Fact {
		label: string;
		value: string;
		mono?: boolean;
		href?: string;
		title?: string;
	}

	const facts = $derived.by<Fact[]>(() => {
		const rows: (Fact | null)[] = [
			{ label: 'Model ID', value: model.model_id, mono: true },
			{ label: 'Provider', value: model.provider },
			md?.family ? { label: 'Family', value: md.family, mono: true } : null,
			released ? { label: 'Released', value: released, mono: true, title: md?.release_date ? 'models.dev' : 'Added to OpenRouter' } : null,
			knowledge ? { label: 'Knowledge cutoff', value: knowledge, mono: true } : null,
			md?.last_updated ? { label: 'Last updated', value: md.last_updated, mono: true } : null,
			md?.open_weights != null || hfId
				? { label: 'Weights', value: hfId ? `Open · ${hfId}` : md?.open_weights ? 'Open' : 'Closed (API only)', mono: !!hfId, href: hfId ? `https://huggingface.co/${hfId}` : undefined }
				: null,
			hf?.license ? { label: 'License', value: hf.license_name || hf.license, mono: true } : null,
			hf?.parameters ? { label: 'Parameters', value: fmtParams(hf.parameters), mono: true, title: Object.entries(hf.parameters_by_dtype).map(([k, v]) => `${k}: ${fmtParams(v)}`).join(', ') } : null,
			hf?.architectures.length ? { label: 'Architecture', value: hf.architectures.join(', '), mono: true } : null,
			hf?.base_models.length ? { label: 'Base model', value: hf.base_models.join(', '), mono: true } : null,
			{ label: 'Modalities', value: `${inputMods.join(', ') || 'text'} → ${outputMods.join(', ') || 'text'}` },
			arch.tokenizer ? { label: 'Tokenizer', value: String(arch.tokenizer), mono: true } : null,
			arch.instruct_type ? { label: 'Instruct format', value: String(arch.instruct_type), mono: true } : null,
			{ label: 'Context', value: `${model.context_window.toLocaleString()} tokens` },
			model.max_output_tokens ? { label: 'Max output', value: `${model.max_output_tokens.toLocaleString()} tokens` } : null,
			{ label: 'Moderation', value: topProvider.is_moderated ? 'Moderated by the provider' : 'Unmoderated' },
			model.quantization ? { label: 'Quantization', value: model.quantization, mono: true } : null,
			model.parameter_size ? { label: 'Size', value: model.parameter_size, mono: true } : null,
			model.endpoint ? { label: 'Endpoint', value: model.endpoint.name, href: `/endpoints/${model.endpoint.slug}` } : null,
		];
		return rows.filter((r): r is Fact => r !== null);
	});

	const gen = $derived(insights?.generation);
	const pos = $derived(insights?.ranking?.positions);
	const cat = $derived(insights?.catalog);

	function percentileHint(p: number | null, n: number, more: string, less: string): string {
		if (p == null) return '';
		const share = Math.round(p * 100);
		return share >= 50 ? `${more} ${share}% of ${n} models` : `${less} ${100 - share}% of ${n} models`;
	}
</script>

<div class="space-y-4">
	{#if expiration}
		<div class="flex items-start gap-2 rounded-md border border-warning/40 bg-warning/10 px-3 py-2 text-sm text-warning-fg" role="status">
			<TriangleAlert class="mt-0.5 size-4 shrink-0" aria-hidden="true" />
			<span>OpenRouter retires this model on <b class="font-mono">{expiration}</b>. Experiments that pin it will stop running after that date.</span>
		</div>
	{/if}

	<div class="grid gap-4 lg:grid-cols-[minmax(0,3fr)_minmax(0,2fr)]">
		<div class="min-w-0 space-y-4">
			<Card.Root>
				<Card.Header class="pb-0"><Card.Title class="text-base">About</Card.Title></Card.Header>
				<Card.Content class="space-y-3">
					<p class="text-sm leading-relaxed break-words text-muted-foreground">
						{#if model.description}{stripMarkdown(model.description)}{:else}{model.model_name} by {model.provider}.{/if}
					</p>
					{#if aliasTarget}
						<p class="text-xs text-muted-foreground">Alias of <span class="font-mono text-foreground">{aliasTarget}</span> — requests are routed to that model.</p>
					{/if}
					<div class="flex flex-wrap items-center gap-x-4 gap-y-1.5 text-sm">
						{#if !model.is_local}
							<a href="https://openrouter.ai/{model.model_id}" target="_blank" rel="noopener noreferrer" class="inline-flex items-center gap-1 text-primary hover:underline">
								OpenRouter<ExternalLink class="size-3" aria-hidden="true" />
							</a>
						{/if}
						{#if md}
							<a href="https://models.dev/?search={encodeURIComponent(model.model_id)}" target="_blank" rel="noopener noreferrer" class="inline-flex items-center gap-1 text-primary hover:underline">
								models.dev<ExternalLink class="size-3" aria-hidden="true" />
							</a>
						{/if}
						{#if hfId}
							<a href="https://huggingface.co/{hfId}" target="_blank" rel="noopener noreferrer" class="inline-flex items-center gap-1 text-primary hover:underline">
								Hugging Face<ExternalLink class="size-3" aria-hidden="true" />
							</a>
						{/if}
						{#each hf?.arxiv ?? [] as paper (paper)}
							<a href="https://arxiv.org/abs/{paper}" target="_blank" rel="noopener noreferrer" class="inline-flex items-center gap-1 font-mono text-xs text-primary hover:underline">
								arXiv:{paper}<ExternalLink class="size-3" aria-hidden="true" />
							</a>
						{/each}
					</div>
					{#if hf}
						<p class="header-stats">
							<span class="header-stat"><b>{compactNumber(hf.downloads)}</b> downloads / 30 d</span>
							<span class="header-stat"><b>{compactNumber(hf.likes)}</b> likes</span>
							{#if hf.spaces_count}<span class="header-stat"><b>{compactNumber(hf.spaces_count)}</b> Spaces</span>{/if}
							{#if hf.gated}<Badge variant="outline" class="h-5 px-1.5 text-[11px]">Gated</Badge>{/if}
						</p>
					{/if}
				</Card.Content>
			</Card.Root>

			<Card.Root>
				<Card.Header class="pb-0">
					<Card.Title class="text-base">Model card</Card.Title>
					<Card.Description class="text-xs">OpenRouter catalog{md ? ', models.dev' : ''}{hf ? ', Hugging Face Hub' : ''}</Card.Description>
				</Card.Header>
				<Card.Content>
					<dl class="grid gap-x-6 gap-y-2.5 text-sm sm:grid-cols-2">
						{#each facts as fact (fact.label)}
							<div class="grid min-w-0 grid-cols-[7.5rem_minmax(0,1fr)] gap-2" title={fact.title}>
								<dt class="text-muted-foreground">{fact.label}</dt>
								<dd class="min-w-0 break-words {fact.mono ? 'font-mono text-xs leading-5' : ''}">
									{#if fact.href}
										<a href={fact.href} target={fact.href.startsWith('http') ? '_blank' : undefined} rel="noopener noreferrer" class="text-primary hover:underline">{fact.value}</a>
									{:else}{fact.value}{/if}
								</dd>
							</div>
						{/each}
					</dl>
				</Card.Content>
			</Card.Root>
		</div>

		<div class="min-w-0 space-y-4">
			<Card.Root>
				<Card.Header class="pb-0">
					<Card.Title class="text-base">On this platform</Card.Title>
					<Card.Description class="text-xs">Your generations with this model</Card.Description>
				</Card.Header>
				<Card.Content>
					{#if insightsError}
						<ErrorAlert message={insightsError} onretry={onRetryInsights} />
					{:else if !insights || !gen}
						<LoadingState label="loading measurements" class="py-6" />
					{:else if gen.jobs_total === 0}
						<p class="text-sm text-muted-foreground">Not generated with yet. Run it on a template to measure build success, conformance and code findings.</p>
						<Button size="sm" variant="outline" class="mt-3" href="/sample-generator?mode=agent&model={encodeURIComponent(slug)}">
							<Sparkles class="size-3.5" />Generate an app
						</Button>
					{:else}
						<dl class="grid grid-cols-2 gap-x-4 gap-y-3">
							<div>
								<dt class="kpi-label">Apps</dt>
								<dd class="font-mono text-xl font-medium tabular-nums">{gen.jobs_total}</dd>
								<dd class="text-[11px] text-muted-foreground">{gen.completed} completed</dd>
							</div>
							<div>
								<dt class="kpi-label">Build success</dt>
								<dd class="font-mono text-xl font-medium tabular-nums">{fmtRate(gen.success.rate)}</dd>
								<dd class="text-[11px] text-muted-foreground">{fmtCI(gen.success.ci_low, gen.success.ci_high) || '—'} · n={gen.success.n}</dd>
							</div>
							<div>
								<dt class="kpi-label">Conformance</dt>
								<dd class="font-mono text-xl font-medium tabular-nums">{fmtRate(gen.functional.rate)}</dd>
								<dd class="text-[11px] text-muted-foreground">{gen.functional.n ? `${fmtCI(gen.functional.ci_low, gen.functional.ci_high)} · n=${gen.functional.n}` : 'not evaluated'}</dd>
							</div>
							<div>
								<dt class="kpi-label">Rank</dt>
								<dd class="font-mono text-xl font-medium tabular-nums">{pos?.empirical ? `#${pos.empirical.rank}` : '—'}</dd>
								<dd class="text-[11px] text-muted-foreground">{pos?.empirical ? `of ${pos.empirical.of} measured models` : 'no measured quality yet'}</dd>
							</div>
						</dl>
						<button type="button" class="mt-3 cursor-pointer text-xs font-medium text-primary hover:underline" onclick={() => onOpenTab('results')}>
							All results →
						</button>
					{/if}
				</Card.Content>
			</Card.Root>

			<Card.Root>
				<Card.Header class="pb-0">
					<Card.Title class="text-base">Catalog position</Card.Title>
					<Card.Description class="text-xs">Against {cat?.catalog_size ?? '…'} OpenRouter models; the centre tick is the median</Card.Description>
				</Card.Header>
				<Card.Content class="space-y-3.5">
					{#if !cat}
						{#if !insightsError}<LoadingState label="loading catalog" class="py-6" />{/if}
					{:else}
						{#if !model.is_free}
							<PercentileMeter
								label="Input price / 1M"
								value={formatPrice(model.input_price_per_million)}
								percentile={cat.input_price.percentile}
								hint={percentileHint(cat.input_price.percentile, cat.input_price.n, 'Pricier than', 'Cheaper than')}
							/>
							<PercentileMeter
								label="Output price / 1M"
								value={formatPrice(model.output_price_per_million)}
								percentile={cat.output_price.percentile}
								hint={percentileHint(cat.output_price.percentile, cat.output_price.n, 'Pricier than', 'Cheaper than')}
							/>
						{/if}
						<PercentileMeter
							label="Context window"
							value={formatTokens(model.context_window)}
							percentile={cat.context_window.percentile}
							hint={percentileHint(cat.context_window.percentile, cat.context_window.n, 'Larger than', 'Smaller than')}
						/>
						{#if model.max_output_tokens}
							<PercentileMeter
								label="Max output"
								value={formatTokens(model.max_output_tokens)}
								percentile={cat.max_output_tokens.percentile}
								hint={percentileHint(cat.max_output_tokens.percentile, cat.max_output_tokens.n, 'Longer than', 'Shorter than')}
							/>
						{/if}
						{#each [['coding_index', 'AA coding index'], ['intelligence_index', 'AA intelligence index']] as const as [key, label] (key)}
							{@const e = cat.artificial_analysis[key]}
							{#if e.value != null}
								<PercentileMeter
									{label}
									value={String(e.value)}
									percentile={e.percentile}
									hint={percentileHint(e.percentile, e.n, 'Above', 'Below')}
								/>
							{/if}
						{/each}
						<p class="text-[11px] text-muted-foreground">{cat.provider_models} models from {model.provider} in the catalog.</p>
					{/if}
				</Card.Content>
			</Card.Root>
		</div>
	</div>

	<ModelComparisonChart provider={model.provider} {slug} {relatedModels} />
</div>
