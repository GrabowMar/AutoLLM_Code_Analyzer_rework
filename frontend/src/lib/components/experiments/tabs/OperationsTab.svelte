<script lang="ts">
	/**
	 * OperationsTab — §5 Operations, everything about running the experiment
	 * rather than its science: 5.1 capacity (slot lanes) and one EndpointLane
	 * per endpoint, 5.2 the activity feed, 5.3 execution limits (priority,
	 * concurrency, retries, repeats, preloading — editable while it runs),
	 * 5.4 automation (schedules and post-run). Refetches the post-run list on
	 * `post_run` events (`postRunSignal`).
	 *
	 * Props: `experiment`, `live` (null for drafts), `appName(id)`, `finished`,
	 * `postRunSignal`, `onlaunched()` (a schedule's "Run now" queued runs here),
	 * `onupdated(detail)` (execution limits saved).
	 *
	 * Archived experiments are read-only (the API refuses new schedules and
	 * post-run edits); post-run "Run now" needs at least one completed run.
	 */
	import { untrack } from 'svelte';
	import { toast } from 'svelte-sonner';
	import { cn } from '$lib/utils';
	import { Button } from '$lib/components/ui/button';
	import { Label } from '$lib/components/ui/label';
	import { Switch } from '$lib/components/ui/switch';
	import * as Select from '$lib/components/ui/select';
	import ErrorAlert from '$lib/components/ErrorAlert.svelte';
	import LoadingState from '$lib/components/LoadingState.svelte';
	import NumberField from '$lib/components/generator/composer/NumberField.svelte';
	import {
		getPostRun,
		listSchedules,
		updateExperiment,
		type ExperimentDetail,
		type ExperimentUpdateV2,
		type PostRunConfig,
		type ScheduleOut,
	} from '$lib/api/experiments';
	import { formatApiError } from '$lib/api/core';
	import LoaderCircle from '@lucide/svelte/icons/loader-circle';
	import Save from '@lucide/svelte/icons/save';
	import ActivityFeed from '../monitor/ActivityFeed.svelte';
	import EndpointLane, { type LaneJob } from '../monitor/EndpointLane.svelte';
	import PostRunCard from '../PostRunCard.svelte';
	import SchedulesCard from '../SchedulesCard.svelte';
	import { cellIndex, endpointGroupKey } from '../cells';
	import { MAX_REPEATS } from '../composer';
	import { isTerminal, priorityBucket } from '../format';
	import type { ExperimentLive } from '../live.svelte';
	import StudySection from '../study/StudySection.svelte';

	interface Props {
		experiment: ExperimentDetail;
		live: ExperimentLive | null;
		appName: (id: number | null) => string;
		finished?: boolean;
		postRunSignal?: number;
		onlaunched?: () => void;
		onupdated: (detail: ExperimentDetail) => void;
	}

	let { experiment, live, appName, finished = false, postRunSignal = 0, onlaunched, onupdated }: Props = $props();

	const status = $derived(live?.status ?? null);
	const grid = $derived(live?.grid ?? null);
	const conditionLabel = $derived(new Map((grid?.conditions ?? []).map((c) => [c.id, c.label])));
	const readonly = $derived(experiment.status === 'archived');
	const hasCompleted = $derived((experiment.progress?.completed ?? 0) > 0);
	const editable = $derived(experiment.status !== 'draft' && !isTerminal(experiment.status));

	// Sections are numbered in the order they show (drafts and finished runs hide some).
	const showActivity = $derived(!!live && (!finished || live.activity.length > 0));
	const sections = $derived(
		[live ? 'capacity' : '', showActivity ? 'activity' : '', 'execution', 'automation'].filter(Boolean)
	);
	const num = (id: string) => `5.${sections.indexOf(id) + 1}`;

	// ── capacity ────────────────────────────────────────────────────
	const PHASES: Record<string, string> = { r: 'running', q: 'awaiting eval', e: 'evaluating' };

	/** This experiment's in-flight cells per endpoint slug (from the grid). */
	const laneJobs = $derived.by(() => {
		const out = new Map<string, LaneJob[]>();
		if (!grid) return out;
		grid.conditions.forEach((condition, ci) => {
			const key = endpointGroupKey(condition.endpoint_slug);
			grid.apps.forEach((app, ai) => {
				for (let r = 0; r < grid.repeats; r++) {
					const i = cellIndex(grid, ci, ai, r);
					const code = grid.codes[i];
					const jobId = grid.jobIds[i];
					if (!code || !jobId || !(code in PHASES)) continue;
					const list = out.get(key) ?? [];
					list.push({ jobId, label: condition.label, app: app.name, repeat: r, phase: PHASES[code] });
					out.set(key, list);
				}
			});
		});
		return out;
	});

	const lanes = $derived(
		status
			? [
					{ label: 'Sandboxes', hint: 'Agent / build sandboxes on apps-dind', ...status.lanes.sandbox },
					{ label: 'Evaluation', hint: 'Grading slots', ...status.lanes.eval },
					{ label: 'LLM jobs', hint: 'Generation threads waiting on a model', ...status.lanes.llm },
					{ label: 'Post-run', hint: 'Analysis and report steps', ...status.lanes.post },
				]
			: []
	);

	// ── execution limits ────────────────────────────────────────────
	let priority = $state('0');
	let maxConcurrency = $state<number | null>(null);
	let infraRetries = $state<number | null>(null);
	let repeats = $state<number | null>(null);
	let warmup = $state(true);

	/** The values last seeded into (or saved from) the form. */
	let seeded: string | null = null;
	const formKey = () => JSON.stringify([priority, maxConcurrency, infraRetries, repeats, warmup]);

	// (Re)seed the form from the saved experiment: on first render, after a
	// save, and on silent detail reloads (state events, lifecycle actions) —
	// but only while the form is unedited, so a reload never drops pending edits.
	$effect(() => {
		const e = experiment;
		void e.updated_at;
		untrack(() => {
			if (seeded !== null && formKey() !== seeded) return;
			priority = String(priorityBucket(e.priority));
			maxConcurrency = e.max_concurrency;
			infraRetries = e.infra_retries;
			repeats = e.repeats;
			warmup = e.execution_options?.warmup ?? true;
			seeded = formKey();
		});
	});
	let invalid = $state({ conc: false, retries: false, repeats: false });
	let saving = $state(false);

	const patch = $derived.by((): ExperimentUpdateV2 => {
		const out: ExperimentUpdateV2 = {};
		// Only a changed level is sent: a staff-set value (e.g. 3) shows as "High"
		// and must not be rewritten to 5 when another field is saved.
		if (Number(priority) !== priorityBucket(experiment.priority)) out.priority = Number(priority);
		if (maxConcurrency !== experiment.max_concurrency) out.max_concurrency = maxConcurrency;
		if (infraRetries != null && infraRetries !== experiment.infra_retries) out.infra_retries = infraRetries;
		if (repeats != null && repeats !== experiment.repeats) out.repeats = repeats;
		if (warmup !== (experiment.execution_options?.warmup ?? true)) {
			out.execution_options = { ...experiment.execution_options, warmup };
		}
		return out;
	});
	const dirty = $derived(Object.keys(patch).length > 0);
	const anyInvalid = $derived(invalid.conc || invalid.retries || invalid.repeats);

	async function save() {
		saving = true;
		try {
			const detail = await updateExperiment(experiment.id, patch);
			seeded = formKey();
			toast.success(patch.repeats ? 'Saved. New repeats run at the next top-up.' : 'Saved');
			onupdated(detail);
		} catch (e) {
			toast.error(formatApiError(e, 'Could not save.'));
		} finally {
			saving = false;
		}
	}

	const PRIORITIES = [
		{ value: '-5', label: 'Low' },
		{ value: '0', label: 'Normal' },
		{ value: '5', label: 'High', description: 'Staff only' },
	];

	const readonlyLimits = $derived([
		{ label: 'Priority', value: PRIORITIES.find((p) => p.value === String(priorityBucket(experiment.priority)))?.label ?? 'Normal' },
		{ label: 'Max concurrent runs', value: experiment.max_concurrency != null ? String(experiment.max_concurrency) : 'Unlimited' },
		{ label: 'Infra retries', value: String(experiment.infra_retries) },
		{ label: 'Model preload', value: (experiment.execution_options?.warmup ?? true) ? 'On' : 'Off' },
	]);

	// ── automation ─────────────────────────────────────────────────
	let schedules = $state<ScheduleOut[]>(untrack(() => experiment.schedules ?? []));
	let postRun = $state<PostRunConfig | null>(null);
	let automationError = $state('');

	async function loadSchedules() {
		try {
			schedules = await listSchedules(experiment.id);
		} catch (e) {
			automationError = formatApiError(e, 'Could not load the schedules.');
		}
	}
	async function loadPostRun() {
		try {
			postRun = await getPostRun(experiment.id);
		} catch (e) {
			automationError = formatApiError(e, 'Could not load the post-run settings.');
		}
	}
	function reloadAutomation() {
		automationError = '';
		void loadSchedules();
		void loadPostRun();
	}
	$effect(() => {
		void experiment.id;
		untrack(reloadAutomation);
	});
	$effect(() => {
		if (postRunSignal === 0) return;
		untrack(() => void loadPostRun());
	});
</script>

<div class="space-y-10">
	{#if live}
		<StudySection id="ops-capacity" number={num('capacity')} title="Capacity and endpoints" lead="Where this experiment's runs execute right now: shared slot lanes, then each endpoint's slots, loaded models and queue.">
			{#if !status}
				{#if live.loading}
					<LoadingState label="loading live status" />
				{:else}
					<ErrorAlert message={live.error || 'The live status could not be loaded.'} onretry={() => void live?.resync()} />
				{/if}
			{:else}
				<dl class="grid grid-cols-2 overflow-clip rounded-md border border-border bg-card shadow-[var(--shadow-card)] sm:grid-cols-4">
					{#each lanes as lane, i (lane.label)}
						<div class={cn('px-4 py-2.5', i > 0 && 'border-border/60 sm:border-l', i % 2 === 1 && 'max-sm:border-l', i > 1 && 'max-sm:border-t')} title={lane.hint}>
							<dt class="section-label">{lane.label}</dt>
							<dd class="mt-0.5 font-mono text-lg tabular-nums">
								<span class={lane.total > 0 && lane.used >= lane.total ? 'text-warning-fg' : 'text-foreground'}>{lane.used}</span><span class="text-sm text-muted-foreground">/{lane.total}</span>
							</dd>
						</div>
					{/each}
				</dl>
				{#if status.endpoints.length > 0}
					<div class={cn('grid gap-4', status.endpoints.length === 2 && 'md:grid-cols-2', status.endpoints.length > 2 && 'md:grid-cols-2 xl:grid-cols-3')}>
						{#each status.endpoints as endpoint (endpoint.id)}
							<EndpointLane
								{endpoint}
								experimentId={experiment.id}
								activeJobs={laneJobs.get(endpoint.kind === 'openrouter' ? 'openrouter' : endpoint.slug) ?? []}
							/>
						{/each}
					</div>
				{/if}
			{/if}
		</StudySection>

		{#if showActivity}
			<StudySection id="ops-activity" number={num('activity')} title="Activity" lead="The latest run transitions while this page is open.">
				<div class="rounded-md border border-border bg-card p-4 shadow-[var(--shadow-card)]">
					<ActivityFeed
						items={live.activity}
						conditionLabel={(id) => (id != null ? (conditionLabel.get(id) ?? `Condition #${id}`) : '—')}
						{appName}
					/>
				</div>
			</StudySection>
		{/if}
	{/if}

	<StudySection
		id="ops-execution"
		number={num('execution')}
		title="Execution limits"
		lead={editable
			? 'The run order is frozen; priority, limits and preloading can change while it runs, and repeats can only grow.'
			: experiment.status === 'draft'
				? 'Set in the composer until launch.'
				: 'The experiment has finished; limits apply again if a top-up reopens it.'}
	>
		{#if editable}
			<div class="space-y-4 rounded-md border border-border bg-card p-4 shadow-[var(--shadow-card)]">
				<div class="grid gap-3 sm:grid-cols-2 lg:grid-cols-4">
					<div class="space-y-1.5">
						<Label class="text-xs" for="ops-priority">Priority</Label>
						<Select.Simple id="ops-priority" size="sm" bind:value={priority} items={PRIORITIES} />
					</div>
					<NumberField id="ops-conc" label="Max concurrent runs" bind:value={maxConcurrency} min={1} max={64} placeholder="Unlimited" bind:invalid={invalid.conc} />
					<NumberField id="ops-retries" label="Infra retries" bind:value={infraRetries} min={0} max={5} required bind:invalid={invalid.retries} />
					<NumberField
						id="ops-repeats"
						label="Repeats per cell"
						bind:value={repeats}
						min={experiment.repeats}
						max={MAX_REPEATS}
						required
						bind:invalid={invalid.repeats}
						hint="Increase only; new repeats run at the next top-up."
					/>
				</div>
				<div class="flex flex-wrap items-center justify-between gap-3 border-t border-border/60 pt-3">
					<label class="flex cursor-pointer items-center gap-2 text-xs max-sm:min-h-11">
						<Switch size="sm" checked={warmup} label="Preload Ollama models" onCheckedChange={(v) => (warmup = v)} />
						Preload Ollama models before their runs
					</label>
					<Button size="sm" disabled={!dirty || anyInvalid || saving} onclick={save}>
						{#if saving}<LoaderCircle class="animate-spin motion-reduce:animate-none" />{:else}<Save />{/if}Save changes
					</Button>
				</div>
			</div>
		{:else}
			<dl class="grid grid-cols-2 overflow-clip rounded-md border border-border bg-card shadow-[var(--shadow-card)] sm:grid-cols-4">
				{#each readonlyLimits as item, i (item.label)}
					<div class={cn('px-4 py-2.5', i > 0 && 'border-border/60 sm:border-l', i % 2 === 1 && 'max-sm:border-l', i > 1 && 'max-sm:border-t')}>
						<dt class="section-label">{item.label}</dt>
						<dd class="mt-0.5 text-sm">{item.value}</dd>
					</div>
				{/each}
			</dl>
		{/if}
	</StudySection>

	<StudySection id="ops-automation" number={num('automation')} title="Automation" lead="Recurring launches on a schedule, and what runs once the cells are done.">
		{#if automationError}
			<ErrorAlert message={automationError} onretry={reloadAutomation} />
		{/if}
		<SchedulesCard experimentId={experiment.id} {schedules} {readonly} onchange={loadSchedules} {onlaunched} />
		{#if postRun}
			<PostRunCard experimentId={experiment.id} mode={experiment.mode} data={postRun} {readonly} canRun={hasCompleted} onchange={loadPostRun} />
		{:else if !automationError}
			<LoadingState label="loading post-run" class="py-8" />
		{/if}
	</StudySection>
</div>
