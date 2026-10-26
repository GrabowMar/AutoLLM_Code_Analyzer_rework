<script lang="ts">
	import * as Card from '$lib/components/ui/card';
	import TrendingUp from '@lucide/svelte/icons/trending-up';
	import TrendAreaChart from '$lib/components/charts/TrendAreaChart.svelte';
	import ChartEmpty from '$lib/components/charts/ChartEmpty.svelte';
	import ChartSkeleton from '$lib/components/charts/ChartSkeleton.svelte';
	import type { AnalysisTrends } from '$lib/api/client';

	interface Props {
		trends: AnalysisTrends | null;
		loading?: boolean;
	}

	let { trends, loading = false }: Props = $props();
</script>

<Card.Root class="h-full">
	<Card.Header>
		<div class="flex items-center justify-between gap-2">
			<div class="flex min-w-0 items-center gap-2">
				<TrendingUp class="h-4 w-4 shrink-0 text-muted-foreground" />
				<Card.Title>Analysis Activity</Card.Title>
				<span class="truncate text-xs text-muted-foreground">last {trends?.days ?? 30} days</span>
			</div>
			<span class="flex shrink-0 items-baseline gap-1 text-[11px] text-muted-foreground">
				<span class="font-mono text-base font-medium text-foreground tabular-nums">{trends?.total ?? 0}</span>runs
			</span>
		</div>
	</Card.Header>
	<Card.Content>
		{#if loading}
			<ChartSkeleton />
		{:else if trends && trends.series.length > 1 && trends.total > 0}
			<TrendAreaChart points={trends.series} />
		{:else}
			<ChartEmpty height={56} message={trends ? `No analyses in the last ${trends.days} days.` : undefined} />
		{/if}
	</Card.Content>
</Card.Root>
