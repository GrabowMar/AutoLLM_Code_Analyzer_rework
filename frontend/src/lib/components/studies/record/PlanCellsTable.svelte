<script lang="ts">
	/**
	 * PlanCellsTable — the cells of a persisted plan (spec 10.3 §2): paginated, filterable by condition
	 * and unit, each linking to the cell inspector (`/studies/[id]/cells/[cell]`).
	 *
	 * Props: `studyId`, `planHash`, `conditions` / `units` (filter options: `{index, label}`).
	 */
	import { untrack } from 'svelte';
	import { listPlanCells } from '$lib/api/v3/studies';
	import type { Page, ResolvedCellOut } from '$lib/api/v3/types';
	import { formatApiError } from '$lib/api/core';
	import * as Select from '$lib/components/ui/select';
	import ErrorAlert from '$lib/components/ErrorAlert.svelte';
	import LoadingState from '$lib/components/LoadingState.svelte';
	import PaginationBar from '$lib/components/PaginationBar.svelte';
	import { TableToolbar, rowLink, tableFrame } from '$lib/components/table';
	import { formatUsd, shortHash } from '../format';

	interface Props {
		studyId: string;
		planHash: string;
		conditions?: { index: number; label: string }[];
		units?: { index: number; label: string }[];
	}

	let { studyId, planHash, conditions = [], units = [] }: Props = $props();

	const PAGE_SIZE = 50;
	let condition = $state('');
	let unit = $state('');
	let pageNum = $state(1);
	let data = $state<Page<ResolvedCellOut> | null>(null);
	let error = $state('');
	let seq = 0;

	async function load() {
		const mine = ++seq;
		try {
			const out = await listPlanCells(studyId, planHash, {
				condition: condition === '' ? undefined : Number(condition),
				unit: unit === '' ? undefined : Number(unit),
				page: pageNum,
				page_size: PAGE_SIZE,
			});
			if (mine === seq) {
				data = out;
				error = '';
			}
		} catch (e) {
			if (mine === seq) error = formatApiError(e, 'Could not load the cells.');
		}
	}

	$effect(() => {
		void planHash;
		void condition;
		void unit;
		void pageNum;
		untrack(() => load());
	});

	const pages = $derived(data ? Math.max(1, Math.ceil(data.total / data.page_size)) : 1);
	const href = (c: ResolvedCellOut) => `/studies/${studyId}/cells/${c.cell_key}?plan=${planHash}`;
	const condLabel = (i: number) => conditions.find((c) => c.index === i)?.label ?? `c${i}`;
</script>

<div class="space-y-0">
	<TableToolbar density={false}>
		{#snippet summary()}{data?.total ?? 0} cells{/snippet}
		{#snippet controls()}
			<Select.Simple
				id="cells-condition"
				size="sm"
				value={condition}
				items={[{ value: '', label: 'Every condition' }, ...conditions.map((c) => ({ value: String(c.index), label: c.label }))]}
				onValueChange={(v) => {
					condition = v;
					pageNum = 1;
				}}
			/>
			<Select.Simple
				id="cells-unit"
				size="sm"
				value={unit}
				items={[{ value: '', label: 'Every unit' }, ...units.map((u) => ({ value: String(u.index), label: u.label }))]}
				onValueChange={(v) => {
					unit = v;
					pageNum = 1;
				}}
			/>
		{/snippet}
	</TableToolbar>
	{#if error}
		<ErrorAlert message={error} onretry={load} />
	{:else if !data}
		<LoadingState label="loading cells" />
	{:else}
		<div class="table-frame" {@attach tableFrame}>
			<table>
				<thead><tr><th>Cell</th><th>Condition</th><th>Unit</th><th class="num">Repeat</th><th>Stage</th><th class="num">Block</th><th class="num">Seed</th><th>Fingerprint</th><th class="num">Est.</th></tr></thead>
				<tbody>
					{#each data.items as c (c.cell_key)}
						<tr class="row-link" onclick={rowLink(href(c))}>
							<td><a class="font-mono text-xs text-primary hover:underline" href={href(c)}>{c.cell_key}</a></td>
							<td class="max-w-48 truncate">{condLabel(c.condition_index)}</td>
							<td class="max-w-40 truncate font-mono text-xs">{c.unit_key}</td>
							<td class="num">{c.repeat_index}</td>
							<td>{c.stage}</td>
							<td class="num">{c.block_index}</td>
							<td class="num">{c.seed}</td>
							<td class="font-mono text-xs">{shortHash(c.identity?.config_fingerprint, 10)}</td>
							<td class="num">{formatUsd(c.estimate?.expected_usd)}</td>
						</tr>
					{/each}
				</tbody>
			</table>
		</div>
		{#if pages > 1}<PaginationBar class="border-t border-border" page={pageNum} {pages} onGoToPage={(p) => (pageNum = p)} />{/if}
	{/if}
</div>
