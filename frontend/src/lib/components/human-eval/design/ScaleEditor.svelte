<script lang="ts">
	/**
	 * ScaleEditor — an integer rating scale: min / max / step, end anchors,
	 * optional per-value labels and a preview of the points. Local checks mirror
	 * the backend (step divides the range, ≤ 100 points span); server errors for
	 * this scale come in as `errors` (paths relative to the scale).
	 *
	 * Props: `id` (prefix for field ids), `scale` (bindable), `errors`, `readOnly`.
	 */
	import * as Select from '$lib/components/ui/select';
	import { Input } from '$lib/components/ui/input';
	import { Label } from '$lib/components/ui/label';
	import { Checkbox } from '$lib/components/ui/checkbox';
	import NumberField from '$lib/components/studies/shared/NumberField.svelte';
	import type { Scale } from '$lib/api/types/human-eval';
	import { messageAt, messagesUnder, type FieldError } from './errors';
	import { SCALE_PRESETS, cloneScale, labelsForGrid, rememberLabels, scaleIssues, scaleStep, scaleValues } from './scale';

	interface Props {
		id: string;
		scale: Scale;
		errors?: FieldError[];
		readOnly?: boolean;
	}

	let { id, scale = $bindable(), errors = [], readOnly = false }: Props = $props();

	const values = $derived(scaleValues(scale));
	const issues = $derived(scaleIssues(scale));
	// min / max / step errors show under their fields; the rest (labels, the scale as a whole) below.
	const serverMessages = $derived(
		messagesUnder(errors.filter((e) => !['min', 'max', 'step'].includes(e.path[0] ?? ''))).filter((m) => !issues.includes(m))
	);
	// The server already explains a bad range / step next to the field: skip the local wording then.
	const serverOnBounds = $derived(errors.some((e) => e.path.length === 0 || ['min', 'max', 'step'].includes(e.path[0] ?? '')));
	const canLabelEach = $derived(values.length > 0 && values.length <= 11);
	let labelEach = $state(false);
	$effect(() => {
		// Open the per-value labels when the scale already has some.
		if (scale.labels && Object.keys(scale.labels).length > 0) labelEach = true;
	});

	let presetKey = $state('');

	// Every per-value label typed here (not rendered). A bound passes through invalid or
	// smaller grids while it is typed, so the scale only holds the labels on its current
	// grid and the rest come back from here once the grid includes their values again.
	// Tied to the scale object: a scale replaced from outside (preset, "use for all") starts over.
	let rememberedLabels: Record<string, string> = {};
	let rememberedFor: Scale | null = null;
	function remembered(): Record<string, string> {
		if (rememberedFor !== scale) {
			rememberedFor = scale;
			rememberedLabels = {};
		}
		return rememberedLabels;
	}

	function applyPreset(key: string) {
		const preset = SCALE_PRESETS.find((p) => p.key === key);
		if (!preset) return;
		scale = cloneScale(preset.scale);
		presetKey = '';
	}

	function setBound(field: 'min' | 'max' | 'step', v: number | null) {
		if (v == null) return;
		rememberedLabels = rememberLabels(remembered(), scale.labels);
		scale[field] = v;
		const labels = labelsForGrid(scale, rememberedLabels);
		if (labels) scale.labels = labels;
		else delete scale.labels;
	}

	function setValueLabel(value: number, text: string) {
		const labels = { ...(scale.labels ?? {}) };
		if (text.trim()) labels[String(value)] = text;
		else delete labels[String(value)];
		rememberedLabels = rememberLabels(remembered(), labels);
		if (!text.trim()) delete rememberedLabels[String(value)];
		if (Object.keys(labels).length) scale.labels = labels;
		else delete scale.labels;
	}

	function toggleLabelEach(on: boolean) {
		labelEach = on;
		if (!on) {
			delete scale.labels;
			remembered();
			rememberedLabels = {};
		}
	}
</script>

<div class="space-y-3">
	{#if readOnly}
		<p class="text-xs text-muted-foreground">
			<span class="font-mono tabular-nums text-foreground">{scale.min}–{scale.max}</span>
			{#if scaleStep(scale) !== 1}<span> · step {scaleStep(scale)}</span>{/if}
			{#if scale.min_label || scale.max_label}<span> · “{scale.min_label || scale.min}” → “{scale.max_label || scale.max}”</span>{/if}
		</p>
	{:else}
		<div class="grid grid-cols-3 gap-2 sm:grid-cols-[repeat(3,minmax(0,6rem))_minmax(0,1fr)]">
			<NumberField
				id="{id}-min"
				label="Min"
				bind:value={() => scale.min, (v) => setBound('min', v)}
				min={-100}
				max={100}
				required
				error={messageAt(errors, 'min')}
			/>
			<NumberField
				id="{id}-max"
				label="Max"
				bind:value={() => scale.max, (v) => setBound('max', v)}
				min={-100}
				max={200}
				required
				error={messageAt(errors, 'max')}
			/>
			<NumberField
				id="{id}-step"
				label="Step"
				bind:value={() => scale.step ?? 1, (v) => setBound('step', v)}
				min={1}
				max={100}
				required
				error={messageAt(errors, 'step')}
			/>
			<div class="col-span-3 space-y-1.5 sm:col-span-1">
				<Label class="text-xs" for="{id}-preset">Start from</Label>
				<Select.Simple
					id="{id}-preset"
					size="sm"
					value={presetKey}
					placeholder="Scale preset…"
					items={SCALE_PRESETS.map((p) => ({ value: p.key, label: p.label }))}
					onValueChange={applyPreset}
				/>
			</div>
		</div>
		<div class="grid gap-2 sm:grid-cols-2">
			<div class="space-y-1.5">
				<Label class="text-xs" for="{id}-min-label">Label at {scale.min}</Label>
				<Input id="{id}-min-label" class="h-8 text-sm" bind:value={() => scale.min_label ?? '', (v) => (scale.min_label = v)} placeholder="e.g. Strongly disagree" maxlength={300} />
			</div>
			<div class="space-y-1.5">
				<Label class="text-xs" for="{id}-max-label">Label at {scale.max}</Label>
				<Input id="{id}-max-label" class="h-8 text-sm" bind:value={() => scale.max_label ?? '', (v) => (scale.max_label = v)} placeholder="e.g. Strongly agree" maxlength={300} />
			</div>
		</div>
		{#if canLabelEach}
			<label class="flex items-center gap-2 text-xs">
				<Checkbox checked={labelEach} onCheckedChange={(v) => toggleLabelEach(!!v)} />
				Label every value
			</label>
			{#if labelEach}
				<div class="grid gap-1.5 sm:grid-cols-2">
					{#each values as v (v)}
						<div class="flex items-center gap-2">
							<span class="w-10 shrink-0 text-right font-mono text-xs tabular-nums text-muted-foreground">{v}</span>
							<Input
								class="h-8 text-sm"
								aria-label="Label for value {v}"
								value={scale.labels?.[String(v)] ?? ''}
								oninput={(e) => setValueLabel(v, (e.currentTarget as HTMLInputElement).value)}
								maxlength={300}
							/>
						</div>
					{/each}
				</div>
			{/if}
		{/if}
	{/if}

	{#if values.length > 0 && values.length <= 21}
		<div class="flex flex-wrap items-center gap-1" aria-label="Scale preview">
			{#each values as v (v)}
				<span
					class="inline-flex min-w-7 items-center justify-center rounded border border-border bg-surface-1 px-1.5 py-0.5 font-mono text-[11px] tabular-nums"
					title={scale.labels?.[String(v)] ?? (v === scale.min ? scale.min_label : v === scale.max ? scale.max_label : '') ?? ''}
				>
					{v}
				</span>
			{/each}
		</div>
	{:else if values.length > 21}
		<p class="text-[11px] text-muted-foreground">{values.length} points from {scale.min} to {scale.max}.</p>
	{/if}

	{#if !serverOnBounds}
		{#each issues as issue (issue)}
			<p class="text-[11px] text-danger-fg">{issue}</p>
		{/each}
	{/if}
	{#each serverMessages as message (message)}
		<p class="text-[11px] text-danger-fg">{message}</p>
	{/each}
</div>
