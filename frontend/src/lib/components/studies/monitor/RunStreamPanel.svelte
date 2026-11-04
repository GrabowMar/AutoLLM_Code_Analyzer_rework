<script lang="ts">
	/**
	 * RunStreamPanel — the live trajectory of one running run (spec 10.3 §6, 9.5): subscribes to SSE
	 * `run:<id>` and lists `trajectory_step` events (throttled to 2/s by the supervisor; a harness without
	 * a line parser reports the step count only), with the live `usage` (tokens, cost, in-flight calls)
	 * and `status`. One run at a time; the picker lists the study's running runs.
	 *
	 * Props: `runs` (`{id, label}` of running runs).
	 */
	import { subscribe } from '$lib/api/sse';
	import type { RunUsageEvent, TrajectoryStepEvent } from '$lib/api/v3/types';
	import * as Select from '$lib/components/ui/select';
	import { Label } from '$lib/components/ui/label';
	import EmptyState from '$lib/components/EmptyState.svelte';
	import { formatTokenCount, formatUsd } from '../format';

	interface Props {
		runs: { id: string; label: string }[];
	}

	let { runs }: Props = $props();

	const MAX_STEPS = 200;
	let selected = $state('');
	let steps = $state<{ key: number; n: number; text: string }[]>([]);
	let usage = $state<RunUsageEvent | null>(null);
	let status = $state('');
	let nSteps = $state(0);
	let connected = $state(false);
	let seq = 0;

	$effect(() => {
		if (!selected || !runs.some((r) => r.id === selected)) selected = runs[0]?.id ?? '';
	});

	function describe(e: TrajectoryStepEvent): string {
		const s = e.step;
		if (!s) return `step ${e.n_steps}`;
		const tool = typeof s.tool === 'string' ? s.tool : typeof s.kind === 'string' ? s.kind : 'step';
		const text = typeof s.summary === 'string' ? s.summary : typeof s.text === 'string' ? s.text : '';
		return `${tool}${text ? ` · ${text.slice(0, 200)}` : ''}`;
	}

	$effect(() => {
		const id = selected;
		steps = [];
		usage = null;
		status = '';
		nSteps = 0;
		connected = false;
		if (!id) return;
		return subscribe(
			[`run:${id}`],
			(e) => {
				const data = e.data as Record<string, unknown>;
				const type = (data.type as string) ?? e.type;
				if (type === 'trajectory_step') {
					const ev = data as unknown as TrajectoryStepEvent;
					nSteps = ev.n_steps;
					steps = [{ key: ++seq, n: ev.n_steps, text: describe(ev) }, ...steps].slice(0, MAX_STEPS);
				} else if (type === 'usage') {
					usage = data as unknown as RunUsageEvent;
				} else if (type === 'status') {
					status = `${data.status}${data.phase ? ` · ${data.phase}` : ''}${data.outcome ? ` · ${data.outcome}` : ''}`;
				}
			},
			{ onState: (s) => (connected = s === 'open') },
		);
	});
</script>

{#if runs.length === 0}
	<EmptyState compact title="No run is generating" description="The live trajectory appears here while a run of this experiment is in its sandbox." />
{:else}
	<div class="space-y-2">
		<div class="flex flex-wrap items-end gap-3">
			<div class="space-y-1">
				<Label for="stream-run" class="text-xs">Run</Label>
				<Select.Simple id="stream-run" size="sm" bind:value={selected} items={runs.map((r) => ({ value: r.id, label: r.label }))} />
			</div>
			<p class="text-xs text-muted-foreground" aria-live="polite">
				{connected ? 'live' : 'connecting'} · {nSteps} steps{status ? ` · ${status}` : ''}
				{#if usage}
					· {formatTokenCount(usage.prompt_tokens + usage.completion_tokens)} tok · {formatUsd(usage.cost_usd)} ({formatUsd(usage.list_cost_usd)} list){usage.inflight ? ` · ${usage.inflight} call in flight` : ''}
					{#if usage.budget_exhausted}<span class="text-warning-fg"> · run budget reached ({usage.budget_exhausted})</span>{/if}
				{/if}
			</p>
			<a class="text-xs text-primary underline" href="/samples/{selected}?tab=trajectory">Open the sample</a>
		</div>
		<ol class="max-h-72 space-y-0.5 overflow-y-auto rounded-md border border-border bg-muted/20 p-2 font-mono text-[11px]">
			{#each steps as s (s.key)}
				<li class="break-words"><span class="text-muted-foreground">{s.n}</span> {s.text}</li>
			{:else}
				<li class="text-muted-foreground">waiting for the first step…</li>
			{/each}
		</ol>
	</div>
{/if}
