<script lang="ts">
	/**
	 * StudyBuilder — `/studies/new` (spec 10.2): numbered steps on the left, the live PlanRail on the right
	 * (sticky from 56rem; below that the rail follows the form and the StartBar sticks to the bottom).
	 *
	 *   1 Mode & kind · 2 Units · 3 Factors · 4 Replicates & seeds · 5 Budget preset · 6 Evaluation ·
	 *   7 Analysis plan (confirmatory) · 8 After the runs
	 *
	 * The draft is saved server-side (`BuilderState`), so leaving and coming back (`?draft=<id>`) resumes
	 * it. Prompt edits, cell inspection and launch need the saved draft.
	 *
	 * Props: `builder` (`BuilderState`), `onlaunched(studyId)`.
	 */
	import type { Design, FactorSpec, IssueFix, LibraryItemOut, ResourcePolicyContent, SourceSpanOut, StudyPromptOverrideOut } from '$lib/api/v3/types';
	import { getStudy } from '$lib/api/v3/studies';
	import { createPostRun } from '$lib/api/v3/studies';
	import { formatApiError } from '$lib/api/core';
	import { Segmented } from '$lib/components/ui/segmented';
	import { Input } from '$lib/components/ui/input';
	import { Textarea } from '$lib/components/ui/textarea';
	import { Label } from '$lib/components/ui/label';
	import { Button } from '$lib/components/ui/button';
	import { Checkbox } from '$lib/components/ui/checkbox';
	import * as Select from '$lib/components/ui/select';
	import { libraryStore } from '$lib/components/library';
	import NumberField from '../shared/NumberField.svelte';
	import PostRunEditor, { defaultAnalysisConfig, type AnalysisPostRunConfig } from '../automation/PostRunEditor.svelte';
	import { MODE_LABELS, formatUsd } from '../format';
	import type { BuilderState } from './builder.svelte';
	import ComposerSection from './ComposerSection.svelte';
	import ComposerSectionNav, { type ComposerNavSection } from './ComposerSectionNav.svelte';
	import StartBar from './StartBar.svelte';
	import PlanRail from './PlanRail.svelte';
	import FactorTable from './FactorTable.svelte';
	import TaskPicker from './TaskPicker.svelte';
	import SlotTargetPicker from './SlotTargetPicker.svelte';
	import PowerHelper from './PowerHelper.svelte';
	import ResourcePolicyPicker from './ResourcePolicyPicker.svelte';
	import EvaluationSection from './EvaluationSection.svelte';
	import AnalysisPlanSection from './AnalysisPlanSection.svelte';
	import PromptOverrideDialog, { type OverrideTarget } from './PromptOverrideDialog.svelte';
	import ResolvedCellDrawer from './ResolvedCellDrawer.svelte';
	import LaunchDialog from './LaunchDialog.svelte';
	import {
		REPEAT_LIMITS,
		clampRepeats,
		defaultAnalysisPlan,
		getLevels,
		moveProseVariants,
		randomSeed,
		setKind,
		switchMode,
		withFactors,
	} from './design';
	import { parseFix, type SummaryIssue } from './issues';
	import { estimateBasisTotals, patchPolicy, savePolicy, withStudyCap } from './policy';
	import { PinnedPolicy, rememberPolicy } from './pinned.svelte';
	import Dices from '@lucide/svelte/icons/dices';

	interface Props {
		builder: BuilderState;
		onlaunched: (studyId: string) => void;
	}

	let { builder: b, onlaunched }: Props = $props();

	const design = $derived(b.design);
	const isSlot = $derived(design.mode === 'slot');
	const locked = $derived(!!b.study?.locked);
	// The preset version the design PINS (not the slug's latest): the launch threshold, cap and pilot, and
	// the base of the cap/knob quick fixes, must be the knobs the run will use.
	const pinnedPolicy = new PinnedPolicy(() => design.resource_policy);
	const policyItem = $derived(pinnedPolicy.item);
	const policyContent = $derived((policyItem?.content as ResourcePolicyContent | undefined) ?? null);
	const units = $derived(isSlot ? design.units.slot_targets.length : design.units.tasks.length);

	let budgetSummary = $state('');
	let budgetOpen = $state(false);
	let evalOpen = $state(false);
	let fixError = $state('');

	function update(next: Design) {
		b.setDesign(next);
	}

	// ── quick fixes from the plan's issues ──
	async function applyFix(_issue: SummaryIssue, fix: IssueFix) {
		fixError = '';
		const action = parseFix(fix);
		try {
			if (action.kind === 'set_repeats') update({ ...design, repeats: clampRepeats(design.mode, action.repeats) });
			else if (action.kind === 'move_prose_variant') update(moveProseVariants(design));
			else if ((action.kind === 'set_study_cap' || action.kind === 'set_knob') && policyContent) {
				const content = action.kind === 'set_study_cap' ? withStudyCap(policyContent, action.cap) : patchPolicy(policyContent, action.values);
				const saved = await savePolicy(policyItem as LibraryItemOut<'resource_policy'>, content, {
					ownUserId: libraryStore.me?.id ?? null,
					latestVersion: pinnedPolicy.latest?.version ?? null,
				});
				rememberPolicy(saved);
				update({ ...design, resource_policy: saved.pinned_ref });
			} else if (action.kind === 'unknown') fixError = `This fix (${action.action}) has to be applied by hand.`;
		} catch (e) {
			fixError = formatApiError(e, 'Could not apply the fix.');
		}
	}

	// ── cell drawer and prompt overrides ──
	let drawerCell = $state('');
	let overrideTarget = $state<OverrideTarget | null>(null);
	const compareKeys = $derived(b.plan?.cells_sample.map((c) => c.cell_key) ?? []);

	function openCell(key: string) {
		drawerCell = key;
	}

	function editSpan(span: SourceSpanOut, role: string) {
		overrideTarget = { kind: 'span', span, role };
	}

	async function overrideApplied(_override: StudyPromptOverrideOut) {
		// The server swapped the factor level to the fork: reload the saved design.
		if (!b.studyId) return;
		b.attach(await getStudy(b.studyId));
	}

	// ── launch ──
	let launchOpen = $state(false);
	let launchError = $state('');

	async function freeze() {
		await b.freeze();
	}

	async function launch(body: Parameters<BuilderState['launch']>[0]) {
		launchError = '';
		try {
			await b.launch(body);
			launchOpen = false;
			if (b.studyId) onlaunched(b.studyId);
		} catch (e) {
			launchError = formatApiError(e, 'Could not start the study.');
		}
	}

	// ── post-run ──
	let postRun = $state<AnalysisPostRunConfig>(defaultAnalysisConfig());
	let postRunNote = $state('');
	async function attachPostRun() {
		if (!b.studyId) return;
		postRunNote = '';
		try {
			await createPostRun(b.studyId, { kind: 'analysis', trigger: 'completed', config: { ...postRun } });
			postRunNote = 'Attached: it runs when the study completes or stops.';
		} catch (e) {
			postRunNote = formatApiError(e, 'Could not attach the post-run.');
		}
	}

	const sections = $derived<ComposerNavSection[]>([
		{ id: 'step-mode', step: 1, label: 'Mode & kind', done: true },
		{ id: 'step-units', step: 2, label: isSlot ? 'Targets' : 'Tasks', done: units > 0 },
		{ id: 'step-factors', step: 3, label: 'Factors', done: getLevels(design, 'model').length > 0 && (isSlot || getLevels(design, 'stack_profile').length > 0) },
		{ id: 'step-repeats', step: 4, label: 'Replicates & seeds', done: design.repeats > 0 },
		{ id: 'step-budget', step: 5, label: 'Budget preset' },
		{ id: 'step-eval', step: 6, label: 'Evaluation' },
		...(design.kind === 'confirmatory' ? [{ id: 'step-analysis', step: 7, label: 'Analysis plan', done: !!design.analysis_plan }] : []),
		{ id: 'step-after', step: design.kind === 'confirmatory' ? 8 : 7, label: 'After the runs' },
	]);
	const errorCount = $derived(b.issues.filter((i) => i.severity === 'error').length);
</script>

<div class="@container/composer">
	<div class="grid gap-4 @4xl/composer:grid-cols-[minmax(0,1fr)_22rem]">
		<div class="min-w-0 space-y-4">
			<ComposerSection step={1} id="step-mode" title="Mode & kind" description="What is generated, and whether the analysis is pre-registered." done>
				<div class="space-y-3">
					<div class="flex flex-wrap gap-3">
						<Segmented
							aria-label="Mode"
							value={design.mode}
							disabled={locked}
							items={[
								{ value: 'app', label: MODE_LABELS.app, title: 'An agent builds a whole app per sample' },
								{ value: 'slot', label: MODE_LABELS.slot, title: 'Regenerate one part of frozen parent apps N times' },
							]}
							onValueChange={(v) => update(switchMode(design, v as 'app' | 'slot'))}
						/>
						<Segmented
							aria-label="Kind"
							value={design.kind}
							disabled={locked}
							items={[
								{ value: 'exploratory', label: 'Exploratory' },
								{ value: 'confirmatory', label: 'Confirmatory', title: 'Pre-registered: the analysis plan is locked at launch' },
							]}
							onValueChange={(v) => update(setKind(design, v as 'exploratory' | 'confirmatory'))}
						/>
					</div>
					<div class="grid gap-3 sm:grid-cols-2">
						<div class="space-y-1.5">
							<Label for="study-name">Name</Label>
							<Input id="study-name" value={b.meta.name} placeholder="Untitled study" oninput={(e) => b.setMeta({ name: (e.currentTarget as HTMLInputElement).value })} />
						</div>
						<div class="space-y-1.5">
							<Label for="study-hypothesis">Hypothesis</Label>
							<Input
								id="study-hypothesis"
								value={b.meta.hypothesis}
								placeholder="e.g. Security guidance lowers the probe exploit rate"
								oninput={(e) => b.setMeta({ hypothesis: (e.currentTarget as HTMLInputElement).value })}
							/>
						</div>
					</div>
					<div class="space-y-1.5">
						<Label for="study-description">Description</Label>
						<Textarea id="study-description" rows={2} value={b.meta.description} oninput={(e: Event) => b.setMeta({ description: (e.currentTarget as HTMLTextAreaElement).value })} />
					</div>
				</div>
			</ComposerSection>

			<ComposerSection
				step={2}
				id="step-units"
				title={isSlot ? 'Slot targets' : 'Tasks'}
				description={isSlot ? 'The parts to regenerate, grouped by parent app. Stack, starter, auth and prose are fixed by the parents.' : 'Graded tasks; each is one unit, crossed with every condition.'}
				done={units > 0}
				meta={`${units} selected`}
			>
				{#if isSlot}
					<SlotTargetPicker
						slotSets={design.units.slot_sets}
						targets={design.units.slot_targets}
						disabled={locked}
						onchange={(u) => update({ ...design, units: { ...design.units, ...u } })}
					/>
				{:else}
					<TaskPicker value={design.units.tasks} disabled={locked} onchange={(tasks) => update({ ...design, units: { ...design.units, tasks } })} />
				{/if}
			</ComposerSection>

			<ComposerSection step={3} id="step-factors" title="Factors" description="What varies between conditions. A factor left empty uses the default shown." done={sections[2].done}>
				<div class="space-y-3">
					<FactorTable
						mode={design.mode}
						factors={design.factors}
						disabled={locked}
						costByLevel={b.planFresh ? (b.plan?.estimate?.by_level ?? null) : null}
						oneditprompt={(level) => (overrideTarget = { kind: 'prompt_set', ref: level })}
						onchange={(factors: FactorSpec[]) => update(withFactors(design, factors))}
					/>
					{#if design.grid.type === 'fractional'}
						<div class="max-w-xs space-y-1.5">
							<Label for="grid-generator">Fraction generator</Label>
							<Input
								id="grid-generator"
								class="font-mono"
								placeholder="e.g. D=ABC"
								value={design.grid.fraction?.generator ?? ''}
								disabled={locked}
								onchange={(e) => update({ ...design, grid: { ...design.grid, fraction: { generator: (e.currentTarget as HTMLInputElement).value.trim().toUpperCase() } } })}
							/>
							<p class="text-xs text-muted-foreground">Letters name the crossed two-level factors in table order (A = first).</p>
						</div>
					{/if}
					<div class="flex flex-wrap items-end gap-4">
						<label class="flex items-center gap-2 text-xs">
							<Checkbox checked={b.dropIncompatible} disabled={locked} onCheckedChange={(v) => b.setDropIncompatible(v === true)} />
							Drop incompatible combinations (listed in the plan; by-factor slices then use marginal means)
						</label>
						<div class="space-y-1">
							<Label for="scheduling" class="text-xs">Scheduling</Label>
							<Select.Simple
								id="scheduling"
								size="sm"
								value={design.scheduling}
								disabled={locked}
								items={[
									{ value: 'auto', label: 'Auto', description: 'Blocked when a condition uses a local endpoint' },
									{ value: 'interleaved', label: 'Interleaved' },
									{ value: 'blocked', label: 'Blocked by model' },
								]}
								onValueChange={(v) => update({ ...design, scheduling: v as Design['scheduling'] })}
							/>
						</div>
					</div>
				</div>
			</ComposerSection>

			<ComposerSection step={4} id="step-repeats" title="Replicates & seeds" description="Repeats per (condition, unit); every condition gets the same seed for the same (unit, repeat)." done>
				<div class="grid gap-4 lg:grid-cols-2">
					<div class="space-y-3">
						<NumberField
							id="repeats"
							label="Repeats"
							value={design.repeats}
							min={REPEAT_LIMITS[design.mode].min}
							max={REPEAT_LIMITS[design.mode].max}
							required
							disabled={locked}
							hint="{REPEAT_LIMITS[design.mode].min}–{REPEAT_LIMITS[design.mode].max} in {MODE_LABELS[design.mode].toLowerCase()} mode"
							onchange={(v) => v !== null && update({ ...design, repeats: clampRepeats(design.mode, v) })}
						/>
						<div class="flex items-end gap-2">
							<NumberField id="base-seed" label="Base seed" value={design.base_seed} min={0} max={2 ** 31 - 1} required disabled={locked} onchange={(v) => v !== null && update({ ...design, base_seed: v })} />
							<Button variant="outline" size="icon" aria-label="Randomise the seeds" disabled={locked} onclick={() => update({ ...design, base_seed: randomSeed(), order_seed: randomSeed() })}>
								<Dices />
							</Button>
						</div>
						<NumberField id="order-seed" label="Order seed" value={design.order_seed} min={0} max={2 ** 31 - 1} required disabled={locked} hint="Shuffles the unit order and the conditions inside each block" onchange={(v) => v !== null && update({ ...design, order_seed: v })} />
					</div>
					<PowerHelper mode={design.mode} {units} repeats={design.repeats} />
				</div>
			</ComposerSection>

			<ComposerSection
				step={5}
				id="step-budget"
				title="Budget preset"
				description="Limits enforced outside the model: the gateway per run, the scheduler per cell and study. Never part of the prompt."
				collapsible
				bind:open={budgetOpen}
				summary={budgetSummary}
			>
				<ResourcePolicyPicker
					value={design.resource_policy}
					disabled={locked}
					warnOnFingerprintChange={!!b.study?.cloned_from || (b.study?.status !== undefined && b.study.status !== 'draft')}
					onchange={(ref) => update({ ...design, resource_policy: ref })}
					onsummary={(line) => (budgetSummary = line)}
				/>
			</ComposerSection>

			<ComposerSection
				step={6}
				id="step-eval"
				title="Evaluation"
				description="What is measured for every sample; part of the design (and of the pre-registration), not of the budget."
				collapsible
				bind:open={evalOpen}
				summary={`${design.evaluation.eval_level} level · ${design.evaluation.eval_suite.split('#')[0]}${Object.keys(design.evaluation.overrides ?? {}).length ? ' · overrides' : ''}`}
			>
				<EvaluationSection
					value={design.evaluation}
					mode={design.mode}
					disabled={locked}
					machineMinutes={b.plan?.estimate?.time?.eval_machine_minutes ?? null}
					onchange={(evaluation) => update({ ...design, evaluation })}
				/>
			</ComposerSection>

			{#if design.kind === 'confirmatory'}
				<ComposerSection step={7} id="step-analysis" title="Analysis plan" description="Pre-registered at launch (prereg_hash); deviations are reported." done={!!design.analysis_plan}>
					<AnalysisPlanSection
						value={design.analysis_plan ?? defaultAnalysisPlan(design.mode)}
						mode={design.mode}
						repeats={design.repeats}
						primaryFeatures={design.slot_primary_features}
						disabled={locked}
						onchange={(plan) => update({ ...design, analysis_plan: plan })}
						onfeatures={(refs) => update({ ...design, slot_primary_features: refs })}
					/>
				</ComposerSection>
			{/if}

			<ComposerSection step={design.kind === 'confirmatory' ? 8 : 7} id="step-after" title="After the runs" description="Static analysis and the report when the study completes. Schedules are set on the study's Operations page.">
				<div class="space-y-3">
					<PostRunEditor bind:value={postRun} mode={design.mode} />
					<div class="flex flex-wrap items-center gap-2">
						<Button variant="outline" size="sm" disabled={!b.studyId || postRun.tool_slugs.length === 0} onclick={attachPostRun}>Attach to this study</Button>
						{#if postRunNote}<span class="text-xs text-muted-foreground" role="status">{postRunNote}</span>{/if}
					</div>
				</div>
			</ComposerSection>
		</div>

		<aside class="min-w-0" aria-label="Plan">
			<div class="space-y-4 @4xl/composer:sticky @4xl/composer:top-16">
				<div class="hidden @4xl/composer:block"><ComposerSectionNav {sections} /></div>
				<div class="rounded-md border border-border bg-card p-3" id="plan-rail">
					<PlanRail state={b} onfix={applyFix} oncell={openCell} onfreeze={freeze} onstart={() => (launchOpen = true)} />
					{#if fixError}<p class="mt-2 text-xs text-danger-fg">{fixError}</p>{/if}
				</div>
			</div>
		</aside>
	</div>

	<StartBar
		runs={b.plan?.n_cells ?? null}
		maxCost={b.plan?.estimate ? estimateBasisTotals(b.plan.estimate).p90 : null}
		errors={errorCount}
		label="Start…"
		canStart={b.canStart}
		busy={b.launching}
		reason={b.canStart ? '' : b.frozen ? 'Freeze the current plan again.' : 'Freeze the plan in the rail first.'}
		summaryHref="#plan-rail"
		onstart={() => (launchOpen = true)}
	/>
</div>

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

<p class="sr-only" aria-live="polite">{b.plan?.estimate ? `Expected ${formatUsd(estimateBasisTotals(b.plan.estimate).expected)}` : ''}</p>
