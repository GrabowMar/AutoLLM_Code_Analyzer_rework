<script lang="ts">
	/**
	 * Severity counts per source, static measurement and AI opinions in
	 * separate groups. A row whose counts are null renders "—" with its reason.
	 *
	 *   <SeverityBreakdown rows={[
	 *     { label: 'Static findings', counts: data.findings, reason: '…' },
	 *     { label: 'AI consensus', counts: data.ai_findings, reason: '…', ai: true },
	 *   ]} />
	 */
	import { Badge } from '$lib/components/ui/badge';
	import { severityColors } from '$lib/constants/colors';
	import NaValue from '$lib/components/analysis/NaValue.svelte';

	const SEVERITIES = ['critical', 'high', 'medium', 'low', 'info'] as const;

	interface Row {
		label: string;
		counts: Partial<Record<string, number>> | null | undefined;
		reason: string;
		/** AI-reviewer row: rendered in the separate "AI opinions" group. */
		ai?: boolean;
		hint?: string;
	}

	interface Props {
		rows: Row[];
		aiNote?: string;
	}

	let {
		rows,
		aiNote = 'Opinions, not measurements: findings reported by a majority of review samples and grounded in the code.'
	}: Props = $props();

	const staticRows = $derived(rows.filter((r) => !r.ai));
	const aiRows = $derived(rows.filter((r) => r.ai));

	function total(counts: Partial<Record<string, number>>): number {
		return SEVERITIES.reduce((s, k) => s + (counts[k] ?? 0), 0);
	}
</script>

{#snippet row(r: Row)}
	<tr>
		<td class="text-xs font-medium">
			{r.label}
			{#if r.hint}<span class="block text-[11px] font-normal text-muted-foreground">{r.hint}</span
				>{/if}
		</td>
		{#each SEVERITIES as s (s)}
			<td class="text-right font-mono text-xs tabular-nums">
				{#if r.counts}{r.counts[s] ?? 0}{:else}<NaValue reason={r.reason} />{/if}
			</td>
		{/each}
		<td class="text-right font-mono text-xs font-semibold tabular-nums">
			{#if r.counts}{total(r.counts)}{:else}<NaValue reason={r.reason} />{/if}
		</td>
	</tr>
{/snippet}

<div class="table-scroll-wrapper">
	<table class="min-w-[32rem]">
		<thead>
			<tr>
				<th scope="col">Source</th>
				{#each SEVERITIES as s (s)}
					<th scope="col" class="text-right">
						<Badge variant="outline" class="text-[11px] capitalize {severityColors[s]}">{s}</Badge>
					</th>
				{/each}
				<th scope="col" class="text-right">Total</th>
			</tr>
		</thead>
		<tbody>
			{#each staticRows as r (r.label)}{@render row(r)}{/each}
		</tbody>
		{#if aiRows.length > 0}
			<tbody>
				<tr>
					<td
						colspan={SEVERITIES.length + 2}
						class="border-t-2 bg-muted/30 text-[11px] text-muted-foreground"
					>
						<span class="font-semibold uppercase tracking-wider">AI review</span> — {aiNote}
					</td>
				</tr>
				{#each aiRows as r (r.label)}{@render row(r)}{/each}
			</tbody>
		{/if}
	</table>
</div>
