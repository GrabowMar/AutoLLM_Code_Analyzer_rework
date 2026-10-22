<script lang="ts">
import * as Card from '$lib/components/ui/card';
import DollarSign from '@lucide/svelte/icons/dollar-sign';
import Hash from '@lucide/svelte/icons/hash';
import Timer from '@lucide/svelte/icons/timer';
import type { GenerationJob } from '$lib/api/client';
import { fmt, fmtDur, fmtCost } from './utils';

interface CostData {
	totalCost: number;
	totalPrompt: number;
	totalCompletion: number;
	totalTokens: number;
	tokensPerSec: number;
	byStage: Record<string, { cost: number; prompt: number; completion: number }>;
}

interface Props {
	job: GenerationJob;
	costData: CostData;
	artifactCount: number;
	iterationCount: number;
}

let { job, costData, artifactCount, iterationCount }: Props = $props();
</script>

<section id="metrics" class="space-y-4">
	<div class="grid grid-cols-1 sm:grid-cols-3 gap-4">
		<!-- Cost Card -->
		<Card.Root>
			<Card.Header class="pb-3"><Card.Title class="text-sm font-medium flex items-center gap-2"><DollarSign class="h-4 w-4" /> Cost</Card.Title></Card.Header>
			<Card.Content>
				<div class="text-3xl font-bold font-display tabular-nums mb-3">{fmtCost(costData.totalCost)}</div>
				{#if Object.keys(costData.byStage).length > 0}
					<div class="space-y-1.5 text-sm">
						{#each Object.entries(costData.byStage) as [stage, data]}
							<div class="flex justify-between">
								<span class="text-muted-foreground">{stage}</span>
								<span>{fmtCost(data.cost)}</span>
							</div>
						{/each}
					</div>
				{/if}
			</Card.Content>
		</Card.Root>

		<!-- Token Breakdown -->
		<Card.Root>
			<Card.Header class="pb-3"><Card.Title class="text-sm font-medium flex items-center gap-2"><Hash class="h-4 w-4" /> Token Usage</Card.Title></Card.Header>
			<Card.Content>
				<div class="text-3xl font-bold font-display tabular-nums mb-3">{fmt(costData.totalTokens, 0)}</div>
				{#if costData.totalTokens > 0}
					<div class="h-4 rounded-full overflow-hidden bg-muted flex mb-2">
						<div class="bg-info h-full" style="width: {(costData.totalPrompt / costData.totalTokens) * 100}%"></div>
						<div class="bg-success h-full" style="width: {(costData.totalCompletion / costData.totalTokens) * 100}%"></div>
					</div>
					<div class="flex justify-between text-xs text-muted-foreground">
						<span class="flex items-center gap-1"><span class="h-2 w-2 rounded-full bg-info inline-block"></span> Prompt: {fmt(costData.totalPrompt, 0)}</span>
						<span class="flex items-center gap-1"><span class="h-2 w-2 rounded-full bg-success inline-block"></span> Completion: {fmt(costData.totalCompletion, 0)}</span>
					</div>
				{/if}
			</Card.Content>
		</Card.Root>

		<!-- Performance -->
		<Card.Root>
			<Card.Header class="pb-3"><Card.Title class="text-sm font-medium flex items-center gap-2"><Timer class="h-4 w-4" /> Performance</Card.Title></Card.Header>
			<Card.Content>
				<div class="text-3xl font-bold font-display tabular-nums mb-3">{costData.tokensPerSec > 0 ? fmt(costData.tokensPerSec, 1) : '—'} <span class="text-sm font-normal text-muted-foreground">tok/s</span></div>
				<div class="space-y-1.5 text-sm">
					<div class="flex justify-between"><span class="text-muted-foreground">Total Duration</span><span>{fmtDur(job.duration_seconds)}</span></div>
					<div class="flex justify-between"><span class="text-muted-foreground">Artifacts</span><span>{artifactCount}</span></div>
					{#if job.mode === 'copilot'}
						<div class="flex justify-between"><span class="text-muted-foreground">Iterations</span><span>{iterationCount}</span></div>
					{/if}
				</div>
			</Card.Content>
		</Card.Root>
	</div>

	<!-- All Metrics (raw) -->
	{#if job.metrics && Object.keys(job.metrics).length > 0}
		<Card.Root>
			<Card.Header class="pb-3"><Card.Title class="text-sm font-medium">All Metrics</Card.Title><Card.Description>Raw metrics reported by the generation run.</Card.Description></Card.Header>
			<Card.Content>
				<dl class="grid grid-cols-2 sm:grid-cols-3 md:grid-cols-4 gap-3">
					{#each Object.entries(job.metrics) as [key, value]}
						<div class="rounded-md border p-2.5">
							<dt class="text-xs text-muted-foreground">{key.replace(/_/g, ' ')}</dt>
							<dd class="text-sm font-medium mt-0.5">
								{typeof value === 'number'
									? Number.isInteger(value)
										? value.toLocaleString()
										: value.toFixed(2)
									: String(value)}
							</dd>
						</div>
					{/each}
				</dl>
			</Card.Content>
		</Card.Root>
	{/if}
</section>
