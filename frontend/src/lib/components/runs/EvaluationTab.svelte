<script lang="ts">
	/**
	 * EvaluationTab — a sample's RunEvaluations (spec 8, 10.4): the stage strip, `ChecksTable` (grouped by
	 * category; infra and flaky checks apart and never counted; the starter baseline shown), `ProbesTable`
	 * (CWE, exploited / not exploited / skipped with reason, evidence), the adherence record, and reuse /
	 * retest markers. Evaluations are append-only: the latest non-retest row per suite is the reading;
	 * anything not measured says so and is never shown as a 0 or a clean result.
	 *
	 * Props: `run`, `version` (bump to reload), `oncreated` (after "Re-evaluate").
	 */
	import { untrack } from 'svelte';
	import * as Card from '$lib/components/ui/card';
	import * as Select from '$lib/components/ui/select';
	import { Badge } from '$lib/components/ui/badge';
	import { Button } from '$lib/components/ui/button';
	import StatusBadge from '$lib/components/StatusBadge.svelte';
	import LoadingState from '$lib/components/LoadingState.svelte';
	import ErrorAlert from '$lib/components/ErrorAlert.svelte';
	import EmptyState from '$lib/components/EmptyState.svelte';
	import SectionHeading from '$lib/components/SectionHeading.svelte';
	import ShieldCheck from '@lucide/svelte/icons/shield-check';
	import RotateCw from '@lucide/svelte/icons/rotate-cw';
	import LoaderCircle from '@lucide/svelte/icons/loader-circle';
	import { toast } from 'svelte-sonner';
	import { formatApiError, isAbortError } from '$lib/api/core';
	import { evaluateSample, listSampleEvaluations } from '$lib/api/v3/samples';
	import { EVAL_STAGE_KEYS, type RunEvaluationOut, type RunOut } from '$lib/api/v3/types';
	import {
		evaluationSummary,
		fmtRate,
		fmtSeconds,
		groupChecks,
		PROBE_STATUS_TEXT,
		PROBE_TONES,
		SKIP_REASON_TEXT,
	} from './format';

	interface Props {
		run: RunOut;
		version?: number;
		oncreated?: () => void;
	}

	let { run, version = 0, oncreated }: Props = $props();

	let evaluations = $state<RunEvaluationOut[]>([]);
	let loading = $state(true);
	let error = $state('');
	let selectedId = $state('');
	let busy = $state(false);

	async function load(signal?: AbortSignal) {
		loading = untrack(() => evaluations.length === 0);
		error = '';
		try {
			evaluations = await listSampleEvaluations(run.id, { signal });
			const reading = readingOf(evaluations);
			if (!selectedId || !evaluations.some((e) => String(e.id) === selectedId)) {
				selectedId = reading ? String(reading.id) : '';
			}
		} catch (e) {
			if (!isAbortError(e)) error = formatApiError(e, 'Could not load the evaluations.');
		} finally {
			loading = false;
		}
	}

	/** The reading: the newest non-retest evaluation (append-only rows; readers take the latest). */
	function readingOf(rows: RunEvaluationOut[]): RunEvaluationOut | null {
		const candidates = rows.filter((e) => e.retest_of === null);
		return candidates.sort((a, b) => b.created_at.localeCompare(a.created_at))[0] ?? null;
	}

	$effect(() => {
		void version;
		const controller = new AbortController();
		void load(controller.signal);
		return () => controller.abort();
	});

	const current = $derived(evaluations.find((e) => String(e.id) === selectedId) ?? null);
	const result = $derived(current?.result ?? null);
	const grouped = $derived(groupChecks(result?.checks ?? [], result?.flaky_checks ?? []));
	const flaky = $derived(new Set(result?.flaky_checks ?? []));
	const measured = $derived(current?.status === 'measured' && result?.measured === true);
	const failed = $derived(
		grouped.agent.flatMap((g) => g.checks).filter((c) => (c.status === 'fail' || c.status === 'error') && !flaky.has(c.id)),
	);
	let showAll = $state(false);

	function jumpTo(id: string) {
		showAll = true;
		requestAnimationFrame(() => {
			const row = document.getElementById(`check-${id}`);
			row?.scrollIntoView({ block: 'center', behavior: 'smooth' });
			row?.classList.add('ev-flash');
			setTimeout(() => row?.classList.remove('ev-flash'), 1600);
		});
	}

	/** Response bodies arrive as JSON strings inside the observed object: unwrap them so they read as JSON. */
	function unwrap(v: unknown): unknown {
		if (typeof v === 'string') {
			const t = v.trim();
			if ((t.startsWith('{') || t.startsWith('[')) && t.length < 200_000) {
				try {
					return JSON.parse(t);
				} catch {
					return v;
				}
			}
			return v;
		}
		if (v && typeof v === 'object' && !Array.isArray(v)) {
			return Object.fromEntries(Object.entries(v as Record<string, unknown>).map(([k, x]) => [k, unwrap(x)]));
		}
		return v;
	}

	function compactJson(v: unknown): string {
		if (v == null) return '—';
		const u = unwrap(v);
		const text = typeof u === 'string' ? u : JSON.stringify(u, null, 1).replace(/\n\s*/g, ' ');
		return text.length > 220 ? `${text.slice(0, 220)}…` : text;
	}

	async function reevaluate() {
		busy = true;
		try {
			await evaluateSample(run.id, { force_rebuild: false });
			toast.success('Evaluation queued on the eval lane');
			await load();
			oncreated?.();
		} catch (e) {
			toast.error(formatApiError(e, 'Could not queue the evaluation.'));
		} finally {
			busy = false;
		}
	}

	function stageStatus(key: string): string {
		const stage = current?.stages?.[key as keyof RunEvaluationOut['stages']];
		return stage ? stage.status : 'not planned';
	}
</script>

{#if loading}
	<LoadingState label="loading evaluations" />
{:else if error}
	<ErrorAlert message={error} onretry={() => load()} />
{:else}
	<div class="space-y-4">
		<div class="flex flex-wrap items-center gap-2">
			{#if evaluations.length > 1}
				<Select.Simple
					size="sm"
					aria-label="Evaluation"
					bind:value={selectedId}
					items={evaluations.map((e) => ({
						value: String(e.id),
						label: `#${e.id} · ${e.eval_suite} · ${e.eval_level}${e.retest_of ? ' · retest' : ''}`,
						description: `${e.status} · ${e.created_at}`,
					}))}
				/>
			{/if}
			<span class="text-sm">{evaluationSummary(current)}</span>
			{#if current?.reused_from}<Badge variant="outline" title="Identical tree: the result of evaluation #{current.reused_from} was reused">reused #{current.reused_from}</Badge>{/if}
			{#if current?.retest_of}<Badge variant="outline" title="A test-retest run; never the reading">retest of #{current.retest_of}</Badge>{/if}
			{#if run.status === 'completed' && run.kind !== 'reference'}
				<Button variant="outline" size="sm" class="ml-auto" disabled={busy} onclick={reevaluate}>
					{#if busy}<LoaderCircle class="mr-1.5 h-3.5 w-3.5 animate-spin motion-reduce:animate-none" />{:else}<RotateCw class="mr-1.5 h-3.5 w-3.5" />{/if}
					Re-evaluate
				</Button>
			{/if}
		</div>

		{#if !current}
			<EmptyState
				icon={ShieldCheck}
				title="Not evaluated yet"
				description="Evaluation runs on the eval lane after generation. Until then every outcome is “not measured”."
			/>
		{:else}
			<ol class="flex flex-wrap gap-1.5 text-xs" aria-label="Evaluation stages">
				{#each EVAL_STAGE_KEYS as key (key)}
					{@const stage = current.stages?.[key]}
					<li class="inline-flex items-center gap-1 rounded border border-border px-2 py-0.5">
						<span class="font-mono">{key}</span>
						<StatusBadge
							status={stageStatus(key)}
							tone={stage?.status === 'ok' ? 'success' : stage?.status === 'fail' || stage?.status === 'error' ? 'danger' : stage?.status === 'running' ? 'info' : 'neutral'}
							label={stageStatus(key).replace(/_/g, ' ')}
						/>
						{#if stage?.seconds != null}<span class="tabular-nums text-muted-foreground">{fmtSeconds(stage.seconds)}</span>{/if}
					</li>
				{/each}
			</ol>

			{#if result && !result.measured}
				<p class="rounded-md border border-warning/30 bg-warning/10 px-3 py-2 text-sm text-warning-fg">
					Not measured{result.not_measured_reason ? `: ${result.not_measured_reason}` : ''}. No pass rate is computed for this evaluation.
				</p>
			{/if}

			{#if result}
				{@const m = result.metrics}
				<div class="ev-facts">
					<span title="Agent checks only; infra and flaky checks excluded"><span class="ev-k">Pass rate</span><b>{measured ? fmtRate(m.agent_pass_rate) : 'n/m'}</b> <span class="text-muted-foreground">{m.agent_passed}/{m.agent_checks}</span></span>
					<span title="Pass rate of the unchanged starter (what a skeleton gets for free)"><span class="ev-k">Starter baseline</span><b>{fmtRate(m.baseline_pass_rate, '—')}</b> <span class="text-muted-foreground">{m.over_baseline == null ? '' : `+${(m.over_baseline * 100).toFixed(1)} pts over it`}</span></span>
					<span title="Checks whose requirement the prose variant stated / left unstated"><span class="ev-k">Stated / unstated</span><b>{fmtRate(m.stated_pass_rate, '—')} / {fmtRate(m.unstated_pass_rate, '—')}</b></span>
					<span><span class="ev-k">Probes exploited</span><b>{m.probes_tested ? `${m.probes_exploited}/${m.probes_tested}` : 'n/m'}</b>{#if m.probes_skipped} <span class="text-muted-foreground">{m.probes_skipped} skipped</span>{/if}</span>
					<span title="All functional checks pass and no tested probe was exploited"><span class="ev-k">Secure pass</span><b>{m.sec_pass === null ? 'n/m' : m.sec_pass ? 'yes' : 'no'}</b></span>
					{#if m.slot_pass_rate !== undefined && m.slot_pass_rate !== null}
						<span title="Discriminating linked checks of the slot target only"><span class="ev-k">Slot pass rate</span><b>{fmtRate(m.slot_pass_rate)}</b></span>
					{/if}
				</div>

				<!-- Check map: the whole suite at a glance, one square per check. -->
				{#if grouped.agent.length}
					<div class="ev-map" aria-label="Checks by category">
						{#each grouped.agent as group (group.category)}
							{@const ratio = group.counted ? group.passed / group.counted : null}
							<div class="ev-tile" data-state={ratio === 1 ? 'all' : ratio === 0 ? 'none' : 'some'}>
								<div class="ev-tile-head">
									<span class="ev-tile-name">{group.category}</span>
									<span class="ev-tile-score">{group.passed}/{group.counted}</span>
								</div>
								<div class="ev-squares">
									{#each group.checks as c (c.id)}
										<button
											type="button"
											class="ev-sq"
											data-status={flaky.has(c.id) ? 'flaky' : c.status}
											title="{c.id}: {flaky.has(c.id) ? 'flaky (excluded)' : c.status}"
											aria-label="{c.id}: {c.status}"
											onclick={() => jumpTo(c.id)}
										></button>
									{/each}
								</div>
							</div>
						{/each}
					</div>
				{/if}

				<!-- Failures first, with what the tester expected vs what the app returned. -->
				{#if failed.length}
					<section class="ev-fails" aria-labelledby="ev-fails-h">
						<h3 id="ev-fails-h" class="ev-fails-h">{failed.length} failing check{failed.length === 1 ? '' : 's'}</h3>
						{#each failed as c (c.id)}
							<article class="ev-fail">
								<div class="flex flex-wrap items-baseline gap-x-3 gap-y-1">
									<span class="font-mono text-sm font-medium">{c.id}</span>
									<span class="text-xs text-muted-foreground">{c.category}{c.endpoint ? ` · ${c.endpoint}` : ''}</span>
									<StatusBadge status={c.status} tone={c.status === 'fail' ? 'danger' : 'warning'} />
								</div>
								{#if c.note}<p class="text-sm">{c.note}</p>{/if}
								<div class="ev-diff">
									<div><span class="ev-k">Expected</span><code>{compactJson(c.expected)}</code></div>
									<div><span class="ev-k">Observed</span><code>{compactJson(c.observed)}</code></div>
								</div>
							</article>
						{/each}
					</section>
				{:else if measured && grouped.agent.length}
					<p class="ev-allpass">Every counted agent check passed.</p>
				{/if}

				<button type="button" class="ev-toggle" aria-expanded={showAll} onclick={() => (showAll = !showAll)}>
					{showAll ? 'Hide' : 'Show'} all {result.checks.length} checks
				</button>
				{#if showAll}
				<Card.Root class="gap-0 overflow-clip py-0">
					<div class="overflow-x-auto">
						<table>
							<thead>
								<tr><th>Check</th><th>Endpoint</th><th>Status</th><th class="num">ms</th><th>Note</th></tr>
							</thead>
							<tbody>
								{#each grouped.agent as group (group.category)}
									<tr class="bg-muted/40">
										<td colspan="5" class="text-xs font-medium">
											{group.category} <span class="font-mono text-muted-foreground">{group.passed}/{group.counted} passed</span>
										</td>
									</tr>
									{#each group.checks as check (check.id)}
										<tr id="check-{check.id}">
											<td class="font-mono text-xs">
												{check.id}
												{#if flaky.has(check.id)}<Badge variant="secondary" class="ml-1" title="Flaky across runs: excluded from every pass rate">flaky</Badge>{/if}
											</td>
											<td class="font-mono text-xs">{check.endpoint ?? '—'}</td>
											<td>
												<StatusBadge
													status={check.status}
													tone={check.status === 'pass' ? 'success' : check.status === 'fail' ? 'danger' : check.status === 'error' ? 'warning' : 'neutral'}
												/>
											</td>
											<td class="num">{check.ms ?? '—'}</td>
											<td class="max-w-96 text-xs text-muted-foreground">
												{check.note ?? ''}
												{#if check.skipped_due_to?.length}skipped: depends on {check.skipped_due_to.join(', ')}{/if}
											</td>
										</tr>
									{/each}
								{/each}
								{#if grouped.infra.length}
									<tr class="bg-muted/40">
										<td colspan="5" class="text-xs font-medium">infra <span class="text-muted-foreground">(platform checks; never in a pass rate)</span></td>
									</tr>
									{#each grouped.infra as check (check.id)}
										<tr>
											<td class="font-mono text-xs">{check.id}</td>
											<td class="font-mono text-xs">{check.endpoint ?? '—'}</td>
											<td><StatusBadge status={check.status} tone={check.status === 'pass' ? 'success' : 'warning'} /></td>
											<td class="num">{check.ms ?? '—'}</td>
											<td class="text-xs text-muted-foreground">{check.note ?? ''}</td>
										</tr>
									{/each}
								{/if}
								{#if result.checks.length === 0}
									<tr><td colspan="5" class="text-muted-foreground">No check ran.</td></tr>
								{/if}
							</tbody>
						</table>
					</div>
				</Card.Root>
				{/if}

				<SectionHeading size="sm" title="Probes" count={result.probes.length} lead="Probes are separate from pass rates and run only when their preconditions hold." />
				<Card.Root class="gap-0 overflow-clip py-0">
					<div class="overflow-x-auto">
						<table>
							<thead><tr><th>Probe</th><th>CWE</th><th>Kind</th><th>Endpoint</th><th>Result</th><th>Evidence</th></tr></thead>
							<tbody>
								{#each result.probes as probe (probe.id)}
									<tr>
										<td class="font-mono text-xs">{probe.id}</td>
										<td class="font-mono text-xs">{probe.cwe}</td>
										<td class="font-mono text-xs">{probe.kind}</td>
										<td class="font-mono text-xs">{probe.endpoint ?? '—'}</td>
										<td>
											<StatusBadge status={probe.status} tones={PROBE_TONES} label={PROBE_STATUS_TEXT[probe.status]} />
											{#if probe.skip_reason}<div class="cell-sub">{SKIP_REASON_TEXT[probe.skip_reason] ?? probe.skip_reason}</div>{/if}
										</td>
										<td class="max-w-96 truncate font-mono text-xs" title={probe.evidence ?? ''}>{probe.evidence ?? '—'}</td>
									</tr>
								{:else}
									<tr><td colspan="6" class="text-muted-foreground">No probe at this evaluation level (not measured).</td></tr>
								{/each}
							</tbody>
						</table>
					</div>
				</Card.Root>
			{/if}

			<SectionHeading size="sm" title="Adherence" lead="Whether the app used the requested stack and style. Adherence never filters: it is a column and a note." />
			{#if !current.adherence}
				<p class="text-sm text-muted-foreground">Not measured at this evaluation level.</p>
			{:else}
				{@const a = current.adherence}
				<Card.Root>
					<Card.Content class="space-y-3 text-sm">
						<div class="flex flex-wrap items-center gap-2">
							<StatusBadge
								status={a.status}
								tone={a.status === 'compliant' ? 'success' : a.status === 'partial' ? 'warning' : a.status === 'violated' ? 'danger' : 'neutral'}
								label={a.status.replace(/_/g, ' ')}
							/>
							<span class="text-xs text-muted-foreground">checker v{a.checker_version}</span>
						</div>
						{#if a.detected}
							<dl class="grid grid-cols-2 gap-x-4 gap-y-1 text-xs sm:grid-cols-5">
								{#each Object.entries(a.detected) as [dimension, detected] (dimension)}
									<div>
										<dt class="text-muted-foreground">{dimension}</dt>
										<dd class="font-mono">{detected?.value ?? '—'} <span class="text-muted-foreground">({detected?.confidence})</span></dd>
									</div>
								{/each}
							</dl>
						{/if}
						{#if a.deviations.length}
							<ul class="space-y-1 text-xs">
								{#each a.deviations as d, i (i)}
									<li><span class="font-mono">{d.dimension}</span>: expected <b>{d.expected}</b>, observed <b>{d.observed}</b></li>
								{/each}
							</ul>
						{/if}
						{#if a.hallucinated.length || a.unpinned.length}
							<p class="text-xs">
								{#if a.hallucinated.length}<span>Packages not found in their registry: <span class="font-mono">{a.hallucinated.join(', ')}</span>. </span>{/if}
								{#if a.unpinned.length}<span>Unpinned: <span class="font-mono">{a.unpinned.join(', ')}</span>.</span>{/if}
							</p>
						{/if}
					</Card.Content>
				</Card.Root>
			{/if}
		{/if}
	</div>
{/if}

<style>
	.ev-k {
		margin-right: 0.375rem;
		font-family: var(--font-mono);
		font-size: 0.625rem;
		text-transform: uppercase;
		letter-spacing: 0.1em;
		color: var(--muted-foreground);
	}
	.ev-facts {
		display: flex;
		flex-wrap: wrap;
		gap: 0.5rem 1.75rem;
		border: 1px solid var(--border);
		border-radius: var(--radius);
		background: var(--card);
		padding: 0.75rem 1.125rem;
		font-size: 0.8125rem;
	}
	.ev-facts b {
		font-family: var(--font-mono);
		font-weight: 600;
		font-variant-numeric: tabular-nums;
	}

	.ev-map {
		display: grid;
		grid-template-columns: repeat(auto-fill, minmax(11rem, 1fr));
		gap: 0.625rem;
	}
	.ev-tile {
		border: 1px solid var(--border);
		border-radius: var(--radius);
		background: var(--card);
		padding: 0.625rem 0.75rem 0.75rem;
		border-top: 2px solid var(--border);
	}
	.ev-tile[data-state='all'] {
		border-top-color: var(--success);
	}
	.ev-tile[data-state='some'] {
		border-top-color: var(--warning);
	}
	.ev-tile[data-state='none'] {
		border-top-color: var(--destructive);
	}
	.ev-tile-head {
		display: flex;
		align-items: baseline;
		justify-content: space-between;
		gap: 0.5rem;
		margin-bottom: 0.5rem;
	}
	.ev-tile-name {
		font-size: 0.8125rem;
		font-weight: 500;
	}
	.ev-tile-score {
		font-family: var(--font-mono);
		font-size: 0.75rem;
		color: var(--muted-foreground);
		font-variant-numeric: tabular-nums;
	}
	.ev-squares {
		display: flex;
		flex-wrap: wrap;
		gap: 3px;
	}
	.ev-sq {
		width: 0.875rem;
		height: 0.875rem;
		cursor: pointer;
		border-radius: 2px;
		background: var(--muted);
		transition: transform 120ms;
	}
	.ev-sq:hover,
	.ev-sq:focus-visible {
		transform: scale(1.25);
		outline: none;
		box-shadow: 0 0 0 2px var(--ring);
	}
	.ev-sq[data-status='pass'] {
		background: color-mix(in oklab, var(--success) 75%, transparent);
	}
	.ev-sq[data-status='fail'] {
		background: var(--destructive);
	}
	.ev-sq[data-status='error'] {
		background: var(--warning);
	}
	.ev-sq[data-status='flaky'] {
		background: repeating-linear-gradient(45deg, var(--muted-foreground) 0 2px, transparent 2px 4px);
		opacity: 0.6;
	}

	.ev-fails {
		display: flex;
		flex-direction: column;
		gap: 0.5rem;
	}
	.ev-fails-h {
		font-family: var(--font-display);
		font-size: 1rem;
		font-weight: 600;
		color: var(--danger-fg);
	}
	.ev-fail {
		display: flex;
		flex-direction: column;
		gap: 0.5rem;
		border: 1px solid color-mix(in oklab, var(--destructive) 35%, var(--border));
		border-left: 3px solid var(--destructive);
		border-radius: var(--radius);
		background: var(--card);
		padding: 0.75rem 1rem;
	}
	.ev-diff {
		display: grid;
		grid-template-columns: repeat(2, minmax(0, 1fr));
		gap: 0.5rem;
	}
	.ev-diff > div {
		display: flex;
		min-width: 0;
		flex-direction: column;
		gap: 0.25rem;
		border-radius: 0.25rem;
		background: var(--muted);
		padding: 0.375rem 0.5rem;
	}
	.ev-diff code {
		overflow-wrap: anywhere;
		font-family: var(--font-mono);
		font-size: 0.75rem;
	}
	.ev-allpass {
		border: 1px solid color-mix(in oklab, var(--success) 35%, var(--border));
		border-radius: var(--radius);
		background: color-mix(in oklab, var(--success) 8%, var(--card));
		padding: 0.625rem 1rem;
		font-size: 0.875rem;
		color: var(--success-fg);
	}
	.ev-toggle {
		cursor: pointer;
		align-self: flex-start;
		font-size: 0.8125rem;
		font-weight: 500;
		color: var(--primary);
	}
	.ev-toggle:hover {
		text-decoration: underline;
	}
	:global(tr.ev-flash) {
		animation: ev-flash 1.6s ease-out;
	}
	@keyframes ev-flash {
		from {
			background: color-mix(in oklab, var(--primary) 25%, transparent);
		}
	}
	@media (prefers-reduced-motion: reduce) {
		:global(tr.ev-flash) {
			animation: none;
			background: color-mix(in oklab, var(--primary) 15%, transparent);
		}
	}
	@media (max-width: 640px) {
		.ev-diff {
			grid-template-columns: minmax(0, 1fr);
		}
	}
</style>
