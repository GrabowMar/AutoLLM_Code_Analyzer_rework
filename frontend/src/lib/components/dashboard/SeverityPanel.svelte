<script lang="ts">
	/**
	 * Severity mix of counted static findings (each analysed job's canonical
	 * run) with the AI reviewer's consensus opinions listed apart — the two
	 * are never added together.
	 *
	 * Props: `severity` (null while loading or on error), `loading`,
	 * `measuredJobs` (optional: how many jobs the counts cover).
	 */
	import * as Card from '$lib/components/ui/card';
	import { Badge } from '$lib/components/ui/badge';
	import ShieldAlert from '@lucide/svelte/icons/shield-alert';
	import SeverityDonut from '$lib/components/charts/SeverityDonut.svelte';
	import ChartEmpty from '$lib/components/charts/ChartEmpty.svelte';
	import ChartSkeleton from '$lib/components/charts/ChartSkeleton.svelte';
	import { severityColors } from '$lib/constants/colors';
	import { severityDistributionOf, type SeverityDistribution } from '$lib/api/statistics';

	interface Props {
		severity: SeverityDistribution | null;
		loading?: boolean;
		measuredJobs?: number | null;
	}

	let { severity, loading = false, measuredJobs = null }: Props = $props();

	const SEVERITIES = ['critical', 'high', 'medium', 'low', 'info'] as const;

	// Older payloads have no split: their distribution is static-only by then.
	const staticDist = $derived(
		severity
			? severity.by_source
				? severityDistributionOf(severity.by_source.static)
				: severity
			: null
	);
	const aiCounts = $derived(severity?.by_source?.ai ?? null);
	const aiTotal = $derived(SEVERITIES.reduce((sum, s) => sum + (aiCounts?.[s] ?? 0), 0));
</script>

<Card.Root class="h-full">
	<Card.Header>
		<div class="flex items-center gap-2">
			<ShieldAlert class="h-4 w-4 text-muted-foreground" />
			<div>
				<Card.Title>Finding severity</Card.Title>
				<Card.Description>
					Counted static findings in each job's canonical run{measuredJobs != null
						? ` · ${measuredJobs} measured job${measuredJobs === 1 ? '' : 's'}`
						: ''}
				</Card.Description>
			</div>
		</div>
	</Card.Header>
	<Card.Content class="space-y-4">
		{#if loading}
			<ChartSkeleton />
		{:else if !staticDist}
			<ChartEmpty message="Severity data is unavailable." />
		{:else if staticDist.total > 0}
			<SeverityDonut severity={staticDist} />
		{:else if measuredJobs === 0}
			<ChartEmpty message="No measured analysis yet — nothing to count." />
		{:else}
			<ChartEmpty message="No counted static findings in the measured runs." />
		{/if}

		{#if !loading && aiCounts}
			<div class="space-y-2 border-t pt-3">
				<div class="flex items-baseline justify-between gap-2">
					<p class="section-label">AI review · consensus</p>
					<span class="text-[11px] text-muted-foreground">opinions, not counted above</span>
				</div>
				{#if aiTotal === 0}
					<p class="text-xs text-muted-foreground">No AI consensus findings.</p>
				{:else}
					<ul class="flex flex-wrap gap-1.5">
						{#each SEVERITIES as s (s)}
							{#if (aiCounts[s] ?? 0) > 0}
								<li>
									<Badge variant="outline" class="gap-1 text-[11px] capitalize {severityColors[s]}">
										{s}<span class="font-mono tabular-nums">{aiCounts[s]}</span>
									</Badge>
								</li>
							{/if}
						{/each}
					</ul>
				{/if}
			</div>
		{/if}
	</Card.Content>
</Card.Root>
