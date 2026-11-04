<script lang="ts">
	/**
	 * AnalysisPlanSection — the pre-registered analysis plan of a confirmatory study (spec 10.2 step 7;
	 * port of the experiment composer's section): primary and secondary metrics, α, correction and
	 * multiplicity family, estimand, random effects, sensitivity analyses (the default analysis is
	 * intention-to-treat; excluding truncated runs is a sensitivity row), attrition thresholds, an optional
	 * group-sequential design and, for slot studies, the locked pattern plan. The server normalizes and
	 * hashes it at launch (`prereg_hash`); adaptive precision stops are refused for confirmatory studies.
	 *
	 * Props: `value` (plan dict), `mode`, `repeats` (planned N per cell), `primaryFeatures` (slot),
	 * `onchange(plan)`, `onfeatures(refs)`, `disabled`.
	 */
	import type { StudyMode } from '$lib/api/v3/types';
	import * as Select from '$lib/components/ui/select';
	import { Checkbox } from '$lib/components/ui/checkbox';
	import { Label } from '$lib/components/ui/label';
	import { Input } from '$lib/components/ui/input';
	import { Textarea } from '$lib/components/ui/textarea';
	import { Button } from '$lib/components/ui/button';
	import { LibraryRefPicker, libraryStore } from '$lib/components/library';
	import { parseRef, type DetectorContent } from '$lib/api/v3/types';
	import NumberField from '../shared/NumberField.svelte';
	import { PLAN_CORRECTIONS, PLAN_ESTIMANDS, PLAN_METRICS, PLAN_MULTIPLICITY, PLAN_RANDOM_EFFECTS, PLAN_SENSITIVITY } from './design';
	import Plus from '@lucide/svelte/icons/plus';
	import X from '@lucide/svelte/icons/x';

	type Plan = Record<string, unknown>;
	interface Question {
		id?: string;
		question: string;
		hypothesis?: string;
		metric?: string;
		direction?: string;
	}

	interface Props {
		value: Plan;
		mode: StudyMode;
		repeats: number;
		primaryFeatures?: string[];
		onchange: (plan: Plan) => void;
		onfeatures?: (refs: string[]) => void;
		disabled?: boolean;
	}

	let { value, mode, repeats, primaryFeatures = [], onchange, onfeatures, disabled = false }: Props = $props();

	const metrics = $derived(PLAN_METRICS.filter((m) => m.modes.includes(mode)).map(({ value: v, label }) => ({ value: v, label })));
	const secondary = $derived((value.secondary_metrics as string[] | undefined) ?? []);
	const randomEffects = $derived((value.random_effects as string[] | undefined) ?? []);
	const sensitivity = $derived((value.sensitivity as string[] | undefined) ?? []);
	const exclusions = $derived((value.exclusions as Record<string, unknown> | undefined) ?? {});
	const sequential = $derived((value.sequential as { looks: number[]; alpha_spending: string; futility: { type: string; threshold: number } | null } | null | undefined) ?? null);
	const questions = $derived((value.research_questions as Question[] | undefined) ?? []);
	const patterns = $derived((value.patterns as Record<string, unknown> | null | undefined) ?? null);

	const detectorList = $derived(mode === 'slot' ? libraryStore.list('detector') : null);

	/** Detector refs → their feature keys (what `patterns.primary_features` names). */
	function featureKeys(refs: string[]): string[] {
		return refs.map((ref) => {
			const slug = parseRef(ref)?.slug ?? ref;
			const item = detectorList?.bySlug(slug);
			return (item?.content as DetectorContent | undefined)?.feature_key ?? slug;
		});
	}

	function set(patch: Plan) {
		onchange({ ...value, ...patch });
	}

	function toggleIn(list: string[], item: string, on: boolean): string[] {
		const next = list.filter((x) => x !== item);
		return on ? [...next, item] : next;
	}

	const LABELS: Record<string, string> = {
		holm: 'Holm',
		bh: 'Benjamini–Hochberg',
		none: 'None',
		all: 'All declared metrics',
		per_metric: 'Per metric',
		marginal: 'Marginal (population of tasks)',
		conditional_on_parent: 'Conditional on the parents',
		exclude_truncated: 'Exclude truncated runs',
		exclude_infra_retried: 'Exclude infra-retried runs',
		majority_fingerprint: 'Majority fingerprint only',
		complete_blocks_only: 'Complete blocks only',
	};
	const items = (values: readonly string[]) => values.map((v) => ({ value: v, label: LABELS[v] ?? v }));

	let looksText = $state('');
	$effect(() => {
		looksText = (sequential?.looks ?? []).join(', ');
	});

	function setLooks(text: string) {
		const looks = [...new Set(text.split(/[,\s]+/).map(Number).filter((n) => Number.isInteger(n) && n > 0))].sort((a, b) => a - b);
		set({ sequential: looks.length ? { looks, alpha_spending: sequential?.alpha_spending ?? 'obrien_fleming', futility: sequential?.futility ?? null, binding: false } : null });
	}
</script>

<div class="space-y-4">
	<div class="grid gap-3 sm:grid-cols-2">
		<div class="space-y-1.5">
			<Label for="plan-primary">Primary metric</Label>
			<Select.Simple id="plan-primary" value={String(value.primary_metric ?? '')} items={metrics} {disabled} onValueChange={(v) => set({ primary_metric: v })} />
		</div>
		<div class="space-y-1 text-xs">
			<p class="section-label">Planned N</p>
			<p class="font-mono tabular-nums">{repeats} repeats per (condition, unit)</p>
			<p class="text-muted-foreground">From step 4; pilot cells are never pooled with the main plan.</p>
		</div>
	</div>

	<fieldset class="space-y-1.5">
		<legend class="text-sm font-medium">Secondary metrics</legend>
		<div class="grid gap-1 sm:grid-cols-2">
			{#each metrics.filter((m) => m.value !== value.primary_metric) as m (m.value)}
				<label class="flex items-center gap-2 text-xs">
					<Checkbox checked={secondary.includes(m.value)} {disabled} onCheckedChange={(v) => set({ secondary_metrics: toggleIn(secondary, m.value, v === true) })} />
					{m.label}
				</label>
			{/each}
		</div>
	</fieldset>

	<div class="grid gap-3 sm:grid-cols-3">
		<NumberField id="plan-alpha" label="α" value={Number(value.alpha ?? 0.05)} min={0.001} max={0.49} integer={false} step={0.01} {disabled} onchange={(v) => set({ alpha: v ?? 0.05 })} />
		<div class="space-y-1.5">
			<Label for="plan-correction">Correction</Label>
			<Select.Simple id="plan-correction" value={String(value.correction ?? 'holm')} items={items(PLAN_CORRECTIONS)} {disabled} onValueChange={(v) => set({ correction: v })} />
		</div>
		<div class="space-y-1.5">
			<Label for="plan-multiplicity">Multiplicity family</Label>
			<Select.Simple id="plan-multiplicity" value={String(value.multiplicity ?? 'all')} items={items(PLAN_MULTIPLICITY)} {disabled} onValueChange={(v) => set({ multiplicity: v })} />
		</div>
	</div>

	<div class="grid gap-3 sm:grid-cols-2">
		<div class="space-y-1.5">
			<Label for="plan-estimand">Estimand</Label>
			<Select.Simple id="plan-estimand" value={String(value.estimand ?? 'marginal')} items={items(PLAN_ESTIMANDS)} {disabled} onValueChange={(v) => set({ estimand: v })} />
		</div>
		<fieldset class="space-y-1">
			<legend class="text-sm font-medium">Random effects</legend>
			<div class="flex flex-wrap gap-x-3 gap-y-1">
				{#each PLAN_RANDOM_EFFECTS as re (re)}
					<label class="flex items-center gap-1.5 text-xs">
						<Checkbox checked={randomEffects.includes(re)} {disabled} onCheckedChange={(v) => set({ random_effects: toggleIn(randomEffects, re, v === true) })} />
						{re}
					</label>
				{/each}
			</div>
		</fieldset>
	</div>

	<fieldset class="space-y-1">
		<legend class="text-sm font-medium">Sensitivity analyses</legend>
		<p class="text-xs text-muted-foreground">The main analysis is intention-to-treat: truncated and budget-stopped runs are included.</p>
		<div class="grid gap-1 sm:grid-cols-2">
			{#each PLAN_SENSITIVITY as s (s)}
				<label class="flex items-center gap-2 text-xs">
					<Checkbox checked={sensitivity.includes(s)} {disabled} onCheckedChange={(v) => set({ sensitivity: toggleIn(sensitivity, s, v === true) })} />
					{LABELS[s]}
				</label>
			{/each}
		</div>
	</fieldset>

	<div class="grid gap-3 sm:grid-cols-2">
		<NumberField
			id="plan-exclusion"
			label="Max attrition per condition"
			value={Number(exclusions.exclusion_rate ?? 0.1)}
			min={0}
			max={1}
			integer={false}
			step={0.01}
			{disabled}
			onchange={(v) => set({ exclusions: { ...exclusions, truncated: false, exclusion_rate: v ?? 0.1 } })}
		/>
		<NumberField
			id="plan-differential"
			label="Max differential attrition"
			value={Number(exclusions.differential_attrition ?? 0.05)}
			min={0}
			max={1}
			integer={false}
			step={0.01}
			{disabled}
			onchange={(v) => set({ exclusions: { ...exclusions, truncated: false, differential_attrition: v ?? 0.05 } })}
		/>
	</div>

	<div class="space-y-1.5">
		<Label for="plan-looks">Group-sequential looks (optional)</Label>
		<Input
			id="plan-looks"
			class="h-8 max-w-sm font-mono text-sm"
			placeholder="e.g. 10, 20, 30 (units per look)"
			value={looksText}
			{disabled}
			onchange={(e) => setLooks((e.currentTarget as HTMLInputElement).value)}
		/>
		{#if sequential}
			<div class="flex flex-wrap items-end gap-3">
				<div class="space-y-1">
					<Label for="plan-spending" class="text-xs">α spending</Label>
					<Select.Simple
						id="plan-spending"
						size="sm"
						value={sequential.alpha_spending}
						items={[
							{ value: 'obrien_fleming', label: "O'Brien–Fleming" },
							{ value: 'pocock', label: 'Pocock' },
						]}
						{disabled}
						onValueChange={(v) => set({ sequential: { ...sequential, alpha_spending: v } })}
					/>
				</div>
				<label class="flex items-center gap-2 text-xs">
					<Checkbox
						checked={!!sequential.futility}
						{disabled}
						onCheckedChange={(v) => set({ sequential: { ...sequential, futility: v === true ? { type: 'conditional_power', threshold: 0.1 } : null } })}
					/>
					Non-binding futility (conditional power &lt; 0.1)
				</label>
			</div>
			<p class="text-xs text-muted-foreground">Interim p-values stay withheld until the final look.</p>
		{/if}
	</div>

	<div class="space-y-2">
		<p class="text-sm font-medium">Research questions</p>
		{#each questions as q, i (i)}
			<div class="grid gap-2 rounded-md border border-border p-2 sm:grid-cols-[1fr_12rem_8rem_auto]">
				<Input
					class="h-8 text-sm"
					aria-label="Question {i + 1}"
					value={q.question}
					{disabled}
					onchange={(e) => set({ research_questions: questions.map((x, j) => (j === i ? { ...x, question: (e.currentTarget as HTMLInputElement).value } : x)) })}
				/>
				<Select.Simple
					id="plan-q-metric-{i}"
					size="sm"
					value={q.metric ?? String(value.primary_metric ?? '')}
					items={metrics}
					{disabled}
					onValueChange={(v) => set({ research_questions: questions.map((x, j) => (j === i ? { ...x, metric: v } : x)) })}
				/>
				<Select.Simple
					id="plan-q-direction-{i}"
					size="sm"
					value={q.direction ?? 'two-sided'}
					items={[
						{ value: 'two-sided', label: 'Two-sided' },
						{ value: 'greater', label: 'Greater' },
						{ value: 'less', label: 'Less' },
					]}
					{disabled}
					onValueChange={(v) => set({ research_questions: questions.map((x, j) => (j === i ? { ...x, direction: v } : x)) })}
				/>
				<Button variant="ghost" size="icon" aria-label="Remove question {i + 1}" {disabled} onclick={() => set({ research_questions: questions.filter((_, j) => j !== i) })}>
					<X />
				</Button>
			</div>
		{/each}
		<Button variant="outline" size="sm" {disabled} onclick={() => set({ research_questions: [...questions, { question: '', direction: 'two-sided' }] })}>
			<Plus />Add a research question
		</Button>
	</div>

	{#if mode === 'slot'}
		<div class="space-y-2 rounded-md border border-border p-3">
			<p class="text-sm font-medium">Locked pattern plan</p>
			<p class="text-xs text-muted-foreground">
				Detector set, primary features, normalizer and cluster parameters enter the pre-registration; any other pattern run on this
				experiment is labelled exploratory.
			</p>
			<div class="space-y-1.5">
				<Label for="plan-detectors" class="text-xs">Detectors</Label>
				<LibraryRefPicker
					id="plan-detectors"
					kind="detector"
					multiple
					value={(patterns?.detectors as string[] | undefined) ?? []}
					{disabled}
					onchange={(refs) => set({ patterns: { ...(patterns ?? {}), detectors: refs } })}
				/>
			</div>
			<div class="space-y-1.5">
				<Label for="plan-features" class="text-xs">Primary features (scored live; required)</Label>
				<LibraryRefPicker
					id="plan-features"
					kind="detector"
					multiple
					value={primaryFeatures}
					{disabled}
					onchange={(refs) => {
						onfeatures?.(refs);
						set({ patterns: { ...(patterns ?? {}), primary_features: featureKeys(refs) } });
					}}
				/>
			</div>
			<NumberField
				id="plan-normalizer"
				label="Normalizer version"
				value={Number(patterns?.normalizer_version ?? 1)}
				min={1}
				{disabled}
				onchange={(v) => set({ patterns: { ...(patterns ?? {}), normalizer_version: v ?? 1 } })}
			/>
		</div>
	{/if}

	<div class="space-y-1.5">
		<Label for="plan-notes">Notes</Label>
		<Textarea id="plan-notes" rows={3} value={String(value.notes ?? '')} {disabled} onchange={(e: Event) => set({ notes: (e.currentTarget as HTMLTextAreaElement).value })} />
	</div>
</div>
