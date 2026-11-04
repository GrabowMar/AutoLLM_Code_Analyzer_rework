<script lang="ts">
	// Agent behaviour of one model, per harness version: descriptive aggregates of the parsed
	// trajectories (`contracts.harness.Trajectory`) and attempt time splits over the caller's
	// completed app_agentic runs. GET /api/generation/models/{modelId}/behaviour/. No tests, n always shown.
	import { onMount } from 'svelte';
	import * as Card from '$lib/components/ui/card';
	import { Badge } from '$lib/components/ui/badge';
	import LoadingState from '$lib/components/LoadingState.svelte';
	import ErrorAlert from '$lib/components/ErrorAlert.svelte';
	import EmptyState from '$lib/components/EmptyState.svelte';
	import Activity from '@lucide/svelte/icons/activity';
	import {
		getModelBehaviour,
		type BehaviourMetricKey,
		type BehaviourRateKey,
		type Describe,
		type HarnessBehaviour,
		type ModelBehaviour,
		type TimeSplitKey
	} from '$lib/api/trajectory';
	import { formatApiError } from '$lib/api/core';

	interface Props {
		modelId: number;
	}

	let { modelId }: Props = $props();

	let data = $state<ModelBehaviour | null>(null);
	let loading = $state(true);
	let error = $state('');

	type Kind = 'int' | 'seconds';
	type Row =
		| { group: 'metrics'; key: BehaviourMetricKey; label: string; kind: Kind; help: string }
		| { group: 'time_split'; key: TimeSplitKey; label: string; kind: Kind; help: string };
	const ROWS: Row[] = [
		{ group: 'metrics', key: 'steps', label: 'Steps', kind: 'int', help: 'Parsed harness steps per run (LLM turns, tools, edits, shell, messages)' },
		{ group: 'metrics', key: 'tool_calls', label: 'Tool calls', kind: 'int', help: 'Tool calls the harness reported' },
		{ group: 'metrics', key: 'edits', label: 'Edits', kind: 'int', help: 'File edits and writes' },
		{ group: 'metrics', key: 'shell_commands', label: 'Shell commands', kind: 'int', help: 'Commands run in the sandbox' },
		{ group: 'metrics', key: 'compactions', label: 'Compactions', kind: 'int', help: 'Context compactions' },
		{ group: 'metrics', key: 'subagent_calls', label: 'Sub-agent calls', kind: 'int', help: 'Calls handed to a sub-agent' },
		{ group: 'time_split', key: 'llm_s', label: 'LLM time', kind: 'seconds', help: 'Time inside LLM calls (attempt time split)' },
		{ group: 'time_split', key: 'tool_s', label: 'Tool time', kind: 'seconds', help: 'Time running tools (attempt time split)' },
		{ group: 'time_split', key: 'install_s', label: 'Install time', kind: 'seconds', help: 'Time installing dependencies (attempt time split)' },
		{ group: 'time_split', key: 'idle_s', label: 'Idle time', kind: 'seconds', help: 'Time with neither an LLM call nor a tool running' }
	];
	const RATES: { key: BehaviourRateKey; label: string }[] = [
		{ key: 'ended_on_llm_error', label: 'Ended on an LLM error' },
		{ key: 'max_turns_hit', label: 'Hit the turn limit' }
	];

	const harnesses = $derived((data?.harnesses ?? []).filter((h) => h.n_runs > 0));
	const anyRecords = $derived(harnesses.some((h) => h.n > 0));

	function isNum(value: unknown): value is number {
		return typeof value === 'number' && Number.isFinite(value);
	}

	function fmtPct(value: number | null | undefined): string {
		return isNum(value) ? `${Math.round(value * 100)}%` : '—';
	}

	function fmtSeconds(value: number): string {
		if (value < 60) return `${value < 10 ? value.toFixed(1) : Math.round(value)} s`;
		const total = Math.round(value);
		const h = Math.floor(total / 3600);
		const m = Math.floor((total % 3600) / 60);
		const sec = total % 60;
		return h ? `${h}h ${String(m).padStart(2, '0')}m` : `${m}m ${String(sec).padStart(2, '0')}s`;
	}

	function fmt(kind: Kind, value: number | null | undefined): string {
		if (!isNum(value)) return '—';
		if (kind === 'seconds') return fmtSeconds(value);
		return Number.isInteger(value) ? Math.round(value).toLocaleString() : value.toFixed(1);
	}

	function describe(h: HarnessBehaviour, row: Row): Describe | null {
		const d = row.group === 'metrics' ? h.metrics?.[row.key] : h.time_split?.[row.key];
		return d && d.n > 0 ? d : null;
	}

	function harnessKey(h: HarnessBehaviour): string {
		return `${h.harness}@${h.harness_version}`;
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
			How this model worked inside each agent harness version, from your completed agentic runs (mean, median and range per run).
			Descriptive only; compare within a harness.
		</Card.Description>
	</Card.Header>
	<Card.Content class="p-0">
		{#if loading}
			<LoadingState label="loading agent behaviour" class="py-10" />
		{:else if error}
			<div class="p-4"><ErrorAlert message={error} onretry={load} /></div>
		{:else if !harnesses.length}
			<EmptyState compact icon={Activity} title="No agentic runs yet" description="Generate apps with this model in an app experiment to see how it works inside a harness." />
		{:else if !anyRecords}
			<EmptyState
				compact
				icon={Activity}
				title="No parsed trajectories yet"
				description="{harnesses.reduce((n, h) => n + h.n_runs, 0)} completed agentic runs ({harnesses.map(harnessKey).join(', ')}) have no usable run with a stored trajectory: the counters are not measured."
			/>
		{:else}
			<div class="overflow-x-auto">
				<table>
					<thead>
						<tr>
							<th>Per run</th>
							{#each harnesses as h (harnessKey(h))}
								<th class="num" title="{h.harness} {h.harness_version} · parsers {h.parsers.join(', ') || '—'}">
									{h.harness}
									<span class="block font-mono text-[11px] normal-case tracking-normal text-muted-foreground">{h.harness_version}</span>
									<span class="block font-mono text-[11px] normal-case tracking-normal text-muted-foreground">n = {h.n}{h.n_runs !== h.n ? ` of ${h.n_runs}` : ''}</span>
								</th>
							{/each}
						</tr>
					</thead>
					<tbody>
						{#each ROWS as row (row.group + row.key)}
							<tr>
								<td title={row.help}>{row.label}</td>
								{#each harnesses as h (harnessKey(h))}
									{@const d = describe(h, row)}
									<td class="num">
										{#if d}
											{fmt(row.kind, d.mean)}
											<span class="cell-sub block text-muted-foreground">
												med {fmt(row.kind, d.median)} · {fmt(row.kind, d.min)}–{fmt(row.kind, d.max)}{d.n !== h.n ? ` · n=${d.n}` : ''}
											</span>
										{:else}
											<span class="text-muted-foreground" title="not measured">—</span>
										{/if}
									</td>
								{/each}
							</tr>
						{/each}
						{#each RATES as rate (rate.key)}
							<tr>
								<td>{rate.label}</td>
								{#each harnesses as h (harnessKey(h))}
									{@const r = h.rates?.[rate.key]}
									<td class="num">
										{#if r && r.n > 0}
											{fmtPct(r.share)}
											<span class="cell-sub block text-muted-foreground">{r.k} of {r.n} runs</span>
										{:else}
											<span class="text-muted-foreground" title="not measured">—</span>
										{/if}
									</td>
								{/each}
							</tr>
						{/each}
						<tr>
							<td title="Agent-check pass rate of the same usable runs; runs without a measured evaluation are left out, never counted as 0">Pass rate</td>
							{#each harnesses as h (harnessKey(h))}
								<td class="num">
									{#if h.pass_rate?.n}
										{fmtPct(h.pass_rate.mean)}
										<span class="cell-sub block text-muted-foreground">med {fmtPct(h.pass_rate.median)} · n={h.pass_rate.n}</span>
									{:else}
										<span class="text-muted-foreground" title="not measured">—</span>
									{/if}
								</td>
							{/each}
						</tr>
					</tbody>
				</table>
			</div>
			<div class="space-y-1 border-t px-4 py-3 text-[11px] text-muted-foreground">
				<div class="flex flex-wrap gap-1.5">
					{#each harnesses as h (harnessKey(h))}
						{#if h.n_unusable_excluded || h.n_without_trajectory}
							<Badge variant="secondary" class="font-mono">
								{harnessKey(h)}: {h.n_unusable_excluded ? `${h.n_unusable_excluded} not usable, excluded` : ''}{h.n_unusable_excluded && h.n_without_trajectory ? ' · ' : ''}{h.n_without_trajectory ? `${h.n_without_trajectory} without trajectory` : ''}
							</Badge>
						{/if}
						{#if h.parsers.length > 1}
							<Badge variant="secondary" class="font-mono">{harnessKey(h)}: {h.parsers.length} parser versions</Badge>
						{/if}
					{/each}
				</div>
				{#each data?.notes ?? [] as note (note)}<p>{note}</p>{/each}
			</div>
		{/if}
	</Card.Content>
</Card.Root>
