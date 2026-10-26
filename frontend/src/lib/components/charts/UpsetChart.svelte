<script lang="ts" module>
	export interface UpsetIntersection {
		/** Members of the intersection (exactly these sets). */
		tools: string[];
		count: number;
	}
</script>

<script lang="ts">
	/**
	 * UpsetChart — which combinations of sets (e.g. tools that detected the
	 * same issue) occur and how often: bars per exact intersection over a
	 * membership dot matrix, with per-set totals. Better than a Venn diagram
	 * beyond three sets.
	 *
	 * Props: `sets` (order of rows), `intersections`, `limit` (default 20
	 * largest), `color` (CSS var), `setLabel` (row noun), `label`.
	 */
	import ChartEmpty from './ChartEmpty.svelte';

	interface Props {
		sets: string[];
		intersections: UpsetIntersection[];
		limit?: number;
		color?: string;
		setLabel?: string;
		label?: string;
	}

	let {
		sets,
		intersections,
		limit = 20,
		color = 'var(--chart-2)',
		setLabel = 'Set',
		label = 'Set intersections'
	}: Props = $props();

	const shown = $derived([...intersections].sort((a, b) => b.count - a.count).slice(0, limit));
	const hidden = $derived(Math.max(0, intersections.length - shown.length));
	const maxCount = $derived(Math.max(1, ...shown.map((s) => s.count)));
	const setTotals = $derived(
		new Map(
			sets.map((s) => [
				s,
				intersections.filter((i) => i.tools.includes(s)).reduce((a, i) => a + i.count, 0)
			])
		)
	);
	// Only sets that occur in a shown intersection get a row (others add nothing).
	const rows = $derived(sets.filter((s) => shown.some((i) => i.tools.includes(s))));
	const maxSet = $derived(Math.max(1, ...rows.map((s) => setTotals.get(s) ?? 0)));
	const BAR_H = 96;
</script>

{#if shown.length === 0}
	<ChartEmpty message="No overlapping sets to show." height={100} />
{:else}
	<figure class="space-y-2" aria-label={label}>
		<div class="table-scroll-wrapper">
			<div
				class="grid w-max items-end gap-x-1 text-xs"
				style="grid-template-columns: minmax(7rem, 10rem) 3.5rem repeat({shown.length}, 1.75rem)"
			>
				<!-- Intersection bars -->
				<div class="self-end pb-1 text-[11px] text-muted-foreground">
					{setLabel} · total
				</div>
				<div></div>
				{#each shown as inter, j (j)}
					<div class="flex flex-col items-center justify-end" style="height: {BAR_H}px">
						<span class="mb-0.5 text-[11px] tabular-nums text-muted-foreground">{inter.count}</span>
						<span
							class="w-3.5 rounded-t-sm"
							style="height: {Math.max(
								2,
								(inter.count / maxCount) * (BAR_H - 16)
							)}px; background: {color}"
							title="{inter.tools.join(' + ')}: {inter.count}"
						></span>
					</div>
				{/each}

				<!-- Membership matrix -->
				{#each rows as set, r (set)}
					<div class="flex h-6 items-center truncate font-mono" title={set}>
						{set}
					</div>
					<div
						class="flex h-6 items-center gap-1"
						title="{set}: {setTotals.get(set) ?? 0} in total"
					>
						<span
							class="h-2 rounded-sm bg-muted-foreground/40"
							style="width: {((setTotals.get(set) ?? 0) / maxSet) * 2}rem"
						></span>
						<span class="text-[11px] tabular-nums text-muted-foreground"
							>{setTotals.get(set) ?? 0}</span
						>
					</div>
					{#each shown as inter, j (j)}
						{@const member = inter.tools.includes(set)}
						<div class="relative flex h-6 items-center justify-center" class:bg-muted={r % 2 === 0}>
							<span
								class="h-2.5 w-2.5 rounded-full"
								style={member ? `background: ${color}` : 'background: var(--border)'}
							></span>
							<span class="sr-only">{member ? `${set} in` : ''}</span>
						</div>
					{/each}
				{/each}
			</div>
		</div>
		<table class="sr-only">
			<caption>{label}</caption>
			<thead><tr><th scope="col">Combination</th><th scope="col">Count</th></tr></thead>
			<tbody>
				{#each shown as inter, j (j)}
					<tr><td>{inter.tools.join(' + ')}</td><td>{inter.count}</td></tr>
				{/each}
			</tbody>
		</table>
		<figcaption class="text-[11px] text-muted-foreground">
			Each column is one exact combination; filled dots mark its members.
			{#if hidden > 0}{hidden} smaller combination(s) not shown.{/if}
		</figcaption>
	</figure>
{/if}
