<script lang="ts">
	import { PieChart } from 'layerchart';
	import type { SeverityDistribution } from '$lib/api/client';
	import { severityChartColors } from './theme';

	interface Props {
		severity: SeverityDistribution;
		height?: number;
	}

	let { severity, height = 208 }: Props = $props();

	interface Slice {
		key: string;
		label: string;
		value: number;
		percent: number;
		color: string;
	}

	const slices = $derived<Slice[]>(
		severity.distribution
			.filter((b) => b.count > 0)
			.map((b) => ({
				key: b.severity.toLowerCase(),
				label: b.severity,
				value: b.count,
				percent: b.percent,
				color: severityChartColors[b.severity.toLowerCase()] ?? '#94a3b8',
			}))
	);
</script>

<div class="flex flex-col items-center gap-4 sm:flex-row sm:items-center">
	<div class="relative shrink-0" style="height: {height}px; width: {height}px">
		<PieChart
			data={slices}
			key="key"
			label="label"
			value="value"
			cRange={slices.map((s) => s.color)}
			innerRadius={-24}
			padding={{ top: 4, bottom: 4, left: 4, right: 4 }}
			props={{
				pie: { padAngle: 0.03 },
				arc: { track: false },
			}}
		/>
		<!-- Center total (hero number for the panel) -->
		<div
			class="pointer-events-none absolute inset-0 flex flex-col items-center justify-center"
			aria-hidden="true"
		>
			<span class="text-2xl font-bold" style="font-family: var(--font-display);">
				{severity.total}
			</span>
			<span class="text-[11px] text-muted-foreground">findings</span>
		</div>
	</div>

	<!-- Legend with always-visible counts (values never gated behind hover) -->
	<div class="grid w-full grid-cols-2 gap-x-4 gap-y-2 text-xs sm:grid-cols-1">
		{#each slices as slice (slice.key)}
			<div class="flex items-center justify-between gap-2">
				<span class="flex min-w-0 items-center gap-1.5">
					<span class="h-2 w-2 shrink-0 rounded-full" style="background: {slice.color}"></span>
					<span class="truncate capitalize">{slice.label}</span>
				</span>
				<span class="tabular-nums text-muted-foreground">
					{slice.value}
					<span class="text-muted-foreground/60">({slice.percent}%)</span>
				</span>
			</div>
		{/each}
	</div>
</div>
