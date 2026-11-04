<script lang="ts">
	// The resolved-cell inspector (spec 10.1): stimuli with their source spans, parameters with their knob
	// classes, identity keys and the estimate of one planned cell (`?plan=<hash>`, default: the active
	// plan), plus the runs of that cell (current run, attempts, beyond-plan runs). Prompt edits are only
	// possible while the study is a draft (from the builder's drawer).
	import { untrack } from 'svelte';
	import { page } from '$app/state';
	import PageHeader from '$lib/components/PageHeader.svelte';
	import ErrorAlert from '$lib/components/ErrorAlert.svelte';
	import LoadingState from '$lib/components/LoadingState.svelte';
	import StatusBadge from '$lib/components/StatusBadge.svelte';
	import * as Card from '$lib/components/ui/card';
	import { formatApiError } from '$lib/api/core';
	import { getStudy, getStudyCells, listPlanCells } from '$lib/api/v3/studies';
	import type { ResolvedCellOut, StudyCellOut, StudyOut } from '$lib/api/v3/types';
	import CellDetailView from '$lib/components/studies/builder/CellDetailView.svelte';
	import { formatUsd, outcomeLabel, runTone } from '$lib/components/studies/format';

	const studyId = $derived(page.params.id ?? '');
	const cellKey = $derived(page.params.cell ?? '');
	let study = $state<StudyOut | null>(null);
	let planned = $state<ResolvedCellOut | null>(null);
	let siblings = $state<string[]>([]);
	let cell = $state<StudyCellOut | null>(null);
	let error = $state('');

	const planHash = $derived(page.url.searchParams.get('plan') || study?.active_plan?.plan_hash || '');
	const KEY_RE = /^c(\d+)-u(\d+)-r(\d+)-(pilot|main)$/;

	async function load() {
		error = '';
		try {
			study = await getStudy(studyId);
			const hash = page.url.searchParams.get('plan') || study.active_plan?.plan_hash;
			const m = KEY_RE.exec(cellKey);
			if (!hash || !m) return;
			const [condition, unit] = [Number(m[1]), Number(m[2])];
			const page1 = await listPlanCells(studyId, hash, { condition, unit, page_size: 200 });
			planned = page1.items.find((c) => c.cell_key === cellKey) ?? null;
			siblings = page1.items.map((c) => c.cell_key);
			if (planned && study.status !== 'draft') {
				const cells = await getStudyCells(studyId, { stage: planned.stage });
				cell =
					cells.cells.find(
						(c) => c.condition_index === condition && c.unit_key === planned!.unit_key && c.repeat_index === planned!.repeat_index && c.stage === planned!.stage,
					) ?? null;
			}
		} catch (e) {
			error = formatApiError(e, 'Could not load the cell.');
		}
	}

	$effect(() => {
		void studyId;
		void cellKey;
		untrack(() => load());
	});
</script>

<div class="space-y-4">
	<PageHeader
		title="Cell {cellKey}"
		description={planned ? `${planned.unit_key} · repeat ${planned.repeat_index} · ${planned.stage} · seed ${planned.seed} · block ${planned.block_index}` : 'One planned sample, fully pinned.'}
		back={{ href: `/experiments/${studyId}?tab=method`, label: study?.name ?? 'Experiment' }}
	/>
	{#if error}<ErrorAlert message={error} onretry={load} />{/if}

	{#if cell}
		<Card.Root>
			<Card.Header><Card.Title>Runs of this cell</Card.Title></Card.Header>
			<Card.Content class="flex flex-wrap items-center gap-3 text-xs">
				<StatusBadge status={cell.state} />
				{#if cell.current_run}
					<a class="font-mono text-primary hover:underline" href="/samples/{cell.current_run.id}">current run {cell.current_run.id.slice(0, 8)}</a>
					<StatusBadge status={cell.current_run.outcome || cell.current_run.status} tone={runTone(cell.current_run.outcome || cell.current_run.status)} label={cell.current_run.outcome ? outcomeLabel(cell.current_run.outcome) : cell.current_run.status} />
				{:else}
					<span class="text-muted-foreground">no run yet</span>
				{/if}
				<span class="text-muted-foreground">{cell.attempts} attempt(s){cell.beyond_plan_runs ? ` · ${cell.beyond_plan_runs} beyond plan (excluded from the analysis)` : ''}</span>
				<span class="text-muted-foreground">spent {formatUsd(cell.spent_usd)} of the {formatUsd(cell.cell_cap_usd)} cell cap</span>
				{#if cell.exclusion_reason}<span class="text-warning-fg">excluded: {cell.exclusion_reason.replace(/_/g, ' ')}</span>{/if}
				{#if cell.adaptive_n}<span class="text-warning-fg">adaptive n (conservative intervals)</span>{/if}
			</Card.Content>
		</Card.Root>
	{/if}

	{#if !study && !error}
		<LoadingState label="loading the cell" />
	{:else if study && planHash}
		<Card.Root>
			<Card.Content class="pt-4">
				<CellDetailView {studyId} {planHash} {cellKey} compareKeys={siblings} />
			</Card.Content>
		</Card.Root>
	{:else if study}
		<p class="text-sm text-muted-foreground">This experiment has no frozen plan yet. Open its cells from the builder's plan rail.</p>
	{/if}
</div>
