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
				<div class="grid grid-cols-2 gap-3 sm:grid-cols-3 lg:grid-cols-6">
					<div class="kpi-card" title="Agent checks only; infra and flaky checks excluded">
						<p class="kpi-label">Agent pass rate</p>
						<p class="kpi-value">{measured ? fmtRate(m.agent_pass_rate) : 'n/m'}</p>
						<p class="text-[11px] text-muted-foreground">{m.agent_passed}/{m.agent_checks} checks</p>
					</div>
					<div class="kpi-card" title="Pass rate of the unchanged starter (what a skeleton gets for free)">
						<p class="kpi-label">Starter baseline</p>
						<p class="kpi-value">{fmtRate(m.baseline_pass_rate, '—')}</p>
						<p class="text-[11px] text-muted-foreground">over baseline {m.over_baseline == null ? '—' : `${(m.over_baseline * 100).toFixed(1)} pts`}</p>
					</div>
					<div class="kpi-card" title="Checks whose requirement the prose variant stated / left unstated">
						<p class="kpi-label">Stated / unstated</p>
						<p class="kpi-value text-lg">{fmtRate(m.stated_pass_rate, '—')} / {fmtRate(m.unstated_pass_rate, '—')}</p>
					</div>
					<div class="kpi-card">
						<p class="kpi-label">Probes exploited</p>
						<p class="kpi-value">{m.probes_tested ? `${m.probes_exploited}/${m.probes_tested}` : 'n/m'}</p>
						<p class="text-[11px] text-muted-foreground">{m.probes_skipped} skipped</p>
					</div>
					<div class="kpi-card" title="All functional checks pass and no tested probe was exploited">
						<p class="kpi-label">Secure pass</p>
						<p class="kpi-value">{m.sec_pass === null ? 'n/m' : m.sec_pass ? 'yes' : 'no'}</p>
					</div>
					{#if m.slot_pass_rate !== undefined && m.slot_pass_rate !== null}
						<div class="kpi-card" title="Discriminating linked checks of the slot target only">
							<p class="kpi-label">Slot pass rate</p>
							<p class="kpi-value">{fmtRate(m.slot_pass_rate)}</p>
						</div>
					{/if}
				</div>

				<SectionHeading size="sm" title="Checks" count={result.checks.length} />
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
										<tr>
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
