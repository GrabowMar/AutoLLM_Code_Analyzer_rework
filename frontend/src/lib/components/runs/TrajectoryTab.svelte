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
	import { fmtInt, fmtSeconds, isActiveStatus, shortSha } from './format';

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
</script>

<div class="space-y-4">
	{#if running}
		<p class="text-sm text-muted-foreground">
			Live: {liveStepCount || steps.length} step{(liveStepCount || steps.length) === 1 ? '' : 's'} so far
			{#if liveSteps.length === 0 && liveStepCount > 0}(this harness reports a step count only){/if}.
		</p>
	{/if}

	{#if trajectory}
		<div class="grid grid-cols-2 gap-3 sm:grid-cols-4 lg:grid-cols-7">
			{#each [['Steps', trajectory.steps.length], ['Tool calls', trajectory.tool_calls], ['Edits', trajectory.edits], ['Shell', trajectory.shell_commands], ['Compactions', trajectory.compactions], ['Subagent calls', trajectory.subagent_calls], ['Unparsed lines', trajectory.unparsed_lines]] as [label, value] (label)}
				<div class="kpi-card">
					<p class="kpi-label">{label}</p>
					<p class="kpi-value">{fmtInt(value as number)}</p>
				</div>
			{/each}
		</div>
		<div class="flex flex-wrap gap-1.5 text-xs">
			<Badge variant="outline">parser {trajectory.parser} v{trajectory.parser_version}</Badge>
			{#if trajectory.max_turns_hit}<Badge variant="secondary">max turns hit</Badge>{/if}
			{#if trajectory.ended_on_llm_error}<Badge variant="destructive">ended on an LLM error</Badge>{/if}
		</div>
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
					aria-label="Step filter"
					bind:value={kindFilter}
					items={[
						{ value: 'all', label: 'All' },
						{ value: 'failed', label: 'Failed' },
						...kinds.slice(0, 2).map((k) => ({ value: k, label: k })),
					]}
				/>
			</div>
			<span class="text-xs text-muted-foreground">{shown.length} of {steps.length} steps</span>
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
		<Card.Root>
			<Card.Header>
				<Card.Title class="font-display">Final message</Card.Title>
				{#if trajectory.final_message_truncated}<Card.Description>Truncated by the parser.</Card.Description>{/if}
			</Card.Header>
			<Card.Content>
				<pre class="max-h-80 overflow-auto whitespace-pre-wrap font-mono text-xs">{trajectory.final_message}</pre>
			</Card.Content>
		</Card.Root>
	{/if}

	{#if observed}
		<Card.Root>
			<Card.Header>
				<Card.Title class="font-display">Observed inputs</Card.Title>
				<Card.Description>What the harness actually sent on its first main-class call (normalized hashes).</Card.Description>
			</Card.Header>
			<Card.Content class="text-xs">
				<dl class="grid grid-cols-[auto_1fr] gap-x-3 gap-y-1">
					<dt class="text-muted-foreground">system prompt</dt><dd class="font-mono">{shortSha(observed.system_prompt_sha)}</dd>
					<dt class="text-muted-foreground">first user message</dt><dd class="font-mono">{shortSha(observed.first_user_message_sha)}</dd>
					<dt class="text-muted-foreground">tool specs</dt><dd class="font-mono">{shortSha(observed.tool_specs_sha)}</dd>
					<dt class="text-muted-foreground">tools</dt><dd class="font-mono">{observed.tool_names?.join(', ') || '—'}</dd>
					<dt class="text-muted-foreground">calls by class</dt>
					<dd class="font-mono">{Object.entries(observed.calls_by_class ?? {}).map(([k, n]) => `${k} ${n}`).join(' · ') || '—'}</dd>
				</dl>
			</Card.Content>
		</Card.Root>
	{/if}
</div>
