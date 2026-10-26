<script lang="ts" module>
	import { getRunFileContent, type RunFileContent } from '$lib/api/analysis';

	// One request per (run, path) per page load: the drawer and the Files tab
	// reopen the same files often. Failed loads are dropped so Retry refetches.
	const cache = new Map<string, Promise<RunFileContent>>();

	function loadFile(runId: string, path: string): Promise<RunFileContent> {
		const key = `${runId}\u0000${path}`;
		let entry = cache.get(key);
		if (!entry) {
			entry = getRunFileContent(runId, path);
			cache.set(key, entry);
			entry.catch(() => cache.delete(key));
		}
		return entry;
	}
</script>

<script lang="ts">
	/**
	 * RunFileViewer — one file of an analysed sample (re-resolved and
	 * sha-verified by the API) in a {@link CodeViewer}.
	 *
	 * Props:
	 * - `runId`, `path`.
	 * - `highlight`, `context`, `markers`, `maxHeight`: passed to CodeViewer.
	 * - `fallbackSnippet` + `fallbackLine`: shown when the file cannot be
	 *   loaded (changed since the run, source gone, binary, too large).
	 * - `onlineclick(line)`: passed to CodeViewer.
	 */
	import CodeViewer, { type LineMarker } from './CodeViewer.svelte';
	import { formatApiError } from '$lib/api/core';
	import { Button } from '$lib/components/ui/button';
	import { toneClasses } from '$lib/constants/colors';
	import { cn } from '$lib/utils';
	import FileWarning from '@lucide/svelte/icons/file-warning';
	import LoaderCircle from '@lucide/svelte/icons/loader-circle';
	import RefreshCw from '@lucide/svelte/icons/refresh-cw';

	interface Props {
		runId: string;
		path: string;
		highlight?: { start: number; end?: number | null } | null;
		context?: number | null;
		markers?: Record<number, LineMarker>;
		maxHeight?: string;
		fallbackSnippet?: string;
		fallbackLine?: number | null;
		onlineclick?: (line: number) => void;
		class?: string;
	}

	let {
		runId,
		path,
		highlight = null,
		context = null,
		markers = {},
		maxHeight,
		fallbackSnippet = '',
		fallbackLine = null,
		onlineclick,
		class: className
	}: Props = $props();

	let file = $state<RunFileContent | null>(null);
	let loading = $state(true);
	let error = $state('');
	let attempt = $state(0);
	let seq = 0;

	$effect(() => {
		const id = runId;
		const p = path;
		void attempt;
		const mine = ++seq;
		loading = true;
		error = '';
		file = null;
		loadFile(id, p)
			.then((content) => {
				if (mine !== seq) return;
				file = content;
			})
			.catch((e) => {
				if (mine !== seq) return;
				file = null;
				error = formatApiError(e, 'Could not load this file.');
			})
			.finally(() => {
				if (mine === seq) loading = false;
			});
	});

	const unavailable = $derived.by(() => {
		if (error) return error;
		if (!file) return '';
		if (file.binary) return 'This is a binary file; it cannot be shown.';
		if (file.too_large)
			return 'This file is too large to show here; download the run export for its content.';
		if (file.content == null) return 'The file content is not available.';
		return '';
	});
</script>

{#if loading && !file}
	<div
		class={cn(
			'flex items-center gap-2 rounded-md border border-border bg-surface-2 px-3 py-6 text-xs text-muted-foreground',
			className
		)}
		role="status"
	>
		<LoaderCircle class="size-3.5 animate-spin motion-reduce:animate-none" aria-hidden="true" />
		Loading {path}…
	</div>
{:else if unavailable}
	<div class={cn('space-y-2', className)}>
		<div
			class={cn(
				'flex flex-wrap items-start gap-2 rounded-md border px-3 py-2 text-xs',
				toneClasses.warning
			)}
			role="status"
		>
			<FileWarning class="mt-px size-3.5 shrink-0" aria-hidden="true" />
			<span class="min-w-0 flex-1">{unavailable}</span>
			{#if error}
				<Button variant="ghost" size="sm" class="h-6 px-2" onclick={() => attempt++}>
					<RefreshCw class="size-3" />Retry
				</Button>
			{/if}
		</div>
		{#if fallbackSnippet}
			<p class="text-[11px] text-muted-foreground">
				Snippet recorded by the tool at analysis time:
			</p>
			<CodeViewer
				code={fallbackSnippet}
				{path}
				language={file?.language ?? null}
				startLine={fallbackLine ?? 1}
				highlight={fallbackLine ? { start: fallbackLine } : null}
				header={false}
				maxHeight="max-h-48"
			/>
		{/if}
	</div>
{:else if file && file.content != null}
	<CodeViewer
		code={file.content}
		{path}
		language={file.language}
		{highlight}
		{context}
		{markers}
		changedLines={file.changed_lines}
		{onlineclick}
		{maxHeight}
		class={className}
	/>
{/if}
