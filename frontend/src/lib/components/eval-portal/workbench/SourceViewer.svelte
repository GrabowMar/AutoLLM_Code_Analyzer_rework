<script lang="ts">
	/**
	 * SourceViewer — the app's source files (when the study shows them): a
	 * filterable file list and the selected file as plain text with line
	 * numbers. `target` opens a file at a line (e.g. from a finding).
	 *
	 * Props: `assignmentId`, `slot`, `label`, `target` ({path, line, seq} | null).
	 */
	import { untrack } from 'svelte';
	import FileText from '@lucide/svelte/icons/file-text';
	import Search from '@lucide/svelte/icons/search';
	import { getSourceFile, getSourceTree } from '$lib/api/human-eval-portal';
	import { isAbortError } from '$lib/api/core';
	import type { SourceFile, SourceFileEntry } from '$lib/api/types/human-eval';
	import ErrorAlert from '$lib/components/ErrorAlert.svelte';
	import LoadingState from '$lib/components/LoadingState.svelte';
	import EmptyState from '$lib/components/EmptyState.svelte';
	import { Input } from '$lib/components/ui/input';
	import { cn } from '$lib/utils';
	import { formatBytes } from './observations';
	import { workbenchError, type Slot } from './workbench';

	interface Props {
		assignmentId: string;
		slot: Slot;
		label: string;
		target?: { path: string; line: number | null; seq: number } | null;
	}

	let { assignmentId, slot, label, target = null }: Props = $props();

	const LINE_PX = 20;

	let files = $state.raw<SourceFileEntry[]>([]);
	let treeLoading = $state(false);
	let treeError = $state('');
	let filter = $state('');
	let selected = $state('');
	let file = $state.raw<SourceFile | null>(null);
	let fileLoading = $state(false);
	let fileError = $state('');
	let pendingLine = $state<number | null>(null);
	let codeBox = $state<HTMLElement | null>(null);
	let treeCtl: AbortController | null = null;
	let fileCtl: AbortController | null = null;

	const shown = $derived(
		filter.trim() ? files.filter((f) => f.path.toLowerCase().includes(filter.trim().toLowerCase())) : files
	);
	const lines = $derived(file ? file.content.split('\n') : []);
	const gutter = $derived(lines.map((_, i) => i + 1).join('\n'));

	async function loadTree(forSlot: Slot) {
		treeCtl?.abort();
		const ctl = new AbortController();
		treeCtl = ctl;
		treeLoading = true;
		treeError = '';
		try {
			const tree = await getSourceTree(assignmentId, forSlot, { signal: ctl.signal });
			files = [...(tree.files ?? [])].sort((a, b) => a.path.localeCompare(b.path));
			if (!selected || !files.some((f) => f.path === selected)) {
				const first = files.find((f) => /(^|\/)(app|main|index|server)\.[a-z]+$/i.test(f.path)) ?? files[0];
				if (first && !target) void openFile(first.path);
			}
		} catch (e) {
			if (isAbortError(e) && ctl.signal.aborted) return;
			treeError = workbenchError(e, 'Could not load the source files.');
		} finally {
			if (treeCtl === ctl) treeLoading = false;
		}
	}

	async function openFile(path: string, line: number | null = null) {
		fileCtl?.abort();
		const ctl = new AbortController();
		fileCtl = ctl;
		selected = path;
		pendingLine = line;
		fileLoading = true;
		fileError = '';
		try {
			file = await getSourceFile(assignmentId, slot, path, { signal: ctl.signal });
		} catch (e) {
			if (isAbortError(e) && ctl.signal.aborted) return;
			file = null;
			fileError = workbenchError(e, 'Could not open this file.');
		} finally {
			if (fileCtl === ctl) fileLoading = false;
		}
	}

	// Reload when the slot changes.
	$effect(() => {
		const s = slot;
		untrack(() => {
			selected = '';
			file = null;
			void loadTree(s);
		});
		return () => {
			treeCtl?.abort();
			fileCtl?.abort();
		};
	});

	// Jump to a requested file and line.
	$effect(() => {
		const t = target;
		if (!t?.path) return;
		untrack(() => void openFile(t.path, t.line));
	});

	// Scroll the requested line into view once the file is rendered.
	$effect(() => {
		const line = pendingLine;
		const box = codeBox;
		if (!line || !box || !file || fileLoading) return;
		box.scrollTop = Math.max(0, (line - 4) * LINE_PX);
		untrack(() => (pendingLine = null));
	});
</script>

<div class="flex h-full min-h-0 flex-col gap-2 md:flex-row">
	<div class="flex min-h-0 flex-col gap-2 md:w-60 md:shrink-0">
		<div class="relative">
			<Search class="pointer-events-none absolute left-2.5 top-1/2 size-3.5 -translate-y-1/2 text-muted-foreground" aria-hidden="true" />
			<Input bind:value={filter} placeholder="Filter files" aria-label="Filter files of {label}" class="h-8 pl-8 text-xs" />
		</div>
		{#if treeError}
			<ErrorAlert message={treeError} onretry={() => loadTree(slot)} />
		{:else if treeLoading && files.length === 0}
			<LoadingState label="loading files" class="py-6" />
		{:else if files.length === 0}
			<EmptyState compact title="No source files" />
		{:else}
			<ul class="max-h-48 min-h-0 overflow-y-auto rounded-md border border-border md:max-h-none md:flex-1" aria-label="Files of {label}">
				{#each shown as entry (entry.path)}
					<li>
						<button
							type="button"
							class={cn(
								'flex w-full cursor-pointer items-center gap-1.5 px-2 py-1 text-left font-mono text-[11px] hover:bg-accent',
								entry.path === selected && 'bg-primary/10 text-primary'
							)}
							aria-current={entry.path === selected ? 'true' : undefined}
							title="{entry.path} · {formatBytes(entry.size)}"
							onclick={() => openFile(entry.path)}
						>
							<FileText class="size-3 shrink-0 opacity-60" aria-hidden="true" />
							<span class="min-w-0 truncate">{entry.path}</span>
						</button>
					</li>
				{:else}
					<li class="px-2 py-2 text-xs text-muted-foreground">No file matches “{filter}”.</li>
				{/each}
			</ul>
		{/if}
	</div>

	<div class="flex min-h-0 min-w-0 flex-1 flex-col rounded-md border border-border bg-card">
		{#if selected}
			<div class="flex items-center justify-between gap-2 border-b border-border px-3 py-1.5">
				<p class="min-w-0 truncate font-mono text-xs font-medium" title={selected}>{selected}</p>
				{#if file?.truncated}<span class="shrink-0 text-[11px] text-warning-fg">Shortened: the file is larger than 512 KB</span>{/if}
			</div>
		{/if}
		{#if fileError}
			<div class="p-3"><ErrorAlert message={fileError} onretry={() => openFile(selected)} /></div>
		{:else if fileLoading && !file}
			<LoadingState label="loading file" class="py-6" />
		{:else if file}
			<div bind:this={codeBox} class="min-h-[12rem] flex-1 overflow-auto" tabindex="-1">
				<div class="flex min-w-max font-mono text-xs" style="line-height: {LINE_PX}px">
					<pre class="sticky left-0 select-none border-r border-border bg-card px-2 py-2 text-right text-muted-foreground/70" aria-hidden="true">{gutter}</pre>
					<pre class="px-3 py-2"><code>{file.content}</code></pre>
				</div>
			</div>
		{:else}
			<EmptyState compact title="Pick a file" description="Choose a file on the left to read it." />
		{/if}
	</div>
</div>
