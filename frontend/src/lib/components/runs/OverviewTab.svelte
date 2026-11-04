<script lang="ts">
	/**
	 * OverviewTab — what happened to a sample (spec 10.4): the outcome and what it means (6.12), termination,
	 * validity notes, identity hashes, the study cell and the attempts table (OOM, request classes,
	 * truncations, time split, served providers).
	 */
	import * as Card from '$lib/components/ui/card';
	import { Badge } from '$lib/components/ui/badge';
	import StatusBadge from '$lib/components/StatusBadge.svelte';
	import SectionHeading from '$lib/components/SectionHeading.svelte';
	import SameSetupPanel from '$lib/components/applications/SameSetupPanel.svelte';
	import type { RunAttemptOut, RunOut } from '$lib/api/v3/types';
	import {
		fmtInt,
		fmtSeconds,
		fmtUsd,
		OUTCOME_TEXT,
		OUTCOME_TONES,
		outcomeLabel,
		shortSha,
		TERMINATION_TEXT,
	} from './format';

	interface Props {
		run: RunOut;
	}

	let { run }: Props = $props();

	const outcomeInfo = $derived(run.outcome ? OUTCOME_TEXT[run.outcome] : null);
	const termination = $derived(run.validity?.termination ?? (run.result?.termination as string | undefined) ?? null);
	const notes = $derived(run.validity?.notes ?? []);
	const identity = $derived(Object.entries(run.identity ?? {}).filter(([, v]) => !!v));
	const steps = $derived(
		Array.isArray(run.result?.steps)
			? (run.result.steps as { step?: string; status?: string; seconds?: number }[])
			: [],
	);

	function duration(a: RunAttemptOut): number | null {
		if (!a.started_at || !a.ended_at) return null;
		return (new Date(a.ended_at).getTime() - new Date(a.started_at).getTime()) / 1000;
	}

	function counts(record: Record<string, number> | undefined): string {
		const entries = Object.entries(record ?? {}).filter(([, n]) => n > 0);
		return entries.length ? entries.map(([k, n]) => `${k} ${n}`).join(' · ') : '—';
	}

	const NOTE_TONES = { info: 'neutral', warn: 'warning', error: 'danger' } as const;
</script>

<div class="space-y-4">
	<div class="grid gap-4 lg:grid-cols-2">
		<Card.Root>
			<Card.Header>
				<Card.Title class="font-display">Outcome</Card.Title>
				<Card.Description>The outcome taxonomy is shared by app and slot runs.</Card.Description>
			</Card.Header>
			<Card.Content class="space-y-3 text-sm">
				<div class="flex flex-wrap items-center gap-2">
					{#if run.outcome}
						<StatusBadge status={run.outcome} tones={OUTCOME_TONES} label={outcomeLabel(run.outcome)} />
					{:else}
						<StatusBadge status={run.status} />
					{/if}
					{#if run.outcome_reason}
						<Badge variant="outline" class="font-mono">{run.outcome_reason}</Badge>
					{/if}
				</div>
				<p class="text-muted-foreground">
					{outcomeInfo?.explain ?? 'The run has no outcome yet; it is set when generation and evaluation finish.'}
				</p>
				{#if termination}
					<div>
						<p class="section-label">Termination</p>
						<p><span class="font-mono">{termination}</span> — {TERMINATION_TEXT[termination] ?? 'see the evidence.'}</p>
					</div>
				{/if}
				{#if run.error_message}
					<div>
						<p class="section-label">Error</p>
						<pre class="max-h-40 overflow-auto whitespace-pre-wrap rounded border border-border bg-muted p-2 font-mono text-xs">{run.error_message}</pre>
					</div>
				{/if}
			</Card.Content>
		</Card.Root>

		<Card.Root>
			<Card.Header>
				<Card.Title class="font-display">Validity</Card.Title>
				<Card.Description>Typed notes the pipeline recorded; truncated runs stay in the default (ITT) analysis.</Card.Description>
			</Card.Header>
			<Card.Content class="space-y-3 text-sm">
				{#if notes.length === 0}
					<p class="text-muted-foreground">No validity notes.</p>
				{:else}
					<ul class="flex flex-wrap gap-1.5">
						{#each notes as note (note.code)}
							<li>
								<StatusBadge status={note.level} tone={NOTE_TONES[note.level]} label={note.code} class="normal-case" />
								{#if note.detail}<span class="ml-1 text-xs text-muted-foreground">{note.detail}</span>{/if}
							</li>
						{/each}
					</ul>
				{/if}
				{#if run.study.study_id}
					<div>
						<p class="section-label">Experiment cell</p>
						<p>
							<a class="text-primary hover:underline" href={`/experiments/${run.study.study_id}`}>{run.study.study_name ?? 'Experiment'}</a>
							· unit <span class="font-mono">{run.study.unit_key ?? '—'}</span>
							· repeat {run.study.repeat_index ?? '—'} · {run.study.stage || 'main'}
							{#if run.cell_attempt}· cell attempt {run.cell_attempt}{/if}
						</p>
					</div>
				{/if}
				{#if run.parent_run}
					<div>
						<p class="section-label">Parent app</p>
						<a class="font-mono text-primary hover:underline" href={`/samples/${run.parent_run}`}>{shortSha(run.parent_run, 8)}</a>
						{#if run.slot_target}
							· target <a class="font-mono text-primary hover:underline" href={`/slots/targets/${run.slot_target}`}>{shortSha(run.slot_target, 8)}</a>
						{/if}
					</div>
				{/if}
				{#if identity.length}
					<div>
						<p class="section-label">Identity</p>
						<dl class="grid grid-cols-[auto_1fr] gap-x-3 gap-y-0.5 text-xs">
							{#each identity as [key, value] (key)}
								<dt class="text-muted-foreground">{key.replace(/_/g, ' ')}</dt>
								<dd class="truncate font-mono" title={value}>{shortSha(value, 16)}</dd>
							{/each}
						</dl>
					</div>
				{/if}
			</Card.Content>
		</Card.Root>
	</div>

	<SectionHeading size="sm" title="Attempts" count={run.attempts.length} />
	<Card.Root class="gap-0 overflow-clip py-0">
		{#if run.attempts.length === 0}
			<Card.Content class="py-4 text-sm text-muted-foreground">No attempt has started yet.</Card.Content>
		{:else}
			<div class="overflow-x-auto">
				<table>
					<thead>
						<tr>
							<th class="num">#</th>
							<th>Termination</th>
							<th>Outcome</th>
							<th class="num">Duration</th>
							<th>Requests by class</th>
							<th class="num">Tokens in / out</th>
							<th class="num">Truncations</th>
							<th>Time split</th>
							<th>Served by</th>
							<th class="num">Cost</th>
						</tr>
					</thead>
					<tbody>
						{#each run.attempts as attempt (attempt.attempt)}
							<tr>
								<td class="num">{attempt.attempt}</td>
								<td class="font-mono text-xs">
									{attempt.termination || '—'}
									{#if attempt.oom_killed}<Badge variant="destructive" class="ml-1">OOM</Badge>{/if}
									{#if attempt.budget_exhausted}<Badge variant="secondary" class="ml-1">budget: {attempt.budget_exhausted}</Badge>{/if}
								</td>
								<td>
									{#if attempt.outcome}
										<StatusBadge status={attempt.outcome} tones={OUTCOME_TONES} label={outcomeLabel(attempt.outcome)} />
									{:else}—{/if}
									{#if attempt.infra_error}<div class="cell-sub font-mono">{attempt.infra_error}</div>{/if}
								</td>
								<td class="num">{fmtSeconds(duration(attempt))}</td>
								<td class="font-mono text-xs">{counts(attempt.usage.requests_by_class)}</td>
								<td class="num">{fmtInt(attempt.usage.prompt_tokens)} / {fmtInt(attempt.usage.completion_tokens)}</td>
								<td class="num">{fmtInt(attempt.usage.call_truncations ?? 0)}</td>
								<td class="text-xs">
									LLM {fmtSeconds(attempt.time_split.llm_s)} · tools {fmtSeconds(attempt.time_split.tool_s)}
									· install {fmtSeconds(attempt.time_split.install_s)} · idle {fmtSeconds(attempt.time_split.idle_s)}
								</td>
								<td class="font-mono text-xs">{counts(attempt.served.providers)}</td>
								<td class="num">
									{fmtUsd(attempt.usage.cost_usd)}
									{#if attempt.usage.list_cost_usd !== undefined && attempt.usage.list_cost_usd !== attempt.usage.cost_usd}
										<div class="cell-sub" title="List price (what the calls would cost at catalogue prices)">list {fmtUsd(attempt.usage.list_cost_usd)}</div>
									{/if}
								</td>
							</tr>
						{/each}
					</tbody>
				</table>
			</div>
		{/if}
	</Card.Root>

	{#if steps.length}
		<SectionHeading size="sm" title="Pipeline steps" count={steps.length} />
		<ol class="flex flex-wrap gap-1.5 text-xs">
			{#each steps as step, i (i)}
				<li class="inline-flex items-center gap-1 rounded border border-border px-2 py-0.5">
					<span class="font-mono">{step.step ?? '?'}</span>
					<StatusBadge status={step.status === 'ok' ? 'completed' : (step.status ?? 'unknown')} label={step.status ?? '?'} />
					<span class="tabular-nums text-muted-foreground">{fmtSeconds(step.seconds)}</span>
				</li>
			{/each}
		</ol>
	{/if}
	{#if run.kind === 'app_agentic' && run.status === 'completed'}
		<SameSetupPanel jobId={run.id} />
	{/if}
</div>
