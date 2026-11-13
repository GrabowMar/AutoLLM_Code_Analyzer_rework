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

	// The chart shows one metric at a time across harnesses (mean bar, median tick, min–max whisker);
	// the full harness × metric table stays one click away.
	type Pick = { id: string; label: string; help: string } & (
		| { type: 'describe'; row: Row }
		| { type: 'rate'; key: BehaviourRateKey }
		| { type: 'pass' }
	);
	const PICKS: Pick[] = [
		...ROWS.map((row) => ({ id: row.group + row.key, label: row.label, help: row.help, type: 'describe' as const, row })),
		...RATES.map((rt) => ({ id: rt.key, label: rt.label, help: 'Share of runs', type: 'rate' as const, key: rt.key })),
		{
			id: 'pass',
			label: 'Pass rate',
			help: 'Agent-check pass rate of the same usable runs; runs without a measured evaluation are left out, never counted as 0',
			type: 'pass' as const,
		},
	];
	let pickId = $state('metricssteps');
	const pick = $derived(PICKS.find((p) => p.id === pickId) ?? PICKS[0]);
	let showTable = $state(false);

	interface Bar {
		key: string;
		harness: string;
		version: string;
		n: number;
		mean: number | null;
		median: number | null;
		min: number | null;
		max: number | null;
		text: string;
		sub: string;
	}

	const bars = $derived.by<Bar[]>(() =>
		harnesses.map((h) => {
			const base = { key: harnessKey(h), harness: h.harness, version: h.harness_version };
			if (pick.type === 'describe') {
				const d = describe(h, pick.row);
				return {
					...base,
					n: d?.n ?? 0,
					mean: d?.mean ?? null,
					median: d?.median ?? null,
					min: d?.min ?? null,
					max: d?.max ?? null,
					text: d ? fmt(pick.row.kind, d.mean) : '—',
					sub: d ? `med ${fmt(pick.row.kind, d.median)} · ${fmt(pick.row.kind, d.min)}–${fmt(pick.row.kind, d.max)} · n=${d.n}` : 'not measured',
				};
			}
			if (pick.type === 'rate') {
				const rt = h.rates?.[pick.key];
				const ok = rt && rt.n > 0;
				return { ...base, n: rt?.n ?? 0, mean: ok ? rt.share : null, median: null, min: null, max: null, text: ok ? fmtPct(rt.share) : '—', sub: ok ? `${rt.k} of ${rt.n} runs` : 'not measured' };
			}
			const pr = h.pass_rate;
			const ok = !!pr?.n;
			return {
				...base,
				n: pr?.n ?? 0,
				mean: ok ? pr.mean : null,
				median: ok ? pr.median : null,
				min: ok ? pr.min : null,
				max: ok ? pr.max : null,
				text: ok ? fmtPct(pr.mean) : '—',
				sub: ok ? `med ${fmtPct(pr.median)} · n=${pr.n}` : 'not measured',
			};
		}),
	);
	/** Shares are on a 0–1 axis; counts and times on 0–the largest value any harness reached. */
	const axisMax = $derived(
		pick.type === 'describe' ? Math.max(1e-9, ...bars.map((b) => (isNum(b.max) ? b.max : isNum(b.mean) ? b.mean : 0))) : 1,
	);
	const pos = (v: number | null) => (isNum(v) ? Math.max(0, Math.min(1, v / axisMax)) * 100 : 0);

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
			Descriptive only; harnesses differ in what they count, so compare within a harness.
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
			<div class="bh-picks" role="radiogroup" aria-label="Metric">
				{#each PICKS as p (p.id)}
					<button
						type="button"
						role="radio"
						aria-checked={pickId === p.id}
						class="bh-pick"
						title={p.help}
						onclick={() => (pickId = p.id)}
					>{p.label}</button>
				{/each}
			</div>
			<ul class="bh-chart" aria-label="{pick.label} by harness">
				{#each bars as b (b.key)}
					<li class="bh-row">
						<div class="min-w-0">
							<div class="truncate text-sm font-medium">{b.harness}</div>
							<div class="truncate font-mono text-[11px] text-muted-foreground">{b.version}</div>
						</div>
						<div class="bh-track" aria-hidden="true">
							{#if isNum(b.mean)}
								{#if isNum(b.min) && isNum(b.max)}
									<span class="bh-range" style:left="{pos(b.min)}%" style:width="{Math.max(0.5, pos(b.max) - pos(b.min))}%"></span>
								{/if}
								<span class="bh-fill" style:width="{Math.max(0.6, pos(b.mean))}%"></span>
								{#if isNum(b.median)}<span class="bh-median" style:left="{pos(b.median)}%"></span>{/if}
							{/if}
						</div>
						<div class="text-right">
							<div class="font-mono text-sm font-medium tabular-nums {isNum(b.mean) ? '' : 'text-muted-foreground'}">{b.text}</div>
							<div class="truncate font-mono text-[11px] text-muted-foreground">{b.sub}</div>
						</div>
					</li>
				{/each}
			</ul>
			<p class="bh-legend">
				<span><span class="bh-key bh-key-fill"></span>mean</span>
				<span><span class="bh-key bh-key-median"></span>median</span>
				<span><span class="bh-key bh-key-range"></span>min – max</span>
				<button type="button" class="ml-auto cursor-pointer font-medium text-primary hover:underline" aria-expanded={showTable} onclick={() => (showTable = !showTable)}>
					{showTable ? 'Hide full table' : 'Full table'}
				</button>
			</p>
			{#if showTable}
			<div class="overflow-x-auto border-t border-border">
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
			{/if}
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

<style>
	.bh-picks {
		display: flex;
		flex-wrap: wrap;
		gap: 0.375rem;
		padding: 0.75rem 1.25rem;
		border-bottom: 1px solid var(--border);
	}
	.bh-pick {
		cursor: pointer;
		border-radius: 9999px;
		border: 1px solid var(--border);
		padding: 0.125rem 0.625rem;
		font-size: 0.75rem;
		line-height: 1.25rem;
		color: var(--muted-foreground);
		transition:
			color 150ms,
			background-color 150ms,
			border-color 150ms;
	}
	.bh-pick:hover {
		color: var(--foreground);
		border-color: color-mix(in oklab, var(--primary) 40%, var(--border));
	}
	.bh-pick[aria-checked='true'] {
		color: var(--accent-foreground);
		background: var(--accent);
		border-color: color-mix(in oklab, var(--primary) 45%, var(--border));
	}
	.bh-pick:focus-visible {
		outline: 2px solid var(--ring);
		outline-offset: 1px;
	}
	.bh-chart {
		padding: 0.5rem 0;
	}
	.bh-row {
		display: grid;
		grid-template-columns: minmax(7rem, 10rem) minmax(0, 1fr) minmax(9rem, 13rem);
		align-items: center;
		gap: 1rem;
		padding: 0.5rem 1.25rem;
	}
	.bh-track {
		position: relative;
		height: 1.25rem;
		border-radius: 0.25rem;
		background: color-mix(in oklab, var(--muted) 70%, transparent);
	}
	.bh-range {
		position: absolute;
		top: 50%;
		height: 0.75rem;
		transform: translateY(-50%);
		border-inline: 1.5px solid color-mix(in oklab, var(--foreground) 45%, transparent);
	}
	.bh-range::after {
		content: '';
		position: absolute;
		inset: 50% 0 auto 0;
		height: 1px;
		background: color-mix(in oklab, var(--foreground) 45%, transparent);
	}
	.bh-fill {
		position: absolute;
		inset: 0.375rem auto 0.375rem 0;
		border-radius: 0.1875rem;
		background: color-mix(in oklab, var(--primary) 75%, var(--muted-foreground));
	}
	.bh-median {
		position: absolute;
		top: 0.125rem;
		bottom: 0.125rem;
		width: 2px;
		margin-left: -1px;
		background: var(--chart-3, #c2850c);
	}
	.bh-legend {
		display: flex;
		flex-wrap: wrap;
		align-items: center;
		gap: 0.25rem 1rem;
		padding: 0.5rem 1.25rem 0.75rem;
		font-size: 0.6875rem;
		color: var(--muted-foreground);
	}
	.bh-legend > span {
		display: inline-flex;
		align-items: center;
		gap: 0.375rem;
	}
	.bh-key {
		display: inline-block;
		width: 0.875rem;
		height: 0.5rem;
		border-radius: 0.125rem;
	}
	.bh-key-fill {
		background: color-mix(in oklab, var(--primary) 75%, var(--muted-foreground));
	}
	.bh-key-median {
		width: 2px;
		height: 0.75rem;
		background: var(--chart-3, #c2850c);
	}
	.bh-key-range {
		height: 0.5rem;
		border-inline: 1.5px solid color-mix(in oklab, var(--foreground) 45%, transparent);
		background: linear-gradient(color-mix(in oklab, var(--foreground) 45%, transparent), color-mix(in oklab, var(--foreground) 45%, transparent)) center / 100% 1px no-repeat;
	}
	@media (max-width: 640px) {
		.bh-row {
			grid-template-columns: minmax(0, 1fr) auto;
			padding-inline: 1rem;
		}
		.bh-track {
			grid-column: 1 / -1;
			grid-row: 2;
		}
		.bh-picks,
		.bh-legend {
			padding-inline: 1rem;
		}
	}
</style>
