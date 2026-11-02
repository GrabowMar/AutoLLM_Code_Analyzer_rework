<script lang="ts">
	// One row per LLM call of an agent run: when, how long, tokens, and the
	// tool calls it issued (category chips, failed markers). Expanding a row
	// fetches the full step lazily (StepDetail).
	import * as Card from '$lib/components/ui/card';
	import { Badge } from '$lib/components/ui/badge';
	import { Segmented } from '$lib/components/ui/segmented';
	import EmptyState from '$lib/components/EmptyState.svelte';
	import { tableFrame } from '$lib/components/table';
	import ChevronDown from '@lucide/svelte/icons/chevron-down';
	import ChevronRight from '@lucide/svelte/icons/chevron-right';
	import CircleX from '@lucide/svelte/icons/circle-x';
	import ListTree from '@lucide/svelte/icons/list-tree';
	import type { TrajectoryStep } from '$lib/api/trajectory';
	import StepDetail from './StepDetail.svelte';
	import { categoryLabel, fmtOffset, fmtSeconds, fmtTokensShort, outcomeLabel } from './format';

	interface Props {
		jobId: string;
		steps: TrajectoryStep[];
		truncated?: boolean;
		totalSteps?: number;
		source?: 'llm_calls' | 'artifacts';
	}

	let { jobId, steps, truncated = false, totalSteps, source = 'llm_calls' }: Props = $props();

	let filter = $state('all');
	let expanded = $state<Set<number>>(new Set());

	const failedSteps = $derived(steps.filter((s) => s.failed || s.tool_calls.some((t) => t.failed)).length);
	const auxSteps = $derived(steps.filter((s) => s.role && s.role !== 'main').length);
	const shown = $derived(
		filter === 'failed'
			? steps.filter((s) => s.failed || s.tool_calls.some((t) => t.failed))
			: filter === 'aux'
				? steps.filter((s) => s.role && s.role !== 'main')
				: steps,
	);
	const filterItems = $derived([
		{ value: 'all', label: `All ${steps.length}` },
		{ value: 'failed', label: `With failures ${failedSteps}` },
		...(auxSteps ? [{ value: 'aux', label: `Auxiliary ${auxSteps}` }] : []),
	]);

	function toggle(seq: number) {
		const next = new Set(expanded);
		if (next.has(seq)) next.delete(seq);
		else next.add(seq);
		expanded = next;
	}
</script>

<Card.Root class="gap-0 overflow-clip py-0">
	<Card.Header class="border-b border-border py-3">
		<div class="flex flex-wrap items-center justify-between gap-2">
			<div class="space-y-0.5">
				<Card.Title class="text-sm">Trajectory</Card.Title>
				<Card.Description class="text-xs">
					{#if source === 'artifacts'}
						Rebuilt from the compact per-call artifacts (run record unavailable): tool outcomes unknown.
					{:else}
						Every LLM call in order, from the gateway call log. Expand a row for the full step.
					{/if}
				</Card.Description>
			</div>
			{#if steps.length}
				<Segmented aria-label="Filter steps" size="sm" bind:value={filter} items={filterItems} />
			{/if}
		</div>
	</Card.Header>
	<Card.Content class="p-0">
		{#if steps.length === 0}
			<EmptyState compact icon={ListTree} title="No LLM calls recorded" description="This run has no call log to show." />
		{:else if shown.length === 0}
			<EmptyState compact icon={ListTree} title="No matching steps" description="No call in this run matches the filter." />
		{:else}
			<div class="table-frame" {@attach tableFrame}>
				<table>
					<thead>
						<tr>
							<th class="w-8"><span class="sr-only">Expand</span></th>
							<th class="num">#</th>
							<th class="num hidden sm:table-cell" title="Start of the call, relative to the first call">At</th>
							<th class="num hidden sm:table-cell">Duration</th>
							<th class="num" title="Prompt / completion tokens (cached prompt tokens)">Tokens</th>
							<th>Tool calls</th>
						</tr>
					</thead>
					<tbody>
						{#each shown as step (step.seq)}
							{@const open = expanded.has(step.seq)}
							<tr class="cursor-pointer align-top" data-selected={open} onclick={() => toggle(step.seq)}>
								<td class="w-px">
									<button
										type="button"
										class="-m-1 flex rounded p-1 text-muted-foreground hover:text-foreground"
										aria-expanded={open}
										aria-label={open ? `Collapse step ${step.seq}` : `Expand step ${step.seq}`}
										onclick={(e) => {
											e.stopPropagation();
											toggle(step.seq);
										}}
									>
										{#if open}<ChevronDown class="size-4" />{:else}<ChevronRight class="size-4" />{/if}
									</button>
								</td>
								<td class="num">{step.seq}</td>
								<td class="num hidden text-muted-foreground sm:table-cell">{fmtOffset(step.offset_s)}</td>
								<td class="num hidden sm:table-cell">{fmtSeconds(step.duration_s)}</td>
								<td class="num whitespace-nowrap">
									{fmtTokensShort(step.prompt_tokens)} / {fmtTokensShort(step.completion_tokens)}
									{#if step.cached_tokens != null}<span class="cell-sub block text-muted-foreground">{fmtTokensShort(step.cached_tokens)} cached</span>{/if}
								</td>
								<td class="min-w-[14rem]">
									<div class="flex flex-wrap items-center gap-1">
										{#if step.role && step.role !== 'main'}
											<Badge variant="default" title="Auxiliary call (classified by its system prompt)">{step.role}</Badge>
										{/if}
										{#if step.failed}
											<span class="inline-flex items-center gap-1 text-[11px] text-danger-fg"><CircleX class="h-3.5 w-3.5" />LLM call failed (HTTP {step.status ?? '—'})</span>
										{/if}
										{#if step.format_error_feedback}
											<Badge variant="secondary" title="This call's input was the harness's format-error feedback on the previous response">after format error</Badge>
										{/if}
										{#if !step.tool_calls.length && !step.failed}
											<span class="text-[11px] text-muted-foreground">
												no tool call · {step.finish_reason ?? '—'}{step.content_chars ? ` · ${step.content_chars.toLocaleString()} chars of text` : ''}
											</span>
										{/if}
									</div>
									{#each step.tool_calls as tc, i (i)}
										<div class="mt-1 flex min-w-0 flex-wrap items-start gap-1.5 text-xs sm:flex-nowrap">
											<Badge variant="secondary" class="shrink-0">{categoryLabel(tc.category)}</Badge>
											{#if tc.failed}
												<span class="inline-flex shrink-0 items-center gap-0.5 text-[11px] text-danger-fg" title={outcomeLabel(tc.outcome)}>
													<CircleX class="h-3.5 w-3.5" />{tc.outcome === 'policy_denial' ? 'denied' : 'failed'}
												</span>
											{/if}
											<span class="w-full min-w-0 break-all font-mono text-[11px] text-muted-foreground sm:w-auto sm:flex-1" title={tc.summary}>
												<span class="text-foreground">{tc.name}</span> {tc.summary.length > 140 ? tc.summary.slice(0, 139) + '…' : tc.summary}
											</span>
										</div>
									{/each}
								</td>
							</tr>
							{#if open}
								<tr>
									<td colspan="6" class="bg-muted/20 p-3">
										<StepDetail {jobId} seq={step.seq} />
									</td>
								</tr>
							{/if}
						{/each}
					</tbody>
				</table>
			</div>
			{#if truncated}
				<p class="border-t px-3 py-2 text-[11px] text-muted-foreground">Showing the first {steps.length} of {totalSteps ?? '—'} calls.</p>
			{/if}
		{/if}
	</Card.Content>
</Card.Root>
