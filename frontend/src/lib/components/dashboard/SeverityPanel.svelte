<script lang="ts">
	import * as Card from '$lib/components/ui/card';
	import ShieldAlert from '@lucide/svelte/icons/shield-alert';
	import SeverityDonut from '$lib/components/charts/SeverityDonut.svelte';
	import ChartEmpty from '$lib/components/charts/ChartEmpty.svelte';
	import ChartSkeleton from '$lib/components/charts/ChartSkeleton.svelte';
	import type { SeverityDistribution } from '$lib/api/client';

	interface Props {
		severity: SeverityDistribution | null;
		loading?: boolean;
	}

	let { severity, loading = false }: Props = $props();
</script>

<Card.Root class="gradient-border-top h-full">
	<Card.Header>
		<div class="flex items-center gap-2">
			<ShieldAlert class="h-4 w-4 text-muted-foreground" />
			<div>
				<Card.Title>Finding Severity</Card.Title>
				<Card.Description>Distribution across all analyses</Card.Description>
			</div>
		</div>
	</Card.Header>
	<Card.Content>
		{#if loading}
			<ChartSkeleton />
		{:else if severity && severity.total > 0}
			<SeverityDonut {severity} />
		{:else}
			<ChartEmpty message="No findings recorded yet." />
		{/if}
	</Card.Content>
</Card.Root>
