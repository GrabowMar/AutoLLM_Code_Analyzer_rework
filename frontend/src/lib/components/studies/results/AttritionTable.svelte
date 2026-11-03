<script lang="ts">
	/**
	 * AttritionTable — per condition: planned cells, runs per outcome, usable, truncated (kept: ITT),
	 * excluded by reason, infra errors and retries, beyond-plan runs and the attrition rate (spec 6.13).
	 * Rates above the plan's threshold are flagged; a missing rate is "—".
	 *
	 * Props: `rows` (`AttritionRow[]`), `over` (condition ids above the threshold).
	 */
	import type { AttritionRow } from '$lib/api/v3/types';
	import { formatPct, outcomeLabel } from '../format';

	interface Props {
		rows: AttritionRow[];
		over?: number[];
	}

	let { rows, over = [] }: Props = $props();
	const outcomes = $derived([...new Set(rows.flatMap((r) => Object.keys(r.runs_by_outcome)))].sort());
</script>

<div class="overflow-x-auto rounded-md border border-border">
	<table>
		<thead>
			<tr>
				<th class="col-sticky">Condition</th>
				<th class="num">Planned</th>
				{#each outcomes as o (o)}<th class="num">{outcomeLabel(o)}</th>{/each}
				<th class="num">Usable</th>
				<th class="num" title="Truncated runs stay in the main (ITT) analysis">Truncated</th>
				<th class="num">Excluded</th>
				<th class="num">Infra</th>
				<th class="num">Retries</th>
				<th class="num">Beyond plan</th>
				<th class="num">Attrition</th>
			</tr>
		</thead>
		<tbody>
			{#each rows as r (r.condition_id)}
				<tr>
					<td class="col-sticky max-w-56 truncate" title={r.label}>{r.label || `condition ${r.condition_id}`}</td>
					<td class="num">{r.planned_cells}</td>
					{#each outcomes as o (o)}<td class="num">{r.runs_by_outcome[o] ?? 0}</td>{/each}
					<td class="num">{r.usable}</td>
					<td class="num" title={Object.entries(r.truncated_by_kind).map(([k, n]) => `${k}: ${n}`).join(', ')}>{r.truncated}</td>
					<td class="num" title={Object.entries(r.excluded_by_reason).map(([k, n]) => `${k}: ${n}`).join(', ')}>{r.excluded}</td>
					<td class="num">{r.infra_error}</td>
					<td class="num">{r.infra_retries}</td>
					<td class="num">{r.beyond_plan_runs}</td>
					<td class="num {over.includes(r.condition_id) ? 'text-warning-fg' : ''}">{formatPct(r.attrition_rate, 1)}</td>
				</tr>
			{/each}
		</tbody>
	</table>
</div>
