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

<Card.Root class="gradient-border-top h-full">
	<Card.Header>
		<div class="flex items-center justify-between">
			<div class="flex items-center gap-2">
				<TrendingUp class="h-4 w-4 text-muted-foreground" />
				<div>
					<Card.Title>Analysis Activity</Card.Title>
					<Card.Description>Last {trends?.days ?? 30} days</Card.Description>
				</div>
			</div>
			<div class="text-right">
				<div class="text-xl font-bold" style="font-family: var(--font-display);">
					{trends?.total ?? 0}
				</div>
				<p class="text-[11px] text-muted-foreground">total runs</p>
			</div>
		</div>
	</Card.Header>
	<Card.Content>
		{#if loading}
			<ChartSkeleton />
		{:else if trends && trends.series.length > 1}
			<TrendAreaChart points={trends.series} />
		{:else}
			<ChartEmpty />
		{/if}
	</Card.Content>
</Card.Root>
