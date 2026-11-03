<script lang="ts">
	/**
	 * ConditionGrid — every cell of the active plan as a square, condition × unit × repeat (spec 10.3 §6),
	 * coloured by its state and, once done, by the run's outcome. Shape carries meaning as well as hue:
	 * solid = finished, ring = waiting, pulsing = running; every square has a label. Clicking a square
	 * opens its current run (or the planned cell when it has none). Replicate cells (`beyond_plan`, added
	 * after launch) carry a dot and a "replicate, not in the planned sample" label, and the legend counts
	 * them apart from the planned sample.
	 *
	 * Props: `cells` (`StudyCellsPayload`), `conditionLabel(conditionId)`, `stage`.
	 */
	import type { StudyCellOut, StudyCellsPayload } from '$lib/api/v3/types';
	import { cellTone, toneClasses } from '$lib/constants/colors';
	import { cn } from '$lib/utils';
	import { outcomeLabel } from '../format';

	interface Props {
		cells: StudyCellsPayload;
		conditionLabel: (conditionId: number) => string;
		stage?: 'pilot' | 'main';
	}

	let { cells, conditionLabel, stage = 'main' }: Props = $props();

	const rows = $derived.by(() => {
		const byCondition = new Map<number, Map<string, StudyCellOut[]>>();
		const order = new Map<number, number>();
		for (const cell of cells.cells) {
			if (cell.stage !== stage) continue;
			order.set(cell.condition_id, cell.condition_index);
			let units = byCondition.get(cell.condition_id);
			if (!units) byCondition.set(cell.condition_id, (units = new Map()));
			const list = units.get(cell.unit_key) ?? [];
			list.push(cell);
			units.set(cell.unit_key, list);
		}
		return [...byCondition.entries()]
			.sort((a, b) => (order.get(a[0]) ?? 0) - (order.get(b[0]) ?? 0))
			.map(([conditionId, units]) => ({
				conditionId,
				units: [...units.entries()].sort((a, b) => a[0].localeCompare(b[0])).map(([unit, list]) => ({ unit, list: list.sort((x, y) => x.repeat_index - y.repeat_index) })),
			}));
	});

	const replicates = $derived(cells.cells.filter((c) => c.stage === stage && c.beyond_plan).length);

	const tone = (cell: StudyCellOut) => toneClasses[cellTone(cell.state, cell.outcome)];

	function label(cell: StudyCellOut) {
		const run = cell.current_run;
		return `${cell.unit_key} · repeat ${cell.repeat_index} · ${cell.state.replace(/_/g, ' ')}${cell.outcome ? ` · ${outcomeLabel(cell.outcome)}` : ''}${cell.exclusion_reason ? ` (${cell.exclusion_reason})` : ''}${run && run.attempt > 1 ? ` · attempt ${run.attempt}` : ''}${cell.adaptive_n ? ' · adaptive n' : ''}${cell.beyond_plan ? ' · replicate, not in the planned sample' : ''}`;
	}

	function href(cell: StudyCellOut) {
		return cell.current_run ? `/samples/${cell.current_run.id}` : undefined;
	}
</script>

{#if rows.length === 0}
	<p class="text-xs text-muted-foreground">No {stage} cells.</p>
{:else}
	<div class="overflow-x-auto">
		<table class="text-xs">
			<tbody>
				{#each rows as row (row.conditionId)}
					<tr>
						<th class="col-sticky max-w-48 truncate pr-3 text-left font-normal" scope="row" title={conditionLabel(row.conditionId)}>{conditionLabel(row.conditionId)}</th>
						{#each row.units as u (u.unit)}
							<td class="px-1">
								<span class="flex gap-0.5" title={u.unit}>
									{#each u.list as cell (cell.id)}
										{@const link = href(cell)}
										<svelte:element
											this={link ? 'a' : 'span'}
											href={link}
											class={cn(
												'relative block size-3 rounded-[2px] border',
												tone(cell),
												cell.state === 'done' || cell.state === 'excluded' ? '' : 'bg-transparent',
												cell.state === 'running' && 'status-dot-live',
												cell.state === 'excluded' && 'border-dashed',
											)}
											aria-label={label(cell)}
											title={label(cell)}
										>{#if cell.beyond_plan}<span class="absolute -top-0.5 -right-0.5 size-1.5 rounded-full bg-foreground ring-1 ring-background" aria-hidden="true"></span>{/if}</svelte:element>
									{/each}
								</span>
							</td>
						{/each}
					</tr>
				{/each}
			</tbody>
		</table>
	</div>
	<p class="mt-2 flex flex-wrap gap-3 text-[11px] text-muted-foreground">
		{#each Object.entries(cells.counts) as [state, n] (state)}
			<span><b class="font-mono tabular-nums text-foreground">{n}</b> {state.replace(/_/g, ' ')}</span>
		{/each}
		{#if replicates > 0}
			<span class="inline-flex items-center gap-1">
				<span class="relative inline-block size-3 rounded-[2px] border border-border"><span class="absolute -top-0.5 -right-0.5 size-1.5 rounded-full bg-foreground ring-1 ring-background"></span></span>
				<b class="font-mono tabular-nums text-foreground">{replicates}</b> replicate, not in the planned sample
			</span>
		{/if}
	</p>
{/if}
