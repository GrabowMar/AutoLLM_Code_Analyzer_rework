<script lang="ts">
	/**
	 * ReviewStep — the last step: what blocks launching (a focusable summary linking each open choice
	 * and error to its step), the whole design at a glance (one card per step, each with Edit), the
	 * resolved plan (cells, cost against the cap, wall time, which levels drive the cost), the issues with
	 * their quick fixes, and sample cells to inspect the exact prompts. Launch lives in the panel footer.
	 *
	 * Props: `builder`, `steps`, `missing`, `issues` (server issues, to-dos removed), `postRun`,
	 * `onselect(step)`, `oncell(key)`, `onfix(issue, fix)`, `fixError`, `blockersEl` (bindable: focused when
	 * Launch is pressed while blocked).
	 */
	import { onMount } from 'svelte';
	import type { IssueFix } from '$lib/api/v3/types';
	import type { LLMModelSummary } from '$lib/api/models';
	import { cn } from '$lib/utils';
	import { KIND_LABELS, MODE_LABELS, formatSecondsRange, formatUsd, plural, refLabel } from '../../format';
	import { loadModelCatalog } from '../../shared/catalog';
	import type { AnalysisPostRunConfig } from '../../automation/PostRunEditor.svelte';
	import type { BuilderState } from '../builder.svelte';
	import CostByLevelBar from '../CostByLevelBar.svelte';
	import SummaryIssues from '../SummaryIssues.svelte';
	import { FACTOR_BY_KEY, MODE_DEFAULTS, getLevels } from '../design';
	import { estimateBasisTotals } from '../policy';
	import type { SummaryIssue } from '../issues';
	import { guardConditions, type MissingChoice, type StepDef, type StepId } from '../steps';
	import { expandGrid, unitCount } from '../expansion';
	import CircleAlert from '@lucide/svelte/icons/circle-alert';
	import CircleCheck from '@lucide/svelte/icons/circle-check';
	import FileSearch from '@lucide/svelte/icons/file-search';
	import Pencil from '@lucide/svelte/icons/pencil';

	interface Props {
		builder: BuilderState;
		steps: StepDef[];
		missing: MissingChoice[];
		issues: SummaryIssue[];
		postRun: AnalysisPostRunConfig;
		onselect: (step: StepId) => void;
		oncell: (key: string) => void;
		onfix: (issue: SummaryIssue, fix: IssueFix) => void;
		fixError?: string;
		blockersEl?: HTMLElement | null;
	}

	let { builder: b, steps, missing, issues, postRun, onselect, oncell, onfix, fixError = '', blockersEl = $bindable(null) }: Props = $props();

	const design = $derived(b.design);
	const plan = $derived(b.plan);
	const fresh = $derived(b.planFresh);
	const estimate = $derived(plan?.estimate ?? null);
	const totals = $derived(estimate ? estimateBasisTotals(estimate) : null);
	const errors = $derived(issues.filter((i) => i.severity === 'error'));
	const blocked = $derived(missing.length > 0 || errors.length > 0);
	const label = $derived(new Map(steps.map((s) => [s.id, s.label])));

	let catalog = $state<LLMModelSummary[]>([]);
	onMount(() => {
		void loadModelCatalog()
			.then((list) => (catalog = list))
			.catch(() => {});
	});
	const modelName = $derived.by(() => {
		const byId = new Map(catalog.map((m) => [m.model_id, m.model_name]));
		return (id: string) => byId.get(id) ?? id;
	});

	const local = $derived.by(() => {
		try {
			return design.factors.length ? expandGrid(design).conditions.length : 0;
		} catch {
			return null;
		}
	});
	const nConditions = $derived(guardConditions(design, fresh && plan ? plan.n_conditions : local) ?? 0);
	const nUnits = $derived(fresh && plan ? plan.n_units : unitCount(design));
	const nCells = $derived(fresh && plan ? (plan.n_cells_by_stage.main ?? plan.n_cells) : nConditions * nUnits * design.repeats);

	const models = $derived(getLevels(design, 'model'));
	const units = $derived(design.mode === 'app' ? design.units.tasks : design.units.slot_targets);
	const treatments = $derived(design.factors.filter((f) => f.key !== 'model'));
	const analysisPlan = $derived(design.analysis_plan as { primary_metric?: string; alpha?: number; correction?: string } | null);
	const sample = $derived(plan?.cells_sample.slice(0, 16) ?? []);
	const fit = $derived(estimate?.fit.max_repeats_under_cap ?? null);

	function listText(values: string[], fmt: (v: string) => string, max = 4): string {
		if (values.length === 0) return '';
		const head = values.slice(0, max).map(fmt).join(', ');
		return values.length > max ? `${head} +${values.length - max}` : head;
	}
</script>

{#if blocked}
	<div class="rv-blockers" tabindex="-1" bind:this={blockersEl} role="region" aria-labelledby="rv-blockers-title">
		<h3 id="rv-blockers-title" class="flex items-center gap-2 text-sm font-semibold">
			<CircleAlert class="size-4 text-danger-fg" aria-hidden="true" />
			{missing.length + errors.length === 1 ? 'One thing to do' : `${missing.length + errors.length} things to do`} before launching
		</h3>
		<ul class="mt-2 space-y-1">
			{#each missing as m (m.label)}
				<li>
					<button type="button" class="rv-blocker-link" onclick={() => onselect(m.step)}>
						{m.label}<span class="text-muted-foreground"> · {label.get(m.step)}</span>
					</button>
				</li>
			{/each}
			{#each errors as e, i (`${e.code}-${i}`)}
				<li class="text-xs">
					<span class="font-medium">{e.detail}</span>
				</li>
			{/each}
		</ul>
	</div>
{:else}
	<div class="rv-ready" role="status">
		<CircleCheck class="size-4 shrink-0 text-success-fg" aria-hidden="true" />
		<p class="text-sm">
			<span class="font-medium">Ready to launch.</span>
			<span class="text-muted-foreground">Launching freezes this exact plan; the cost is confirmed in the next dialog.</span>
		</p>
	</div>
{/if}

<section aria-labelledby="rv-design-title" class="space-y-2.5">
	<h3 id="rv-design-title" class="rv-h">Design</h3>
	<div class="rv-grid">
		{#snippet card(step: StepId, rows: [string, string][])}
			<div class="rv-card">
				<div class="rv-card-head">
					<p class="rv-card-title">{label.get(step)}</p>
					<button type="button" class="rv-edit" onclick={() => onselect(step)} aria-label="Edit {label.get(step)}">
						<Pencil class="size-3" aria-hidden="true" />Edit
					</button>
				</div>
				<dl class="rv-dl">
					{#each rows as [k, v] (k)}
						<dt>{k}</dt>
						<dd class={cn(!v && 'text-muted-foreground')}>{v || '—'}</dd>
					{/each}
				</dl>
			</div>
		{/snippet}
		{@render card('setup', [
			['Name', b.meta.name.trim() || 'Untitled experiment'],
			['Mode', MODE_LABELS[design.mode]],
			['Kind', KIND_LABELS[design.kind]],
			['Hypothesis', b.meta.hypothesis.trim()],
		])}
		{@render card('models', [
			['Chosen', models.length ? `${models.length}` : ''],
			['Which', listText(models, modelName, 5)],
		])}
		{@render card('units', [
			['Chosen', units.length ? `${units.length}` : ''],
			['Which', design.mode === 'app' ? listText(units, refLabel, 5) : plural(units.length, 'target')],
		])}
		{@render card(
			'treatments',
			treatments.length
				? treatments.map((f) => [FACTOR_BY_KEY[f.key]?.label ?? f.key, `${listText(f.levels, refLabel, 3)}${f.levels.length > 1 ? ` (${f.role})` : ''}`] as [string, string])
				: [['Factors', `defaults (${Object.keys(MODE_DEFAULTS[design.mode]).length})`]],
		)}
		{@render card('replication', [
			['Repeats', `${design.repeats}`],
			['Budget preset', refLabel(design.resource_policy)],
			['Seeds', `${design.base_seed} / ${design.order_seed}`],
		])}
		{@render card('measurement', [
			['Evaluation', `${design.evaluation.eval_level} · ${refLabel(design.evaluation.eval_suite)}`],
			['Analysis', design.kind === 'confirmatory' ? `${analysisPlan?.primary_metric ?? '—'} · α ${analysisPlan?.alpha ?? 0.05} · ${analysisPlan?.correction ?? 'holm'}` : 'exploratory'],
			['After the runs', [postRun.tool_slugs.length ? plural(postRun.tool_slugs.length, 'analyzer') : '', postRun.report ? 'report' : ''].filter(Boolean).join(' + ') || 'nothing'],
		])}
	</div>
</section>

<section aria-labelledby="rv-plan-title" class="space-y-3">
	<div class="flex flex-wrap items-baseline justify-between gap-2">
		<h3 id="rv-plan-title" class="rv-h">Plan</h3>
		<p class="font-mono text-[11px] text-muted-foreground">
			{#if b.planLoading}resolving…{:else if fresh && plan}plan {plan.plan_hash.slice(0, 12)} · resolver v{plan.resolver_version}{:else if missing.length}resolves once the required choices are made{:else}out of date{/if}
		</p>
	</div>
	<div class={cn('rv-kpis', !fresh && 'opacity-60')}>
		<div class="rv-kpi">
			<p class="kpi-label">Conditions</p>
			<p class="rv-kpi-v">{nConditions}</p>
		</div>
		<div class="rv-kpi">
			<p class="kpi-label">{design.mode === 'app' ? 'Tasks' : 'Targets'} × repeats</p>
			<p class="rv-kpi-v">{nUnits} × {design.repeats}</p>
		</div>
		<div class="rv-kpi rv-kpi-main">
			<p class="kpi-label">Cells</p>
			<p class="rv-kpi-v">{nCells.toLocaleString()}</p>
		</div>
		<div class="rv-kpi">
			<p class="kpi-label">Expected</p>
			<p class="rv-kpi-v">{totals ? formatUsd(totals.expected) : '—'}</p>
		</div>
		<div class="rv-kpi">
			<p class="kpi-label">p90</p>
			<p class="rv-kpi-v">{totals ? formatUsd(totals.p90) : '—'}</p>
		</div>
		<div class="rv-kpi">
			<p class="kpi-label">Wall time</p>
			<p class="rv-kpi-v text-base">{estimate?.time ? formatSecondsRange(estimate.time.expected_wall_s_low, estimate.time.expected_wall_s_high) : '—'}</p>
		</div>
	</div>
	{#if estimate && totals}
		<p class="text-xs text-muted-foreground">
			Cap {estimate.cap_usd == null ? 'not set' : formatUsd(estimate.cap_usd)}{totals.basis === 'list' ? ' at list price' : ''} · worst case {formatUsd(estimate.worst_case_total)}
			{#if fit !== null}· {fit < design.repeats ? `only ${fit} of ${design.repeats} repeats fit under the cap` : `all ${design.repeats} repeats fit under the cap`}{/if}
			{#if plan?.n_cells_by_stage.pilot}· the pilot runs {plural(plan.n_cells_by_stage.pilot, 'cell')} first{/if}
			{#if plan?.unbalanced}· unbalanced grid{/if}
			· estimate from {estimate.method}
		</p>
		<CostByLevelBar byLevel={estimate.by_level} factorLabel={(f) => FACTOR_BY_KEY[f as keyof typeof FACTOR_BY_KEY]?.label ?? f} labelOf={(f, l) => (f === 'model' ? modelName(l) : refLabel(l))} />
	{/if}
</section>

{#if issues.length > 0}
	<section aria-labelledby="rv-issues-title" class="space-y-2.5">
		<h3 id="rv-issues-title" class="rv-h">Issues</h3>
		<SummaryIssues {issues} {onfix} oncell={b.studyId ? oncell : undefined} />
		{#if fixError}<p class="text-xs text-danger-fg" role="alert">{fixError}</p>{/if}
	</section>
{/if}

{#if sample.length > 0 && b.studyId}
	<section aria-labelledby="rv-cells-title" class="space-y-2.5">
		<div>
			<h3 id="rv-cells-title" class="rv-h">Inspect a cell</h3>
			<p class="text-xs text-muted-foreground">Exactly what one sample will be generated from: rendered prompts, pinned versions, seed. Prompts can be edited from there.</p>
		</div>
		<ul class="rv-cells">
			{#each sample as cell (cell.cell_key)}
				<li>
					<button
						type="button"
						class={cn('rv-cell', cell.excluded && 'rv-cell-excluded')}
						title="{cell.unit_key} · repeat {cell.repeat_index} · seed {cell.seed}{cell.excluded ? ' · excluded (config invalid)' : ''}"
						onclick={() => oncell(cell.cell_key)}
					>
						<FileSearch class="size-3.5 shrink-0 text-muted-foreground" aria-hidden="true" />
						<span class="min-w-0 flex-1">
							<span class="block truncate font-mono">{cell.cell_key}</span>
							<span class="rv-cell-sub">{refLabel(cell.unit_key.replace(/^task:/, ''))} · repeat {cell.repeat_index}</span>
						</span>
					</button>
				</li>
			{/each}
		</ul>
	</section>
{/if}

<style>
	.rv-h {
		font-family: var(--font-display);
		font-size: 1.0625rem;
		font-weight: 600;
	}
	.rv-blockers {
		padding: 0.75rem 0.875rem;
		border: 1px solid color-mix(in oklab, var(--destructive) 40%, transparent);
		border-radius: var(--radius);
		background: color-mix(in oklab, var(--destructive) 7%, transparent);
	}
	.rv-blockers:focus-visible,
	.rv-blockers:focus {
		outline: 2px solid var(--ring);
		outline-offset: 2px;
	}
	.rv-blocker-link {
		cursor: pointer;
		font-size: 0.75rem;
		font-weight: 500;
		text-decoration: underline;
		text-decoration-style: dotted;
		text-underline-offset: 3px;
	}
	.rv-blocker-link:hover {
		text-decoration-style: solid;
	}
	.rv-ready {
		display: flex;
		align-items: flex-start;
		gap: 0.5rem;
		padding: 0.625rem 0.875rem;
		border: 1px solid color-mix(in oklab, var(--success) 40%, transparent);
		border-radius: var(--radius);
		background: color-mix(in oklab, var(--success) 8%, transparent);
	}
	.rv-grid {
		display: grid;
		gap: 0.625rem;
		grid-template-columns: repeat(auto-fill, minmax(17rem, 1fr));
	}
	.rv-card {
		min-width: 0;
		border: 1px solid var(--border);
		border-radius: var(--radius);
		background: color-mix(in oklab, var(--muted) 15%, var(--card));
	}
	.rv-card-head {
		display: flex;
		align-items: center;
		justify-content: space-between;
		padding: 0.375rem 0.375rem 0.375rem 0.75rem;
		border-bottom: 1px solid color-mix(in oklab, var(--border) 70%, transparent);
	}
	.rv-card-title {
		font-family: var(--font-mono);
		font-size: 0.6875rem;
		letter-spacing: 0.1em;
		text-transform: uppercase;
		color: var(--muted-foreground);
	}
	.rv-edit {
		display: inline-flex;
		min-height: 1.75rem;
		cursor: pointer;
		align-items: center;
		gap: 0.25rem;
		border-radius: calc(var(--radius) - 2px);
		padding-inline: 0.5rem;
		font-size: 0.75rem;
		color: var(--primary);
		transition: background-color 150ms;
	}
	.rv-edit:hover {
		background: color-mix(in oklab, var(--primary) 10%, transparent);
	}
	.rv-edit:focus-visible {
		outline: 2px solid var(--ring);
	}
	.rv-dl {
		display: grid;
		grid-template-columns: auto minmax(0, 1fr);
		gap: 0.25rem 0.75rem;
		padding: 0.5rem 0.75rem 0.625rem;
		font-size: 0.75rem;
	}
	.rv-dl dt {
		color: var(--muted-foreground);
		white-space: nowrap;
	}
	.rv-dl dd {
		min-width: 0;
		overflow-wrap: anywhere;
	}
	.rv-kpis {
		display: grid;
		gap: 0.5rem;
		grid-template-columns: repeat(auto-fill, minmax(8.5rem, 1fr));
		transition: opacity 200ms;
	}
	.rv-kpi {
		border: 1px solid var(--border);
		border-radius: var(--radius);
		background: var(--surface-2);
		padding: 0.5rem 0.75rem;
	}
	.rv-kpi-main {
		border-color: color-mix(in oklab, var(--primary) 45%, var(--border));
		background: color-mix(in oklab, var(--primary) 9%, var(--surface-2));
	}
	.rv-kpi-v {
		margin-top: 0.125rem;
		font-family: var(--font-mono);
		font-size: 1.25rem;
		font-weight: 500;
		font-variant-numeric: tabular-nums;
		white-space: nowrap;
	}
	.rv-kpi-main .rv-kpi-v {
		color: var(--primary);
	}
	.rv-cells {
		display: grid;
		gap: 0.375rem;
		grid-template-columns: repeat(auto-fill, minmax(14rem, 1fr));
	}
	.rv-cell {
		display: flex;
		width: 100%;
		min-height: 2.75rem;
		cursor: pointer;
		align-items: center;
		gap: 0.5rem;
		border: 1px solid var(--border);
		border-radius: calc(var(--radius) - 1px);
		padding: 0.375rem 0.625rem;
		font-size: 0.75rem;
		text-align: left;
		transition: border-color 150ms;
	}
	.rv-cell:hover {
		border-color: var(--primary);
	}
	.rv-cell:focus-visible {
		outline: 2px solid var(--ring);
		outline-offset: 1px;
	}
	.rv-cell-excluded {
		border-style: dashed;
		color: var(--muted-foreground);
	}
	.rv-cell-sub {
		display: block;
		overflow: hidden;
		text-overflow: ellipsis;
		white-space: nowrap;
		font-size: 0.6875rem;
		color: var(--muted-foreground);
	}
</style>
