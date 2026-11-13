<script lang="ts" module>
	import type { EvalLevel, EvaluationOverrides } from '$lib/api/v3/types';

	/** What each level measures (spec 4.8 table); `analysis_static` is the default SAST set. */
	export const LEVEL_MEASURES: Record<EvalLevel, Record<'lock' | 'build' | 'functional' | 'probes' | 'adherence' | 'baseline' | 'analysis_static' | 'analysis_dynamic', boolean>> = {
		none: { lock: false, build: false, functional: false, probes: false, adherence: false, baseline: false, analysis_static: false, analysis_dynamic: false },
		static: { lock: true, build: false, functional: false, probes: false, adherence: true, baseline: false, analysis_static: true, analysis_dynamic: false },
		build: { lock: true, build: true, functional: false, probes: false, adherence: true, baseline: false, analysis_static: false, analysis_dynamic: false },
		functional: { lock: true, build: true, functional: true, probes: false, adherence: true, baseline: true, analysis_static: false, analysis_dynamic: false },
		security: { lock: true, build: true, functional: true, probes: true, adherence: true, baseline: true, analysis_static: true, analysis_dynamic: false },
		full: { lock: true, build: true, functional: true, probes: true, adherence: true, baseline: true, analysis_static: true, analysis_dynamic: true },
	};

	export const MEASURE_LABELS: Record<string, string> = {
		build: 'Build',
		functional: 'Start + functional checks',
		probes: 'Security probes',
		adherence: 'Adherence (stack, style)',
		baseline: 'Starter baseline',
		analysis_static: 'Static analysis (default tools)',
		analysis_dynamic: 'Dynamic analysis',
	};

	/** The effective on/off of each measurement after the overrides (`analysis_static` as a boolean). */
	export function effectiveMeasures(level: EvalLevel, overrides: EvaluationOverrides): Record<string, boolean> {
		const base = LEVEL_MEASURES[level];
		const out: Record<string, boolean> = {};
		for (const key of Object.keys(MEASURE_LABELS)) {
			const o = (overrides as Record<string, unknown>)[key];
			if (key === 'analysis_static') out[key] = o === null || o === undefined ? base.analysis_static : o !== 'none';
			else out[key] = typeof o === 'boolean' ? o : base[key as keyof typeof base];
		}
		return out;
	}
</script>

<script lang="ts">
	/**
	 * EvaluationSection — `Design.evaluation` (spec 10.2 step 6, 4.8): the only place evaluation is set
	 * (it is part of the design and of `prereg_hash`, never of the budget preset). An eval level with
	 * per-measurement overrides, the eval suite, the test-retest fraction, a machine-time estimate and, in
	 * slot mode, the evaluation mode and regression checks. Whatever is off is previewed as "not measured".
	 *
	 * Props: `value` (`EvaluationSpec`), `mode`, `onchange(spec)`, `machineMinutes` (plan estimate),
	 * `disabled`.
	 */
	import type { EvaluationSpec, StudyMode } from '$lib/api/v3/types';
	import { EVAL_LEVELS } from '$lib/api/v3/types';
	import * as Select from '$lib/components/ui/select';
	import { Checkbox } from '$lib/components/ui/checkbox';
	import { Segmented } from '$lib/components/ui/segmented';
	import { Label } from '$lib/components/ui/label';
	import { LibraryRefPicker } from '$lib/components/library';
	import NumberField from '../shared/NumberField.svelte';

	interface Props {
		value: EvaluationSpec;
		mode: StudyMode;
		onchange: (spec: EvaluationSpec) => void;
		machineMinutes?: number | null;
		disabled?: boolean;
	}

	let { value, mode, onchange, machineMinutes = null, disabled = false }: Props = $props();

	const LEVEL_ITEMS = EVAL_LEVELS.map((level) => ({
		value: level,
		label: level === 'security' ? 'Security (default)' : level.charAt(0).toUpperCase() + level.slice(1),
		description: {
			none: 'Nothing is evaluated: every outcome is not measured',
			static: 'Lock, static adherence and default SAST; no build',
			build: 'Lock, build and adherence',
			functional: 'Build, start, functional checks, baseline',
			security: 'Functional plus probes and default SAST',
			full: 'Security plus dynamic analysis',
		}[level],
	}));

	const measures = $derived(effectiveMeasures(value.eval_level, value.overrides ?? {}));
	const notMeasured = $derived(Object.entries(measures).filter(([, on]) => !on).map(([k]) => MEASURE_LABELS[k]));

	function setOverride(key: string, on: boolean) {
		const base = LEVEL_MEASURES[value.eval_level][key as keyof (typeof LEVEL_MEASURES)['none']];
		const overrides = { ...(value.overrides ?? {}) } as Record<string, unknown>;
		if (on === base) delete overrides[key];
		else overrides[key] = key === 'analysis_static' ? (on ? 'default' : 'none') : on;
		onchange({ ...value, overrides });
	}

	function setLevel(level: string) {
		onchange({ ...value, eval_level: level as EvaluationSpec['eval_level'], overrides: {} });
	}
</script>

<div class="space-y-4">
	<div class="grid gap-3 sm:grid-cols-2">
		<div class="space-y-1.5">
			<Label for="eval-level">Evaluation level</Label>
			<Select.Simple id="eval-level" value={value.eval_level} items={LEVEL_ITEMS} {disabled} onValueChange={setLevel} />
		</div>
		<div class="space-y-1.5">
			<Label for="eval-suite">Eval suite</Label>
			<LibraryRefPicker
				id="eval-suite"
				kind="eval_suite"
				value={value.eval_suite ? [value.eval_suite] : []}
				onchange={(refs) => refs[0] && onchange({ ...value, eval_suite: refs[0] })}
				{disabled}
				aria-label="Eval suite"
			/>
		</div>
	</div>

	<fieldset class="grid gap-1.5 sm:grid-cols-2" aria-label="Measurements">
		{#each Object.entries(MEASURE_LABELS) as [key, label] (key)}
			{@const overridden = (value.overrides as Record<string, unknown>)?.[key] !== undefined && (value.overrides as Record<string, unknown>)?.[key] !== null}
			<label class="flex items-center gap-2 text-xs">
				<Checkbox checked={measures[key]} {disabled} onCheckedChange={(v) => setOverride(key, v === true)} />
				{label}
				{#if overridden}<span class="text-muted-foreground">(override)</span>{/if}
			</label>
		{/each}
		<label class="flex items-center gap-2 text-xs">
			<Checkbox
				checked={value.overrides?.only_passing === true}
				{disabled}
				onCheckedChange={(v) => onchange({ ...value, overrides: { ...value.overrides, only_passing: v === true ? true : null } })}
			/>
			Analyse only passing runs <span class="text-warning-fg">(biases conditional analyses)</span>
		</label>
		<label class="flex items-center gap-2 text-xs">
			<Checkbox
				checked={value.overrides?.human_eval_pool === true}
				{disabled}
				onCheckedChange={(v) => onchange({ ...value, overrides: { ...value.overrides, human_eval_pool: v === true ? true : null } })}
			/>
			Offer the samples to human evaluation
		</label>
	</fieldset>

	{#if mode === 'slot'}
		<div class="grid gap-3 sm:grid-cols-2">
			<div class="space-y-1.5">
				<p class="text-sm font-medium" id="slot-eval-mode">Slot evaluation</p>
				<Segmented
					aria-labelledby="slot-eval-mode"
					size="sm"
					value={value.slot?.mode ?? 'full'}
					items={[
						{ value: 'full', label: 'Full rebuild' },
						{ value: 'overlay', label: 'Overlay', title: 'Parent image + variant file; interpreted backends only, else falls back to full' },
						{ value: 'static_only', label: 'Static only' },
					]}
					{disabled}
					onValueChange={(v) => onchange({ ...value, slot: { mode: v as 'full' | 'overlay' | 'static_only', regression: value.slot?.regression ?? false } })}
				/>
			</div>
			<label class="flex items-center gap-2 self-end text-xs">
				<Checkbox
					checked={value.slot?.regression ?? false}
					{disabled}
					onCheckedChange={(v) => onchange({ ...value, slot: { mode: value.slot?.mode ?? 'full', regression: v === true } })}
				/>
				Also run the parent's other checks (regression)
			</label>
		</div>
	{/if}

	<div class="grid gap-3 sm:grid-cols-2">
		<NumberField
			id="retest-fraction"
			label="Test-retest share"
			value={value.retest_fraction}
			min={0}
			max={1}
			integer={false}
			step={0.01}
			hint="Random re-evaluations for agreement (0.05 = 5 %)"
			{disabled}
			onchange={(v) => onchange({ ...value, retest_fraction: v ?? 0 })}
		/>
		<div class="space-y-1 text-xs">
			<p class="section-label">Machine time</p>
			<p class="font-mono tabular-nums">{machineMinutes === null ? '—' : `≈ ${Math.round(machineMinutes)} machine-minutes`}</p>
			<p class="text-muted-foreground">Build, test and analysis time; costs no USD.</p>
		</div>
	</div>

	{#if notMeasured.length > 0}
		<p class="rounded-md border border-border bg-muted/40 px-3 py-2 text-xs text-muted-foreground">
			Reported as <b>not measured</b> (never clean or 0): {notMeasured.join(', ')}.
		</p>
	{/if}
</div>
