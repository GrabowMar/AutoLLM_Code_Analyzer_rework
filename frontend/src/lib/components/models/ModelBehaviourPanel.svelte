<script lang="ts">
	// Agent behaviour of one model, per harness (contract C4): descriptive
	// aggregates of metrics.behaviour over the caller's completed agent runs.
	// GET /api/generation/models/{modelId}/behaviour/. No tests, n always shown.
	import { onMount } from 'svelte';
	import * as Card from '$lib/components/ui/card';
	import { Badge } from '$lib/components/ui/badge';
	import LoadingState from '$lib/components/LoadingState.svelte';
	import ErrorAlert from '$lib/components/ErrorAlert.svelte';
	import EmptyState from '$lib/components/EmptyState.svelte';
	import Activity from '@lucide/svelte/icons/activity';
	import { getModelBehaviour, type Describe, type HarnessBehaviour, type ModelBehaviour } from '$lib/api/trajectory';
	import { formatApiError } from '$lib/api/core';
	import { fmtInt, fmtPct, fmtSeconds, fmtTokensShort, isNum } from '$lib/components/applications/agent/format';

	interface Props {
		modelId: number;
	}

	let { modelId }: Props = $props();

	let data = $state<ModelBehaviour | null>(null);
	let loading = $state(true);
	let error = $state('');

	type Kind = 'int' | 'seconds' | 'tokens' | 'share' | 'index';
	const ROWS: { key: string; label: string; kind: Kind; help: string }[] = [
		{ key: 'actions_total', label: 'Actions', kind: 'int', help: 'Tool calls per run (gateway log)' },
		{ key: 'failed_actions', label: 'Failed actions', kind: 'int', help: 'Non-zero exits, kills, failed edits, failing piped builds/tests (harness policy denials counted apart)' },
		{ key: 'killed_actions', label: 'Killed / timed out', kind: 'int', help: 'Commands killed by a signal or a harness timeout' },
		{ key: 'policy_denials', label: 'Policy denials', kind: 'int', help: "Tool calls refused by the harness's own permission rules" },
		{ key: 'builds', label: 'Builds', kind: 'int', help: 'Build commands per run' },
		{ key: 'first_build_action_index', label: 'First build at action', kind: 'index', help: 'Action index of the first build (runs that built)' },
		{ key: 'files_written', label: 'Files written', kind: 'int', help: 'Distinct workspace files written or edited' },
		{ key: 'writes_after_first_build', label: 'Writes after first build', kind: 'int', help: 'File writes after the first build (runs that built)' },
		{ key: 'llm_seconds', label: 'LLM time', kind: 'seconds', help: 'Time inside LLM calls (gateway-measured, retry backoff of retried calls included)' },
		{ key: 'between_calls_seconds', label: 'Time between calls', kind: 'seconds', help: 'Tool execution and harness overhead' },
		{ key: 'context_tokens_first', label: 'First-call prompt', kind: 'tokens', help: 'Prompt tokens of the first call (harness overhead)' },
		{ key: 'context_tokens_max', label: 'Peak context', kind: 'tokens', help: 'Largest prompt of a main-role call' },
		{ key: 'cache_hit_ratio', label: 'Cache hit ratio', kind: 'share', help: 'Cached / prompt tokens' },
		{ key: 'reasoning_share', label: 'Reasoning share', kind: 'share', help: 'Reasoning / completion tokens' },
	];
	const RATES: { key: string; label: string }[] = [
		{ key: 'ran_server', label: 'Ran the server' },
		{ key: 'probed_http', label: 'Probed over HTTP' },
		{ key: 'ran_tests', label: 'Ran tests' },
	];

	const harnesses = $derived((data?.harnesses ?? []).filter((h) => h.n_jobs > 0));
	const anyRecords = $derived(harnesses.some((h) => h.n > 0));

	function fmt(kind: Kind, value: number | null | undefined): string {
		if (!isNum(value)) return '—';
		if (kind === 'seconds') return fmtSeconds(value);
		if (kind === 'tokens') return fmtTokensShort(value);
		if (kind === 'share') return fmtPct(value);
		return Number.isInteger(value) ? fmtInt(value) : value.toFixed(1);
	}

	function describe(h: HarnessBehaviour, key: string): Describe | null {
		const d = h.metrics?.[key];
		return d && d.n > 0 ? d : null;
	}

	async function load() {
		loading = true;
		error = '';
		try {
			data = await getModelBehaviour(modelId);
		} catch (e) {
			error = formatApiError(e);
		} finally {
			loading = false;
		}
	}

	onMount(load);
</script>

<Card.Root class="gap-0 overflow-clip py-0">
	<Card.Header class="border-b border-border py-3">
		<div class="flex items-center gap-2">
			<Activity class="h-4 w-4 text-muted-foreground" />
			<Card.Title class="text-base">Behaviour by harness</Card.Title>
		</div>
		<Card.Description class="text-xs">
			How this model worked inside each agent harness, from your completed agent runs (mean, median and range per run).
			Descriptive only; compare within a harness.
		</Card.Description>
	</Card.Header>
	<Card.Content class="p-0">
		{#if loading}
			<LoadingState label="loading agent behaviour" class="py-10" />
		{:else if error}
			<div class="p-4"><ErrorAlert message={error} onretry={load} /></div>
		{:else if !harnesses.length}
			<EmptyState compact icon={Activity} title="No agent runs yet" description="Run this model in agent mode to see how it builds apps." />
		{:else if !anyRecords}
			<EmptyState
				compact
				icon={Activity}
				title="No behaviour records yet"
				description="{harnesses.reduce((n, h) => n + h.n_jobs, 0)} completed agent runs ({harnesses.map((h) => h.harness).join(', ')}) have no trajectory record yet: records are built when a run finishes; older runs need backfill_agent_trajectories."
			/>
		{:else}
			<div class="overflow-x-auto">
				<table>
					<thead>
						<tr>
							<th>Per run</th>
							{#each harnesses as h (h.harness)}
								<th class="num" title="{h.harness} {h.harness_versions.join(', ')}">
									{h.harness}
									<span class="block font-mono text-[11px] normal-case tracking-normal text-muted-foreground">n = {h.n}{h.n_jobs !== h.n ? ` of ${h.n_jobs}` : ''}</span>
									{#if h.max_llm_requests?.length}
										<span class="block font-mono text-[11px] normal-case tracking-normal text-muted-foreground" title="LLM-request budgets of these runs: counts and times scale with the budget">budget {h.max_llm_requests.join(' / ')}</span>
									{/if}
								</th>
							{/each}
						</tr>
					</thead>
					<tbody>
						{#each ROWS as row (row.key)}
							<tr>
								<td title={row.help}>{row.label}</td>
								{#each harnesses as h (h.harness)}
									{@const d = describe(h, row.key)}
									<td class="num">
										{#if d}
											{fmt(row.kind, d.mean)}
											<span class="cell-sub block text-muted-foreground">
												med {fmt(row.kind, d.median)} · {fmt(row.kind, d.min)}–{fmt(row.kind, d.max)}{d.n !== h.n ? ` · n=${d.n}` : ''}
											</span>
										{:else}
											<span class="text-muted-foreground">—</span>
										{/if}
									</td>
								{/each}
							</tr>
						{/each}
						{#each RATES as rate (rate.key)}
							<tr>
								<td>{rate.label}</td>
								{#each harnesses as h (h.harness)}
									{@const r = h.rates?.[rate.key]}
									<td class="num">
										{#if r && r.n > 0}
											{fmtPct(r.share)}
											<span class="cell-sub block text-muted-foreground">{r.k} of {r.n} runs</span>
										{:else}
											<span class="text-muted-foreground">—</span>
										{/if}
									</td>
								{/each}
							</tr>
						{/each}
						<tr>
							<td title="Conformance pass rate of the same valid runs (graded runs only)">Pass rate</td>
							{#each harnesses as h (h.harness)}
								<td class="num">
									{#if h.pass_rate?.n}
										{fmtPct(h.pass_rate.mean)}
										<span class="cell-sub block text-muted-foreground">med {fmtPct(h.pass_rate.median)} · n={h.pass_rate.n}</span>
									{:else}
										<span class="text-muted-foreground">—</span>
									{/if}
								</td>
							{/each}
						</tr>
					</tbody>
				</table>
			</div>
			<div class="space-y-1 border-t px-4 py-3 text-[11px] text-muted-foreground">
				<div class="flex flex-wrap gap-1.5">
					{#each harnesses as h (h.harness)}
						{#if h.n_invalid_excluded || h.n_without_record}
							<Badge variant="secondary" class="font-mono">
								{h.harness}: {h.n_invalid_excluded ? `${h.n_invalid_excluded} invalid excluded` : ''}{h.n_invalid_excluded && h.n_without_record ? ' · ' : ''}{h.n_without_record ? `${h.n_without_record} without record` : ''}
							</Badge>
						{/if}
						{#if !h.comparable && h.n}
							<Badge variant="secondary" class="font-mono">{h.harness}: no tool actions (edit-format harness)</Badge>
						{/if}
					{/each}
				</div>
				{#each data?.notes ?? [] as note (note)}<p>{note}</p>{/each}
			</div>
		{/if}
	</Card.Content>
</Card.Root>
