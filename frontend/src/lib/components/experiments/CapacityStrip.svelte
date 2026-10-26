<script lang="ts">
	/**
	 * CapacityStrip — one line of where runs can execute right now: executor
	 * state, lane usage (sandboxes · eval · LLM jobs) and one chip per visible
	 * endpoint, plus capacity warnings behind a disclosure chip. Other users' models appear as "+N".
	 *
	 * Props: `capacity` (`GET /execution/capacity/`, patched by SSE `capacity`).
	 */
	import { cn } from '$lib/utils';
	import { toneClasses } from '$lib/constants/colors';
	import { EndpointChip } from '$lib/components/inference';
	import type { Capacity } from '$lib/api/types/execution';
	import TriangleAlert from '@lucide/svelte/icons/triangle-alert';

	interface Props {
		capacity: Capacity;
		class?: string;
	}

	let { capacity, class: className }: Props = $props();

	const executor = $derived.by(() => {
		const e = capacity.executor;
		if (!e.online) return { label: 'Executor offline', tone: 'danger' as const, title: 'No runs start until the executor service is back.' };
		if (e.draining) {
			const r = e.draining_remaining;
			return {
				label: 'Executor draining',
				tone: 'warning' as const,
				title: `No new runs start; ${r.jobs} running job(s) and ${r.post_runs} post-run(s) finish first.`,
			};
		}
		if (e.code_stale) return { label: 'Executor on old code', tone: 'warning' as const, title: 'The executor runs older code than the web app; restart it when idle (drain first).' };
		return { label: 'Executor online', tone: 'success' as const, title: e.leader ? `Since ${new Date(e.leader.started_at).toLocaleString()}` : '' };
	});

	const lanes = $derived([
		{ label: 'Sandboxes', ...capacity.lanes.sandbox },
		{ label: 'Eval', ...capacity.lanes.eval },
		{ label: 'LLM jobs', ...capacity.lanes.llm },
	]);
	const warnings = $derived(capacity.limits?.warnings ?? []);
</script>

<div
	class={cn('flex flex-wrap items-center gap-x-4 gap-y-2 rounded-md border border-border bg-card px-3 py-2 text-xs', className)}
	role="status"
	aria-label="Execution capacity"
>
	<span class={cn('inline-flex items-center gap-1.5 rounded-full border px-2 py-0.5 text-[11px] font-medium', toneClasses[executor.tone])} title={executor.title}>
		<span class={cn('status-dot h-1.5 w-1.5', executor.tone === 'success' && 'status-dot-live')} aria-hidden="true"></span>
		{executor.label}
	</span>
	<span class="inline-flex flex-wrap items-center gap-x-2 font-mono tabular-nums text-muted-foreground">
		{#each lanes as lane, i (lane.label)}
			{#if i > 0}<span aria-hidden="true">·</span>{/if}
			<span title="{lane.label}: {lane.used} of {lane.total} in use">
				<span class="font-sans">{lane.label}</span>
				<span class={cn(lane.total > 0 && lane.used >= lane.total ? 'text-warning-fg' : 'text-foreground')}>{lane.used}/{lane.total}</span>
			</span>
		{/each}
	</span>
	{#if capacity.endpoints.length > 0}
		<span class="flex min-w-0 flex-wrap items-center gap-1.5">
			{#each capacity.endpoints as ep (ep.id)}
				<EndpointChip
					endpoint={ep}
					status={ep.status}
					slots={ep.kind === 'openrouter' ? null : ep.slots}
					models={ep.resident_models}
					othersCount={ep.resident_others}
					href="/endpoints/{ep.id}"
				/>
			{/each}
		</span>
	{/if}
	<!-- Warnings are sentences: a chip that discloses them, so the strip stays one line. -->
	{#if warnings.length > 0}
		<details class="open:basis-full">
			<summary
				class={cn('inline-flex cursor-pointer list-none items-center gap-1.5 rounded-full border px-2 py-0.5 text-[11px] font-medium', toneClasses.warning)}
				title={warnings.join('\n')}
			>
				<TriangleAlert class="size-3 shrink-0" aria-hidden="true" />
				{warnings.length} capacity warning{warnings.length === 1 ? '' : 's'}
			</summary>
			<ul class="mt-1.5 space-y-1 text-warning-fg">
				{#each warnings as warning (warning)}
					<li class="flex items-start gap-1.5">
						<TriangleAlert class="mt-px size-3.5 shrink-0" aria-hidden="true" />
						<span>{warning}</span>
					</li>
				{/each}
			</ul>
		</details>
	{/if}
</div>
