<script lang="ts">
	/** Expandable per-group detail (severity, per-trial statistics, outcomes, LOC). */
	import ChevronRight from '@lucide/svelte/icons/chevron-right';
	import GroupDetail from './GroupDetail.svelte';
	import LocPerJob from './LocPerJob.svelte';
	import type { ComparisonGroup } from './GroupComparisonTable.svelte';

	interface Props {
		groups: ComparisonGroup[];
	}

	let { groups }: Props = $props();
</script>

<ul class="divide-y rounded-md border">
	{#each groups as g (g.key)}
		<li>
			<details class="group">
				<summary
					class="flex cursor-pointer list-none items-center gap-2 px-4 py-3 text-sm select-none hover:bg-muted/40 [&::-webkit-details-marker]:hidden"
				>
					<ChevronRight
						class="h-4 w-4 shrink-0 text-muted-foreground transition-transform duration-200 group-open:rotate-90"
						aria-hidden="true"
					/>
					<span class="font-medium">{g.label}</span>
					{#if g.sublabel}<span class="truncate font-mono text-[11px] text-muted-foreground"
							>{g.sublabel}</span
						>{/if}
					<span class="ml-auto shrink-0 text-xs text-muted-foreground tabular-nums">
						{g.row.stats.analyzed}/{g.row.stats.trials} measured
					</span>
				</summary>
				<div class="space-y-5 border-t px-4 py-4">
					<GroupDetail row={g.row} />
					<section class="space-y-2">
						<h3 class="section-label">Lines of code</h3>
						<LocPerJob loc={g.row.loc} />
					</section>
				</div>
			</details>
		</li>
	{/each}
</ul>
