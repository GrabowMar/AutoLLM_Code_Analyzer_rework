<script lang="ts">
	// Expanded view of one compact `agent_call_NNNN` artifact: what the harness
	// sent (model, params, last message) and what came back (text, reasoning,
	// tool calls, usage). Texts were truncated at ingestion; the full call is in
	// the run record (Agent Run tab › Trajectory).
	import { Badge } from '$lib/components/ui/badge';
	import ChevronRight from '@lucide/svelte/icons/chevron-right';
	import type { GenerationArtifact } from '$lib/api/client';
	import CallTelemetry from './CallTelemetry.svelte';
	import { fmtInt, fmtSeconds, prettyArgs } from './format';

	interface Props {
		art: GenerationArtifact & { telemetry?: unknown };
	}

	let { art }: Props = $props();

	let showReasoning = $state(false);
	let showParams = $state(false);

	const request = $derived((art.request_payload ?? {}) as Record<string, any>);
	const response = $derived((art.response_payload ?? {}) as Record<string, any>);
	const usage = $derived((response.usage ?? {}) as Record<string, any>);
	const toolCalls = $derived((Array.isArray(response.tool_calls) ? response.tool_calls : []) as Record<string, any>[]);
	const lastMessage = $derived(parseMessage(request.last_message));
	const policyDiff = $derived(request.policy_diff && Object.keys(request.policy_diff).length ? request.policy_diff : null);

	function parseMessage(raw: unknown): { role: string | null; content: string } | null {
		if (typeof raw !== 'string' || !raw) return null;
		try {
			const msg = JSON.parse(raw);
			if (msg && typeof msg === 'object') {
				const content = Array.isArray(msg.content)
					? msg.content.map((p: any) => (typeof p?.text === 'string' ? p.text : '')).join('')
					: typeof msg.content === 'string'
						? msg.content
						: JSON.stringify(msg.content ?? '', null, 2);
				return { role: typeof msg.role === 'string' ? msg.role : null, content };
			}
		} catch {
			// Truncated at ingestion (no longer valid JSON): unwrap the common prefix.
			const m = /^\{"role": "(\w+)", (?:"tool_call_id": "[^"]*", )?"content": "/.exec(raw);
			if (m) {
				const body = raw
					.slice(m[0].length)
					.replace(/\\n/g, '\n')
					.replace(/\\t/g, '\t')
					.replace(/\\"/g, '"');
				return { role: m[1], content: body };
			}
		}
		return { role: null, content: raw };
	}
</script>

<div class="space-y-3 text-xs">
	<dl class="grid grid-cols-2 gap-x-4 gap-y-1 sm:grid-cols-4">
		<div><dt class="text-[11px] text-muted-foreground">Status</dt><dd class="font-mono">{response.status ?? '—'}</dd></div>
		<div><dt class="text-[11px] text-muted-foreground">Duration</dt><dd class="font-mono tabular-nums">{fmtSeconds(response.duration_s)}</dd></div>
		<div><dt class="text-[11px] text-muted-foreground">Provider</dt><dd class="truncate font-mono">{response.provider ?? '—'}</dd></div>
		<div><dt class="text-[11px] text-muted-foreground">Finish</dt><dd class="font-mono">{response.finish_reason ?? '—'}</dd></div>
		<div><dt class="text-[11px] text-muted-foreground">Messages sent</dt><dd class="font-mono tabular-nums">{fmtInt(request.message_count)}</dd></div>
		<div><dt class="text-[11px] text-muted-foreground">Tools offered</dt><dd class="font-mono tabular-nums">{fmtInt(request.tool_count)}</dd></div>
		<div><dt class="text-[11px] text-muted-foreground">Cached tokens</dt><dd class="font-mono tabular-nums">{fmtInt(usage.prompt_tokens_details?.cached_tokens)}</dd></div>
		<div><dt class="text-[11px] text-muted-foreground">Reasoning tokens</dt><dd class="font-mono tabular-nums">{fmtInt(usage.completion_tokens_details?.reasoning_tokens)}</dd></div>
	</dl>

	<CallTelemetry telemetry={art.telemetry} />

	{#if response.error}
		<pre class="max-h-32 overflow-auto rounded-md border border-destructive/30 p-2 font-mono text-[11px] text-danger-fg whitespace-pre-wrap break-all">{typeof response.error === 'string' ? response.error : JSON.stringify(response.error, null, 2)}</pre>
	{/if}

	{#if lastMessage}
		<div class="rounded-md border">
			<div class="flex items-center gap-2 border-b bg-muted/30 px-2 py-1">
				<Badge variant="outline" class="text-[11px]">{lastMessage.role ?? 'message'}</Badge>
				<span class="text-muted-foreground">last message of the request</span>
			</div>
			<pre class="max-h-48 overflow-auto p-2 font-mono text-[11px] whitespace-pre-wrap break-all">{lastMessage.content}</pre>
		</div>
	{/if}

	{#if response.reasoning}
		<div class="space-y-1">
			<button type="button" class="flex cursor-pointer items-center gap-1 text-muted-foreground transition-colors hover:text-foreground" aria-expanded={showReasoning} onclick={() => (showReasoning = !showReasoning)}>
				<ChevronRight class="h-3.5 w-3.5 transition-transform duration-150 {showReasoning ? 'rotate-90' : ''}" />
				Reasoning ({String(response.reasoning).length.toLocaleString()} chars)
			</button>
			{#if showReasoning}
				<pre class="max-h-64 overflow-auto rounded-md border bg-muted/20 p-2 font-mono text-[11px] whitespace-pre-wrap break-words">{response.reasoning}</pre>
			{/if}
		</div>
	{/if}

	{#if response.content}
		<div class="space-y-1">
			<p class="section-label">Response text</p>
			<pre class="max-h-56 overflow-auto rounded-md border bg-muted/20 p-2 font-mono text-[11px] whitespace-pre-wrap break-words">{response.content}</pre>
		</div>
	{/if}

	{#if toolCalls.length}
		<div class="space-y-1.5">
			<p class="section-label">Tool calls ({toolCalls.length})</p>
			{#each toolCalls as tc, i (tc.id ?? i)}
				<div class="rounded-md border">
					<div class="flex items-center gap-2 border-b bg-muted/30 px-2 py-1">
						<span class="font-mono font-medium">{tc.name ?? 'tool'}</span>
						{#if tc.id}<span class="font-mono text-[11px] text-muted-foreground">{tc.id}</span>{/if}
					</div>
					<pre class="max-h-56 overflow-auto p-2 font-mono text-[11px] whitespace-pre-wrap break-all">{prettyArgs(tc.arguments)}</pre>
				</div>
			{/each}
		</div>
	{/if}

	<div class="space-y-1">
		<button type="button" class="flex cursor-pointer items-center gap-1 text-muted-foreground transition-colors hover:text-foreground" aria-expanded={showParams} onclick={() => (showParams = !showParams)}>
			<ChevronRight class="h-3.5 w-3.5 transition-transform duration-150 {showParams ? 'rotate-90' : ''}" />
			Request params{policyDiff ? ' and gateway policy changes' : ''}
		</button>
		{#if showParams}
			<pre class="max-h-48 overflow-auto rounded-md border bg-muted/20 p-2 font-mono text-[11px] whitespace-pre-wrap break-all">{JSON.stringify({ model: request.model, params: request.params ?? {}, policy_diff: policyDiff, usage }, null, 2)}</pre>
		{/if}
	</div>
	<p class="text-[11px] text-muted-foreground">Texts here were truncated when stored; the full call is in the Agent Run tab's trajectory.</p>
</div>
