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
	import ErrorAlert from '$lib/components/ErrorAlert.svelte';
	import type { LLMModelDetail, LLMModelSummary, ModelInsights } from '$lib/api/client';
	import ModelComparisonChart from './ModelComparisonChart.svelte';
	import PercentileMeter from './PercentileMeter.svelte';
	import ModelHfEcosystem from './ModelHfEcosystem.svelte';
	import InheritedNote from './InheritedNote.svelte';
	import {
		compactNumber,
		fmtActiveParams,
		fmtCI,
		fmtParams,
		fmtRate,
		fmtSpaces,
		formatPrice,
		formatTokens,
		getArchitecture,
		getCaps,
		isoDay,
		knownTokenLimit,
		metadataView,
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
	// A local endpoint model shows its catalog twin's external metadata (flagged as inherited).
	const view = $derived(metadataView(model));
	const md = $derived(view.md);
	const hf = $derived(view.hf);
	const params = $derived(view.params);
	const hfId = $derived((view.caps.hugging_face_id as string | undefined) || hf?.id || '');
	const expiration = $derived(isoDay(caps.expiration_date));
	const aliasTarget = $derived((caps.alias_target as string | undefined) || '');
	const topProvider = $derived((caps.top_provider || {}) as Record<string, unknown>);
	const inputMods = $derived((arch.input_modalities as string[] | undefined) ?? md?.modalities?.input ?? []);
	const outputMods = $derived((arch.output_modalities as string[] | undefined) ?? md?.modalities?.output ?? []);
	const released = $derived(md?.release_date || isoDay(caps.created));
	const knowledge = $derived(md?.knowledge || isoDay(caps.knowledge_cutoff));
	// 0 = not reported (local endpoint models): shown as "—", never compared with the catalog.
	const contextWindow = $derived(knownTokenLimit(model.context_window));
	const maxOutput = $derived(knownTokenLimit(model.max_output_tokens));

	interface Fact {
		label: string;
		value: string;
		mono?: boolean;
		href?: string;
		title?: string;
		/** Unknown value: the "—" renders muted. */
		muted?: boolean;
	}

	const facts = $derived.by<Fact[]>(() => {
		const rows: (Fact | null)[] = [
			{ label: 'Model ID', value: model.model_id, mono: true },
			{ label: 'Provider', value: model.provider },
			md?.family ? { label: 'Family', value: md.family, mono: true } : null,
			released ? { label: 'Released', value: released, mono: true, title: md?.release_date ? 'models.dev' : 'Added to OpenRouter' } : null,
			knowledge ? { label: 'Knowledge cutoff', value: knowledge, mono: true } : null,
			md?.last_updated ? { label: 'Last updated', value: md.last_updated, mono: true } : null,
			md?.open_weights != null || hfId ? weightsFact() : null,
			hf?.license ? { label: 'License', value: hf.license_name || hf.license, mono: true } : null,
			hf?.parameters
				? {
						label: 'Checkpoint parameters',
						value: fmtParams(hf.parameters),
						mono: true,
						title: `Every tensor stored in the repo (safetensors count). ${Object.entries(hf.parameters_by_dtype).map(([k, v]) => `${k}: ${fmtParams(v)}`).join(', ')}`,
					}
				: null,
			params?.reported_total ? { label: 'Reported total', value: fmtParams(params.reported_total), mono: true, title: params.active_note ?? undefined } : null,
			params ? { label: 'Active parameters', value: fmtActiveParams(params), mono: true, title: params.active_note ?? 'Unknown: no curated figure, name pattern or covered architecture' } : null,
			hf?.architectures.length ? { label: 'Architecture', value: hf.architectures.join(', '), mono: true } : null,
			hf?.base_models.length ? { label: 'Base model', value: hf.base_models.join(', '), mono: true } : null,
			{ label: 'Modalities', value: `${inputMods.join(', ') || 'text'} → ${outputMods.join(', ') || 'text'}` },
			arch.tokenizer ? { label: 'Tokenizer', value: String(arch.tokenizer), mono: true } : null,
			arch.instruct_type ? { label: 'Instruct format', value: String(arch.instruct_type), mono: true } : null,
			contextWindow != null
				? { label: 'Context', value: `${contextWindow.toLocaleString()} tokens` }
				: { label: 'Context', value: '—', muted: true, title: 'Context window not reported by the source' },
			maxOutput != null ? { label: 'Max output', value: `${maxOutput.toLocaleString()} tokens` } : null,
			{ label: 'Moderation', value: topProvider.is_moderated ? 'Moderated by the provider' : 'Unmoderated' },
			model.quantization ? { label: 'Quantization', value: model.quantization, mono: true } : null,
			model.parameter_size ? { label: 'Size', value: model.parameter_size, mono: true } : null,
			model.endpoint ? { label: 'Endpoint', value: model.endpoint.name, href: `/endpoints/${model.endpoint.slug}` } : null,
		];
		return rows.filter((r): r is Fact => r !== null);
	});

	/** Open only when models.dev does not say otherwise: OpenRouter links some API models to an open sibling repo. */
	function weightsFact(): Fact {
		if (hfId && md?.open_weights === false) {
			return {
				label: 'Weights',
				value: `Closed (API model) · linked repo ${hfId} may differ`,
				mono: true,
				href: `https://huggingface.co/${hfId}`,
				title: 'models.dev lists this model as closed-weights; OpenRouter links an open repo (often a sibling release)',
			};
		}
		if (hfId) return { label: 'Weights', value: `Open · ${hfId}`, mono: true, href: `https://huggingface.co/${hfId}` };
		return { label: 'Weights', value: md?.open_weights ? 'Open' : 'Closed (API only)' };
	}

	const gen = $derived(insights?.generation);
	const pos = $derived(insights?.ranking?.positions);
	const cat = $derived(insights?.catalog);

	function fmtDuration(sec: number | null | undefined): string {
		if (sec == null) return '—';
		if (sec < 60) return `${Math.round(sec)}s`;
		const m = Math.floor(sec / 60);
		return m < 60 ? `${m}m ${Math.round(sec % 60)}s` : `${Math.floor(m / 60)}h ${m % 60}m`;
	}

	function fmtUsd(v: number | null | undefined): string {
		if (v == null) return '—';
		if (v === 0) return '$0';
		return v < 0.01 ? `$${v.toFixed(4)}` : `$${v.toFixed(2)}`;
	}

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

	<!-- How it did here first: the platform's own measurements are why this page exists. -->
	<section class="snap" aria-labelledby="snap-title">
		<div class="snap-head">
			<h2 id="snap-title" class="snap-title">On this platform</h2>
			<span class="text-xs text-muted-foreground">
				{#if gen && gen.runs_total > 0 && gen.first_run_at}Your runs · {isoDay(gen.first_run_at)} – {isoDay(gen.last_run_at)}{:else}Your generations with this model{/if}
			</span>
			{#if gen && gen.runs_total > 0}
				<button type="button" class="snap-link" onclick={() => onOpenTab('results')}>All results →</button>
			{/if}
		</div>
		{#if insightsError}
			<div class="p-4"><ErrorAlert message={insightsError} onretry={onRetryInsights} /></div>
		{:else if !insights || !gen}
			<div class="snap-grid" aria-busy="true" aria-label="Loading measurements">
				{#each Array(6) as _, i (i)}
					<div class="snap-kpi"><span class="skel h-2.5 w-16"></span><span class="skel mt-1 h-6 w-14"></span><span class="skel h-2.5 w-20"></span></div>
				{/each}
			</div>
		{:else if gen.runs_total === 0}
			<div class="flex flex-wrap items-center justify-between gap-3 px-4 py-4">
				<p class="text-sm text-muted-foreground">Not generated with yet. Run it on a task to measure build success, conformance and code findings.</p>
				<Button size="sm" href="/generate?model={encodeURIComponent(slug)}"><Sparkles class="size-3.5" />Generate an app</Button>
			</div>
		{:else}
			{@const res = gen.resources}
			<dl class="snap-grid">
				<div class="snap-kpi">
					<dt class="snap-label">Apps</dt>
					<dd class="snap-val">{gen.runs_total.toLocaleString()}</dd>
					<dd class="snap-sub">{gen.completed.toLocaleString()} completed</dd>
				</div>
				<div class="snap-kpi" title="Built and started (Wilson 95% interval)">
					<dt class="snap-label">Build success</dt>
					<dd class="snap-val">{fmtRate(gen.success.rate)}</dd>
					<dd class="snap-sub">{fmtCI(gen.success.ci_low, gen.success.ci_high) || '—'} · n={gen.success.n}</dd>
				</div>
				<div class="snap-kpi" title="Passed the hidden API conformance suite (Wilson 95% interval)">
					<dt class="snap-label">Conformance</dt>
					<dd class="snap-val">{fmtRate(gen.functional.rate)}</dd>
					<dd class="snap-sub">{gen.functional.n ? `${fmtCI(gen.functional.ci_low, gen.functional.ci_high)} · n=${gen.functional.n}` : 'not evaluated'}</dd>
				</div>
				<div class="snap-kpi">
					<dt class="snap-label">Rank</dt>
					<dd class="snap-val">{pos?.empirical ? `#${pos.empirical.rank}` : '—'}</dd>
					<dd class="snap-sub">{pos?.empirical ? `of ${pos.empirical.of} measured` : 'no measured quality yet'}</dd>
				</div>
				<div class="snap-kpi">
					<dt class="snap-label">Cost / app</dt>
					<dd class="snap-val">{fmtUsd(res.mean_cost)}</dd>
					<dd class="snap-sub">{fmtUsd(res.total_cost)} total</dd>
				</div>
				<div class="snap-kpi">
					<dt class="snap-label">Duration</dt>
					<dd class="snap-val">{fmtDuration(res.median_duration)}</dd>
					<dd class="snap-sub">median per app</dd>
				</div>
			</dl>
		{/if}
	</section>

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
							{#if hf.downloads_all_time != null}<span class="header-stat"><b>{compactNumber(hf.downloads_all_time)}</b> all-time</span>{/if}
							<span class="header-stat"><b>{compactNumber(hf.likes)}</b> likes</span>
							{#if hf.spaces_count}<span class="header-stat" title={hf.spaces_count_capped ? 'The Hub lists at most 100 Spaces' : undefined}><b>{fmtSpaces(hf)}</b> Spaces</span>{/if}
							{#if hf.gated}<Badge variant="outline" class="h-5 px-1.5 text-[11px]">Gated</Badge>{/if}
						</p>
					{/if}
				</Card.Content>
			</Card.Root>

			<Card.Root>
				<Card.Header class="pb-0">
					<Card.Title class="text-base">Model card</Card.Title>
					<Card.Description class="text-xs">{model.is_local ? 'Endpoint inventory' : 'OpenRouter catalog'}{md ? ', models.dev' : ''}{hf ? ', Hugging Face Hub' : ''}</Card.Description>
					<InheritedNote from={view.inheritedFrom} slug={view.inheritedSlug} source={view.twin?.link.source ?? null} />
				</Card.Header>
				<Card.Content>
					<dl class="grid gap-x-6 gap-y-2.5 text-sm sm:grid-cols-2">
						{#each facts as fact (fact.label)}
							<div class="grid min-w-0 grid-cols-[7.5rem_minmax(0,1fr)] gap-2" title={fact.title}>
								<dt class="text-muted-foreground">{fact.label}</dt>
								<dd class="min-w-0 break-words {fact.mono ? 'font-mono text-xs leading-5' : ''} {fact.muted ? 'text-muted-foreground' : ''}">
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
					<Card.Title class="text-base">Catalog position</Card.Title>
					<Card.Description class="text-xs">Against {cat?.catalog_size ?? '…'} OpenRouter models; the centre tick is the median</Card.Description>
				</Card.Header>
				<Card.Content class="space-y-3.5">
					{#if !cat}
						{#if !insightsError}
							{#each Array(4) as _, i (i)}
								<div class="space-y-1.5" aria-hidden="true"><span class="skel h-2.5 w-24"></span><span class="skel h-1.5 w-full"></span></div>
							{/each}
						{/if}
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
						{#if contextWindow != null}
							<PercentileMeter
								label="Context window"
								value={formatTokens(contextWindow)}
								percentile={cat.context_window.percentile}
								hint={percentileHint(cat.context_window.percentile, cat.context_window.n, 'Larger than', 'Smaller than')}
							/>
						{:else}
							<div class="flex items-baseline justify-between gap-3" title="Context window not reported by the source; not placed in the catalog">
								<span class="text-xs text-muted-foreground">Context window</span>
								<span class="font-mono text-sm text-muted-foreground">—</span>
							</div>
						{/if}
						{#if maxOutput != null}
							<PercentileMeter
								label="Max output"
								value={formatTokens(maxOutput)}
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
			{#if hf}
				<ModelHfEcosystem {hf} inheritedFrom={view.inheritedFrom} inheritedSlug={view.inheritedSlug} />
			{/if}
		</div>
	</div>

	<ModelComparisonChart provider={model.provider} {slug} {relatedModels} />
</div>

<style>
	.snap {
		overflow: hidden;
		border: 1px solid var(--border);
		border-radius: var(--radius);
		background: var(--card);
		box-shadow: var(--shadow-card);
	}
	.snap-head {
		display: flex;
		flex-wrap: wrap;
		align-items: baseline;
		gap: 0.25rem 0.75rem;
		padding: 0.875rem 1.25rem 0.625rem;
		border-bottom: 1px solid var(--border);
	}
	.snap-title {
		font-family: var(--font-display);
		font-size: 1rem;
		font-weight: 600;
	}
	.snap-link {
		margin-left: auto;
		cursor: pointer;
		font-size: 0.75rem;
		font-weight: 500;
		color: var(--primary);
	}
	.snap-link:hover {
		text-decoration: underline;
	}
	.snap-grid {
		display: grid;
		grid-template-columns: repeat(auto-fit, minmax(9rem, 1fr));
	}
	.snap-kpi {
		display: flex;
		min-width: 0;
		flex-direction: column;
		gap: 0.2rem;
		padding: 0.875rem 1.25rem 1rem;
		border-right: 1px solid var(--border);
	}
	.snap-kpi:last-child {
		border-right: 0;
	}
	.snap-label {
		font-family: var(--font-mono);
		font-size: 0.6875rem;
		text-transform: uppercase;
		letter-spacing: 0.1em;
		color: var(--muted-foreground);
	}
	.snap-val {
		font-family: var(--font-mono);
		font-size: 1.5rem;
		font-weight: 500;
		line-height: 1.1;
		letter-spacing: -0.02em;
		font-variant-numeric: tabular-nums;
	}
	.snap-sub {
		font-size: 0.6875rem;
		color: var(--muted-foreground);
		white-space: nowrap;
		overflow: hidden;
		text-overflow: ellipsis;
	}
	.skel {
		display: block;
		border-radius: 0.25rem;
		background: color-mix(in oklab, var(--muted-foreground) 18%, transparent);
		animation: skel-pulse 1.4s ease-in-out infinite;
	}
	@keyframes skel-pulse {
		50% {
			opacity: 0.45;
		}
	}
	@media (prefers-reduced-motion: reduce) {
		.skel {
			animation: none;
		}
	}
</style>
