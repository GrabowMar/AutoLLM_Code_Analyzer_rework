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

<Card.Root class="h-full">
	<Card.Header>
		<div class="flex min-w-0 items-center gap-2">
			<ShieldAlert class="h-4 w-4 shrink-0 text-muted-foreground" />
			<Card.Title>Finding Severity</Card.Title>
			<span class="truncate text-xs text-muted-foreground">all analyses</span>
		</div>
	</Card.Header>
	<Card.Content>
		{#if loading}
			<ChartSkeleton />
		{:else if severity && severity.total > 0}
			<SeverityDonut {severity} />
		{:else}
			<ChartEmpty height={56} message="No findings recorded yet." />
		{/if}
	</Card.Content>
</Card.Root>
