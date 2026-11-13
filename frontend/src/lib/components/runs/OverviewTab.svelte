<script lang="ts">
	/**
	 * OverviewTab — what happened to a sample (spec 10.4), in reading order:
	 *  1. What happened: the outcome and what it means (6.12), termination, error, validity notes.
	 *  2. Where the time went: pipeline steps and evaluation stages as proportional timelines, and the
	 *     attempt's time split (LLM / tools / install / idle).
	 *  3. Attempt facts (single attempt) or the attempts table (several).
	 *  4. Provenance: experiment cell, parent app, identity hashes (collapsed).
	 *  5. Same-setup runs (completed agentic apps).
	 */
	import StatusBadge from '$lib/components/StatusBadge.svelte';
	import SameSetupPanel from '$lib/components/applications/SameSetupPanel.svelte';
	import ChevronDown from '@lucide/svelte/icons/chevron-down';
	import TriangleAlert from '@lucide/svelte/icons/triangle-alert';
	import type { RunAttemptOut, RunOut } from '$lib/api/v3/types';
	import { EVAL_STAGE_KEYS } from '$lib/api/v3/types';
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

	interface Seg {
		name: string;
		status: string;
		seconds: number | null;
	}

	const pipeline = $derived.by<Seg[]>(() =>
		Array.isArray(run.result?.steps)
			? (run.result.steps as { step?: string; status?: string; seconds?: number }[]).map((s) => ({
					name: s.step ?? '?',
					status: s.status ?? 'unknown',
					seconds: typeof s.seconds === 'number' ? s.seconds : null,
				}))
			: [],
	);
	const evalStages = $derived.by<Seg[]>(() => {
		const stages = run.latest_evaluation?.stages ?? {};
		return EVAL_STAGE_KEYS.filter((k) => stages[k]).map((k) => ({
			name: k,
			status: stages[k]!.status,
			seconds: stages[k]!.seconds ?? null,
		}));
	});

	/**
	 * Proportional widths with a floor, so a 10 ms step stays visible next to an 8-minute sandbox.
	 * Returns percentages summing to 100.
	 */
	function widths(segs: Seg[]): number[] {
		const total = segs.reduce((a, s) => a + (s.seconds ?? 0), 0);
		if (!total) return segs.map(() => 100 / Math.max(1, segs.length));
		const floor = 6.5;
		const raw = segs.map((s) => ((s.seconds ?? 0) / total) * 100);
		const small = raw.filter((w) => w < floor).length;
		const budget = 100 - small * floor;
		const bigTotal = raw.filter((w) => w >= floor).reduce((a, w) => a + w, 0) || 1;
		return raw.map((w) => (w < floor ? floor : (w / bigTotal) * budget));
	}

	const lastAttempt = $derived(run.attempts.at(-1) ?? null);
	const split = $derived.by(() => {
		const t = lastAttempt?.time_split;
		if (!t) return null;
		const parts = [
			{ key: 'llm', label: 'LLM', s: t.llm_s ?? 0 },
			{ key: 'tool', label: 'Tools', s: t.tool_s ?? 0 },
			{ key: 'install', label: 'Install', s: t.install_s ?? 0 },
			{ key: 'idle', label: 'Idle', s: t.idle_s ?? 0 },
		];
		const total = parts.reduce((a, p) => a + p.s, 0);
		return total ? { parts, total } : null;
	});

	function duration(a: RunAttemptOut): number | null {
		if (!a.started_at || !a.ended_at) return null;
		return (new Date(a.ended_at).getTime() - new Date(a.started_at).getTime()) / 1000;
	}

	function counts(record: Record<string, number> | undefined): string {
		const entries = Object.entries(record ?? {}).filter(([, n]) => n > 0);
		return entries.length ? entries.map(([k, n]) => `${k} ${n}`).join(' · ') : '—';
	}

	const NOTE_TONES = { info: 'neutral', warn: 'warning', error: 'danger' } as const;
	let provenanceOpen = $state(false);
</script>

<div class="space-y-4">
	<!-- 1 · What happened -->
	<section class="ov-card ov-story">
		<div class="ov-story-main">
			<h2 class="ov-h">What happened</h2>
			<div class="flex flex-wrap items-center gap-2">
				{#if run.outcome}
					<StatusBadge status={run.outcome} tones={OUTCOME_TONES} label={outcomeLabel(run.outcome)} />
				{:else}
					<StatusBadge status={run.status} />
				{/if}
				{#if run.outcome_reason}<span class="ov-code">{run.outcome_reason}</span>{/if}
			</div>
			<p class="ov-lead">
				{outcomeInfo?.explain ?? 'The run has no outcome yet; it is set when generation and evaluation finish.'}
			</p>
			{#if termination}
				<p class="text-sm">
					<span class="ov-label-inline">Stopped</span>
					<span class="font-mono">{termination}</span>
					<span class="text-muted-foreground">— {TERMINATION_TEXT[termination] ?? 'see the evidence.'}</span>
				</p>
			{/if}
			{#if run.error_message}
				<div class="ov-error" role="alert">
					<TriangleAlert class="size-4 shrink-0" aria-hidden="true" />
					<pre>{run.error_message}</pre>
				</div>
			{/if}
		</div>
		<div class="ov-story-side">
			<h3 class="ov-label">Validity</h3>
			{#if notes.length === 0}
				<p class="text-sm text-muted-foreground">No validity notes: nothing unusual recorded.</p>
			{:else}
				<ul class="space-y-1.5">
					{#each notes as note (note.code)}
						<li class="flex flex-wrap items-baseline gap-x-2 gap-y-0.5">
							<StatusBadge status={note.level} tone={NOTE_TONES[note.level]} label={note.code} class="normal-case" />
							{#if note.detail}<span class="text-xs text-muted-foreground">{note.detail}</span>{/if}
						</li>
					{/each}
				</ul>
			{/if}
			<p class="mt-3 text-[11px] text-muted-foreground">Truncated runs stay in the default (ITT) analysis.</p>
		</div>
	</section>

	<!-- 2 · Where the time went -->
	{#if pipeline.length || evalStages.length || split}
		<section class="ov-card">
			<div class="ov-card-head">
				<h2 class="ov-h">Where the time went</h2>
				{#if run.wall_s != null}<span class="text-xs text-muted-foreground">{fmtSeconds(run.wall_s)} wall clock</span>{/if}
			</div>
			<div class="ov-timelines">
				{#if pipeline.length}
					{@const w = widths(pipeline)}
					<div class="ov-tl">
						<span class="ov-label">Generation pipeline</span>
						<div class="ov-tl-bar" role="list">
							{#each pipeline as seg, i (i)}
								<div
									role="listitem"
									class="ov-seg"
									data-status={seg.status}
									style:width="{w[i]}%"
									title="{seg.name}: {seg.status} · {fmtSeconds(seg.seconds)}"
								>
									<span class="ov-seg-name">{seg.name}</span>
									<span class="ov-seg-time">{fmtSeconds(seg.seconds)}</span>
								</div>
							{/each}
						</div>
					</div>
				{/if}
				{#if evalStages.length}
					{@const w = widths(evalStages)}
					<div class="ov-tl">
						<span class="ov-label">Evaluation</span>
						<div class="ov-tl-bar" role="list">
							{#each evalStages as seg, i (i)}
								<div
									role="listitem"
									class="ov-seg ov-seg-eval"
									data-status={seg.status}
									style:width="{w[i]}%"
									title="{seg.name}: {seg.status} · {fmtSeconds(seg.seconds)}"
								>
									<span class="ov-seg-name">{seg.name}</span>
									<span class="ov-seg-time">{fmtSeconds(seg.seconds)}</span>
								</div>
							{/each}
						</div>
					</div>
				{/if}
				{#if split}
					<div class="ov-tl">
						<span class="ov-label">Agent time split</span>
						<div class="ov-split" aria-hidden="true">
							{#each split.parts as p (p.key)}
								{#if p.s > 0}<span class="ov-split-{p.key}" style:width="{(p.s / split.total) * 100}%"></span>{/if}
							{/each}
						</div>
						<ul class="ov-legend">
							{#each split.parts as p (p.key)}
								<li><span class="ov-dot ov-split-{p.key}"></span>{p.label} <b>{fmtSeconds(p.s)}</b> <span class="text-muted-foreground">{Math.round((p.s / split.total) * 100)}%</span></li>
							{/each}
						</ul>
					</div>
				{/if}
			</div>
		</section>
	{/if}

	<!-- 3 · Attempt(s) -->
	{#if run.attempts.length === 0}
		<section class="ov-card px-5 py-4 text-sm text-muted-foreground">No attempt has started yet.</section>
	{:else if run.attempts.length === 1 && lastAttempt}
		{@const a = lastAttempt}
		<section class="ov-card">
			<div class="ov-card-head"><h2 class="ov-h">Attempt</h2><span class="text-xs text-muted-foreground">one attempt</span></div>
			<dl class="ov-facts">
				<div><dt>Termination</dt><dd class="font-mono">{a.termination || '—'}{#if a.budget_exhausted}<span class="ov-code ml-1.5">budget: {a.budget_exhausted}</span>{/if}{#if a.oom_killed}<span class="ov-code ov-code-bad ml-1.5">OOM</span>{/if}</dd></div>
				<div><dt>Duration</dt><dd>{fmtSeconds(duration(a))}</dd></div>
				<div><dt>Requests</dt><dd class="font-mono">{counts(a.usage.requests_by_class)}</dd></div>
				<div><dt>Tokens in / out</dt><dd>{fmtInt(a.usage.prompt_tokens)} / {fmtInt(a.usage.completion_tokens)}</dd></div>
				<div><dt>Truncated calls</dt><dd>{fmtInt(a.usage.call_truncations ?? 0)}</dd></div>
				<div><dt>Served by</dt><dd class="font-mono">{counts(a.served.providers)}</dd></div>
				<div>
					<dt>Cost</dt>
					<dd>{fmtUsd(a.usage.cost_usd)}{#if a.usage.list_cost_usd !== undefined && a.usage.list_cost_usd !== a.usage.cost_usd}<span class="text-muted-foreground" title="List price: what the calls would cost at catalogue prices"> · list {fmtUsd(a.usage.list_cost_usd)}</span>{/if}</dd>
				</div>
				{#if a.infra_error}<div><dt>Infra error</dt><dd class="font-mono text-danger-fg">{a.infra_error}</dd></div>{/if}
			</dl>
		</section>
	{:else}
		<section class="ov-card gap-0 overflow-clip">
			<div class="ov-card-head"><h2 class="ov-h">Attempts</h2><span class="text-xs text-muted-foreground">{run.attempts.length}</span></div>
			<div class="overflow-x-auto">
				<table>
					<thead>
						<tr>
							<th class="num">#</th>
							<th>Termination</th>
							<th>Outcome</th>
							<th class="num">Duration</th>
							<th>Requests</th>
							<th class="num">Tokens in / out</th>
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
									{#if attempt.oom_killed}<span class="ov-code ov-code-bad ml-1">OOM</span>{/if}
									{#if attempt.budget_exhausted}<span class="ov-code ml-1">budget: {attempt.budget_exhausted}</span>{/if}
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
		</section>
	{/if}

	<!-- 4 · Provenance -->
	{#if run.study.study_id || run.parent_run || identity.length}
		<section class="ov-card">
			<button type="button" class="ov-disclosure" aria-expanded={provenanceOpen} onclick={() => (provenanceOpen = !provenanceOpen)}>
				<h2 class="ov-h">Provenance</h2>
				<span class="min-w-0 truncate text-xs text-muted-foreground">
					{#if run.study.study_id}{run.study.study_name ?? 'Experiment'} · repeat {run.study.repeat_index ?? '—'} · {run.study.stage || 'main'}{/if}
					{#if identity.length} · {identity.length} identity hashes{/if}
				</span>
				<ChevronDown class="ml-auto size-4 shrink-0 text-muted-foreground transition-transform duration-150 {provenanceOpen ? 'rotate-180' : ''}" aria-hidden="true" />
			</button>
			{#if provenanceOpen}
				<div class="ov-prov">
					{#if run.study.study_id}
						<div>
							<h3 class="ov-label">Experiment cell</h3>
							<p class="text-sm">
								<a class="text-primary hover:underline" href={`/experiments/${run.study.study_id}`}>{run.study.study_name ?? 'Experiment'}</a>
								· unit <span class="font-mono">{run.study.unit_key ?? '—'}</span>
								· repeat {run.study.repeat_index ?? '—'} · {run.study.stage || 'main'}
								{#if run.cell_attempt}· cell attempt {run.cell_attempt}{/if}
							</p>
						</div>
					{/if}
					{#if run.parent_run}
						<div>
							<h3 class="ov-label">Parent app</h3>
							<p class="text-sm">
								<a class="font-mono text-primary hover:underline" href={`/samples/${run.parent_run}`}>{shortSha(run.parent_run, 8)}</a>
								{#if run.slot_target}
									· target <a class="font-mono text-primary hover:underline" href={`/slots/targets/${run.slot_target}`}>{shortSha(run.slot_target, 8)}</a>
								{/if}
							</p>
						</div>
					{/if}
					{#if identity.length}
						<div>
							<h3 class="ov-label">Identity</h3>
							<dl class="grid grid-cols-[auto_1fr] gap-x-3 gap-y-0.5 text-xs">
								{#each identity as [key, value] (key)}
									<dt class="text-muted-foreground">{key.replace(/_/g, ' ')}</dt>
									<dd class="truncate font-mono" title={value}>{shortSha(value, 16)}</dd>
								{/each}
							</dl>
						</div>
					{/if}
				</div>
			{/if}
		</section>
	{/if}

	{#if run.kind === 'app_agentic' && run.status === 'completed'}
		<SameSetupPanel jobId={run.id} />
	{/if}
</div>

<style>
	.ov-card {
		display: flex;
		flex-direction: column;
		border: 1px solid var(--border);
		border-radius: var(--radius);
		background: var(--card);
		box-shadow: var(--shadow-card);
	}
	.ov-card-head {
		display: flex;
		align-items: baseline;
		gap: 0.75rem;
		padding: 0.875rem 1.25rem 0.5rem;
	}
	.ov-h {
		font-family: var(--font-display);
		font-size: 1rem;
		font-weight: 600;
	}
	.ov-label {
		display: block;
		margin-bottom: 0.375rem;
		font-family: var(--font-mono);
		font-size: 0.6875rem;
		text-transform: uppercase;
		letter-spacing: 0.1em;
		color: var(--muted-foreground);
	}
	.ov-label-inline {
		margin-right: 0.375rem;
		font-family: var(--font-mono);
		font-size: 0.6875rem;
		text-transform: uppercase;
		letter-spacing: 0.1em;
		color: var(--muted-foreground);
	}
	.ov-code {
		border-radius: 0.25rem;
		border: 1px solid var(--border);
		padding: 0 0.375rem;
		font-family: var(--font-mono);
		font-size: 0.6875rem;
		line-height: 1.125rem;
		white-space: nowrap;
	}
	.ov-code-bad {
		color: var(--danger-fg);
		border-color: color-mix(in oklab, var(--destructive) 40%, transparent);
	}

	/* 1 · story */
	.ov-story {
		display: grid;
		grid-template-columns: minmax(0, 3fr) minmax(0, 2fr);
	}
	.ov-story-main {
		display: flex;
		flex-direction: column;
		gap: 0.75rem;
		padding: 1rem 1.25rem 1.125rem;
	}
	.ov-story-side {
		padding: 1rem 1.25rem 1.125rem;
		border-left: 1px solid var(--border);
		background: color-mix(in oklab, var(--muted) 35%, var(--card));
	}
	.ov-lead {
		font-size: 0.9375rem;
		line-height: 1.55;
	}
	.ov-error {
		display: flex;
		gap: 0.5rem;
		border-radius: calc(var(--radius) - 2px);
		border: 1px solid color-mix(in oklab, var(--destructive) 40%, transparent);
		background: color-mix(in oklab, var(--destructive) 8%, transparent);
		padding: 0.5rem 0.75rem;
		color: var(--danger-fg);
	}
	.ov-error pre {
		max-height: 10rem;
		overflow: auto;
		white-space: pre-wrap;
		font-family: var(--font-mono);
		font-size: 0.75rem;
		color: var(--foreground);
	}

	/* 2 · timelines */
	.ov-timelines {
		display: flex;
		flex-direction: column;
		gap: 1rem;
		padding: 0.25rem 1.25rem 1.125rem;
	}
	.ov-tl-bar {
		display: flex;
		gap: 2px;
		height: 2.5rem;
	}
	.ov-seg {
		display: flex;
		min-width: 0;
		flex-direction: column;
		justify-content: center;
		overflow: hidden;
		border-radius: 0.25rem;
		padding: 0 0.5rem;
		background: color-mix(in oklab, var(--primary) 16%, var(--card));
		border-top: 2px solid color-mix(in oklab, var(--primary) 70%, transparent);
	}
	.ov-seg-eval {
		background: color-mix(in oklab, var(--chart-2, #0d9488) 16%, var(--card));
		border-top-color: color-mix(in oklab, var(--chart-2, #0d9488) 75%, transparent);
	}
	.ov-seg:not([data-status='ok']) {
		background: color-mix(in oklab, var(--destructive) 14%, var(--card));
		border-top-color: var(--destructive);
	}
	.ov-seg[data-status='skipped'] {
		background: var(--muted);
		border-top-color: var(--border);
	}
	.ov-seg-name {
		overflow: hidden;
		text-overflow: ellipsis;
		white-space: nowrap;
		font-family: var(--font-mono);
		font-size: 0.6875rem;
		font-weight: 500;
	}
	.ov-seg-time {
		overflow: hidden;
		text-overflow: ellipsis;
		white-space: nowrap;
		font-size: 0.625rem;
		color: var(--muted-foreground);
		font-variant-numeric: tabular-nums;
	}
	.ov-split {
		display: flex;
		height: 0.625rem;
		overflow: hidden;
		border-radius: 9999px;
		background: var(--muted);
	}
	.ov-split-llm {
		background: var(--primary);
	}
	.ov-split-tool {
		background: var(--chart-2, #0d9488);
	}
	.ov-split-install {
		background: var(--chart-3, #c2850c);
	}
	.ov-split-idle {
		background: color-mix(in oklab, var(--muted-foreground) 45%, transparent);
	}
	.ov-legend {
		display: flex;
		flex-wrap: wrap;
		gap: 0.25rem 1.25rem;
		margin-top: 0.5rem;
		font-size: 0.75rem;
	}
	.ov-legend li {
		display: inline-flex;
		align-items: center;
		gap: 0.375rem;
	}
	.ov-legend b {
		font-family: var(--font-mono);
		font-weight: 500;
	}
	.ov-dot {
		width: 0.5rem;
		height: 0.5rem;
		border-radius: 9999px;
	}

	/* 3 · attempt facts */
	.ov-facts {
		display: grid;
		grid-template-columns: repeat(auto-fill, minmax(11rem, 1fr));
		gap: 0.75rem 1.5rem;
		padding: 0.25rem 1.25rem 1.125rem;
	}
	.ov-facts dt {
		font-size: 0.6875rem;
		color: var(--muted-foreground);
	}
	.ov-facts dd {
		font-size: 0.8125rem;
		font-variant-numeric: tabular-nums;
		overflow-wrap: anywhere;
	}

	/* 4 · provenance */
	.ov-disclosure {
		display: flex;
		width: 100%;
		min-width: 0;
		cursor: pointer;
		align-items: baseline;
		gap: 0.75rem;
		padding: 0.875rem 1.25rem;
		text-align: left;
	}
	.ov-disclosure:focus-visible {
		outline: 2px solid var(--ring);
		outline-offset: -2px;
		border-radius: var(--radius);
	}
	.ov-prov {
		display: grid;
		grid-template-columns: repeat(auto-fit, minmax(16rem, 1fr));
		gap: 1rem 2rem;
		padding: 0 1.25rem 1.125rem;
	}

	@media (max-width: 900px) {
		.ov-story {
			grid-template-columns: minmax(0, 1fr);
		}
		.ov-story-side {
			border-left: 0;
			border-top: 1px solid var(--border);
		}
	}
</style>
