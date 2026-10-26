<script lang="ts">
	/**
	 * ToolOutputDialog — one tool's raw result for debugging: the exact command,
	 * exit code, target URL (dynamic tools), version, options, warnings, error and
	 * the stored stdout (first 30k characters).
	 *
	 * Props: `open` (bindable), `runId`, `result` (the tool result to show).
	 */
	import * as Dialog from '$lib/components/ui/dialog';
	import { Button } from '$lib/components/ui/button';
	import Copy from '@lucide/svelte/icons/copy';
	import ExternalLink from '@lucide/svelte/icons/external-link';
	import { toast } from 'svelte-sonner';
	import LoadingState from '$lib/components/LoadingState.svelte';
	import ErrorAlert from '$lib/components/ErrorAlert.svelte';
	import { formatApiError } from '$lib/api/core';
	import { getRunResult, type RunToolResult, type ToolResultDetail } from '$lib/api/runs';
	import { formatSpan, resultState } from './format';
	import { safeUrl } from './finding-data';

	interface Props {
		open: boolean;
		runId: string;
		result: RunToolResult | null;
	}

	let { open = $bindable(), runId, result }: Props = $props();

	let detail = $state<ToolResultDetail | null>(null);
	let loading = $state(false);
	let error = $state('');
	let loadedFor = '';

	async function load(id: string) {
		loading = true;
		error = '';
		try {
			detail = await getRunResult(runId, id);
			loadedFor = id;
		} catch (e) {
			error = formatApiError(e, 'Could not load the tool output.');
		} finally {
			loading = false;
		}
	}

	$effect(() => {
		if (open && result && result.id !== loadedFor) {
			detail = null;
			void load(result.id);
		}
	});

	const raw = $derived(detail?.raw_output ?? {});
	const config = $derived(Object.entries((detail?.summary?.config ?? {}) as Record<string, unknown>));
	const stdout = $derived(typeof raw.stdout === 'string' ? raw.stdout : '');

	async function copy(text: string) {
		try {
			await navigator.clipboard.writeText(text);
			toast.success('Copied');
		} catch {
			toast.error('Copy failed');
		}
	}
</script>

<Dialog.Root bind:open>
	<Dialog.Content class="flex max-h-[90dvh] w-[calc(100vw-2rem)] max-w-3xl flex-col gap-3 p-5">
		<Dialog.Header>
			<Dialog.Title class="font-display text-lg">{detail?.tool_name ?? result?.tool_slug ?? 'Tool'} output</Dialog.Title>
			<Dialog.Description class="text-xs text-muted-foreground">
				{#if result}{resultState(result).label}{#if result.duration_seconds != null} · {formatSpan(result.duration_seconds)}{/if}{/if}
				{#if detail} · {detail.findings_count} finding{detail.findings_count === 1 ? '' : 's'} stored{/if}
			</Dialog.Description>
		</Dialog.Header>

		{#if loading}
			<LoadingState label="loading output" />
		{:else if error}
			<ErrorAlert message={error} onretry={() => result && load(result.id)} />
		{:else if detail}
			<div class="min-h-0 flex-1 space-y-3 overflow-y-auto pr-1 text-sm">
				<dl class="grid grid-cols-[7rem_minmax(0,1fr)] gap-x-3 gap-y-1.5 text-xs">
					{#if raw.command}
						<dt class="text-muted-foreground">Command</dt>
						<dd class="flex items-start gap-1.5">
							<code class="min-w-0 flex-1 break-all rounded bg-muted px-1.5 py-0.5 font-mono">{raw.command}</code>
							<button type="button" class="cursor-pointer rounded p-0.5 text-muted-foreground hover:text-foreground" aria-label="Copy command" onclick={() => copy(String(raw.command))}><Copy class="size-3.5" /></button>
						</dd>
					{/if}
					{#if raw.exit_code !== undefined}
						<dt class="text-muted-foreground">Exit code</dt>
						<dd class="font-mono">{raw.exit_code ?? '—'}</dd>
					{/if}
					{#if raw.target_url}
						<dt class="text-muted-foreground">Target</dt>
						<dd class="break-all font-mono">{raw.target_url}</dd>
					{/if}
					{#if detail.summary?.tool_version}
						<dt class="text-muted-foreground">Version</dt>
						<dd class="font-mono">{detail.summary.tool_version}</dd>
					{/if}
					{#if config.length > 0}
						<dt class="text-muted-foreground">Options</dt>
						<dd class="flex flex-wrap gap-1">
							{#each config as [k, v] (k)}
								<span class="rounded bg-muted px-1.5 py-px font-mono text-[11px]">{k}={Array.isArray(v) ? v.join(',') : String(v)}</span>
							{/each}
						</dd>
					{/if}
					{#if detail.tool_docs_url && safeUrl(detail.tool_docs_url)}
						<dt class="text-muted-foreground">Docs</dt>
						<dd>
							<a href={safeUrl(detail.tool_docs_url)} target="_blank" rel="noopener noreferrer" class="inline-flex items-center gap-1 text-primary hover:underline">{detail.tool_docs_url.replace(/^https?:\/\//, '')}<ExternalLink class="size-3" /></a>
						</dd>
					{/if}
				</dl>

				{#if detail.error_message}
					<ErrorAlert title={detail.status === 'failed' ? 'Why it failed' : 'Note'} message={detail.error_message} />
				{/if}
				{#if (detail.summary?.warnings ?? []).length > 0}
					<div class="rounded-md border border-warning/30 bg-warning/10 px-3 py-2 text-xs text-warning-fg">
						{#each detail.summary.warnings as w, i (i)}<p class="[overflow-wrap:anywhere]">{w}</p>{/each}
					</div>
				{/if}

				<div>
					<div class="mb-1 flex items-center justify-between">
						<p class="section-label">Output{stdout.length >= 30000 ? ' (first 30,000 characters)' : ''}</p>
						{#if stdout}<Button variant="ghost" size="sm" class="h-6 px-2 text-xs" onclick={() => copy(stdout)}><Copy class="size-3" />Copy</Button>{/if}
					</div>
					{#if stdout}
						<pre class="max-h-[50dvh] overflow-auto rounded-md border border-border bg-muted/50 p-3 font-mono text-[11px] leading-4 whitespace-pre-wrap [overflow-wrap:anywhere]">{stdout}</pre>
					{:else}
						<p class="text-xs text-muted-foreground">No output was stored{detail.status === 'skipped' ? ' (the tool did not run)' : ''}.</p>
					{/if}
				</div>
			</div>
		{/if}
	</Dialog.Content>
</Dialog.Root>
