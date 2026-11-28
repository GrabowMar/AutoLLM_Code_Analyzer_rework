<script lang="ts">
	/**
	 * ReportSelection — the "Sample selection" section of a study report built from the newest unique
	 * runs across experiments (`report_data.selection`, the final assessment): the rule in words, the
	 * filters, headline counts (the analysed sample: truncated and dropped runs are counted apart), notes on
	 * unequal task sets, complete cases and mixed Budget presets, then Figure S1 — the group × task matrix
	 * (`SelectionMatrix`).
	 *
	 * The selection's tables (coverage per group, experiments pooled, cells without a sampled run, superseded
	 * attempts, analysis results that count) are the server's "Sample selection" tables: they stay in the Tables
	 * section with the report-table toolbar (filter, sort, columns, CSV, Markdown), and this section links to
	 * them, so each table appears once.
	 *
	 * Props: `selection` (normalised by `reportSelection`), `tableLinks` (those tables' anchors and numbers,
	 * once the tables have loaded), `onCompleteCases` (regenerate with `common_tasks_only`; the button is hidden
	 * without it), `busy`.
	 */
	import TriangleAlert from '@lucide/svelte/icons/triangle-alert';
	import RotateCcw from '@lucide/svelte/icons/rotate-ccw';
	import LoaderCircle from '@lucide/svelte/icons/loader-circle';
	import type { ReportSelection } from '$lib/api/reports';
	import SectionHeading from '$lib/components/SectionHeading.svelte';
	import StudyFigure from '$lib/components/studies/study/StudyFigure.svelte';
	import { Button } from '$lib/components/ui/button';
	import SelectionMatrix from './SelectionMatrix.svelte';
	import { groupDisplay, modelShortLabels, selectionMatrix, taskRange } from './selection';

	interface Props {
		selection: ReportSelection;
		tableLinks?: { id: string; number: number; label: string }[];
		onCompleteCases?: () => void;
		busy?: boolean;
	}

	let { selection, tableLinks = [], onCompleteCases, busy = false }: Props = $props();

	const matrix = $derived(selectionMatrix(selection));
	const counts = $derived(selection.counts);
	const n = (v: number | undefined) => (typeof v === 'number' ? v : 0);
	const fmt = (v: number | undefined) => (typeof v === 'number' ? v.toLocaleString() : '—');

	const filterLines = $derived.by(() => {
		const f = selection.filters;
		const pooled = selection.studies.length;
		return [
			['Experiments', f.study_ids.length ? `${pooled} ticked` : `every experiment you can see (${pooled} with runs)`],
			['Models', f.models?.length ? [...modelShortLabels(f.models).values()].join(', ') : 'all'],
			['Tasks', f.tasks?.length ? `${f.tasks.length} picked` : 'all'],
			[
				'Design',
				(f.common_tasks_only ? 'complete cases: tasks every group has' : 'every task a group has') +
					(f.exclude_truncated ? '; truncated runs left out' : '')
			]
		] as [string, string][];
	});

	const leftOut = $derived(
		(
			[
				['pilot_excluded', 'pilot-stage'],
				['beyond_plan_excluded', 'beyond-plan replicates'],
				['reference_excluded', 'reference runs'],
				['superseded_plan_excluded', 'runs of a replaced plan'],
				['filtered_out', 'outside the model/task filters']
			] as const
		)
			.map(([k, label]) => [n(counts[k]), label] as const)
			.filter(([v]) => v > 0)
	);
	const truncated = $derived(n(counts.truncated_excluded));
	const keptTasks = $derived(matrix.tasks.length - selection.dropped_tasks.length);

	const showUnequal = $derived(matrix.unequalTasks && !selection.filters.common_tasks_only);
	const mixedPolicies = $derived(selection.groups.filter((g) => g.resource_policies.length > 1));
	// Groups with no run in the sample are absent from every result table (all truncated, all attrition…).
	const emptyGroups = $derived(matrix.groups.filter((g) => g.selected === 0));
	const blocking = $derived(selection.dropped_by_group ?? []);
	const reasonsText = (r: Record<string, number>) =>
		Object.entries(r)
			.map(([k, v]) => `${k.replace(/_/g, ' ')} ${v}`)
			.join(', ');
</script>

<section class="space-y-3" aria-labelledby="rs-heading" id="report-selection">
	<SectionHeading id="rs-heading" size="sm" title="Sample selection" count="{fmt(counts.selected)} runs" />

	<div class="space-y-3 rounded-md border border-border bg-card px-4 py-3.5 shadow-[var(--shadow-card)]">
		<p class="page-eyebrow">Newest unique runs · across experiments</p>
		{#if selection.rule}
			<p class="max-w-3xl font-display text-[15px] leading-relaxed">{selection.rule}</p>
		{/if}
		<dl class="grid gap-x-6 gap-y-1.5 text-sm sm:grid-cols-2 xl:grid-cols-4">
			{#each filterLines as [k, v] (k)}
				<div class="min-w-0">
					<dt class="section-label">{k}</dt>
					<dd class="truncate" title={v}>{v}</dd>
				</div>
			{/each}
		</dl>
	</div>

	<dl class="fig-strip" aria-label="Selection counts">
		<div class="fig-cell">
			<dt class="fig-label">Runs in the sample</dt>
			<dd class="fig-value">{fmt(counts.selected)}</dd>
			<dd class="fig-sub">of {fmt(counts.candidates)} candidate runs</dd>
		</div>
		<div class="fig-cell">
			<dt class="fig-label">Groups × tasks</dt>
			<dd class="fig-value">{matrix.groups.length} × {keptTasks}</dd>
			<dd class="fig-sub">{selection.common_tasks.length} tasks every group has</dd>
		</div>
		<div class="fig-cell">
			<dt class="fig-label">Superseded</dt>
			<dd class="fig-value">{fmt(counts.superseded)}</dd>
			<dd class="fig-sub">older or unusable attempts</dd>
		</div>
		{#if truncated > 0}
			<div class="fig-cell" data-tone="warn">
				<dt class="fig-label">Truncated, left out</dt>
				<dd class="fig-value">{fmt(truncated)}</dd>
				<dd class="fig-sub">selected runs excluded as truncated</dd>
			</div>
		{/if}
		<div class="fig-cell" data-tone={n(counts.attrition_cells) > 0 ? 'warn' : undefined}>
			<dt class="fig-label">Attrition</dt>
			<dd class="fig-value">{fmt(counts.attrition_cells)}</dd>
			<dd class="fig-sub">units with no usable run</dd>
		</div>
		<div class="fig-cell" data-tone={n(counts.missing_cells) > 0 ? 'warn' : undefined}>
			<dt class="fig-label">No attempt</dt>
			<dd class="fig-value">{fmt(counts.missing_cells)}</dd>
			<dd class="fig-sub">units never run</dd>
		</div>
		{#if n(counts.in_flight_cells) > 0}
			<div class="fig-cell" data-tone="warn">
				<dt class="fig-label">Still running</dt>
				<dd class="fig-value">{fmt(counts.in_flight_cells)}</dd>
				<dd class="fig-sub">units not finished yet</dd>
			</div>
		{/if}
	</dl>
	<p class="px-1 text-xs text-muted-foreground">
		{#if leftOut.length}
			Left out before selection:
			{#each leftOut as [v, label], i (label)}{i > 0 ? ' · ' : ' '}<b class="font-mono font-medium tabular-nums text-foreground">{v.toLocaleString()}</b> {label}{/each}.
		{:else}
			Nothing was left out before selection (no pilot-stage, beyond-plan or reference runs, no filter).
		{/if}
		{#if truncated > 0}
			After selection, <b class="font-mono font-medium tabular-nums text-foreground">{truncated.toLocaleString()}</b>
			truncated run{truncated === 1 ? ' was' : 's were'} left out (sensitivity analysis; no older attempt takes their place){selection.filters
				.common_tasks_only
				? ', before complete cases were computed'
				: ''}.
		{/if}
		{#if selection.dropped_tasks.length}
			Complete cases dropped <b class="font-mono font-medium tabular-nums text-foreground">{selection.dropped_tasks.length}</b>
			task{selection.dropped_tasks.length === 1 ? '' : 's'} not every group has{n(counts.dropped_runs) > 0
				? ` (with their ${n(counts.dropped_runs)} selected runs)`
				: ''}: <span class="font-mono">{selection.dropped_tasks.join(', ')}</span>.
		{/if}
	</p>

	{#if showUnequal || mixedPolicies.length || emptyGroups.length || blocking.length}
		<div role="note" class="space-y-1.5 rounded-md border border-warning/40 bg-warning/10 px-4 py-3 text-sm">
			{#if blocking.length}
				<p class="flex items-start gap-2">
					<TriangleAlert class="mt-0.5 size-4 shrink-0 text-warning-fg" aria-hidden="true" />
					<span>
						{#if keptTasks === 0}
							<b class="font-medium">Complete cases left no task</b>: no task has a run in the sample for every group, so the
							sample is empty. Leave the blocking groups out with the Models filter, or turn complete cases off.
						{:else}
							<b class="font-medium">Complete cases kept {keptTasks} of {matrix.tasks.length} tasks.</b>
						{/if}
						Groups without a run on the dropped tasks:
						{#each blocking as b, i (b.group)}{i > 0 ? '; ' : ' '}<span title={b.group}>{groupDisplay(b.group).name}</span>
							{b.tasks} ({reasonsText(b.reasons)}){/each}.
					</span>
				</p>
			{/if}
			{#if emptyGroups.length && keptTasks > 0}
				<p class="flex items-start gap-2">
					<TriangleAlert class="mt-0.5 size-4 shrink-0 text-warning-fg" aria-hidden="true" />
					<span>
						<b class="font-medium">No run in the sample</b> for {emptyGroups.map((g) => g.name).join(', ')}: absent from every result
						table and from the ranking.
					</span>
				</p>
			{/if}
			{#if showUnequal}
				<p class="flex items-start gap-2">
					<TriangleAlert class="mt-0.5 size-4 shrink-0 text-warning-fg" aria-hidden="true" />
					<span>
						<b class="font-medium">Groups cover different task sets</b> ({taskRange(matrix.groups.filter((g) => g.selected > 0))} tasks each).
						Means, rates and rankings then compare unequal task sets;
						{#if selection.common_tasks.length}
							with complete cases every group is compared on the {selection.common_tasks.length} tasks they all have.
						{:else}
							no task has a run in the sample for every group, so complete cases would leave nothing: leave groups out with the Models
							filter first.
						{/if}
					</span>
				</p>
				{#if onCompleteCases && selection.common_tasks.length}
					<div class="pl-6">
						<Button size="sm" variant="outline" onclick={onCompleteCases} disabled={busy}>
							{#if busy}<LoaderCircle class="size-3.5 animate-spin motion-reduce:animate-none" />{:else}<RotateCcw class="size-3.5" />{/if}
							Regenerate with complete cases
						</Button>
					</div>
				{/if}
			{/if}
			{#if mixedPolicies.length}
				<p class="flex items-start gap-2">
					<TriangleAlert class="mt-0.5 size-4 shrink-0 text-warning-fg" aria-hidden="true" />
					<span>
						<b class="font-medium">Mixed Budget presets</b> within
						{mixedPolicies.map((g) => groupDisplay(g.group).name).join(', ')}: the selected runs of one group ran under different limits.
					</span>
				</p>
			{/if}
		</div>
	{/if}

	<StudyFigure id="report-selection-matrix" number="S1" title="The run that counts for each group and task">
		{#snippet controls()}
			<span class="font-mono text-[11px] text-muted-foreground tabular-nums">{matrix.groups.length} × {matrix.tasks.length}</span>
		{/snippet}
		{#if matrix.groups.length === 0 || matrix.tasks.length === 0}
			<p class="text-sm text-muted-foreground">No group or task in scope: the pooled experiments have no main-stage runs under these filters.</p>
		{:else}
			<SelectionMatrix {selection} {matrix} id="rs-matrix" />
		{/if}
		{#snippet caption()}
			One cell per group and task: the newest usable run (by creation time), pooled across the experiments and conditions of the
			group. A newer attempt that failed for infrastructure reasons or was excluded never replaces a usable run. Column labels drop the
			shared version{matrix.version ? ` (${matrix.version})` : ''}; the row ends count each group's runs in the sample and the bottom
			row the groups with a run in the sample, below {matrix.groups.length} when not every group has the task.
		{/snippet}
	</StudyFigure>

	{#if tableLinks.length}
		<nav aria-label="Sample selection tables" class="px-1 text-xs text-muted-foreground">
			<span>The selection in detail, with filter, sort and CSV, in the tables below:</span>
			<ul class="mt-1 flex flex-wrap gap-x-3 gap-y-1">
				{#each tableLinks as t (t.id)}
					<li>
						<a class="settings-link" href="#{t.id}"><span class="font-mono tabular-nums">Table {t.number}</span> {t.label}</a>
					</li>
				{/each}
			</ul>
		</nav>
	{/if}
</section>
