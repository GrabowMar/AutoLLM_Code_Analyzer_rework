<script lang="ts">
	/**
	 * PowerHelper — planning numbers next to the repeats field (spec 10.2 step 4). Planning aids only:
	 * every number states its assumptions, which the researcher can change inline.
	 *
	 * - App mode: the smallest paired difference in mean pass rate that the design detects (two-sided
	 *   α = .05, power .8), with tasks as pairs.
	 * - Slot mode: the Wilson 95% half-width per (condition, target) at p = .5 and at a chosen p, the pooled
	 *   half-width across targets with a between-target spread, and what a `ci_width_reached` stop saves.
	 *
	 * Props: `mode`, `units` (tasks or targets), `repeats`.
	 */
	import NumberField from '../shared/NumberField.svelte';
	import { formatPct } from '../format';
	import { pairedDetectableDifference, pooledHalfWidth, precisionStopSaving, wilsonHalfWidth } from './power';
	import type { StudyMode } from '$lib/api/v3/types';

	interface Props {
		mode: StudyMode;
		units: number;
		repeats: number;
	}

	let { mode, units, repeats }: Props = $props();

	let p = $state<number | null>(0.2);
	let sdUnit = $state<number | null>(0.2);
	let rho = $state<number | null>(0.5);
	let tau = $state<number | null>(0.1);
	let threshold = $state<number | null>(0.15);
	let showAssumptions = $state(false);

	const mde = $derived(
		pairedDetectableDifference({ units, repeats, p: 0.5, sdUnit: sdUnit ?? 0.2, rho: rho ?? 0.5 }),
	);
	const halfAtHalf = $derived(wilsonHalfWidth(0.5, repeats));
	const halfAtP = $derived(wilsonHalfWidth(p ?? 0.2, repeats));
	const pooled = $derived(pooledHalfWidth(p ?? 0.2, repeats, units, tau ?? 0));
	const stop = $derived(precisionStopSaving(p ?? 0.2, threshold ?? 0.15, 10, repeats));
	const pp = (x: number | null | undefined) => (x === null || x === undefined || Number.isNaN(x) ? '—' : `±${(x * 100).toFixed(1)} pp`);
</script>

<div class="space-y-2 rounded-md border border-border bg-muted/30 px-3 py-2.5 text-xs" aria-live="polite">
	<p class="section-label">Precision at this size</p>
	{#if mode === 'app'}
		{#if mde === null}
			<p class="text-muted-foreground">Paired comparisons need at least 2 tasks.</p>
		{:else}
			<p>
				Detects a paired difference of about <b class="font-mono tabular-nums">{formatPct(mde, 1)}</b> in mean pass rate
				<span class="text-muted-foreground">({units} tasks × {repeats} repeats, α = .05 two-sided, power .8)</span>.
			</p>
		{/if}
	{:else}
		<p>
			Per target and condition: Wilson 95% half-width <b class="font-mono tabular-nums">{pp(halfAtHalf)}</b> at p = .5,
			<b class="font-mono tabular-nums">{pp(halfAtP)}</b> at p = {p ?? '—'}.
		</p>
		{#if units > 1}
			<p>
				Pooled over {units} targets: <b class="font-mono tabular-nums">{pp(pooled)}</b>
				<span class="text-muted-foreground">(between-target spread τ = {tau ?? 0})</span>.
			</p>
		{/if}
		{#if stop}
			<p>
				A precision stop at {pp(threshold)} would end near n ≈ <b class="font-mono tabular-nums">{stop.stopAt}</b>
				{#if stop.saving > 0}(saves about {formatPct(stop.saving)} of the variants){:else}(no saving at this size){/if};
				exploratory experiments only.
			</p>
		{/if}
	{/if}
	<button
		type="button"
		class="inline-flex min-h-6 cursor-pointer items-center text-primary underline underline-offset-2"
		aria-expanded={showAssumptions}
		onclick={() => (showAssumptions = !showAssumptions)}
	>
		{showAssumptions ? 'Hide assumptions' : 'Change assumptions'}
	</button>
	{#if showAssumptions}
		<div class="grid gap-2 sm:grid-cols-3">
			{#if mode === 'app'}
				<NumberField id="power-sd" label="Between-task SD" bind:value={sdUnit} min={0} max={0.5} integer={false} step={0.05} />
				<NumberField id="power-rho" label="Pairing correlation ρ" bind:value={rho} min={0} max={0.99} integer={false} step={0.05} />
			{:else}
				<NumberField id="power-p" label="Expected prevalence p" bind:value={p} min={0.01} max={0.99} integer={false} step={0.05} />
				<NumberField id="power-tau" label="Between-target SD τ" bind:value={tau} min={0} max={0.5} integer={false} step={0.05} />
				<NumberField id="power-threshold" label="Stop half-width" bind:value={threshold} min={0.01} max={0.5} integer={false} step={0.01} />
			{/if}
		</div>
	{/if}
</div>
