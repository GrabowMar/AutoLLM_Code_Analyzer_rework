<script lang="ts">
	/**
	 * TrajectoryTab — the harness trajectory of a sample (spec 9.5, `contracts.harness.Trajectory`): step
	 * counts, the steps table (kind, tool, ok, duration, tokens, files), the final message and the observed
	 * inputs. While the run is going it shows the live `trajectory_step` events of the stream.
	 *
	 * Props: `run`, `liveSteps` / `liveStepCount` (from `SampleLive`), `version` (bump to reload).
	 */
	import * as Card from '$lib/components/ui/card';
	import { Badge } from '$lib/components/ui/badge';
	import * as Select from '$lib/components/ui/select';
	import { Segmented } from '$lib/components/ui/segmented';
	import LoadingState from '$lib/components/LoadingState.svelte';
	import ErrorAlert from '$lib/components/ErrorAlert.svelte';
	import EmptyState from '$lib/components/EmptyState.svelte';
	import { tableFrame } from '$lib/components/table';
	import ListTree from '@lucide/svelte/icons/list-tree';
	import { formatApiError, isAbortError } from '$lib/api/core';
	import { getSampleTrajectory } from '$lib/api/v3/samples';
	import type { RunOut, RunTrajectoryOut, TrajectoryStepOut } from '$lib/api/v3/types';
	import { fmtInt, fmtSeconds, groupTurns, isActiveStatus, shortSha, turnTools } from './format';
	import TrajectoryChart from './TrajectoryChart.svelte';
	import ChevronDown from '@lucide/svelte/icons/chevron-down';

	interface Props {
		run: RunOut;
		liveSteps?: TrajectoryStepOut[];
		liveStepCount?: number;
		version?: number;
	}

	let { run, liveSteps = [], liveStepCount = 0, version = 0 }: Props = $props();

	const attempts = $derived(run.attempts.map((a) => a.attempt));
	let attempt = $state<string>('');
	let data = $state<RunTrajectoryOut | null>(null);
	let loading = $state(false);
	let error = $state('');
	let missing = $state(false);
	let kindFilter = $state('all');

	$effect(() => {
		if (!attempt && attempts.length) attempt = String(attempts[attempts.length - 1]);
	});

	const running = $derived(isActiveStatus(run.status));

	$effect(() => {
		void version;
		const n = attempt ? Number(attempt) : undefined;
		if (running && liveSteps.length > 0) return;
		const controller = new AbortController();
		loading = true;
		error = '';
		missing = false;
		getSampleTrajectory(run.id, n, { signal: controller.signal })
			.then((out) => (data = out))
			.catch((e) => {
				if (isAbortError(e)) return;
				if ((e as { httpStatus?: number })?.httpStatus === 404) missing = true;
				else error = formatApiError(e, 'Could not load the trajectory.');
			})
			.finally(() => (loading = false));
		return () => controller.abort();
	});

	const steps = $derived(running && liveSteps.length > 0 ? liveSteps : (data?.trajectory?.steps ?? []));
	const shown = $derived(
		kindFilter === 'all'
			? steps
			: kindFilter === 'failed'
				? steps.filter((s) => s.ok === false)
				: steps.filter((s) => s.kind === kindFilter),
	);
	const kinds = $derived([...new Set(steps.map((s) => s.kind))].sort());
	const trajectory = $derived(data?.trajectory ?? null);
	// Parsers record `started` as the harness's own timestamp (epoch seconds for opencode and
	// mini-swe-agent), so the column shows the offset from the first timed step.
	const t0 = $derived.by(() => {
		const times = steps.map((s) => s.started).filter((t): t is number => typeof t === 'number');
		return times.length ? Math.min(...times) : 0;
	});
	const observed = $derived(data?.observed_inputs ?? null);

	// Turn view (default): each LLM call with the actions it triggered. "Steps" keeps the raw table.
	let view = $state<'turns' | 'steps'>('turns');
	const turns = $derived(groupTurns(steps));
	const shownTurns = $derived(kindFilter === 'failed' ? turns.filter((t) => t.failed > 0) : turns);
	const maxIn = $derived(Math.max(1, ...turns.map((t) => t.tokensIn)));
	let selectedTurn = $state<number | null>(null);
	let observedOpen = $state(false);

	function selectTurn(n: number) {
		selectedTurn = n;
		view = 'turns';
		kindFilter = 'all';
		requestAnimationFrame(() => document.getElementById(`turn-${n}`)?.scrollIntoView({ block: 'center', behavior: 'smooth' }));
	}

	const counts = $derived(
		trajectory
			? ([
					['steps', trajectory.steps.length],
					['turns', turns.filter((t) => t.llm).length],
					['tool calls', trajectory.tool_calls],
					['shell', trajectory.shell_commands],
					['edits', trajectory.edits],
					['compactions', trajectory.compactions],
					['subagent calls', trajectory.subagent_calls],
					['unparsed lines', trajectory.unparsed_lines],
				] as [string, number][])
			: [],
	);
</script>

<div class="space-y-4">
	{#if running}
		<p class="text-sm text-muted-foreground">
			Live: {liveStepCount || steps.length} step{(liveStepCount || steps.length) === 1 ? '' : 's'} so far
			{#if liveSteps.length === 0 && liveStepCount > 0}(this harness reports a step count only){/if}.
		</p>
	{/if}

	{#if trajectory}
		<section class="tj-card">
			<div class="tj-summary">
				{#each counts as [label, value] (label)}
					<span class={value ? '' : 'text-muted-foreground/70'}><b class={value ? '' : 'font-normal'}>{fmtInt(value)}</b> {label}</span>
				{/each}
				<span class="tj-tags">
					<Badge variant="outline">parser {trajectory.parser} v{trajectory.parser_version}</Badge>
					{#if trajectory.max_turns_hit}<Badge variant="secondary">max turns hit</Badge>{/if}
					{#if trajectory.ended_on_llm_error}<Badge variant="destructive">ended on an LLM error</Badge>{/if}
				</span>
			</div>
			{#if turns.filter((t) => t.llm).length > 1}
				<div class="tj-chart">
					<TrajectoryChart {turns} selected={selectedTurn} onselect={selectTurn} />
				</div>
			{/if}
		</section>
	{/if}

	<Card.Root class="gap-0 overflow-clip py-0">
		<div class="table-toolbar">
			<div class="flex flex-wrap items-center gap-2">
				{#if attempts.length > 1}
					<Select.Simple
						size="sm"
						aria-label="Attempt"
						bind:value={attempt}
						items={attempts.map((a) => ({ value: String(a), label: `Attempt ${a}` }))}
					/>
				{/if}
				<Segmented
					size="sm"
					aria-label="View"
					bind:value={view}
					items={[
						{ value: 'turns', label: 'Turns' },
						{ value: 'steps', label: 'Raw steps' },
					]}
				/>
				<Segmented
					size="sm"
					aria-label="Step filter"
					bind:value={kindFilter}
					items={[
						{ value: 'all', label: 'All' },
						{ value: 'failed', label: 'Failed' },
						...(view === 'steps' ? kinds.slice(0, 2).map((k) => ({ value: k, label: k })) : []),
					]}
				/>
			</div>
			<span class="text-xs text-muted-foreground">
				{#if view === 'turns'}{shownTurns.length} of {turns.length} turns{:else}{shown.length} of {steps.length} steps{/if}
			</span>
		</div>
		{#if loading && steps.length === 0}
			<LoadingState label="loading trajectory" />
		{:else if error}
			<div class="p-4"><ErrorAlert message={error} /></div>
		{:else if steps.length === 0}
			<EmptyState
				compact
				icon={ListTree}
				title={missing ? 'No trajectory recorded' : 'No steps'}
				description={running ? 'Steps appear as the harness works.' : 'The harness output could not be parsed into steps, or the attempt never started.'}
			/>
		{:else if view === 'turns'}
			<ol class="tj-turns">
				{#each shownTurns as t (t.n)}
					<li id="turn-{t.n}" class="tj-turn" data-selected={selectedTurn === t.n} data-failed={t.failed > 0}>
						<span class="tj-n">{t.n || '·'}</span>
						<div class="tj-ctx" title="{fmtInt(t.tokensIn)} prompt tokens (context)">
							<span class="tj-ctx-bar"><span style:width="{(t.tokensIn / maxIn) * 100}%"></span></span>
							<span class="tj-ctx-val">{t.llm ? fmtInt(t.tokensIn) : '—'}</span>
						</div>
						<span class="tj-out" title="Output tokens">{t.llm ? `+${fmtInt(t.tokensOut)}` : ''}</span>
						<div class="tj-acts">
							{#if t.actions.length === 0}
								<span class="text-muted-foreground">{t.llm ? 'no tool call' : ''}</span>
							{:else}
								{#each t.actions as a (a.index)}
									<span
										class="tj-act"
										data-ok={a.ok}
										title="Step {a.index}: {a.kind}{a.tool ? ` · ${a.tool}` : ''}{a.command_sha ? ` · cmd ${shortSha(a.command_sha, 8)}` : ''}{a.ok === false ? ' · failed' : ''}{a.duration_s != null ? ` · ${fmtSeconds(a.duration_s)}` : ''}"
										>{a.tool ?? a.kind}</span
									>
								{/each}
							{/if}
						</div>
						<span class="tj-files" title={t.files.join('\n')}>{t.files.length ? t.files.map((f) => f.split('/').pop()).join(', ') : ''}</span>
						<span class="tj-time">{t.seconds != null ? fmtSeconds(t.seconds) : ''}</span>
					</li>
				{:else}
					<li class="px-4 py-6 text-center text-sm text-muted-foreground">No turn with a failed tool call.</li>
				{/each}
			</ol>
		{:else}
			<div class="table-frame" {@attach tableFrame}>
				<table>
					<thead>
						<tr>
							<th class="num">#</th>
							<th>Kind</th>
							<th>Tool</th>
							<th>Result</th>
							<th class="num" title="Since the first step">Start</th>
							<th class="num">Duration</th>
							<th class="num">Tokens in</th>
							<th class="num">Tokens out</th>
							<th>Files</th>
						</tr>
					</thead>
					<tbody>
						{#each shown as step (step.index)}
							<tr>
								<td class="num">{step.index}</td>
								<td class="font-mono text-xs">{step.kind}</td>
								<td class="font-mono text-xs">
									{step.tool ?? '—'}
									{#if step.command_sha}<div class="cell-sub" title={step.command_sha}>cmd {shortSha(step.command_sha, 8)}</div>{/if}
								</td>
								<td>
									{#if step.ok === true}<span class="text-xs">ok</span>{:else if step.ok === false}<Badge variant="destructive">failed</Badge>{:else}<span class="text-muted-foreground">—</span>{/if}
								</td>
								<td class="num">{step.started == null ? '—' : fmtSeconds(step.started - t0)}</td>
								<td class="num">{fmtSeconds(step.duration_s)}</td>
								<td class="num">{fmtInt(step.tokens_in)}</td>
								<td class="num">{fmtInt(step.tokens_out)}</td>
								<td class="max-w-80 truncate font-mono text-xs" title={step.files.join('\n')}>{step.files.join(', ') || '—'}</td>
							</tr>
						{/each}
					</tbody>
				</table>
			</div>
		{/if}
	</Card.Root>

	{#if trajectory?.final_message}
		<section class="tj-card tj-final">
			<h2 class="tj-h">Agent's last words</h2>
			<blockquote>{trajectory.final_message}</blockquote>
			{#if trajectory.final_message_truncated}<p class="text-[11px] text-muted-foreground">Truncated by the parser.</p>{/if}
		</section>
	{/if}

	{#if observed}
		<section class="tj-card">
			<button type="button" class="tj-disclosure" aria-expanded={observedOpen} onclick={() => (observedOpen = !observedOpen)}>
				<h2 class="tj-h">Observed inputs</h2>
				<span class="truncate text-xs text-muted-foreground">What the harness sent on its first main-class call · {observed.tool_names?.join(', ') || 'no tools'}</span>
				<ChevronDown class="ml-auto size-4 shrink-0 text-muted-foreground transition-transform duration-150 {observedOpen ? 'rotate-180' : ''}" aria-hidden="true" />
			</button>
			{#if observedOpen}
			<div class="px-5 pb-4 text-xs">
				<dl class="grid grid-cols-[auto_1fr] gap-x-3 gap-y-1">
					<dt class="text-muted-foreground">system prompt</dt><dd class="font-mono">{shortSha(observed.system_prompt_sha)}</dd>
					<dt class="text-muted-foreground">first user message</dt><dd class="font-mono">{shortSha(observed.first_user_message_sha)}</dd>
					<dt class="text-muted-foreground">tool specs</dt><dd class="font-mono">{shortSha(observed.tool_specs_sha)}</dd>
					<dt class="text-muted-foreground">tools</dt><dd class="font-mono">{observed.tool_names?.join(', ') || '—'}</dd>
					<dt class="text-muted-foreground">calls by class</dt>
					<dd class="font-mono">{Object.entries(observed.calls_by_class ?? {}).map(([k, n]) => `${k} ${n}`).join(' · ') || '—'}</dd>
				</dl>
			</div>
			{/if}
		</section>
	{/if}
</div>

<style>
	.tj-card {
		overflow: hidden;
		border: 1px solid var(--border);
		border-radius: var(--radius);
		background: var(--card);
		box-shadow: var(--shadow-card);
	}
	.tj-h {
		font-family: var(--font-display);
		font-size: 1rem;
		font-weight: 600;
	}
	.tj-summary {
		display: flex;
		flex-wrap: wrap;
		align-items: center;
		gap: 0.375rem 1.25rem;
		padding: 0.75rem 1.25rem;
		font-size: 0.8125rem;
		color: var(--muted-foreground);
	}
	.tj-summary b {
		font-family: var(--font-mono);
		font-weight: 600;
		color: var(--foreground);
		font-variant-numeric: tabular-nums;
	}
	.tj-tags {
		display: inline-flex;
		flex-wrap: wrap;
		gap: 0.375rem;
		margin-left: auto;
	}
	.tj-chart {
		border-top: 1px solid var(--border);
		padding: 0.875rem 1.25rem 0.75rem;
	}

	/* Turn list */
	.tj-turns {
		max-height: 70vh;
		overflow-y: auto;
	}
	.tj-turn {
		display: grid;
		grid-template-columns: 2.5rem minmax(7rem, 11rem) 4.5rem minmax(0, 1fr) minmax(0, 14rem) 4rem;
		align-items: center;
		gap: 0.75rem;
		padding: 0.4rem 1rem;
		border-bottom: 1px solid color-mix(in oklab, var(--border) 60%, transparent);
		font-size: 0.8125rem;
		scroll-margin: 4rem;
	}
	.tj-turn[data-failed='true'] {
		box-shadow: inset 2px 0 0 var(--destructive);
	}
	.tj-turn[data-selected='true'] {
		background: color-mix(in oklab, var(--primary) 9%, transparent);
	}
	.tj-turn:hover {
		background: var(--surface-2, var(--muted));
	}
	.tj-n {
		font-family: var(--font-mono);
		font-size: 0.75rem;
		color: var(--muted-foreground);
		text-align: right;
		font-variant-numeric: tabular-nums;
	}
	.tj-ctx {
		display: flex;
		align-items: center;
		gap: 0.5rem;
	}
	.tj-ctx-bar {
		display: block;
		flex: 1;
		height: 4px;
		border-radius: 9999px;
		background: var(--muted);
		overflow: hidden;
	}
	.tj-ctx-bar > span {
		display: block;
		height: 100%;
		background: color-mix(in oklab, var(--primary) 70%, transparent);
	}
	.tj-ctx-val,
	.tj-out,
	.tj-time {
		font-family: var(--font-mono);
		font-size: 0.75rem;
		font-variant-numeric: tabular-nums;
		white-space: nowrap;
	}
	.tj-ctx-val {
		min-width: 3.75rem;
		text-align: right;
	}
	.tj-out {
		color: color-mix(in oklab, var(--chart-3, #c2850c) 85%, var(--foreground));
		text-align: right;
	}
	.tj-time {
		color: var(--muted-foreground);
		text-align: right;
	}
	.tj-acts {
		display: flex;
		min-width: 0;
		flex-wrap: wrap;
		gap: 0.25rem;
	}
	.tj-act {
		border-radius: 0.25rem;
		border: 1px solid var(--border);
		padding: 0 0.375rem;
		font-family: var(--font-mono);
		font-size: 0.6875rem;
		line-height: 1.125rem;
	}
	.tj-act[data-ok='false'] {
		color: var(--danger-fg);
		border-color: color-mix(in oklab, var(--destructive) 45%, transparent);
		background: color-mix(in oklab, var(--destructive) 10%, transparent);
	}
	.tj-files {
		min-width: 0;
		overflow: hidden;
		text-overflow: ellipsis;
		white-space: nowrap;
		font-family: var(--font-mono);
		font-size: 0.6875rem;
		color: var(--muted-foreground);
	}

	.tj-final {
		display: flex;
		flex-direction: column;
		gap: 0.625rem;
		padding: 1rem 1.25rem 1.125rem;
	}
	.tj-final blockquote {
		max-height: 20rem;
		overflow: auto;
		white-space: pre-wrap;
		border-left: 2px solid var(--primary);
		border-radius: 0 calc(var(--radius) - 2px) calc(var(--radius) - 2px) 0;
		background: color-mix(in oklab, var(--primary) 6%, transparent);
		padding: 0.625rem 0.875rem;
		font-size: 0.875rem;
		line-height: 1.55;
	}
	.tj-disclosure {
		display: flex;
		width: 100%;
		min-width: 0;
		cursor: pointer;
		align-items: baseline;
		gap: 0.75rem;
		padding: 0.875rem 1.25rem;
		text-align: left;
	}
	.tj-disclosure:focus-visible {
		outline: 2px solid var(--ring);
		outline-offset: -2px;
	}

	@media (max-width: 760px) {
		.tj-turn {
			grid-template-columns: 2rem minmax(0, 1fr) 4rem;
		}
		.tj-acts {
			grid-column: 2 / -1;
		}
		.tj-files,
		.tj-time {
			display: none;
		}
	}
</style>
