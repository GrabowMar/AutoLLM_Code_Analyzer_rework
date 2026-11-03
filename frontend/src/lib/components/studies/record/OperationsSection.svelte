<script lang="ts">
	/**
	 * OperationsSection — "Operations" of the study record (spec 10.3 §6): execution, not science.
	 * Live spend (spent / reserved / cap, billed and list, burn rate, ETA, cost per usable and per tested
	 * sample), the pilot gates while piloting, condition pause / resume, top-ups (missing or infra cells
	 * only: a usable run is never superseded), the condition grid, failures by outcome and reason, the
	 * activity feed and the live trajectory of a running run, executor capacity, the spend ledger and the
	 * spend charts, then schedules and post-runs. Fed by `StudyLive` (SSE `study:<id>`).
	 *
	 * Props: `live` (`StudyLive`), `number`, `onchanged()` (after an action that changes the study).
	 */
	import { onMount, untrack } from 'svelte';
	import type { ScheduleOut, PostRunOut, ExpandOut } from '$lib/api/v3/types';
	import {
		createPostRun,
		createSchedule,
		deleteSchedule,
		listPostRuns,
		listSchedules,
		runScheduleNow,
		setConditionPaused,
		topUpStudy,
		updateSchedule,
	} from '$lib/api/v3/studies';
	import { getCapacity, type Capacity } from '$lib/api/execution';
	import { formatApiError } from '$lib/api/core';
	import * as Card from '$lib/components/ui/card';
	import { Button } from '$lib/components/ui/button';
	import { Switch } from '$lib/components/ui/switch';
	import StatusBadge from '$lib/components/StatusBadge.svelte';
	import ErrorAlert from '$lib/components/ErrorAlert.svelte';
	import StudySection from '../study/StudySection.svelte';
	import StudyFigure from '../study/StudyFigure.svelte';
	import LiveSpendStrip from '../monitor/LiveSpendStrip.svelte';
	import PilotGateCard from '../monitor/PilotGateCard.svelte';
	import ConditionGrid from '../monitor/ConditionGrid.svelte';
	import FailureSummary from '../monitor/FailureSummary.svelte';
	import ActivityFeed from '../monitor/ActivityFeed.svelte';
	import RunStreamPanel from '../monitor/RunStreamPanel.svelte';
	import CapacityStrip from '../monitor/CapacityStrip.svelte';
	import LedgerTable from '../budget/LedgerTable.svelte';
	import SpendCharts from '../budget/SpendCharts.svelte';
	import ScheduleEditor from '../automation/ScheduleEditor.svelte';
	import PostRunEditor, { defaultAnalysisConfig, type AnalysisPostRunConfig } from '../automation/PostRunEditor.svelte';
	import { defaultSchedule, describeCron, SCHEDULE_ACTION_LABELS, type ScheduleSpec } from '../automation/cron';
	import { refreshSpendChip } from '../spend';
	import { isTerminal, pauseLabel, plural } from '../format';
	import type { StudyLive } from '../monitor/live.svelte';
	import Pause from '@lucide/svelte/icons/pause';
	import Play from '@lucide/svelte/icons/play';
	import Plus from '@lucide/svelte/icons/plus';
	import RotateCcw from '@lucide/svelte/icons/rotate-ccw';
	import Trash2 from '@lucide/svelte/icons/trash-2';

	interface Props {
		live: StudyLive;
		number?: string;
		onchanged?: () => void;
	}

	let { live, number = '5', onchanged }: Props = $props();

	const study = $derived(live.study);
	const budget = $derived(live.budget);
	const cells = $derived(live.cells);
	const labels = $derived(new Map((budget?.by_condition ?? []).map((c) => [c.condition, c.label])));
	const conditionLabel = (id: number) => labels.get(id) ?? `condition ${id}`;
	const running = $derived(
		(cells?.cells ?? [])
			.filter((c) => c.current_run && c.current_run.status === 'running')
			.map((c) => ({ id: c.current_run!.id, label: `${conditionLabel(c.condition_id)} · ${c.unit_key} · r${c.repeat_index}` })),
	);
	const pausedConditions = $derived(
		new Map(((budget?.deviations?.paused_conditions as { condition_id: number; reason: string }[] | undefined) ?? []).map((p) => [p.condition_id, p.reason])),
	);
	const editable = $derived(!!study && !isTerminal(study.status) && study.status !== 'draft');

	let actionError = $state('');
	let actionNote = $state('');
	let busy = $state(false);

	async function act(fn: () => Promise<unknown>, done: (out: unknown) => string, fallback: string) {
		busy = true;
		actionError = '';
		actionNote = '';
		try {
			const out = await fn();
			actionNote = done(out);
			live.restart();
			refreshSpendChip();
			onchanged?.();
		} catch (e) {
			actionError = formatApiError(e, fallback);
		} finally {
			busy = false;
		}
	}

	function topUp(kind: 'missing' | 'infra') {
		if (!study) return;
		void act(
			() => topUpStudy(study.id, { cells: kind, reason: `manual top-up (${kind})` }),
			(out) => {
				const o = out as { created_runs: number; refused: unknown[] };
				return `${plural(o.created_runs, 'run')} queued${o.refused.length ? `; ${o.refused.length} cell(s) refused (they already have a usable run)` : ''}.`;
			},
			'Could not top up.',
		);
	}

	function toggleCondition(id: number, paused: boolean) {
		if (!study) return;
		void act(() => setConditionPaused(study.id, id, paused), () => (paused ? 'Condition paused.' : 'Condition resumed.'), 'Could not change the condition.');
	}

	// ── capacity ──
	let capacity = $state<Capacity | null>(null);
	onMount(() => {
		const load = () => getCapacity().then((c) => (capacity = c)).catch(() => {});
		void load();
		const timer = setInterval(() => !document.hidden && load(), 30_000);
		return () => clearInterval(timer);
	});

	// ── schedules and post-runs ──
	let schedules = $state<ScheduleOut[] | null>(null);
	let postRuns = $state<PostRunOut[] | null>(null);
	let newSchedule = $state<ScheduleSpec | null>(null);
	let newPostRun = $state<AnalysisPostRunConfig | null>(null);
	let autoError = $state('');

	async function loadAutomation() {
		if (!study) return;
		try {
			[schedules, postRuns] = await Promise.all([listSchedules(study.id), listPostRuns(study.id)]);
		} catch (e) {
			autoError = formatApiError(e, 'Could not load schedules and post-runs.');
		}
	}

	$effect(() => {
		if (study?.id && schedules === null) void loadAutomation();
	});

	async function auto(fn: () => Promise<unknown>, fallback: string) {
		autoError = '';
		try {
			await fn();
			await loadAutomation();
		} catch (e) {
			autoError = formatApiError(e, fallback);
		}
	}

	// Refetch the ledger when the spend moves (at most every 10 s).
	let ledgerSignal = $state(0);
	let lastLedger = 0;
	$effect(() => {
		void budget?.spent;
		untrack(() => {
			const now = Date.now();
			if (now - lastLedger >= 10_000) {
				lastLedger = now;
				ledgerSignal++;
			}
		});
	});

	function onExpanded(out: ExpandOut) {
		actionNote = `Main stage started: ${plural(out.created_runs, 'run')} queued${out.reused_pilot_cells ? `, ${out.reused_pilot_cells} pilot cells reused` : ''}.`;
		live.restart();
		onchanged?.();
	}
</script>

<StudySection id="operations" {number} title="Operations" lead="How the study is running: spend, progress, failures and automation. None of this changes what a sample is.">
	{#if !study}
		<p class="text-xs text-muted-foreground">Loading…</p>
	{:else}
		<StudyFigure id="figure-spend" kind="figure" number="O1" title="Spend against the cap">
			<LiveSpendStrip {budget} liveBurnRate={live.liveBurnRate} alertFraction={live.alertFraction} />
			{#snippet caption()}
				Spent is what the ledger settled (billed when the provider reports it); reserved is held for the blocks that are running.
				{#if live.connection !== 'open'}<span class="text-warning-fg">Live updates {live.connection}{live.polling ? '; polling' : ''}.</span>{/if}
			{/snippet}
		</StudyFigure>

		{#if study.status === 'piloting'}
			<PilotGateCard studyId={study.id} onexpanded={onExpanded} disabled={busy} />
		{/if}

		{#if editable}
			<div class="flex flex-wrap items-center gap-2">
				<Button variant="outline" size="sm" disabled={busy} onclick={() => topUp('infra')}><RotateCcw />Retry infra errors</Button>
				<Button variant="outline" size="sm" disabled={busy} onclick={() => topUp('missing')}><Plus />Fill missing cells</Button>
				{#if actionNote}<span class="text-xs text-muted-foreground" role="status">{actionNote}</span>{/if}
			</div>
		{/if}
		{#if actionError}<ErrorAlert message={actionError} />{/if}

		{#if budget && budget.by_condition.length > 0}
			<StudyFigure id="table-o1" kind="table" number="O2" title="Conditions" flush>
				<div class="overflow-x-auto">
					<table>
						<thead><tr><th class="col-sticky">Condition</th><th class="num">Runs</th><th class="num">Usable</th><th class="num">Tested</th><th>State</th><th></th></tr></thead>
						<tbody>
							{#each budget.by_condition as c (c.condition)}
								{@const reason = pausedConditions.get(c.condition)}
								<tr>
									<td class="col-sticky max-w-64 truncate">{c.label || `condition ${c.condition}`}</td>
									<td class="num">{c.runs}</td>
									<td class="num">{c.usable}</td>
									<td class="num">{c.tested}</td>
									<td>{#if reason}<StatusBadge status="paused" tone="warning" label="paused · {pauseLabel(reason)}" />{:else}<span class="text-xs text-muted-foreground">active</span>{/if}</td>
									<td class="text-right">
										{#if editable}
											<Button variant="ghost" size="sm" disabled={busy} onclick={() => toggleCondition(c.condition, !reason)}>
												{#if reason}<Play />Resume{:else}<Pause />Pause{/if}
											</Button>
										{/if}
									</td>
								</tr>
							{/each}
						</tbody>
					</table>
				</div>
			</StudyFigure>
		{/if}

		{#if cells}
			<StudyFigure id="figure-grid" kind="figure" number="O3" title="Every cell, by condition and unit">
				<ConditionGrid {cells} {conditionLabel} />
				{#if cells.cells.some((c) => c.stage === 'pilot')}
					<p class="section-label mt-3">Pilot</p>
					<ConditionGrid {cells} {conditionLabel} stage="pilot" />
				{/if}
				{#snippet caption()}One square per cell, in repeat order: ring = waiting, solid = finished (coloured by outcome), dashed = excluded.{/snippet}
			</StudyFigure>

			<div class="grid gap-4 lg:grid-cols-2">
				<Card.Root>
					<Card.Header><Card.Title>Failures</Card.Title></Card.Header>
					<Card.Content><FailureSummary {cells} /></Card.Content>
				</Card.Root>
				<Card.Root>
					<Card.Header><Card.Title>Activity</Card.Title></Card.Header>
					<Card.Content><ActivityFeed items={live.activity} {conditionLabel} /></Card.Content>
				</Card.Root>
			</div>
		{/if}

		<Card.Root>
			<Card.Header><Card.Title>Live trajectory</Card.Title></Card.Header>
			<Card.Content><RunStreamPanel runs={running} /></Card.Content>
		</Card.Root>

		{#if capacity}<CapacityStrip {capacity} />{/if}

		{#if budget}
			<StudyFigure id="figure-spend-by" kind="figure" number="O4" title="Where the money went">
				<SpendCharts {budget} />
			</StudyFigure>
		{/if}

		<StudyFigure id="table-ledger" kind="table" number="O5" title="Spend ledger">
			<LedgerTable studyId={study.id} refreshSignal={ledgerSignal} />
		</StudyFigure>

		<div class="grid gap-4 lg:grid-cols-2">
			<Card.Root>
				<Card.Header class="flex flex-row items-center justify-between">
					<Card.Title>Schedules</Card.Title>
					{#if !newSchedule}<Button variant="outline" size="sm" onclick={() => (newSchedule = defaultSchedule())}><Plus />Add</Button>{/if}
				</Card.Header>
				<Card.Content class="space-y-3">
					{#each schedules ?? [] as s (s.id)}
						<div class="flex flex-wrap items-center gap-2 text-xs">
							<Switch checked={s.enabled} onCheckedChange={(v) => auto(() => updateSchedule(study.id, s.id, { enabled: v }), 'Could not change the schedule.')} />
							<span class="font-medium">{SCHEDULE_ACTION_LABELS[s.action] ?? s.action}</span>
							<span class="text-muted-foreground">{describeCron(s.cron)} ({s.timezone})</span>
							{#if s.next_run_at}<span class="text-muted-foreground">next {new Date(s.next_run_at).toLocaleString()}</span>{/if}
							<Button variant="ghost" size="sm" class="ml-auto" onclick={() => auto(() => runScheduleNow(study.id, s.id), 'Could not run the schedule.')}>Run now</Button>
							<Button variant="ghost" size="icon" aria-label="Delete the schedule" onclick={() => auto(() => deleteSchedule(study.id, s.id), 'Could not delete the schedule.')}><Trash2 /></Button>
						</div>
					{:else}
						{#if !newSchedule}<p class="text-xs text-muted-foreground">No schedule.</p>{/if}
					{/each}
					{#if newSchedule}
						<ScheduleEditor bind:value={newSchedule} />
						<div class="flex gap-2">
							<Button size="sm" onclick={() => auto(async () => { await createSchedule(study.id, { ...newSchedule! }); newSchedule = null; }, 'Could not add the schedule.')}>Save schedule</Button>
							<Button variant="ghost" size="sm" onclick={() => (newSchedule = null)}>Cancel</Button>
						</div>
					{/if}
				</Card.Content>
			</Card.Root>

			<Card.Root>
				<Card.Header class="flex flex-row items-center justify-between">
					<Card.Title>Post-runs</Card.Title>
					{#if !newPostRun}<Button variant="outline" size="sm" onclick={() => (newPostRun = defaultAnalysisConfig())}><Plus />Analysis</Button>{/if}
				</Card.Header>
				<Card.Content class="space-y-3">
					{#each postRuns ?? [] as p (p.id)}
						<div class="flex flex-wrap items-center gap-2 text-xs">
							<StatusBadge status={p.status} />
							<span class="font-medium">{p.kind}</span>
							<span class="text-muted-foreground">{p.trigger === 'completed' ? 'when the study completes' : 'manual'}</span>
							{#if p.pattern_run_id}<a class="text-primary underline" href="/studies/{study.id}?tab=patterns">patterns</a>{/if}
							{#if p.report_id}<a class="text-primary underline" href="/reports/{p.report_id}">report</a>{/if}
							{#if p.error}<span class="text-danger-fg">{p.error}</span>{/if}
						</div>
					{:else}
						{#if !newPostRun}<p class="text-xs text-muted-foreground">No post-run.</p>{/if}
					{/each}
					{#if newPostRun}
						<PostRunEditor bind:value={newPostRun} mode={study.mode} />
						<div class="flex gap-2">
							<Button size="sm" onclick={() => auto(async () => { await createPostRun(study.id, { kind: 'analysis', trigger: isTerminal(study.status) ? 'manual' : 'completed', config: { ...newPostRun! } }); newPostRun = null; }, 'Could not queue the post-run.')}>
								{isTerminal(study.status) ? 'Run now' : 'Run when the study completes'}
							</Button>
							<Button variant="ghost" size="sm" onclick={() => (newPostRun = null)}>Cancel</Button>
						</div>
					{/if}
				</Card.Content>
			</Card.Root>
		</div>
		{#if autoError}<ErrorAlert message={autoError} />{/if}
	{/if}
</StudySection>
