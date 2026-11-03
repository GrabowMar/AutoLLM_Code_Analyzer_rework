<script lang="ts">
	/**
	 * CallsTab — per-call gateway telemetry of a sample (spec 9.5 `/runs/{id}/calls/`): request class, finish
	 * reason, served provider and response model, tokens (cached, reasoning), cost vs list price, latency,
	 * tokens/s, protocol mode and translation drops.
	 *
	 * Props: `run`, `version` (bump to reload).
	 */
	import { untrack } from 'svelte';
	import * as Card from '$lib/components/ui/card';
	import * as Select from '$lib/components/ui/select';
	import { Badge } from '$lib/components/ui/badge';
	import LoadingState from '$lib/components/LoadingState.svelte';
	import ErrorAlert from '$lib/components/ErrorAlert.svelte';
	import EmptyState from '$lib/components/EmptyState.svelte';
	import { tableFrame, TableToolbar } from '$lib/components/table';
	import Activity from '@lucide/svelte/icons/activity';
	import { formatApiError, isAbortError } from '$lib/api/core';
	import { listSampleCalls } from '$lib/api/v3/samples';
	import type { RunCallOut, RunOut } from '$lib/api/v3/types';
	import { fmtInt, fmtSeconds, fmtUsd } from './format';

	interface Props {
		run: RunOut;
		version?: number;
	}

	let { run, version = 0 }: Props = $props();

	let calls = $state<RunCallOut[]>([]);
	let loading = $state(true);
	let error = $state('');
	let attempt = $state('');
	let requestClass = $state('');

	$effect(() => {
		void version;
		const n = attempt ? Number(attempt) : undefined;
		const controller = new AbortController();
		loading = untrack(() => calls.length === 0);
		listSampleCalls(run.id, n, { signal: controller.signal })
			.then((rows) => {
				calls = rows;
				error = '';
			})
			.catch((e) => {
				if (!isAbortError(e)) error = formatApiError(e, 'Could not load the calls.');
			})
			.finally(() => (loading = false));
		return () => controller.abort();
	});

	const classes = $derived([...new Set(calls.map((c) => c.request_class ?? 'unknown'))].sort());
	const shown = $derived(requestClass ? calls.filter((c) => (c.request_class ?? 'unknown') === requestClass) : calls);
	const totals = $derived({
		cost: shown.reduce((s, c) => s + (c.cost ?? 0), 0),
		list: shown.reduce((s, c) => s + (c.list_cost ?? 0), 0),
		prompt: shown.reduce((s, c) => s + (c.prompt_tokens ?? 0), 0),
		completion: shown.reduce((s, c) => s + (c.completion_tokens ?? 0), 0),
		truncated: shown.filter((c) => c.finish_reason === 'length').length,
	});
	const lossy = $derived(shown.filter((c) => Object.values(c.translation_dropped ?? {}).some((n) => n > 0)).length);
</script>

{#if loading}
	<LoadingState label="loading calls" />
{:else if error}
	<ErrorAlert message={error} />
{:else if calls.length === 0}
	<EmptyState
		icon={Activity}
		title="No gateway calls recorded"
		description="Every model call goes through the per-run gateway; a run without calls made none (or never started)."
	/>
{:else}
	<Card.Root class="gap-0 overflow-clip py-0">
		<TableToolbar>
			{#snippet summary()}
				<span>{shown.length} calls · {fmtInt(totals.prompt)} in / {fmtInt(totals.completion)} out · {fmtUsd(totals.cost)} billed · {fmtUsd(totals.list)} list</span>
				{#if totals.truncated}<Badge variant="secondary">{totals.truncated} truncated (length)</Badge>{/if}
				{#if lossy}<Badge variant="secondary" title="The translator dropped request fields on these calls">{lossy} lossy translations</Badge>{/if}
			{/snippet}
			{#snippet controls()}
				{#if run.attempts.length > 1}
					<Select.Simple
						size="sm"
						aria-label="Attempt"
						bind:value={attempt}
						clearLabel="All attempts"
						placeholder="All attempts"
						items={run.attempts.map((a) => ({ value: String(a.attempt), label: `Attempt ${a.attempt}` }))}
					/>
				{/if}
				<Select.Simple
					size="sm"
					aria-label="Request class"
					bind:value={requestClass}
					clearLabel="All classes"
					placeholder="All classes"
					items={classes.map((c) => ({ value: c, label: c }))}
				/>
			{/snippet}
		</TableToolbar>
		<div class="table-frame" {@attach tableFrame}>
			<table>
				<thead>
					<tr>
						<th class="num">#</th>
						<th>Class</th>
						<th>Finish</th>
						<th>Provider / model</th>
						<th class="num">Prompt</th>
						<th class="num">Cached</th>
						<th class="num">Completion</th>
						<th class="num">Reasoning</th>
						<th class="num">Cost</th>
						<th class="num">Latency</th>
						<th class="num">tok/s</th>
						<th>Protocol</th>
					</tr>
				</thead>
				<tbody>
					{#each shown as call (`${call.attempt}:${call.index}`)}
						{@const dropped = Object.entries(call.translation_dropped ?? {}).filter(([, n]) => n > 0)}
						<tr>
							<td class="num">{call.index}{#if run.attempts.length > 1}<div class="cell-sub">a{call.attempt}</div>{/if}</td>
							<td class="font-mono text-xs">{call.request_class ?? '—'}</td>
							<td class="font-mono text-xs">{call.finish_reason ?? '—'}{#if call.status && call.status >= 400}<div class="cell-sub">HTTP {call.status}</div>{/if}</td>
							<td class="text-xs">
								<span class="font-mono">{call.provider ?? '—'}</span>
								{#if call.response_model}<div class="cell-sub font-mono">{call.response_model}</div>{/if}
							</td>
							<td class="num">{fmtInt(call.prompt_tokens)}</td>
							<td class="num">{fmtInt(call.cached_tokens)}</td>
							<td class="num">{fmtInt(call.completion_tokens)}</td>
							<td class="num">{fmtInt(call.reasoning_tokens)}</td>
							<td class="num">
								{fmtUsd(call.cost)}
								{#if call.list_cost !== null && call.list_cost !== call.cost}<div class="cell-sub">list {fmtUsd(call.list_cost)}</div>{/if}
							</td>
							<td class="num">{fmtSeconds(call.ms === null ? null : call.ms / 1000)}</td>
							<td class="num">{call.tokens_per_s === null ? '—' : call.tokens_per_s.toFixed(1)}</td>
							<td class="text-xs">
								<span class="font-mono">{call.front_protocol ?? '—'}</span>
								{#if call.protocol_mode}<span class="text-muted-foreground"> · {call.protocol_mode}</span>{/if}
								{#if call.serving_tier}<div class="cell-sub">tier {call.serving_tier}</div>{/if}
								{#if dropped.length}<div class="cell-sub" title="Fields the translator dropped">dropped {dropped.map(([k, n]) => `${k}×${n}`).join(', ')}</div>{/if}
							</td>
						</tr>
					{/each}
				</tbody>
			</table>
		</div>
	</Card.Root>
{/if}
