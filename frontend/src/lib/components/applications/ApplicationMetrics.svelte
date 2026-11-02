<script lang="ts">
import * as Card from '$lib/components/ui/card';
import DollarSign from '@lucide/svelte/icons/dollar-sign';
import Hash from '@lucide/svelte/icons/hash';
import Timer from '@lucide/svelte/icons/timer';
import type { GenerationJob } from '$lib/api/client';
import { fmt, fmtDur, fmtCost } from './utils';
import { humanizeKey, isPlainObject, llmSecondsOf, outputTokensPerLlmSecond, scalarText } from './insights/format';

interface CostData {
	totalCost: number;
	totalPrompt: number;
	totalCompletion: number;
	totalTokens: number;
	tokensPerSec: number;
	byStage: Record<string, { cost: number; prompt: number; completion: number }>;
	/** False when nothing was recorded (no artifacts, no token/cost metrics): totals are unknown, not 0. */
	known?: boolean;
}

interface Props {
	job: GenerationJob;
	costData: CostData;
	artifactCount: number;
	iterationCount: number;
}

let { job, costData, artifactCount, iterationCount }: Props = $props();

const usageKnown = $derived(costData.known !== false);

// Agent runs log one artifact per LLM call (agent_call_0001 …): one row for all of them.
const AGENT_CALL = /^agent_call_\d+$/;
const stageRows = $derived.by(() => {
	const rows: { stage: string; cost: number; count: number }[] = [];
	const calls = { stage: 'LLM calls', cost: 0, count: 0 };
	for (const [stage, data] of Object.entries(costData.byStage)) {
		if (AGENT_CALL.test(stage)) {
			calls.cost += data.cost;
			calls.count += 1;
		} else {
			rows.push({ stage, cost: data.cost, count: 1 });
		}
	}
	if (calls.count) rows.unshift({ ...calls, stage: `LLM calls (${calls.count})` });
	return rows;
});

// Subscription-billed runs record cost 0; the provider's list price is kept apart (never billed).
const listCost = $derived(typeof job.metrics?.list_cost === 'number' ? (job.metrics.list_cost as number) : null);
const billingSource = $derived(typeof job.metrics?.billing_source === 'string' ? (job.metrics.billing_source as string) : null);

// Throughput: output tokens per LLM-second when the gateway's LLM time is known.
// (prompt + completion) / wall clock is dominated by re-sent cached context and
// tool time for agent runs, so it is only an end-to-end figure.
const llmSeconds = $derived(llmSecondsOf(job.result_data));
const decodeRate = $derived(outputTokensPerLlmSecond(costData.totalCompletion || null, llmSeconds));
const llmCalls = $derived(typeof job.result_data?.agent?.llm_calls === 'number' ? (job.result_data.agent.llm_calls as number) : null);

type MetricEntry = [string, unknown];
// Scalars first, nested objects (functional, providers, behaviour …) after, so the grid has no holes.
const metricEntries = $derived(
	(Object.entries(job.metrics ?? {}) as MetricEntry[]).sort(
		([, a], [, b]) => Number(isPlainObject(a)) - Number(isPlainObject(b))
	)
);
</script>

{#snippet metricValue(value: unknown, depth: number)}
	{#if isPlainObject(value)}
		{@const entries = Object.entries(value)}
		{#if entries.length === 0}
			<span class="text-muted-foreground">—</span>
		{:else}
			<dl class="mt-0.5 space-y-0.5 text-xs font-normal">
				{#each entries as [k, v] (k)}
					<div class="flex min-w-0 items-baseline gap-2">
						<dt class="shrink-0 text-muted-foreground">{k.replace(/_/g, ' ')}</dt>
						<dd class="min-w-0 flex-1 truncate text-right font-mono tabular-nums" title={isPlainObject(v) ? JSON.stringify(v) : scalarText(v)}>
							{#if isPlainObject(v) && depth < 1}{@render metricValue(v, depth + 1)}{:else if isPlainObject(v) || Array.isArray(v)}{JSON.stringify(v)}{:else}{scalarText(v)}{/if}
						</dd>
					</div>
				{/each}
			</dl>
		{/if}
	{:else if Array.isArray(value)}
		<span class="block truncate font-mono text-xs" title={JSON.stringify(value)}>{value.length ? JSON.stringify(value) : '—'}</span>
	{:else if typeof value === 'number'}
		{Number.isInteger(value) ? value.toLocaleString() : value.toFixed(2)}
	{:else}
		{scalarText(value)}
	{/if}
{/snippet}

<section id="metrics" class="space-y-4">
	<div class="grid grid-cols-1 sm:grid-cols-3 gap-4">
		<!-- Cost Card -->
		<Card.Root>
			<Card.Header class="pb-3"><Card.Title class="text-sm font-medium flex items-center gap-2"><DollarSign class="h-4 w-4" /> Cost</Card.Title></Card.Header>
			<Card.Content>
				<div class="text-3xl font-medium font-mono tabular-nums mb-3" title={usageKnown ? undefined : 'No LLM usage was recorded for this job'}>{!usageKnown ? '—' : listCost != null && costData.totalCost === 0 ? '$0' : fmtCost(costData.totalCost)}</div>
				{#if listCost != null}
					<p class="mb-3 text-xs" title="Reported by the provider per call; not billed, never part of cost or budget">
						<span class="font-mono tabular-nums">${listCost.toFixed(listCost < 0.01 ? 5 : 3)}</span>
						<span class="text-muted-foreground">list-price equivalent{billingSource ? ` · billed via ${billingSource}` : ''}</span>
					</p>
				{/if}
				{#if stageRows.length > 0}
					<div class="space-y-1.5 text-sm">
						{#each stageRows as row (row.stage)}
							<div class="flex justify-between">
								<span class="text-muted-foreground">{row.stage}</span>
								<span>{listCost != null && row.cost === 0 ? '$0' : fmtCost(row.cost)}</span>
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
				<div class="text-3xl font-medium font-mono tabular-nums mb-3" title={usageKnown ? undefined : 'No LLM usage was recorded for this job'}>{usageKnown ? fmt(costData.totalTokens, 0) : '—'}</div>
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
				{#if decodeRate != null}
					<div class="text-3xl font-medium font-mono tabular-nums" title="Completion tokens / LLM seconds (gateway time of all calls; includes time to first token and provider queueing)">{fmt(decodeRate, 1)} <span class="text-sm font-normal text-muted-foreground">output tok / LLM-s</span></div>
					<p class="mb-3 text-[11px] text-muted-foreground">Completion tokens per second of LLM time ({fmtDur(llmSeconds)}).</p>
				{:else}
					<div class="text-3xl font-medium font-mono tabular-nums" title="(prompt + completion tokens) / wall-clock duration">{costData.tokensPerSec > 0 ? fmt(costData.tokensPerSec, 1) : '—'} <span class="text-sm font-normal text-muted-foreground">tok/s end-to-end</span></div>
					<p class="mb-3 text-[11px] text-muted-foreground">All tokens over the whole wall clock; LLM time was not recorded.</p>
				{/if}
				<div class="space-y-1.5 text-sm">
					{#if decodeRate != null}
						<div class="flex justify-between" title="(prompt + completion tokens) / wall-clock duration: dominated by re-sent cached context"><span class="text-muted-foreground">End-to-end tok/s</span><span class="font-mono tabular-nums">{costData.tokensPerSec > 0 ? fmt(costData.tokensPerSec, 1) : '—'}</span></div>
					{/if}
					{#if llmCalls != null && llmSeconds != null && llmCalls > 0}
						<div class="flex justify-between"><span class="text-muted-foreground">Mean LLM call</span><span class="font-mono tabular-nums">{fmtDur(llmSeconds / llmCalls)}</span></div>
					{/if}
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
				<dl class="grid grid-flow-row-dense grid-cols-2 sm:grid-cols-3 md:grid-cols-4 gap-3">
					{#each metricEntries as [key, value] (key)}
						<div class="min-w-0 rounded-md border p-2.5 {isPlainObject(value) ? 'col-span-2' : ''}">
							<dt class="text-xs text-muted-foreground" title={key}>{humanizeKey(key)}</dt>
							<dd class="mt-0.5 min-w-0 break-words text-sm font-medium">{@render metricValue(value, 0)}</dd>
						</div>
					{/each}
				</dl>
			</Card.Content>
		</Card.Root>
	{/if}
</section>
