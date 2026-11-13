<script lang="ts">
	/**
	 * BlueprintRail — the builder's always-visible plan (beside the open step, from 56rem): the run
	 * matrix as arithmetic (conditions × units × repeats = cells; 0 conditions while a required factor is
	 * missing), the cost estimate against the cap and the wall time once the server has resolved the
	 * plan, the open required choices as a neutral to-do list, problems per step, and the way forward
	 * (Review & launch, or Launch on the review step).
	 *
	 * Props: `builder`, `current`, `steps` (labels), `missing`, `stepIssues`, `onselect(step)`,
	 * `onprimary()`, `primaryLabel`, `primaryBusy`.
	 */
	import { Button } from '$lib/components/ui/button';
	import { cn } from '$lib/utils';
	import { formatSecondsRange, formatUsd, plural } from '../format';
	import type { BuilderState } from './builder.svelte';
	import type { RunFactor } from './RunFormula.svelte';
	import { expandGrid, unitCount } from './expansion';
	import { estimateBasisTotals } from './policy';
	import { guardConditions, type MissingChoice, type StepDef, type StepId } from './steps';
	import ArrowRight from '@lucide/svelte/icons/arrow-right';
	import Circle from '@lucide/svelte/icons/circle';
	import CircleAlert from '@lucide/svelte/icons/circle-alert';
	import CircleCheck from '@lucide/svelte/icons/circle-check';
	import LoaderCircle from '@lucide/svelte/icons/loader-circle';
	import Rocket from '@lucide/svelte/icons/rocket';
	import TriangleAlert from '@lucide/svelte/icons/triangle-alert';

	interface Props {
		builder: BuilderState;
		current: StepId;
		steps: StepDef[];
		missing: MissingChoice[];
		stepIssues: Record<StepId, { errors: number; warnings: number }>;
		onselect: (step: StepId) => void;
		onprimary: () => void;
		primaryLabel: string;
		primaryBusy?: boolean;
	}

	let { builder: b, current, steps, missing, stepIssues, onselect, onprimary, primaryLabel, primaryBusy = false }: Props = $props();

	const design = $derived(b.design);
	const plan = $derived(b.plan);
	const fresh = $derived(b.planFresh);
	const local = $derived.by(() => {
		try {
			return design.factors.length ? expandGrid(design).conditions.length : 0;
		} catch {
			return null;
		}
	});
	const nConditions = $derived(guardConditions(design, fresh && plan ? plan.n_conditions : local));
	const nUnits = $derived(fresh && plan ? plan.n_units : unitCount(design));
	const factors = $derived<RunFactor[]>([
		{ label: 'conditions', singular: 'condition', count: nConditions },
		design.mode === 'app' ? { label: 'tasks', singular: 'task', count: nUnits } : { label: 'targets', singular: 'target', count: nUnits },
		{ label: 'repeats', singular: 'repeat', count: design.repeats },
	]);
	const total = $derived(
		nConditions === 0 ? 0 : fresh && plan ? (plan.n_cells_by_stage.main ?? plan.n_cells) : nConditions !== null ? nConditions * nUnits * design.repeats : null,
	);
	const estimate = $derived(missing.length === 0 ? (plan?.estimate ?? null) : null);
	const totals = $derived(estimate ? estimateBasisTotals(estimate) : null);
	const labelOf = $derived(new Map(steps.map((s) => [s.id, s.label])));
	const problems = $derived(
		steps.filter((s) => stepIssues[s.id].errors + stepIssues[s.id].warnings > 0).map((s) => ({ ...s, ...stepIssues[s.id] })),
	);
	const overCap = $derived(!!totals && estimate?.cap_usd != null && totals.p90 > estimate.cap_usd);
	// One atomic status message for screen readers, only once the server has answered (never a bare number,
	// never one per tile, and silent while edits are still settling).
	const announcement = $derived(
		fresh && plan && total !== null
			? `Plan updated: ${plural(total, 'cell')}${totals ? `, about ${formatUsd(totals.expected)} expected${overCap ? ', p90 above the cap' : ''}` : ''}.`
			: '',
	);
	const timeLine = $derived(
		estimate?.time
			? [
					`≈ ${formatSecondsRange(estimate.time.expected_wall_s_low, estimate.time.expected_wall_s_high)} at ${estimate.time.effective_parallel} parallel`,
					totals?.basis === 'list' ? 'list price' : '',
				]
					.filter(Boolean)
					.join(' · ')
			: '',
	);
</script>

<div class="br" aria-labelledby="br-title" role="region" aria-busy={b.planLoading}>
	<p class="sr-only" role="status" aria-atomic="true">{announcement}</p>
	<div class="br-head">
		<h2 id="br-title" class="br-title">Plan</h2>
		{#if b.planLoading}
			<span class="br-status"><LoaderCircle class="size-3 animate-spin motion-reduce:animate-none" aria-hidden="true" />resolving</span>
		{:else if fresh}
			<span class="br-status text-success-fg"><CircleCheck class="size-3" aria-hidden="true" />up to date</span>
		{:else if b.studyId && b.pending}
			<span class="br-status">waiting for edits</span>
		{/if}
	</div>

	<div class="br-section">
		<div class="flex items-baseline justify-between gap-2">
			<p class="br-cells"><b class={cn(!total && 'text-muted-foreground')}>{total ?? '—'}</b> {total === 1 ? 'cell' : 'cells'}</p>
			{#if fresh && plan?.n_cells_by_stage.pilot}
				<span class="text-[11px] text-muted-foreground">+ {plural(plan.n_cells_by_stage.pilot, 'pilot cell')}</span>
			{/if}
		</div>
		<p class="br-terms">
			{#each factors as f, i (f.label)}
				{#if i > 0}<span class="br-op">×</span>{/if}
				<span class={cn(f.count === 0 && 'text-muted-foreground')}><b>{f.count ?? '—'}</b> {f.count === 1 ? f.singular : f.label}</span>
			{/each}
		</p>
	</div>

	{#if estimate && totals}
		<div class={cn('br-section', !fresh && 'opacity-60')}>
			<dl class="br-cost">
				<div>
					<dt>Expected</dt>
					<dd>{formatUsd(totals.expected)}</dd>
				</div>
				<div>
					<dt>p90</dt>
					<dd class={cn(overCap && 'text-warning-fg')}>
						{formatUsd(totals.p90)}{#if overCap}<TriangleAlert class="ml-1 inline size-3.5 align-[-2px]" aria-hidden="true" />{/if}
					</dd>
				</div>
				<div>
					<dt>Cap</dt>
					<dd>{estimate.cap_usd == null ? 'none' : formatUsd(estimate.cap_usd)}</dd>
				</div>
			</dl>
			{#if overCap}
				<p class="mt-1.5 text-[11px] text-warning-fg">p90 is above the cap: the experiment may stop before the last block.</p>
			{/if}
			{#if timeLine}<p class="mt-1.5 text-[11px] text-muted-foreground">{timeLine}</p>{/if}
		</div>
	{:else if missing.length === 0 && (b.planLoading || b.pending || !plan)}
		<!-- Reserve the cost block's space while the first estimate is computed (no jump when it lands). -->
		<div class="br-section" aria-hidden="true">
			<div class="br-cost">
				{#each ['Expected', 'p90', 'Cap'] as label (label)}
					<div><p class="br-skel-label">{label}</p><span class="br-skel"></span></div>
				{/each}
			</div>
			<span class="br-skel mt-2 block w-2/3"></span>
		</div>
	{/if}

	{#if missing.length > 0}
		<div class="br-section">
			<p class="section-label mb-1.5">To do</p>
			<ul class="space-y-0.5">
				{#each missing as m (m.label)}
					<li>
						<button type="button" class="br-link" onclick={() => onselect(m.step)}>
							<Circle class="size-3.5 shrink-0 opacity-60" aria-hidden="true" />
							<span class="min-w-0 flex-1">{m.label}</span>
							<span class="br-step">{labelOf.get(m.step)}</span>
						</button>
					</li>
				{/each}
			</ul>
		</div>
	{/if}

	{#if problems.length > 0}
		<div class="br-section">
			<p class="section-label mb-1.5">Check</p>
			<ul class="space-y-0.5">
				{#each problems as p (p.id)}
					<li>
						<button type="button" class="br-link" onclick={() => onselect(p.id)}>
							{#if p.errors}
								<CircleAlert class="size-3.5 shrink-0 text-danger-fg" aria-hidden="true" />
							{:else}
								<TriangleAlert class="size-3.5 shrink-0 text-warning-fg" aria-hidden="true" />
							{/if}
							<span class="min-w-0 flex-1">{p.label}</span>
							<span class="br-step">
								{#if p.errors}{plural(p.errors, 'error')}{/if}{#if p.errors && p.warnings} · {/if}{#if p.warnings}{plural(p.warnings, 'warning')}{/if}
							</span>
						</button>
					</li>
				{/each}
			</ul>
		</div>
	{/if}

	{#if b.saveError}
		<p class="br-section text-xs text-danger-fg" role="alert">{b.saveError}</p>
	{:else if b.planError}
		<div class="br-section text-xs text-danger-fg" role="alert">
			{b.planError}
			<button type="button" class="ml-1 cursor-pointer underline underline-offset-2" onclick={() => b.refreshPlan()}>Retry</button>
		</div>
	{/if}

	<div class="br-foot">
		<!-- Secondary: the open step's Continue is the screen's one primary action. -->
		<Button class="w-full" variant="outline" disabled={primaryBusy} onclick={onprimary}>
			{#if primaryBusy}<LoaderCircle class="animate-spin motion-reduce:animate-none" />{:else if current === 'review'}<Rocket />{/if}
			{primaryLabel}
			{#if current !== 'review' && !primaryBusy}<ArrowRight />{/if}
		</Button>
	</div>
</div>

<style>
	.br {
		border: 1px solid var(--border);
		border-radius: calc(var(--radius) + 2px);
		background: var(--card);
		box-shadow: var(--shadow-card);
	}
	.br-head {
		display: flex;
		align-items: center;
		justify-content: space-between;
		gap: 0.5rem;
		padding: 0.75rem 1rem 0.5rem;
	}
	.br-title {
		font-family: var(--font-display);
		font-size: 1.0625rem;
		font-weight: 600;
	}
	.br-status {
		display: inline-flex;
		align-items: center;
		gap: 0.3rem;
		font-family: var(--font-mono);
		font-size: 0.6875rem;
		color: var(--muted-foreground);
	}
	.br-section {
		padding: 0.75rem 1rem;
		border-top: 1px solid color-mix(in oklab, var(--border) 70%, transparent);
	}
	.br-cells {
		font-size: 0.8125rem;
		color: var(--muted-foreground);
	}
	.br-cells b {
		margin-right: 0.125rem;
		font-family: var(--font-mono);
		font-size: 1.75rem;
		line-height: 1;
		font-weight: 500;
		font-variant-numeric: tabular-nums;
		color: var(--primary);
	}
	.br-terms {
		display: flex;
		flex-wrap: wrap;
		align-items: baseline;
		gap: 0.25rem 0.375rem;
		margin-top: 0.375rem;
		font-size: 0.75rem;
	}
	.br-terms b {
		font-family: var(--font-mono);
		font-weight: 600;
		font-variant-numeric: tabular-nums;
	}
	.br-op {
		font-family: var(--font-mono);
		color: var(--muted-foreground);
	}
	.br-cost {
		display: grid;
		grid-template-columns: repeat(3, minmax(0, 1fr));
		gap: 0.375rem;
	}
	.br-cost > div {
		border: 1px solid var(--border);
		border-radius: calc(var(--radius) - 2px);
		background: var(--surface-2);
		padding: 0.3125rem 0.5rem;
	}
	.br-cost dt,
	.br-skel-label {
		font-family: var(--font-mono);
		font-size: 0.6875rem;
		letter-spacing: 0.08em;
		text-transform: uppercase;
		color: var(--muted-foreground);
	}
	.br-cost dd {
		font-family: var(--font-mono);
		font-size: 0.875rem;
		font-weight: 500;
		font-variant-numeric: tabular-nums;
	}
	.br-skel {
		display: block;
		height: 0.875rem;
		margin-top: 0.3125rem;
		border-radius: 3px;
		background: color-mix(in oklab, var(--muted-foreground) 18%, transparent);
		animation: br-pulse 1.4s ease-in-out infinite;
	}
	.br-skel-label {
		letter-spacing: 0.08em;
		text-transform: uppercase;
		color: var(--muted-foreground);
	}
	@keyframes br-pulse {
		50% {
			opacity: 0.45;
		}
	}
	@media (prefers-reduced-motion: reduce) {
		.br-skel {
			animation: none;
		}
	}
	.br-link {
		display: flex;
		width: 100%;
		min-height: 1.875rem;
		cursor: pointer;
		align-items: center;
		gap: 0.5rem;
		border-radius: calc(var(--radius) - 2px);
		padding: 0.25rem 0.375rem;
		margin-inline: -0.375rem;
		width: calc(100% + 0.75rem);
		text-align: left;
		font-size: 0.75rem;
		transition: background-color 150ms;
	}
	.br-link:hover {
		background: var(--muted);
	}
	.br-link:focus-visible {
		outline: 2px solid var(--ring);
		outline-offset: -2px;
	}
	.br-step {
		flex-shrink: 0;
		font-size: 0.6875rem;
		color: var(--muted-foreground);
	}
	.br-foot {
		padding: 0.75rem 1rem 1rem;
		border-top: 1px solid color-mix(in oklab, var(--border) 70%, transparent);
	}
</style>
