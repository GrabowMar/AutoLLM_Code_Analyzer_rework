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

	// Facts shared by every shown call are stated once above the table instead of on every row.
	function uniform<T>(pick: (c: RunCallOut) => T): T | undefined {
		if (!shown.length) return undefined;
		const first = pick(shown[0]);
		return shown.every((c) => pick(c) === first) ? first : undefined;
	}
	const sameProvider = $derived(uniform((c) => c.provider ?? null));
	const sameModel = $derived(uniform((c) => c.response_model ?? null));
	const sameProtocol = $derived(uniform((c) => `${c.front_protocol ?? ''}|${c.protocol_mode ?? ''}|${c.serving_tier ?? ''}`));
	const showProvider = $derived(sameProvider === undefined || sameModel === undefined);
	const showProtocol = $derived(sameProtocol === undefined);
	const showList = $derived(shown.some((c) => c.list_cost !== null && c.list_cost !== c.cost));
	const servedBy = $derived(
		[
			sameProvider ?? null,
			sameModel ?? null,
			sameProtocol ? sameProtocol.split('|').filter(Boolean).join(' · ') : null,
		].filter(Boolean) as string[],
	);

	function cachedPct(c: RunCallOut): string {
		if (!c.prompt_tokens || c.cached_tokens == null) return '—';
		return `${Math.round((c.cached_tokens / c.prompt_tokens) * 100)}%`;
	}
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
				{#if servedBy.length}<span class="calls-served" title="The same for every call shown">· all via <b>{servedBy.join(' · ')}</b></span>{/if}
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
						{#if showProvider}<th>Provider / model</th>{/if}
						<th class="num">Prompt</th>
						<th class="num" title="Share of the prompt served from the provider's cache">Cached</th>
						<th class="num">Completion</th>
						<th class="num">Reasoning</th>
						<th class="num">Cost</th>
						{#if showList}<th class="num" title="What the call would cost at catalogue prices">List</th>{/if}
						<th class="num">Latency</th>
						<th class="num">tok/s</th>
						{#if showProtocol}<th>Protocol</th>{:else}<th>Notes</th>{/if}
					</tr>
				</thead>
				<tbody>
					{#each shown as call (`${call.attempt}:${call.index}`)}
						{@const dropped = Object.entries(call.translation_dropped ?? {}).filter(([, n]) => n > 0)}
						<tr>
							<td class="num">{call.index}{#if run.attempts.length > 1}<div class="cell-sub">a{call.attempt}</div>{/if}</td>
							<td class="font-mono text-xs">{call.request_class ?? '—'}</td>
							<td class="font-mono text-xs">{call.finish_reason ?? '—'}{#if call.status && call.status >= 400}<div class="cell-sub">HTTP {call.status}</div>{/if}</td>
							{#if showProvider}
								<td class="text-xs">
									<span class="font-mono">{call.provider ?? '—'}</span>
									{#if call.response_model}<div class="cell-sub font-mono">{call.response_model}</div>{/if}
								</td>
							{/if}
							<td class="num">{fmtInt(call.prompt_tokens)}</td>
							<td class="num text-muted-foreground" title="{fmtInt(call.cached_tokens)} cached tokens">{cachedPct(call)}</td>
							<td class="num">{fmtInt(call.completion_tokens)}</td>
							<td class="num">{fmtInt(call.reasoning_tokens)}</td>
							<td class="num">{fmtUsd(call.cost)}</td>
							{#if showList}<td class="num text-muted-foreground">{fmtUsd(call.list_cost)}</td>{/if}
							<td class="num">{fmtSeconds(call.ms === null ? null : call.ms / 1000)}</td>
							<td class="num">{call.tokens_per_s === null ? '—' : call.tokens_per_s.toFixed(1)}</td>
							<td class="whitespace-nowrap text-xs">
								{#if showProtocol}
									<span class="font-mono">{call.front_protocol ?? '—'}</span>
									{#if call.protocol_mode}<span class="text-muted-foreground"> · {call.protocol_mode}</span>{/if}
									{#if call.serving_tier}<span class="text-muted-foreground"> · tier {call.serving_tier}</span>{/if}
								{/if}
								{#if dropped.length}
									<span class="calls-drop" title="Fields the translator dropped: {dropped.map(([k, n]) => `${k} ×${n}`).join(', ')}"
										>dropped {dropped.reduce((a, [, n]) => a + n, 0)}</span
									>
								{/if}
							</td>
						</tr>
					{/each}
				</tbody>
			</table>
		</div>
	</Card.Root>
{/if}

<style>
	.calls-served {
		color: var(--muted-foreground);
	}
	.calls-served b {
		font-family: var(--font-mono);
		font-weight: 500;
		color: var(--foreground);
	}
	.calls-drop {
		border-radius: 0.25rem;
		border: 1px solid color-mix(in oklab, var(--warning) 40%, var(--border));
		padding: 0 0.375rem;
		font-family: var(--font-mono);
		font-size: 0.6875rem;
		color: var(--warning-fg);
	}
</style>
