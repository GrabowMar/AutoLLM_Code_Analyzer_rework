<script lang="ts">
	/**
	 * MethodSection — section 2 of the study record (spec 10.3): the read-only design (factors with their
	 * pinned levels, units), the evaluation level, the analysis plan, the plan summary and the paginated
	 * cells table with the cell inspector, plus Clone (exact pins, or re-pinned to the latest versions).
	 * Execution limits (budget, parallelism) are not part of the method: they are in Operations.
	 *
	 * Props: `study`, `conditions` (`{index, label}`, e.g. from the results), `number`.
	 */
	import type { FactorKey, StudyOut } from '$lib/api/v3/types';
	import { Button } from '$lib/components/ui/button';
	import { PinnedRefBadge } from '$lib/components/library';
	import StudySection from '../study/StudySection.svelte';
	import StudyFigure from '../study/StudyFigure.svelte';
	import { FACTOR_BY_KEY } from '../builder/design';
	import { formatUsd, plural, refLabel, shortHash } from '../format';
	import PlanCellsTable from './PlanCellsTable.svelte';
	import Copy from '@lucide/svelte/icons/copy';

	interface Props {
		study: StudyOut;
		conditions?: { index: number; label: string }[];
		number?: string;
	}

	let { study, conditions = [], number = '2' }: Props = $props();

	const design = $derived(study.design);
	const plan = $derived(study.active_plan);
	const units = $derived(
		design.mode === 'app'
			? design.units.tasks.map((t, i) => ({ index: i, label: refLabel(t), ref: t }))
			: design.units.slot_targets.map((t, i) => ({ index: i, label: t.slice(0, 8), ref: t })),
	);
	const analysisPlan = $derived((study.analysis_plan ?? null) as Record<string, unknown> | null);
	const source = (key: string) => FACTOR_BY_KEY[key as FactorKey]?.source;
	const fmt = (v: unknown) => (Array.isArray(v) ? v.join(', ') || '—' : v === null || v === undefined || v === '' ? '—' : typeof v === 'object' ? JSON.stringify(v) : String(v));
</script>

<StudySection id="method" {number} title="Method">
	{#snippet aside()}
		<div class="flex gap-2">
			<Button variant="outline" size="sm" href="/studies/new?clone={study.id}"><Copy />Clone</Button>
			<Button variant="ghost" size="sm" href="/studies/new?clone={study.id}&clone_mode=latest_versions">Clone with latest versions</Button>
		</div>
	{/snippet}

	<StudyFigure id="table-m1" kind="table" number="M1" title="Factors and their levels" flush>
		<div class="overflow-x-auto">
			<table>
				<thead><tr><th>Factor</th><th>Role</th><th>Levels</th></tr></thead>
				<tbody>
					{#each study.factors.length ? study.factors : design.factors.map((f, i) => ({ ...f, position: i })) as f (f.key)}
						{@const s = source(f.key)}
						<tr>
							<td>{FACTOR_BY_KEY[f.key]?.label ?? f.key}</td>
							<td>{f.role}</td>
							<td>
								<span class="flex flex-wrap gap-1.5">
									{#each f.levels as level (level)}
										{#if s?.type === 'library'}<PinnedRefBadge ref={level} kind={s.kind} />{:else}<span class="font-mono text-xs">{level}</span>{/if}
									{/each}
								</span>
							</td>
						</tr>
					{/each}
				</tbody>
			</table>
		</div>
		{#snippet caption()}
			Factors left out use the mode defaults. {design.grid.type === 'fractional' ? `Fractional grid ${design.grid.fraction?.generator ?? ''}.` : 'Full grid.'}
			{#if design.grid.exclude.length}{plural(design.grid.exclude.length, 'combination')} excluded by design.{/if}
		{/snippet}
	</StudyFigure>

	<StudyFigure id="table-m2" kind="table" number="M2" title={design.mode === 'app' ? 'Tasks (units)' : 'Slot targets (units)'}>
		<ul class="flex flex-wrap gap-1.5">
			{#each units as u (u.ref)}
				<li>{#if design.mode === 'app'}<PinnedRefBadge ref={u.ref} kind="task" />{:else}<a class="font-mono text-xs text-primary hover:underline" href="/slots/targets/{u.ref}">{u.label}</a>{/if}</li>
			{/each}
		</ul>
		{#snippet caption()}
			{plural(study.repeats, 'repeat')} per (condition, unit); base seed <span class="font-mono">{design.base_seed}</span>, order seed <span class="font-mono">{design.order_seed}</span>.
			Evaluation level <b>{design.evaluation.eval_level}</b> with {design.evaluation.eval_suite.split('#')[0]}{Object.keys(design.evaluation.overrides ?? {}).length ? ' (with overrides)' : ''}; whatever a level skips is reported as not measured.
		{/snippet}
	</StudyFigure>

	{#if analysisPlan}
		<StudyFigure id="table-m3" kind="table" number="M3" title="Analysis plan{study.locked ? ' (pre-registered)' : ''}">
			<dl class="grid gap-x-6 gap-y-1 text-xs sm:grid-cols-[12rem_1fr]">
				{#each ['primary_metric', 'secondary_metrics', 'alpha', 'correction', 'multiplicity', 'estimand', 'random_effects', 'itt', 'sensitivity', 'sequential', 'patterns'] as key (key)}
					{#if key in analysisPlan}
						<dt class="text-muted-foreground">{key.replace(/_/g, ' ')}</dt>
						<dd class="font-mono break-words">{fmt(analysisPlan[key])}</dd>
					{/if}
				{/each}
			</dl>
			{#snippet caption()}
				{#if study.prereg_hash}prereg_hash <span class="font-mono">{shortHash(study.prereg_hash, 16)}</span> · {/if}analysis plan hash <span class="font-mono">{shortHash(study.analysis_plan_hash, 16)}</span>
			{/snippet}
		</StudyFigure>
	{/if}

	{#if plan}
		<StudyFigure id="table-m4" kind="table" number="M4" title="Resolved plan: every planned cell" flush>
			<PlanCellsTable studyId={study.id} planHash={plan.plan_hash} {conditions} units={units.map(({ index, label }) => ({ index, label }))} />
			{#snippet caption()}
				Plan <span class="font-mono">{shortHash(plan.plan_hash, 16)}</span> (resolver v{plan.resolver_version}): {plural(plan.n_conditions, 'condition')}, {plural(plan.n_units, 'unit')},
				{plural(plan.n_cells, 'cell')} in {plural(plan.n_blocks, 'block')}. {#if plan.estimate?.cap_basis === 'list'}Estimated {formatUsd(plan.estimate.expected_list_total)} at list price (p90 {formatUsd(plan.estimate.p90_list_total)}; billed {formatUsd(plan.estimate.expected_total)}).{:else}Estimated {formatUsd(plan.estimate?.expected_total)} (p90 {formatUsd(plan.estimate?.p90_total)}).{/if}
			{/snippet}
		</StudyFigure>
	{:else}
		<p class="text-xs text-muted-foreground">No plan has been frozen yet.</p>
	{/if}
</StudySection>
