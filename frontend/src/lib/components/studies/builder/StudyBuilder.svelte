<script lang="ts">
	/**
	 * StudyBuilder — `/experiments/new` (spec 10.2) as a step-by-step workspace instead of one long form:
	 *
	 *   [ stepper: Setup · Models · Tasks · Treatments · Runs & budget · Measurement · Review ]
	 *   [ the open step (StepPanel)                        ] [ Plan rail (sticky, ≥ 56rem) ]
	 *
	 * - One step is open at a time; the stepper is non-linear (any step opens), shows what each step holds
	 *   and marks complete / required / problem steps. The open step is in the URL hash (`#models`), so a
	 *   reload or a shared link returns to it. Opening a step moves focus to its heading.
	 * - Required choices are never shown as red errors before the user acts: the rail lists them as a
	 *   neutral to-do, and a step only turns amber after the user continued past it unfinished.
	 * - Launch is one action on Review: it saves, freezes the current plan (if needed) and opens the cost
	 *   confirmation. The post-run analysis chosen in Measurement is attached at launch.
	 * - Below 56rem the rail gives way to a sticky bottom bar (step, cells, cost, next).
	 *
	 * The draft lifecycle (server-side autosave, debounced plan resolve, freeze, launch) is `BuilderState`.
	 *
	 * Props: `builder` (`BuilderState`), `onlaunched(studyId)`.
	 */
	import { onMount, tick, untrack } from 'svelte';
	import { page } from '$app/state';
	import { replaceState } from '$app/navigation';
	import type { IssueFix, LibraryItemOut, PostRunOut, ResourcePolicyContent, SourceSpanOut, StudyPromptOverrideOut } from '$lib/api/v3/types';
	import { createPostRun, getStudy, listPostRuns } from '$lib/api/v3/studies';
	import { formatApiError } from '$lib/api/core';
	import { toast } from 'svelte-sonner';
	import { Button } from '$lib/components/ui/button';
	import { libraryStore } from '$lib/components/library';
	import { defaultAnalysisConfig, type AnalysisPostRunConfig } from '../automation/PostRunEditor.svelte';
	import { MODE_LABELS, formatUsd, plural, refLabel } from '../format';
	import type { BuilderState } from './builder.svelte';
	import BuilderStepper, { type StepperItem, type StepState } from './BuilderStepper.svelte';
	import BlueprintRail from './BlueprintRail.svelte';
	import StepPanel from './StepPanel.svelte';
	import PromptOverrideDialog, { type OverrideTarget } from './PromptOverrideDialog.svelte';
	import ResolvedCellDrawer from './ResolvedCellDrawer.svelte';
	import LaunchDialog from './LaunchDialog.svelte';
	import SetupStep from './steps/SetupStep.svelte';
	import ModelsStep from './steps/ModelsStep.svelte';
	import TreatmentsStep from './steps/TreatmentsStep.svelte';
	import ReplicationStep from './steps/ReplicationStep.svelte';
	import MeasurementStep from './steps/MeasurementStep.svelte';
	import ReviewStep from './steps/ReviewStep.svelte';
	import TaskPicker from './TaskPicker.svelte';
	import SlotTargetPicker from './SlotTargetPicker.svelte';
	import { clampRepeats, getLevels, moveProseVariants, setLevels } from './design';
	import { parseFix, type SummaryIssue } from './issues';
	import { shouldAttachPostRun } from './launch';
	import { estimateBasisTotals, patchPolicy, savePolicy, withStudyCap } from './policy';
	import { PinnedPolicy, rememberPolicy } from './pinned.svelte';
	import {
		STEP_IDS,
		TODO_CODES,
		isStepId,
		issuesByStep,
		missingChoices,
		nextStep,
		prevStep,
		requiredSteps,
		stepsFor,
		type StepId,
	} from './steps';
	import ArrowRight from '@lucide/svelte/icons/arrow-right';
	import LoaderCircle from '@lucide/svelte/icons/loader-circle';
	import Rocket from '@lucide/svelte/icons/rocket';

	interface Props {
		builder: BuilderState;
		onlaunched: (studyId: string) => void;
	}

	let { builder: b, onlaunched }: Props = $props();

	const design = $derived(b.design);
	const isSlot = $derived(design.mode === 'slot');
	const locked = $derived(!!b.study?.locked);
	const steps = $derived(stepsFor(design.mode));
	const models = $derived(getLevels(design, 'model'));
	const units = $derived(isSlot ? design.units.slot_targets.length : design.units.tasks.length);
	const missing = $derived(missingChoices(design));
	const required = $derived(requiredSteps(design));
	const serverIssues = $derived(b.issues.filter((i) => !TODO_CODES.has(i.code)));
	const stepIssues = $derived(issuesByStep(serverIssues));
	const errorCount = $derived(serverIssues.filter((i) => i.severity === 'error').length);

	// The version of the Budget preset the design PINS: the launch dialog and the cap/knob quick fixes use it.
	const pinnedPolicy = new PinnedPolicy(() => design.resource_policy);
	const policyItem = $derived(pinnedPolicy.item);
	const policyContent = $derived((policyItem?.content as ResourcePolicyContent | undefined) ?? null);

	// ── which step is open ──
	function fromHash(hash: string): StepId | null {
		const id = hash.replace(/^#/, '');
		return isStepId(id) ? id : null;
	}
	let current = $state<StepId>(untrack(() => fromHash(page.url.hash) ?? 'setup'));
	// Steps the user has left: an open required choice only turns amber after that. A resumed draft has
	// been walked through already.
	let left = $state(new Set<StepId>(untrack(() => (b.studyId ? STEP_IDS.filter((s) => s !== 'review') : []))));
	let panelEl = $state<HTMLElement | null>(null);

	async function go(step: StepId, opts: { focus?: boolean } = {}) {
		if (step === current) return;
		if (current !== 'review') left = new Set([...left, current]);
		current = step;
		const url = new URL(page.url);
		url.hash = step;
		try {
			replaceState(url, page.state);
		} catch {
			/* the router is not ready during the first render */
		}
		await tick();
		const top = panelEl?.getBoundingClientRect().top ?? 0;
		if (top < 56 || top > window.innerHeight * 0.6) panelEl?.scrollIntoView({ block: 'start' });
		if (opts.focus !== false) document.getElementById(`step-${step}-title`)?.focus({ preventScroll: true });
	}

	function stepState(id: StepId): StepState {
		if (id === 'review') return 'idle';
		if (stepIssues[id].errors > 0) return 'error';
		const open = missing.some((m) => m.step === id);
		if (open) return left.has(id) ? 'attention' : required.has(id) ? 'todo' : 'idle';
		if (required.has(id) || left.has(id)) return 'done';
		return 'idle';
	}

	const summaries = $derived.by((): Record<StepId, string> => {
		const varied = design.factors.filter((f) => f.key !== 'model' && f.levels.length > 1).length;
		const stack = getLevels(design, 'stack_profile');
		const plan = b.planFresh ? b.plan : null;
		const totals = plan?.estimate ? estimateBasisTotals(plan.estimate) : null;
		const stackText = stack.length ? (stack.length === 1 ? refLabel(stack[0]) : plural(stack.length, 'stack')) : 'no stack yet';
		return {
			setup: `${MODE_LABELS[design.mode]} · ${design.kind}`,
			models: models.length ? plural(models.length, 'model') : 'none yet',
			units: units ? plural(units, isSlot ? 'target' : 'task') : 'none yet',
			treatments: isSlot ? (varied ? `${plural(varied, 'factor')} varied` : 'defaults') : [stackText, varied ? `${varied} varied` : ''].filter(Boolean).join(' · '),
			replication: `${plural(design.repeats, 'repeat')} · ${refLabel(design.resource_policy)}`,
			measurement: `${design.evaluation.eval_level}${design.kind === 'confirmatory' ? ' · pre-registered' : ''}`,
			review: plan ? `${plural(plan.n_cells_by_stage.main ?? plan.n_cells, 'cell')}${totals ? ` · ≈ ${formatUsd(totals.expected)}` : ''}` : '',
		};
	});
	const stepperItems = $derived<StepperItem[]>(
		steps.map((s) => ({ id: s.id, label: s.label, summary: summaries[s.id], state: stepState(s.id), required: required.has(s.id) })),
	);
	const index = $derived(STEP_IDS.indexOf(current));
	const def = $derived(steps[index]);
	const next = $derived(nextStep(current));
	const prev = $derived(prevStep(current));
	const labelOf = (id: StepId | null) => (id ? (steps.find((s) => s.id === id)?.label ?? '') : '');
	const blockersHere = $derived(missing.filter((m) => m.step === current).map((m) => m.label.charAt(0).toLowerCase() + m.label.slice(1)));
	let warned = $state(new Set<StepId>());

	function continueOn() {
		if (blockersHere.length > 0 && !warned.has(current)) {
			// First press says what is still missing here; a second press goes on anyway.
			warned = new Set([...warned, current]);
			return;
		}
		if (next) void go(next);
	}

	// ── quick fixes from the plan's issues ──
	let fixError = $state('');
	async function applyFix(_issue: SummaryIssue, fix: IssueFix) {
		fixError = '';
		const action = parseFix(fix);
		try {
			if (action.kind === 'set_repeats') b.setDesign({ ...design, repeats: clampRepeats(design.mode, action.repeats) });
			else if (action.kind === 'move_prose_variant') b.setDesign(moveProseVariants(design));
			else if ((action.kind === 'set_study_cap' || action.kind === 'set_knob') && policyContent) {
				const content = action.kind === 'set_study_cap' ? withStudyCap(policyContent, action.cap) : patchPolicy(policyContent, action.values);
				const saved = await savePolicy(policyItem as LibraryItemOut<'resource_policy'>, content, {
					ownUserId: libraryStore.me?.id ?? null,
					latestVersion: pinnedPolicy.latest?.version ?? null,
				});
				rememberPolicy(saved);
				b.setDesign({ ...design, resource_policy: saved.pinned_ref });
			} else if (action.kind === 'unknown') fixError = `This fix (${action.action}) has to be applied by hand.`;
		} catch (e) {
			fixError = formatApiError(e, 'Could not apply the fix.');
		}
	}

	// ── cell drawer and prompt overrides ──
	let drawerCell = $state('');
	let overrideTarget = $state<OverrideTarget | null>(null);
	const compareKeys = $derived(b.plan?.cells_sample.map((c) => c.cell_key) ?? []);

	function editSpan(span: SourceSpanOut, role: string) {
		overrideTarget = { kind: 'span', span, role };
	}

	async function overrideApplied(_override: StudyPromptOverrideOut) {
		// The server swapped the factor level to the fork: reload the saved design.
		if (!b.studyId) return;
		b.attach(await getStudy(b.studyId));
	}

	// ── post-run (attached at launch) ──
	let postRun = $state<AnalysisPostRunConfig>(defaultAnalysisConfig());
	let attachedPostRuns = $state<PostRunOut[]>([]);
	let postRunsFor = '';
	$effect(() => {
		const id = b.studyId;
		if (!id || id === postRunsFor) return;
		postRunsFor = id;
		listPostRuns(id)
			.then((list) => (attachedPostRuns = list))
			.catch(() => {});
	});

	// ── launch: save → freeze → confirm the cost → attach the post-run → launch ──
	let launchOpen = $state(false);
	let launchError = $state('');
	let preparing = $state(false);
	let prepareError = $state('');
	let blockersEl = $state<HTMLElement | null>(null);

	async function requestLaunch() {
		prepareError = '';
		if (current !== 'review') {
			await go('review');
			return;
		}
		if (missing.length > 0 || errorCount > 0) {
			blockersEl?.focus();
			return;
		}
		preparing = true;
		try {
			if (!b.canStart) await b.freeze();
			if (b.canStart) launchOpen = true;
			else prepareError = b.planError || b.saveError || 'The plan could not be frozen: resolve the issues above first.';
		} finally {
			preparing = false;
		}
	}

	async function launch(body: Parameters<BuilderState['launch']>[0]) {
		launchError = '';
		try {
			if (b.studyId && !locked && shouldAttachPostRun(postRun, attachedPostRuns)) {
				const created = await createPostRun(b.studyId, { kind: 'analysis', trigger: 'completed', config: { ...postRun } });
				attachedPostRuns = [...attachedPostRuns, created];
			}
			await b.launch(body);
			launchOpen = false;
			toast.success(`Launched “${b.meta.name.trim() || 'Untitled experiment'}”`, { description: 'The pilot runs first; follow it on Operations.' });
			if (b.studyId) onlaunched(b.studyId);
		} catch (e) {
			launchError = formatApiError(e, 'Could not start the experiment.');
		}
	}

	const barStatus = $derived.by(() => {
		if (missing.length > 0) return `${plural(missing.length, 'required choice')} left`;
		const plan = b.plan;
		if (!plan) return 'Resolving the plan…';
		const cells = plural(plan.n_cells_by_stage.main ?? plan.n_cells, 'cell');
		return plan.estimate ? `${cells} · ≈ ${formatUsd(estimateBasisTotals(plan.estimate).expected)}` : cells;
	});
	const primaryLabel = $derived(current === 'review' ? (preparing ? 'Freezing the plan…' : 'Launch experiment…') : 'Review & launch');

	// Keyboard focus and in-page jumps must never land under the sticky app bar or the sticky bottom bar
	// (WCAG 2.2 focus not obscured): the browser honours scroll-padding when it scrolls a focused control in.
	onMount(() => {
		const root = document.documentElement;
		const before = { top: root.style.scrollPaddingTop, bottom: root.style.scrollPaddingBottom };
		root.style.scrollPaddingTop = '4.5rem';
		root.style.scrollPaddingBottom = '5rem';
		return () => {
			root.style.scrollPaddingTop = before.top;
			root.style.scrollPaddingBottom = before.bottom;
		};
	});

	// Where to land without a `#step`: a resumed complete draft on Review, a `?models=` link on Tasks.
	onMount(() => {
		if (fromHash(page.url.hash)) return;
		if (b.studyId && missing.length === 0) void go('review', { focus: false });
		else if (models.length > 0 && !b.studyId) void go('units', { focus: false });
	});
</script>

<div class="@container/builder space-y-4">
	<div class="sb-stepper">
		<BuilderStepper items={stepperItems} {current} onselect={(id) => go(id)} />
	</div>

	{#if locked}
		<p class="rounded-md border border-border bg-muted/40 px-3 py-2 text-xs text-muted-foreground" role="note">
			This experiment is locked: the design is read-only.
		</p>
	{/if}

	<div class="grid items-start gap-4 {current === 'review' ? '' : '@4xl/builder:grid-cols-[minmax(0,1fr)_20.5rem]'}">
		<div class="min-w-0 scroll-mt-16" bind:this={panelEl}>
			{#key current}
				<StepPanel
					{index}
					total={steps.length}
					title={def.title}
					lead={def.lead}
					headingId="step-{current}-title"
					backLabel={prev ? labelOf(prev) : undefined}
					onback={prev ? () => go(prev) : undefined}
					nextLabel={next ? (next === 'review' ? 'Review' : `Continue to ${labelOf(next)}`) : undefined}
					onnext={continueOn}
					blockers={blockersHere}
					showBlockers={warned.has(current)}
					footer={current === 'review' ? launchFooter : undefined}
				>
					{#snippet aside()}
						{#if current === 'units' && units > 0}
							<span class="sb-count">{plural(units, isSlot ? 'target' : 'task')} chosen</span>
						{:else if current === 'models' && models.length > 0}
							<span class="sb-count">{plural(models.length, 'model')} chosen</span>
						{/if}
					{/snippet}

					{#if current === 'setup'}
						<SetupStep builder={b} {locked} />
					{:else if current === 'models'}
						<ModelsStep
							value={models}
							mode={design.mode}
							disabled={locked}
							costByLevel={b.planFresh ? (b.plan?.estimate?.by_level?.model ?? null) : null}
							onchange={(levels) => b.setDesign(setLevels(design, 'model', levels))}
						/>
					{:else if current === 'units'}
						{#if isSlot}
							<SlotTargetPicker
								slotSets={design.units.slot_sets}
								targets={design.units.slot_targets}
								disabled={locked}
								onchange={(u) => b.setDesign({ ...design, units: { ...design.units, ...u } })}
							/>
						{:else}
							<TaskPicker value={design.units.tasks} disabled={locked} onchange={(tasks) => b.setDesign({ ...design, units: { ...design.units, tasks } })} />
						{/if}
					{:else if current === 'treatments'}
						<TreatmentsStep builder={b} {locked} onprompt={(level) => (overrideTarget = { kind: 'prompt_set', ref: level })} />
					{:else if current === 'replication'}
						<ReplicationStep
							builder={b}
							{locked}
							{units}
							warnOnFingerprintChange={!!b.study?.cloned_from || (b.study?.status !== undefined && b.study.status !== 'draft')}
						/>
					{:else if current === 'measurement'}
						<MeasurementStep builder={b} {locked} bind:postRun {attachedPostRuns} />
					{:else}
						<ReviewStep
							builder={b}
							{steps}
							{missing}
							issues={serverIssues}
							{postRun}
							onselect={(id) => go(id)}
							oncell={(key) => (drawerCell = key)}
							onfix={applyFix}
							{fixError}
							bind:blockersEl
						/>
					{/if}
				</StepPanel>
			{/key}
		</div>

		{#if current !== 'review'}
			<aside class="hidden min-w-0 @4xl/builder:block" aria-label="Plan">
				<div class="sticky top-16">
					<BlueprintRail
						builder={b}
						{current}
						{steps}
						{missing}
						{stepIssues}
						onselect={(id) => go(id)}
						onprimary={requestLaunch}
						{primaryLabel}
						primaryBusy={preparing}
					/>
				</div>
			</aside>
		{/if}
	</div>

	<!-- Narrow screens: the rail's essentials as a sticky bar. -->
	<div class="sb-bar" data-slot="start-bar">
		<div class="min-w-0 flex-1 text-xs">
			<p class="font-medium">Step {index + 1} of {steps.length} · {def.label}</p>
			<p class="text-muted-foreground" role="status" aria-atomic="true">{barStatus}</p>
		</div>
		{#if current === 'review'}
			<Button size="sm" disabled={preparing || b.launching || locked} onclick={requestLaunch}>
				{#if preparing}<LoaderCircle class="animate-spin motion-reduce:animate-none" />{:else}<Rocket />{/if}Launch…
			</Button>
		{:else if next}
			<Button size="sm" onclick={continueOn}>{next === 'review' ? 'Review' : 'Next'}<ArrowRight /></Button>
		{/if}
	</div>
</div>

{#snippet launchFooter()}
	<div class="flex flex-wrap items-center justify-end gap-3">
		{#if prepareError}
			<p class="max-w-md text-xs text-danger-fg" role="alert">{prepareError}</p>
		{:else if missing.length > 0 || errorCount > 0}
			<p class="text-xs text-muted-foreground">Resolve the items above to launch.</p>
		{:else if b.planLoading || b.pending}
			<p class="text-xs text-muted-foreground" role="status">Resolving the plan…</p>
		{/if}
		<Button disabled={preparing || b.launching || locked} onclick={requestLaunch}>
			{#if preparing}<LoaderCircle class="animate-spin motion-reduce:animate-none" />{:else}<Rocket />{/if}
			{preparing ? 'Freezing the plan…' : 'Launch experiment…'}
		</Button>
	</div>
{/snippet}

{#if b.studyId && b.plan}
	<ResolvedCellDrawer
		studyId={b.studyId}
		planHash={b.plan.plan_hash}
		cellKey={drawerCell}
		open={!!drawerCell}
		onclose={() => (drawerCell = '')}
		{compareKeys}
		onedit={locked ? undefined : editSpan}
	/>
{/if}

<PromptOverrideDialog
	open={!!overrideTarget}
	studyId={b.studyId}
	target={overrideTarget}
	onclose={() => (overrideTarget = null)}
	onapplied={overrideApplied}
/>

<LaunchDialog
	open={launchOpen}
	plan={b.frozen}
	policy={policyContent}
	policyRef={design.resource_policy}
	policyError={pinnedPolicy.error}
	busy={b.launching}
	error={launchError}
	onlaunch={launch}
	onclose={() => (launchOpen = false)}
/>

<style>
	.sb-stepper {
		overflow: hidden;
		border: 1px solid var(--border);
		border-radius: calc(var(--radius) + 2px);
		background: var(--card);
		box-shadow: var(--shadow-card);
	}
	.sb-count {
		border-radius: 9999px;
		background: color-mix(in oklab, var(--primary) 12%, transparent);
		padding: 0.125rem 0.625rem;
		font-family: var(--font-mono);
		font-size: 0.75rem;
		font-variant-numeric: tabular-nums;
		color: var(--primary);
	}
	.sb-bar {
		position: sticky;
		bottom: 0;
		z-index: 20;
		display: flex;
		align-items: center;
		gap: 0.75rem;
		margin-inline: -0.75rem;
		padding: 0.5rem 0.75rem;
		border-top: 1px solid var(--border);
		background: var(--card);
	}
	@container builder (min-width: 56rem) {
		.sb-bar {
			display: none;
		}
	}
	/* Short (landscape phone) viewports: a sticky bar would cover a third of the screen; it ends the step instead. */
	@media (max-height: 30rem) {
		.sb-bar {
			position: static;
			margin-top: 0;
		}
	}
	@media (min-width: 640px) {
		.sb-bar {
			margin-inline: -1rem;
			padding-inline: 1rem;
		}
	}
	@media (min-width: 768px) {
		.sb-bar {
			margin-inline: -1.25rem;
			padding-inline: 1.25rem;
		}
	}
</style>
