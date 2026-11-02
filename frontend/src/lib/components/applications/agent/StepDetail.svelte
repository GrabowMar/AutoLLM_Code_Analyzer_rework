<script lang="ts">
	// One LLM call in full (fetched when its row is expanded): the inputs since
	// the previous turn, reasoning, content, and each tool call with its result.
	import { onMount } from 'svelte';
	import { Badge } from '$lib/components/ui/badge';
	import StatusBadge from '$lib/components/StatusBadge.svelte';
	import LoadingState from '$lib/components/LoadingState.svelte';
	import ErrorAlert from '$lib/components/ErrorAlert.svelte';
	import ChevronRight from '@lucide/svelte/icons/chevron-right';
	import { getTrajectoryStep, type StepDetail } from '$lib/api/trajectory';
	import { formatApiError } from '$lib/api/core';
	import { categoryLabel, fmtInt, fmtSeconds, outcomeLabel } from './format';

	interface Props {
		jobId: string;
		seq: number;
	}

	let { jobId, seq }: Props = $props();

	let detail = $state<StepDetail | null>(null);
	let loading = $state(true);
	let error = $state('');
	let showReasoning = $state(false);
	let showSystem = $state(false);

	async function load() {
		loading = true;
		error = '';
		try {
			detail = await getTrajectoryStep(jobId, seq);
		} catch (e) {
			error = formatApiError(e);
		} finally {
			loading = false;
		}
	}

	onMount(load);

	function outcomeTone(outcome: string, failed: boolean): 'danger' | 'neutral' | 'success' {
		if (failed) return 'danger';
		return outcome === 'ok' ? 'success' : 'neutral';
	}

	const usage = $derived((detail?.response.usage ?? {}) as Record<string, any>);
</script>

{#if loading}
	<LoadingState label="loading step {seq}" class="py-6" />
{:else if error}
	<ErrorAlert message={error} onretry={load} />
{:else if detail}
	<div class="space-y-3 text-xs">
		<dl class="flex flex-wrap gap-x-4 gap-y-1 text-muted-foreground">
			<div><dt class="inline">provider</dt> <dd class="inline font-mono text-foreground">{detail.response.provider ?? '—'}</dd></div>
			<div><dt class="inline">model</dt> <dd class="inline font-mono text-foreground">{detail.response.model ?? detail.request.model ?? '—'}</dd></div>
			<div><dt class="inline">finish</dt> <dd class="inline font-mono text-foreground">{detail.response.finish_reason ?? '—'}</dd></div>
			<div><dt class="inline">duration</dt> <dd class="inline font-mono text-foreground">{fmtSeconds(detail.duration_s)}</dd></div>
			<div><dt class="inline">messages in request</dt> <dd class="inline font-mono text-foreground">{fmtInt(detail.request.message_count)}</dd></div>
			<div><dt class="inline">tokens</dt> <dd class="inline font-mono text-foreground">{fmtInt(usage.prompt_tokens)} in · {fmtInt(usage.completion_tokens)} out · {fmtInt(usage.prompt_tokens_details?.cached_tokens)} cached · {fmtInt(usage.completion_tokens_details?.reasoning_tokens)} reasoning</dd></div>
			{#if detail.upstream_retries}<div><dt class="inline">gateway retries</dt> <dd class="inline font-mono text-foreground">{detail.upstream_retries}</dd></div>{/if}
		</dl>
		{#each detail.notes as note (note)}<p class="text-muted-foreground">{note}</p>{/each}

		{#if detail.request.new_messages.length}
			<div class="space-y-1">
				<p class="section-label">Inputs since the previous turn</p>
				{#each detail.request.new_messages as m, i (i)}
					<div class="rounded-md border">
						<div class="flex items-center gap-2 border-b bg-muted/30 px-2 py-1">
							<Badge variant="outline" class="text-[11px]">{m.role ?? 'message'}</Badge>
							{#if m.name}<span class="font-mono text-muted-foreground">{m.name}</span>{/if}
							<span class="text-muted-foreground">{(m.content ?? '').length.toLocaleString()} chars</span>
						</div>
						<pre class="max-h-48 overflow-auto p-2 font-mono text-[11px] whitespace-pre-wrap break-all">{m.content ?? ''}</pre>
					</div>
				{/each}
			</div>
		{/if}

		{#if detail.response.reasoning}
			<div class="space-y-1">
				<button type="button" class="flex cursor-pointer items-center gap-1 text-muted-foreground transition-colors hover:text-foreground" aria-expanded={showReasoning} onclick={() => (showReasoning = !showReasoning)}>
					<ChevronRight class="h-3.5 w-3.5 transition-transform duration-150 {showReasoning ? 'rotate-90' : ''}" />
					Reasoning ({detail.response.reasoning.length.toLocaleString()} chars)
				</button>
				{#if showReasoning}
					<pre class="max-h-64 overflow-auto rounded-md border bg-muted/20 p-2 font-mono text-[11px] whitespace-pre-wrap break-words">{detail.response.reasoning}</pre>
				{/if}
			</div>
		{/if}

		{#if detail.response.content}
			<div class="space-y-1">
				<p class="section-label">Response text</p>
				<pre class="max-h-64 overflow-auto rounded-md border bg-muted/20 p-2 font-mono text-[11px] whitespace-pre-wrap break-words">{detail.response.content}</pre>
			</div>
		{/if}

		{#if detail.response.error}
			<ErrorAlert message={typeof detail.response.error === 'string' ? detail.response.error : JSON.stringify(detail.response.error)} />
		{/if}

		{#if detail.response.tool_calls.length}
			<div class="space-y-2">
				<p class="section-label">Tool calls ({detail.response.tool_calls.length})</p>
				{#each detail.response.tool_calls as tc, i (tc.id ?? i)}
					<div class="rounded-md border">
						<div class="flex flex-wrap items-center gap-2 border-b bg-muted/30 px-2 py-1">
							<span class="font-mono font-medium">{tc.name}</span>
							<Badge variant="secondary">{categoryLabel(tc.category)}</Badge>
							<StatusBadge status={tc.outcome} tone={outcomeTone(tc.outcome, tc.failed)} label={outcomeLabel(tc.outcome)} />
							{#if tc.exit_code != null}<span class="font-mono text-muted-foreground">exit {tc.exit_code}</span>{/if}
							{#if tc.duration_s != null}<span class="font-mono text-muted-foreground">{fmtSeconds(tc.duration_s)}</span>{/if}
						</div>
						<pre class="max-h-56 overflow-auto p-2 font-mono text-[11px] whitespace-pre-wrap break-all">{tc.arguments}</pre>
						{#if tc.error}
							<pre class="max-h-40 overflow-auto border-t p-2 font-mono text-[11px] text-danger-fg whitespace-pre-wrap break-all">{tc.error}</pre>
						{/if}
						{#if tc.result != null}
							<div class="border-t">
								<p class="px-2 pt-1 text-[11px] text-muted-foreground">result ({tc.result_source === 'harness_events' ? 'OpenCode event' : 'next request'})</p>
								<pre class="max-h-56 overflow-auto p-2 font-mono text-[11px] whitespace-pre-wrap break-all">{tc.result || '(empty)'}</pre>
							</div>
						{:else}
							<p class="border-t px-2 py-1 text-[11px] text-muted-foreground">No result recorded (the run ended before the next call, or the record is gone).</p>
						{/if}
					</div>
				{/each}
			</div>
		{/if}

		{#if detail.request.system_prompt}
			<div class="space-y-1">
				<button type="button" class="flex cursor-pointer items-center gap-1 text-muted-foreground transition-colors hover:text-foreground" aria-expanded={showSystem} onclick={() => (showSystem = !showSystem)}>
					<ChevronRight class="h-3.5 w-3.5 transition-transform duration-150 {showSystem ? 'rotate-90' : ''}" />
					System prompt sent with this call ({detail.request.system_prompt.length.toLocaleString()} chars){detail.request.tools_offered.length ? ` · tools: ${detail.request.tools_offered.join(', ')}` : ''}
				</button>
				{#if showSystem}
					<pre class="max-h-72 overflow-auto rounded-md border bg-muted/20 p-2 font-mono text-[11px] whitespace-pre-wrap break-words">{detail.request.system_prompt}</pre>
				{/if}
			</div>
		{/if}
	</div>
{/if}
