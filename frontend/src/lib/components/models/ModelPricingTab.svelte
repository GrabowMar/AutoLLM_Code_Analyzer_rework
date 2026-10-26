<script lang="ts">
	/**
	 * Pricing & providers tab: every price OpenRouter lists (plus models.dev
	 * context tiers), a cost calculator seeded with the token use measured here,
	 * and the live per-provider endpoints (price, quantization, uptime, speed).
	 */
	import { onMount } from 'svelte';
	import * as Card from '$lib/components/ui/card';
	import { Badge } from '$lib/components/ui/badge';
	import { Button } from '$lib/components/ui/button';
	import RefreshCw from '@lucide/svelte/icons/refresh-cw';
	import LoadingState from '$lib/components/LoadingState.svelte';
	import ErrorAlert from '$lib/components/ErrorAlert.svelte';
	import { formatApiError } from '$lib/api/core';
	import { getModelEndpoints, type LLMModelDetail, type ModelInsights, type ProviderEndpoint, type ProviderEndpoints } from '$lib/api/client';
	import { compactNumber, fmtRate, fmtUsd, formatPrice, formatTokens, getCaps, getModelsDev } from './helpers';

	interface Props {
		model: LLMModelDetail;
		slug: string;
		insights: ModelInsights | null;
	}

	let { model, slug, insights }: Props = $props();

	// OpenRouter pricing keys: token prices are USD per token, the rest per unit.
	const PRICE_ROWS: { key: string; label: string; unit: 'token' | 'request' | 'image' | 'search' | 'second' }[] = [
		{ key: 'prompt', label: 'Input', unit: 'token' },
		{ key: 'completion', label: 'Output', unit: 'token' },
		{ key: 'input_cache_read', label: 'Cache read', unit: 'token' },
		{ key: 'input_cache_write', label: 'Cache write (5 min)', unit: 'token' },
		{ key: 'input_cache_write_1h', label: 'Cache write (1 h)', unit: 'token' },
		{ key: 'internal_reasoning', label: 'Reasoning', unit: 'token' },
		{ key: 'audio', label: 'Audio input', unit: 'token' },
		{ key: 'image', label: 'Image input', unit: 'image' },
		{ key: 'image_output', label: 'Image output', unit: 'token' },
		{ key: 'request', label: 'Per request', unit: 'request' },
		{ key: 'web_search', label: 'Web search', unit: 'search' },
	];

	const pricing = $derived((getCaps(model).pricing || {}) as Record<string, string>);
	const md = $derived(getModelsDev(model));
	const priceRows = $derived(
		PRICE_ROWS.map((r) => ({ ...r, value: parseFloat(pricing[r.key] ?? '') }))
			.filter((r) => Number.isFinite(r.value) && (r.value > 0 || r.key === 'prompt' || r.key === 'completion'))
	);
	const extraKeys = $derived(Object.keys(pricing).filter((k) => !PRICE_ROWS.some((r) => r.key === k) && k !== 'discount' && parseFloat(pricing[k]) > 0));
	const tiers = $derived(md?.cost?.tiers ?? []);

	// ── Calculator (raw token counts; seeded with measured per-app use) ──
	const measured = $derived(insights?.generation.resources);
	let calcIn = $state<number | null>(100_000);
	let calcOut = $state<number | null>(20_000);
	let cacheShare = $state<number | null>(0);
	let seeded = false;
	$effect(() => {
		if (seeded || !measured?.mean_prompt_tokens) return;
		seeded = true;
		calcIn = Math.round(measured.mean_prompt_tokens);
		calcOut = Math.round(measured.mean_completion_tokens ?? 0);
		cacheShare = measured.cache_hit_ratio != null ? Math.round(measured.cache_hit_ratio * 100) : 0;
	});
	const cacheRead = $derived(parseFloat(pricing.input_cache_read ?? '') || null);
	const calc = $derived.by(() => {
		const tin = Math.max(0, calcIn ?? 0);
		const tout = Math.max(0, calcOut ?? 0);
		const share = Math.min(100, Math.max(0, cacheShare ?? 0)) / 100;
		const cached = cacheRead != null ? tin * share : 0;
		const inCost = (tin - cached) * model.input_price_per_token + cached * (cacheRead ?? 0);
		const outCost = tout * model.output_price_per_token;
		return { inCost, outCost, total: inCost + outCost };
	});

	// ── Provider endpoints (live) ──
	let endpoints = $state<ProviderEndpoints | null>(null);
	let epLoading = $state(false);
	let epError = $state('');

	async function loadEndpoints(refresh = false) {
		epLoading = true;
		epError = '';
		try {
			endpoints = await getModelEndpoints(slug, refresh);
		} catch (e) {
			epError = formatApiError(e, 'Could not load providers.');
		} finally {
			epLoading = false;
		}
	}

	onMount(() => {
		if (!model.is_local) loadEndpoints();
	});

	/** OpenRouter returns latency/throughput as a number or a percentile object. */
	function p50(v: ProviderEndpoint['latency_last_30m']): number | null {
		if (v == null) return null;
		if (typeof v === 'number') return v;
		return v.p50 ?? v.median ?? null;
	}

	function uptime(v: number | null): string {
		return v == null ? '—' : `${v.toFixed(v >= 99.95 ? 0 : 1)}%`;
	}

	function perUnit(value: number, unit: string): string {
		return unit === 'token' ? formatPrice(value * 1_000_000) : `$${value < 0.01 ? value.toFixed(4) : value.toFixed(3)}`;
	}

	const UNIT_LABEL: Record<string, string> = { token: 'per 1M tokens', request: 'per request', image: 'per image', search: 'per search', second: 'per second' };
</script>

<div class="space-y-4">
	<div class="grid gap-4 lg:grid-cols-2">
		<Card.Root class="gap-0 overflow-clip py-0">
			<Card.Header class="border-b border-border py-3">
				<Card.Title class="text-sm font-medium">List prices</Card.Title>
				<Card.Description class="text-xs">OpenRouter, cheapest routing; providers below may differ</Card.Description>
			</Card.Header>
			<Card.Content class="p-0">
				<div class="overflow-x-auto">
					<table>
						<thead><tr><th>Type</th><th class="num">Price</th><th>Unit</th></tr></thead>
						<tbody>
							{#each priceRows as r (r.key)}
								<tr>
									<td class={r.unit === 'token' && (r.key === 'prompt' || r.key === 'completion') ? 'font-medium' : 'text-muted-foreground'}>{r.label}</td>
									<td class="num">{perUnit(r.value, r.unit)}</td>
									<td class="text-xs text-muted-foreground">{UNIT_LABEL[r.unit]}</td>
								</tr>
							{/each}
							{#each extraKeys as k (k)}
								<tr><td class="font-mono text-xs text-muted-foreground">{k}</td><td class="num">${pricing[k]}</td><td class="text-xs text-muted-foreground">raw</td></tr>
							{/each}
							{#if parseFloat(pricing.discount ?? '0') > 0}
								<tr><td class="text-muted-foreground">Discount</td><td class="num">{fmtRate(parseFloat(pricing.discount))}</td><td></td></tr>
							{/if}
						</tbody>
					</table>
				</div>
				{#if tiers.length}
					<div class="border-t border-border px-3 py-2.5">
						<div class="section-label mb-1.5">Long-context tiers (models.dev)</div>
						<ul class="space-y-1 text-xs">
							{#each tiers as t, i (i)}
								<li class="font-mono tabular-nums">
									<span class="text-muted-foreground">above {t.tier?.size ? formatTokens(t.tier.size) : '?'} {t.tier?.type ?? ''}:</span>
									{fmtUsd(t.input)} in · {fmtUsd(t.output)} out{#if t.cache_read != null} · {fmtUsd(t.cache_read)} cache read{/if}
								</li>
							{/each}
						</ul>
					</div>
				{/if}
			</Card.Content>
		</Card.Root>

		<Card.Root>
			<Card.Header class="pb-0">
				<Card.Title class="text-sm font-medium">Cost calculator</Card.Title>
				<Card.Description class="text-xs">
					{#if measured?.mean_prompt_tokens}Seeded with your measured mean per app ({compactNumber(measured.mean_prompt_tokens)} in, {compactNumber(measured.mean_completion_tokens)} out).{:else}At list price.{/if}
				</Card.Description>
			</Card.Header>
			<Card.Content class="space-y-3">
				<div class="grid gap-3 sm:grid-cols-3">
					<label class="block space-y-1 text-xs font-medium text-muted-foreground">
						Input tokens
						<input type="number" min="0" step="1000" bind:value={calcIn} class="h-9 w-full rounded-md border border-input bg-surface-1 px-3 text-sm tabular-nums text-foreground hover:border-primary/40 focus-visible:border-ring focus-visible:ring-2 focus-visible:ring-ring/30 focus-visible:outline-none disabled:cursor-not-allowed disabled:opacity-50" />
					</label>
					<label class="block space-y-1 text-xs font-medium text-muted-foreground">
						Output tokens
						<input type="number" min="0" step="1000" bind:value={calcOut} class="h-9 w-full rounded-md border border-input bg-surface-1 px-3 text-sm tabular-nums text-foreground hover:border-primary/40 focus-visible:border-ring focus-visible:ring-2 focus-visible:ring-ring/30 focus-visible:outline-none disabled:cursor-not-allowed disabled:opacity-50" />
					</label>
					<label class="block space-y-1 text-xs font-medium text-muted-foreground" title={cacheRead == null ? 'No cache price published' : 'Share of input tokens read from the prompt cache'}>
						Cached input %
						<input type="number" min="0" max="100" step="5" bind:value={cacheShare} disabled={cacheRead == null} class="h-9 w-full rounded-md border border-input bg-surface-1 px-3 text-sm tabular-nums text-foreground hover:border-primary/40 focus-visible:border-ring focus-visible:ring-2 focus-visible:ring-ring/30 focus-visible:outline-none disabled:cursor-not-allowed disabled:opacity-50" />
					</label>
				</div>
				<div class="flex flex-wrap items-end justify-between gap-3 rounded-md bg-muted/40 px-3 py-2.5">
					<div class="font-mono text-xs tabular-nums text-muted-foreground">
						{fmtUsd(calc.inCost)} input + {fmtUsd(calc.outCost)} output
					</div>
					<div class="text-right">
						<div class="kpi-label">Estimate</div>
						<div class="font-mono text-2xl font-medium tabular-nums {calc.total === 0 ? 'text-success-fg' : ''}">{calc.total === 0 ? 'Free' : fmtUsd(calc.total)}</div>
					</div>
				</div>
				{#if measured?.mean_cost != null}
					<p class="text-[11px] text-muted-foreground">Billed mean per app here: <span class="font-mono text-foreground">{fmtUsd(measured.mean_cost)}</span> (actual provider prices and cache use).</p>
				{/if}
			</Card.Content>
		</Card.Root>
	</div>

	{#if !model.is_local}
		<Card.Root class="gap-0 overflow-clip py-0">
			<Card.Header class="flex flex-row items-start justify-between gap-2 border-b border-border py-3">
				<div>
					<Card.Title class="text-sm font-medium">Providers <span class="font-mono text-xs text-muted-foreground">{endpoints?.endpoints.length ?? ''}</span></Card.Title>
					<Card.Description class="text-xs">
						Who serves this model through OpenRouter — live uptime, p50 latency and throughput over the last 30 min
						{#if endpoints?.fetched_at}· fetched {new Date(endpoints.fetched_at).toLocaleTimeString()}{/if}
					</Card.Description>
				</div>
				<Button variant="ghost" size="sm" class="size-8 p-0" onclick={() => loadEndpoints(true)} disabled={epLoading} title="Refetch providers" aria-label="Refetch providers">
					<RefreshCw class={epLoading ? 'animate-spin' : ''} />
				</Button>
			</Card.Header>
			<Card.Content class="p-0">
				{#if epError}
					<div class="p-4"><ErrorAlert message={epError} onretry={() => loadEndpoints(true)} /></div>
				{:else if !endpoints}
					<LoadingState label="loading providers" class="py-8" />
				{:else if !endpoints.available}
					<p class="p-4 text-sm text-muted-foreground">OpenRouter's endpoint list is unreachable right now.</p>
				{:else if endpoints.endpoints.length === 0}
					<p class="p-4 text-sm text-muted-foreground">No provider currently serves this model.</p>
				{:else}
					<div class="overflow-x-auto">
						<table>
							<thead>
								<tr>
									<th>Provider</th>
									<th>Quant.</th>
									<th class="num">Context</th>
									<th class="num">Max out</th>
									<th class="num">$ in / out</th>
									<th class="num">Cache read</th>
									<th class="num">Uptime 30m</th>
									<th class="num">Uptime 1d</th>
									<th class="num">Latency</th>
									<th class="num">Speed</th>
								</tr>
							</thead>
							<tbody>
								{#each endpoints.endpoints as ep (ep.tag || ep.provider_name)}
									{@const lat = p50(ep.latency_last_30m)}
									{@const tps = p50(ep.throughput_last_30m)}
									<tr>
										<td class="whitespace-nowrap">
											<span class="font-medium">{ep.provider_name}</span>
											{#if ep.status != null && ep.status < 0}<Badge variant="outline" class="ml-1.5 h-4 border-warning/40 px-1 text-[11px] text-warning-fg">degraded</Badge>{/if}
											<span class="cell-sub block font-mono text-[11px] text-muted-foreground" title="{ep.supported_parameters.length} parameters{ep.native_tools.length ? ` · tools: ${ep.native_tools.join(', ')}` : ''}">{ep.tag}</span>
										</td>
										<td class="font-mono text-xs {ep.quantization === 'unknown' ? 'text-muted-foreground' : ''}">{ep.quantization || '—'}</td>
										<td class="num">{ep.context_length ? formatTokens(ep.context_length) : '—'}</td>
										<td class="num">{ep.max_completion_tokens ? formatTokens(ep.max_completion_tokens) : '—'}</td>
										<td class="num whitespace-nowrap">{formatPrice(ep.input_price_per_million)} / {formatPrice(ep.output_price_per_million)}</td>
										<td class="num">{ep.cache_read_per_million != null ? formatPrice(ep.cache_read_per_million) : '—'}</td>
										<td class="num">{uptime(ep.uptime_last_30m)}</td>
										<td class="num">{uptime(ep.uptime_last_1d)}</td>
										<td class="num">{lat != null ? `${Math.round(lat)} ms` : '—'}</td>
										<td class="num">{tps != null ? `${Math.round(tps)} tok/s` : '—'}</td>
									</tr>
								{/each}
							</tbody>
						</table>
					</div>
				{/if}
			</Card.Content>
		</Card.Root>
	{/if}
</div>
