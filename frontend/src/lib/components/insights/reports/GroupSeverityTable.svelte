<script lang="ts">
	/**
	 * Summed counted static issues by severity per group, with the AI
	 * reviewer's consensus total in its own column. "—" when a group has no
	 * usable measurement (never 0).
	 */
	import { Badge } from '$lib/components/ui/badge';
	import { severityColors } from '$lib/constants/colors';
	import NaValue from '$lib/components/analysis/NaValue.svelte';
	import type { ComparisonGroup } from './GroupComparisonTable.svelte';

	interface Props {
		groups: ComparisonGroup[];
		groupLabel?: string;
	}

	let { groups, groupLabel = 'Group' }: Props = $props();

	const SEVERITIES = ['critical', 'high', 'medium', 'low', 'info'] as const;

	function staticReason(g: ComparisonGroup): string {
		return g.row.stats.analyzed === 0
			? 'No measured run in this group'
			: 'Static measurement unavailable in every measured run of this group';
	}
</script>

<div class="table-scroll-wrapper">
	<table class="min-w-[40rem] text-xs">
		<thead>
			<tr>
				<th scope="col">{groupLabel}</th>
				{#each SEVERITIES as s (s)}
					<th scope="col" class="text-right">
						<Badge variant="outline" class="text-[11px] capitalize {severityColors[s]}">{s}</Badge>
					</th>
				{/each}
				<th scope="col" class="text-right">Issues</th>
				<th scope="col" class="text-right">Findings</th>
				<th scope="col" class="border-l text-right">AI consensus</th>
			</tr>
		</thead>
		<tbody>
			{#each groups as g (g.key)}
				<tr>
					<td class="font-medium">{g.label}</td>
					{#each SEVERITIES as s (s)}
						<td class="text-right font-mono tabular-nums">
							{#if g.row.issues}{g.row.issues[s] ?? 0}{:else}<NaValue
									reason={staticReason(g)}
								/>{/if}
						</td>
					{/each}
					<td class="text-right font-mono font-semibold tabular-nums">
						{#if g.row.total_issues != null}{g.row.total_issues}{:else}<NaValue
								reason={staticReason(g)}
							/>{/if}
					</td>
					<td class="text-right font-mono tabular-nums">
						{#if g.row.total_findings != null}{g.row.total_findings}{:else}<NaValue
								reason={staticReason(g)}
							/>{/if}
					</td>
					<td class="border-l text-right font-mono tabular-nums text-muted-foreground">
						{#if g.row.ai_total != null}{g.row.ai_total}{:else}<NaValue
								reason={g.row.stats.analyzed === 0
									? 'No measured run in this group'
									: 'No successful AI review in this group'}
							/>{/if}
					</td>
				</tr>
			{/each}
		</tbody>
	</table>
</div>
<p class="mt-2 text-[11px] text-muted-foreground">
	Severity columns count static issues (deduplicated across tools); "Findings" counts every tool's
	counted findings before deduplication.
</p>
