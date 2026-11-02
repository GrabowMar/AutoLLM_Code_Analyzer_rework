<script lang="ts">
	/**
	 * PrecisionTable — finding precision rows (overall, per tool, per the
	 * tool's own level, per group). Primary estimate: per-finding soft label
	 * tp/(tp+fp), inverse-probability weighted where `weighted`, CI by bootstrap
	 * over apps; majority precision is secondary. tp / fp / unsure are vote
	 * counts (descriptive). Level rows show the tool's own label exactly as
	 * printed ("unrated" when it printed none) — never a normalized severity.
	 *
	 * Props: `rows`, `keyHeader`, `levels` (render labels as tool levels).
	 */
	import LevelBadge from '$lib/components/analysis/LevelBadge.svelte';
	import type { PrecisionRow } from '$lib/api/human-eval';
	import { formatCi, formatShare } from '../../format';

	interface Props {
		rows: PrecisionRow[];
		keyHeader: string;
		levels?: boolean;
	}

	let { rows, keyHeader, levels = false }: Props = $props();
	const share = (v: number) => formatShare(v);
</script>

<div class="overflow-x-auto">
	<table class="text-sm">
		<thead>
			<tr>
				<th>{keyHeader}</th>
				<th class="num" title="Soft-label precision tp/(tp+fp) [bootstrap 95% CI over apps]">Precision [95% CI]</th>
				<th class="num" title="Majority verdict per finding; ties and unsure dropped">Majority</th>
				<th class="num" title="Findings reviewed / apps">Findings · apps</th>
				<th class="num" title="Verdict votes: true positive / false positive / unsure">TP · FP · unsure</th>
				<th class="num" title="Findings whose TP/FP votes disagree">Split</th>
			</tr>
		</thead>
		<tbody>
			{#each rows as r (r.key)}
				<tr>
					<td class="max-w-64">
						{#if levels}
							<LevelBadge label={r.key === 'unrated' ? '' : r.label} fallback="unrated" />
						{:else}
							<span class="break-words">{r.label}</span>
						{/if}
						{#if r.weighted}<span class="ml-1 text-[11px] text-muted-foreground" title="Inverse-probability weighted by each app's sampling frame">weighted</span>{/if}
					</td>
					<td class="num text-xs">
						<span class="font-medium">{formatShare(r.precision)}</span>
						<span class="text-muted-foreground">{formatCi(r.ci, share)}</span>
					</td>
					<td class="num text-xs text-muted-foreground">{formatShare(r.majority_precision)}</td>
					<td class="num text-xs">{r.n_findings} · {r.n_apps}</td>
					<td class="num text-xs text-muted-foreground">{r.tp} · {r.fp} · {r.unsure}</td>
					<td class="num text-xs text-muted-foreground">{r.n_split}</td>
				</tr>
			{:else}
				<tr><td colspan="6" class="text-xs text-muted-foreground">No verdicts yet.</td></tr>
			{/each}
		</tbody>
	</table>
</div>
