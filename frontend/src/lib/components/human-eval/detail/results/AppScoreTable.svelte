<script lang="ts">
	/**
	 * AppScoreTable — per-app values (the unit of analysis): blinded label,
	 * group, n ratings, mean, SD, median and N/A answers.
	 *
	 * Props: `scores` (AppScore[]), `groupLabel(key)`, `groupNoun`, `format`, `naColumn`.
	 */
	import type { AppScore } from '$lib/api/human-eval';

	interface Props {
		scores: AppScore[];
		groupLabel: (key: string) => string;
		groupNoun: string;
		format: (v: number) => string;
		naColumn?: boolean;
	}

	let { scores, groupLabel, groupNoun, format, naColumn = true }: Props = $props();
	const fmt = (v: number | null) => (v == null ? '—' : format(v));
</script>

<div class="max-h-96 overflow-auto">
	<table class="text-sm">
		<thead>
			<tr>
				<th>App</th>
				<th>{groupNoun}</th>
				<th class="num">n</th>
				<th class="num">Mean</th>
				<th class="num">SD</th>
				<th class="num">Median</th>
				{#if naColumn}<th class="num" title="Answered N/A (can't judge)">N/A</th>{/if}
			</tr>
		</thead>
		<tbody>
			{#each scores as s (s.app_id)}
				<tr>
					<td class="font-mono text-xs">{s.label}</td>
					<td class="max-w-56 truncate text-xs" title={groupLabel(s.group)}>{groupLabel(s.group)}</td>
					<td class="num text-xs">{s.n}</td>
					<td class="num text-xs font-medium">{fmt(s.mean)}</td>
					<td class="num text-xs text-muted-foreground">{fmt(s.sd)}</td>
					<td class="num text-xs text-muted-foreground">{fmt(s.median)}</td>
					{#if naColumn}<td class="num text-xs text-muted-foreground">{s.na_count}</td>{/if}
				</tr>
			{:else}
				<tr><td colspan="7" class="text-xs text-muted-foreground">No rated apps.</td></tr>
			{/each}
		</tbody>
	</table>
</div>
