<script lang="ts">
	/**
	 * ModelProviderPerformance — per upstream provider call statistics for one
	 * model over the viewer's runs (`GET /generation/models/{id}/call-telemetry/`):
	 * TTFT (OpenRouter-measured, median · p90), decode and end-to-end output
	 * speed, call duration, cache-hit ratio, reasoning share, retry / error
	 * rates and how many calls have OpenRouter stats. Descriptive only, n on
	 * every value; unmeasured values are "—", never 0.
	 *
	 * Props: `modelId` (the model's numeric primary key). Self-fetching.
	 */
	import { untrack } from 'svelte';
	import * as Card from '$lib/components/ui/card';
	import { Segmented } from '$lib/components/ui/segmented';
	import LoadingState from '$lib/components/LoadingState.svelte';
	import ErrorAlert from '$lib/components/ErrorAlert.svelte';
	import EmptyState from '$lib/components/EmptyState.svelte';
	import { formatApiError, isAbortError } from '$lib/api/core';
	import {
		getModelCallTelemetry,
		type CallQuantiles,
		type CallRatio,
		type CallSummary,
		type ModelCallTelemetry,
	} from '$lib/api/telemetry';
	import Gauge from '@lucide/svelte/icons/gauge';
	import { kindLabel } from '$lib/components/runs/format';

	interface Props {
		modelId: number;
	}

	let { modelId }: Props = $props();

	const ALL = 'all';
	let kind = $state(ALL);
	let data = $state.raw<ModelCallTelemetry | null>(null);
	/** Run kinds present over all calls (kept while a kind filter is applied). */
	let kindsSeen = $state.raw<string[]>([]);
	let loading = $state(true);
	let error = $state('');
	let controller: AbortController | null = null;

	async function load() {
		controller?.abort();
		const mine = new AbortController();
		controller = mine;
		loading = true;
		error = '';
		try {
			const res = await getModelCallTelemetry(modelId, { kind: kind === ALL ? undefined : kind, signal: mine.signal });
			if (mine.signal.aborted) return;
			data = res;
			if (kind === ALL) kindsSeen = Object.keys(res.overall?.kinds ?? {});
		} catch (e) {
			if (mine.signal.aborted || isAbortError(e)) return;
			error = formatApiError(e, 'Could not load provider performance.');
		} finally {
			if (controller === mine) loading = false;
		}
	}

	// Another model (the page component is reused across /models/[slug]): start from all run kinds again.
	let loadedModel = untrack(() => modelId);
	$effect.pre(() => {
		const id = modelId;
		untrack(() => {
			if (id === loadedModel) return;
			loadedModel = id;
			kind = ALL;
			kindsSeen = [];
			data = null;
		});
	});

	$effect(() => {
		void [modelId, kind];
		untrack(() => void load());
		return () => controller?.abort();
	});

	const kindItems = $derived([
		{ value: ALL, label: 'All run kinds' },
		...kindsSeen.map((k) => ({ value: k, label: kindLabel(k) })),
	]);

	const rows = $derived.by(() => {
		if (!data) return [] as { key: string; label: string; summary: CallSummary; overall: boolean }[];
		const providers = (data.providers ?? []).map((p) => ({ key: `p:${p.provider}`, label: p.provider, summary: p as CallSummary, overall: false }));
		return [{ key: 'overall', label: 'All providers', summary: data.overall, overall: true }, ...providers];
	});

	function num(value: number | null | undefined, digits = 0): string {
		if (value == null || !Number.isFinite(value)) return '—';
		return value.toLocaleString(undefined, { minimumFractionDigits: digits, maximumFractionDigits: digits });
	}

	function pct(ratio: CallRatio | undefined): string {
		if (!ratio || ratio.value == null) return '—';
		return `${(ratio.value * 100).toFixed(1)}%`;
	}

	function quantile(q: CallQuantiles | undefined, digits = 0): string {
		if (!q || q.median == null) return '—';
		return q.p90 != null ? `${num(q.median, digits)} · ${num(q.p90, digits)}` : num(q.median, digits);
	}

	function counts(record: Record<string, number> | undefined): string {
		const entries = Object.entries(record ?? {});
		return entries.length ? entries.map(([k, n]) => `${k} ${n}`).join(', ') : '';
	}

	function coverage(s: CallSummary): string {
		const c = s.coverage;
		if (!c || !c.openrouter_eligible) return 'n/a';
		return `${c.openrouter_ok}/${c.openrouter_eligible}`;
	}
</script>

<section id="provider-performance">
	<Card.Root class="gap-0 overflow-clip py-0">
		<Card.Header class="border-b border-border py-3">
			<div class="flex flex-wrap items-start justify-between gap-3">
				<div class="min-w-0 space-y-1">
					<div class="flex items-center gap-2">
						<Gauge class="h-4 w-4 text-muted-foreground" aria-hidden="true" />
						<Card.Title class="text-sm font-medium">Provider performance</Card.Title>
					</div>
					<Card.Description class="text-xs">
						Per-call measurements by the upstream provider that served each call, over your runs of this model.
						{#if data?.overall}
							{data.overall.n_calls.toLocaleString()} calls in {data.overall.n_runs} run{data.overall.n_runs === 1 ? '' : 's'}.
						{/if}
					</Card.Description>
				</div>
				{#if kindItems.length > 2}
					<Segmented aria-label="Run kind" size="sm" bind:value={kind} items={kindItems} />
				{/if}
			</div>
		</Card.Header>
		<Card.Content class="p-0">
			{#if error}
				<div class="p-4"><ErrorAlert message={error} onretry={load} /></div>
			{:else if loading && !data}
				<LoadingState label="loading provider performance" class="py-10" />
			{:else if !data || !data.overall || data.overall.n_calls === 0}
				<EmptyState
					compact
					icon={Gauge}
					title="No LLM calls recorded"
					description="Provider statistics appear once runs of this model have recorded calls."
				/>
			{:else}
				<div class="overflow-x-auto" aria-busy={loading}>
					<table>
						<thead>
							<tr>
								<th>Provider</th>
								<th class="num" title="LLM calls (runs)">Calls</th>
								<th class="num" title="OpenRouter-measured time to first token, incl. provider queueing and prefill: median · p90">TTFT ms</th>
								<th class="num" title="Completion tokens / (generation time − TTFT), OpenRouter calls only: median">Decode tok/s</th>
								<th class="num" title="Platform-measured call duration; retried calls and calls without a recorded retry count excluded: median · p90">Call s</th>
								<th class="num" title="Completion tokens / call duration (includes prefill and queueing): median">End-to-end tok/s</th>
								<th class="num" title="Cached prompt tokens / prompt tokens">Cache hit</th>
								<th class="num" title="Reasoning tokens / completion tokens">Reasoning</th>
								<th class="num" title="Share of calls the gateway or client retried upstream">Retried</th>
								<th class="num" title="Share of calls that failed">Errors</th>
								<th class="num" title="Calls with an OpenRouter generation record / calls that can have one (gen- ids)">Stats</th>
							</tr>
						</thead>
						<tbody>
							{#each rows as row (row.key)}
								{@const s = row.summary}
								<tr class={row.overall ? 'bg-muted/40' : ''}>
									<td title={counts(s.finish_reasons) ? `Finish reasons: ${counts(s.finish_reasons)}` : undefined}>
										<span class={row.overall ? 'font-medium' : ''}>{row.label}</span>
										{#if Object.keys(s.model_permaslugs ?? {}).length}
											<span class="cell-sub block font-mono text-[11px] text-muted-foreground">{Object.keys(s.model_permaslugs).join(', ')}</span>
										{/if}
									</td>
									<td class="num">
										{s.n_calls.toLocaleString()}
										<span class="cell-sub block text-[11px] text-muted-foreground">{s.n_runs} run{s.n_runs === 1 ? '' : 's'}</span>
									</td>
									<td class="num {s.ttft_ms?.median == null ? 'text-muted-foreground' : ''}">
										{quantile(s.ttft_ms)}
										{#if s.ttft_ms?.n}<span class="cell-sub block text-[11px] text-muted-foreground">n={s.ttft_ms.n}</span>{/if}
									</td>
									<td class="num {s.decode_tps?.median == null ? 'text-muted-foreground' : ''}">
										{quantile(s.decode_tps, 1)}
										{#if s.decode_tps?.n}<span class="cell-sub block text-[11px] text-muted-foreground">n={s.decode_tps.n}</span>{/if}
									</td>
									<td class="num {s.duration_s?.median == null ? 'text-muted-foreground' : ''}">
										{quantile(s.duration_s, 2)}
										{#if s.duration_s?.n}<span class="cell-sub block text-[11px] text-muted-foreground">n={s.duration_s.n}</span>{/if}
									</td>
									<td class="num {s.e2e_output_tps?.median == null ? 'text-muted-foreground' : ''}">
										{quantile(s.e2e_output_tps, 1)}
										{#if s.e2e_output_tps?.n}<span class="cell-sub block text-[11px] text-muted-foreground">n={s.e2e_output_tps.n}</span>{/if}
									</td>
									<td class="num {s.cache_hit_ratio?.value == null ? 'text-muted-foreground' : ''}">
										{pct(s.cache_hit_ratio)}
										{#if s.cache_hit_ratio?.n}<span class="cell-sub block text-[11px] text-muted-foreground">n={s.cache_hit_ratio.n}</span>{/if}
									</td>
									<td class="num {s.reasoning_share?.value == null ? 'text-muted-foreground' : ''}">
										{pct(s.reasoning_share)}
										{#if s.reasoning_share?.n}<span class="cell-sub block text-[11px] text-muted-foreground">n={s.reasoning_share.n}</span>{/if}
									</td>
									<td class="num {s.retry_rate?.value == null ? 'text-muted-foreground' : ''}">
										{pct(s.retry_rate)}
										{#if s.retry_rate?.n}<span class="cell-sub block text-[11px] text-muted-foreground">n={s.retry_rate.n}</span>{/if}
									</td>
									<td class="num {s.error_rate?.value == null ? 'text-muted-foreground' : ''}">{pct(s.error_rate)}</td>
									<td class="num {coverage(s) === 'n/a' ? 'text-muted-foreground' : ''}">{coverage(s)}</td>
								</tr>
							{/each}
						</tbody>
					</table>
				</div>
				{#if data.notes?.length}
					<ul class="space-y-1 border-t border-border px-3 py-2 text-xs leading-relaxed text-muted-foreground">
						{#each data.notes as note, i (i)}
							<li>{note}</li>
						{/each}
					</ul>
				{/if}
			{/if}
		</Card.Content>
	</Card.Root>
</section>
