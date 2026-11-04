<script lang="ts">
	/**
	 * PlanRail — the builder's live plan (spec 10.2): counts (conditions × units × repeats = cells, and
	 * blocks), the estimate against the cap with the cost-by-level bars and the fit, the issues with their
	 * quick fixes, the cell sampler (opens `ResolvedCellDrawer`), and Freeze / Start.
	 *
	 * The plan request lifecycle (500 ms debounce, abort when superseded, `persist=false`) lives in
	 * `BuilderState` (spec 10.7), which this rail renders; the counts shown before the first server answer
	 * come from the client expansion (same rules and fixtures as the server).
	 *
	 * Props: `state` (`BuilderState`), `onfix(issue, fix)`, `oncell(cellKey)`, `onfreeze()`, `onstart()`,
	 * `summaries` (issue-code titles), `factorLabel(key)`.
	 */
	import type { IssueFix } from '$lib/api/v3/types';
	import { Button } from '$lib/components/ui/button';
	import ErrorAlert from '$lib/components/ErrorAlert.svelte';
	import { cn } from '$lib/utils';
	import { formatSecondsRange, formatUsd, plural } from '../format';
	import type { BuilderState } from './builder.svelte';
	import CostByLevelBar from './CostByLevelBar.svelte';
	import RunFormula, { type RunFactor } from './RunFormula.svelte';
	import SummaryIssues from './SummaryIssues.svelte';
	import { expandGrid, unitCount } from './expansion';
	import { hasErrors, type SummaryIssue } from './issues';
	import { FACTOR_BY_KEY } from './design';
	import { estimateBasisTotals } from './policy';
	import LoaderCircle from '@lucide/svelte/icons/loader-circle';
	import Lock from '@lucide/svelte/icons/lock';
	import Play from '@lucide/svelte/icons/play';

	interface Props {
		state: BuilderState;
		onfix: (issue: SummaryIssue, fix: IssueFix) => void;
		oncell: (cellKey: string) => void;
		onfreeze: () => void;
		onstart: () => void;
		summaries?: Record<string, string>;
	}

	let { state: b, onfix, oncell, onfreeze, onstart, summaries }: Props = $props();

	const design = $derived(b.design);
	const plan = $derived(b.plan);
	const local = $derived.by(() => {
		try {
			return design.factors.length ? expandGrid(design).conditions.length : 0;
		} catch {
			return null;
		}
	});
	const fresh = $derived(b.planFresh);
	const nConditions = $derived(fresh && plan ? plan.n_conditions : local);
	const nUnits = $derived(fresh && plan ? plan.n_units : unitCount(design));
	const unitLabel = $derived(design.mode === 'app' ? { label: 'tasks', singular: 'task' } : { label: 'targets', singular: 'target' });
	const factors = $derived<RunFactor[]>([
		{ label: 'conditions', singular: 'condition', count: nConditions, href: '#step-factors' },
		{ ...unitLabel, count: nUnits, href: '#step-units' },
		{ label: 'repeats', singular: 'repeat', count: design.repeats, href: '#step-repeats' },
	]);
	const total = $derived(fresh && plan ? plan.n_cells_by_stage.main ?? plan.n_cells : nConditions !== null ? nConditions * nUnits * design.repeats : null);
	const estimate = $derived(plan?.estimate ?? null);
	const cap = $derived(estimate?.cap_usd ?? null);
	// Expected / p90 in the cap's unit: list price when the endpoint is not billed (E2E-13).
	const totals = $derived(estimate ? estimateBasisTotals(estimate) : null);
	const issues = $derived(b.issues);
	const errors = $derived(issues.filter((i) => i.severity === 'error').length);
	const status = $derived(
		b.saving
			? 'saving the draft'
			: b.planLoading
				? 'resolving the plan'
				: b.pending
					? 'waiting for edits to settle'
					: b.saveError
						? 'not saved'
						: !b.studyId
							? 'not saved yet'
							: fresh
								? 'plan up to date'
								: 'plan out of date',
	);
	const startReason = $derived(
		!b.studyId
			? 'Complete the required steps first.'
			: errors > 0
				? 'Fix the errors first.'
				: !b.frozen
					? 'Freeze the plan first.'
					: !b.canStart
						? 'The design changed since the plan was frozen: freeze again.'
						: '',
	);
	const sample = $derived(plan?.cells_sample.slice(0, 12) ?? []);
	const pilot = $derived(plan?.n_cells_by_stage.pilot ?? 0);
</script>

<div class="space-y-4">
	<div class="space-y-1.5">
		<div class="flex items-center justify-between gap-2">
			<p class="section-label">Plan</p>
			<span class="flex items-center gap-1.5 text-[11px] text-muted-foreground" role="status">
				{#if b.saving || b.planLoading}<LoaderCircle class="size-3 animate-spin" aria-hidden="true" />{/if}{status}
			</span>
		</div>
		<RunFormula {factors} {total} unit="cells" />
		{#if fresh && plan}
			<p class="text-xs text-muted-foreground">
				{plural(plan.n_blocks, 'block')}{#if pilot} · plus {plural(pilot, 'pilot cell')}{/if}{#if plan.unbalanced} · unbalanced grid (by-factor slices use marginal means){/if}
			</p>
		{/if}
	</div>

	{#if b.saveError}<ErrorAlert message={b.saveError} />{/if}
	{#if b.planError}<ErrorAlert message={b.planError} onretry={() => b.refreshPlan()} />{/if}

	{#if estimate}
		<div class={cn('space-y-2', !fresh && 'opacity-60')}>
			<p class="section-label">Estimate</p>
			<dl class="grid grid-cols-3 gap-1.5 text-center">
				<div class="rounded border border-border px-1.5 py-1">
					<dt class="text-[11px] text-muted-foreground">expected</dt>
					<dd class="font-mono text-sm tabular-nums">{formatUsd(totals?.expected)}</dd>
				</div>
				<div class="rounded border border-border px-1.5 py-1">
					<dt class="text-[11px] text-muted-foreground">p90</dt>
					<dd class="font-mono text-sm tabular-nums">{formatUsd(totals?.p90)}</dd>
				</div>
				<div class="rounded border border-border px-1.5 py-1">
					<dt class="text-[11px] text-muted-foreground">worst case</dt>
					<dd class="font-mono text-sm tabular-nums">{formatUsd(estimate.worst_case_total)}</dd>
				</div>
			</dl>
			<p class="text-xs text-muted-foreground">
				{#if totals?.basis === 'list'}Cap {cap === null ? 'not set' : formatUsd(cap)} at list price · billed {formatUsd(estimate.expected_total)}{:else}Cap {cap === null ? 'not set' : formatUsd(cap)} · list price {formatUsd(estimate.expected_list_total)}{/if}
				{#if estimate.time}· {formatSecondsRange(estimate.time.expected_wall_s_low, estimate.time.expected_wall_s_high)} at {estimate.time.effective_parallel} parallel{/if}
				{#if estimate.time?.eval_machine_minutes}· {Math.round(estimate.time.eval_machine_minutes)} eval machine-min{/if}
			</p>
			{#if estimate.fit.max_repeats_under_cap !== null && estimate.fit.max_repeats_under_cap < design.repeats}
				<p class="text-xs text-warning-fg">Fits {estimate.fit.max_repeats_under_cap} of {design.repeats} repeats under the cap.</p>
			{:else if estimate.fit.max_repeats_under_cap !== null}
				<p class="text-xs text-muted-foreground">Fits all {design.repeats} repeats under the cap.</p>
			{/if}
			<CostByLevelBar byLevel={estimate.by_level} factorLabel={(f) => FACTOR_BY_KEY[f as keyof typeof FACTOR_BY_KEY]?.label ?? f} />
			<p class="text-[11px] text-muted-foreground">Estimate basis: {estimate.method}. Budget values never enter the prompts.</p>
		</div>
	{/if}

	<SummaryIssues {issues} {onfix} {oncell} {summaries} />

	{#if sample.length > 0 && b.studyId}
		<div class="space-y-1.5">
			<p class="section-label">Cells · open one to see its prompts</p>
			<ul class="flex flex-wrap gap-1">
				{#each sample as cell (cell.cell_key)}
					<li>
						<button
							type="button"
							class={cn(
								'cursor-pointer rounded border border-border px-1.5 py-0.5 font-mono text-[11px] transition-colors hover:border-primary hover:text-primary',
								cell.excluded && 'border-dashed text-muted-foreground',
							)}
							title="{cell.unit_key} · repeat {cell.repeat_index} · seed {cell.seed}{cell.excluded ? ' · excluded (config invalid)' : ''}"
							onclick={() => oncell(cell.cell_key)}
						>
							{cell.cell_key}
						</button>
					</li>
				{/each}
			</ul>
		</div>
	{/if}

	<div class="space-y-2 border-t border-border pt-3">
		<Button variant="outline" class="w-full" disabled={!b.canFreeze || b.freezing} onclick={onfreeze}>
			{#if b.freezing}<LoaderCircle class="animate-spin" />{:else}<Lock />{/if}
			{b.frozen && b.canStart ? 'Plan frozen' : 'Freeze plan'}
		</Button>
		<Button class="w-full" disabled={!b.canStart || b.launching || hasErrors(issues)} onclick={onstart}>
			{#if b.launching}<LoaderCircle class="animate-spin" />{:else}<Play />{/if}Start…
		</Button>
		{#if startReason}<p class="text-xs text-muted-foreground">{startReason}</p>{/if}
	</div>
</div>
