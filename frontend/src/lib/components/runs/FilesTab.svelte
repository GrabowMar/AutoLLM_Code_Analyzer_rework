<script lang="ts">
	/**
	 * FilesTab — a sample's files (spec 10.4): scope (model / app / slot), an origin filter with origin badges
	 * (slot variants overlay their frozen parent's rows in app scope as `parent`), a file tree, a highlighted
	 * code viewer and the platform's resolved lock.
	 *
	 * Props: `run`.
	 */
	import * as Card from '$lib/components/ui/card';
	import { Segmented } from '$lib/components/ui/segmented';
	import { Checkbox } from '$lib/components/ui/checkbox';
	import { Input } from '$lib/components/ui/input';
	import { Button } from '$lib/components/ui/button';
	import LoadingState from '$lib/components/LoadingState.svelte';
	import ErrorAlert from '$lib/components/ErrorAlert.svelte';
	import EmptyState from '$lib/components/EmptyState.svelte';
	import SectionHeading from '$lib/components/SectionHeading.svelte';
	import FolderTree from '@lucide/svelte/icons/folder-tree';
	import Copy from '@lucide/svelte/icons/copy';
	import { cn } from '$lib/utils';
	import { formatApiError, isAbortError } from '$lib/api/core';
	import { getSampleFileContent, listSampleFiles } from '$lib/api/v3/samples';
	import type { FileScope, ResolvedLockOut, RunFileOut, RunOut } from '$lib/api/v3/types';
	import { copyText } from '$lib/components/applications/utils';
	import CodeViewer from './CodeViewer.svelte';
	import FileTree from './FileTree.svelte';
	import {
		buildFileTree,
		defaultFileFilter,
		filterFiles,
		fmtInt,
		ORIGIN_HELP,
		originCounts,
		presentOrigins,
		SCOPE_HELP,
		scopesFor,
		shortSha,
		type FileFilter,
	} from './format';

	interface Props {
		run: RunOut;
		/** bump to reload (e.g. when the run completes) */
		version?: number;
	}

	let { run, version = 0 }: Props = $props();

	const scopes = $derived(scopesFor(run.kind));
	let scope = $state<FileScope>('model');
	let filter = $state<FileFilter>(defaultFileFilter());
	let files = $state<RunFileOut[]>([]);
	let loading = $state(true);
	let error = $state('');
	let selected = $state<string | null>(null);
	let content = $state<string | null>(null);
	let contentError = $state('');
	let contentLoading = $state(false);

	$effect(() => {
		// variants open on their regenerated part
		if (!scopes.includes(scope)) scope = scopes[0];
	});

	async function loadFiles(id: string, s: FileScope, signal: AbortSignal) {
		loading = true;
		error = '';
		try {
			files = await listSampleFiles(id, s, undefined, { signal });
			if (!selected || !files.some((f) => f.path === selected)) {
				const first = filterFiles(files, filter).find((f) => !f.excluded && f.origin !== 'deleted');
				selected = first?.path ?? null;
			}
		} catch (e) {
			if (isAbortError(e)) return;
			error = formatApiError(e, 'Could not load the files.');
		} finally {
			loading = false;
		}
	}

	$effect(() => {
		void version;
		const controller = new AbortController();
		void loadFiles(run.id, scope, controller.signal);
		return () => controller.abort();
	});

	const current = $derived(files.find((f) => f.path === selected) ?? null);

	$effect(() => {
		const file = current;
		content = null;
		contentError = '';
		if (!file) return;
		if (file.origin === 'deleted' || !file.blob) {
			contentError = 'This file was deleted by the model; there is no content.';
			return;
		}
		if (file.excluded === 'binary') {
			contentError = 'Binary file: not shown.';
			return;
		}
		const controller = new AbortController();
		contentLoading = true;
		getSampleFileContent(run.id, file.path, { signal: controller.signal })
			.then((text) => (content = text))
			.catch((e) => {
				if (!isAbortError(e)) contentError = formatApiError(e, 'Could not load the file.');
			})
			.finally(() => (contentLoading = false));
		return () => controller.abort();
	});

	const visible = $derived(filterFiles(files, filter));
	const tree = $derived(buildFileTree(visible));
	const counts = $derived(originCounts(files));
	const origins = $derived(presentOrigins(files));
	const hasParent = $derived((counts.parent ?? 0) > 0);
	const totalLoc = $derived(visible.reduce((sum, f) => sum + (f.is_test || f.excluded ? 0 : f.loc), 0));

	function toggleOrigin(origin: string) {
		const set = new Set(filter.origins);
		if (set.has(origin)) set.delete(origin);
		else set.add(origin);
		filter = { ...filter, origins: [...set] };
	}

	const lock = $derived((run.resolved_lock ?? null) as ResolvedLockOut | null);
	const lockEcosystems = $derived(Object.entries(lock?.ecosystems ?? {}).filter(([, pkgs]) => pkgs.length > 0));
</script>

<div class="space-y-4">
	<Card.Root class="gap-0 overflow-clip py-0">
		<div class="table-toolbar flex-wrap gap-2">
			<div class="flex flex-wrap items-center gap-2">
				<Segmented
					size="sm"
					aria-label="File scope"
					bind:value={scope}
					items={scopes.map((s) => ({ value: s, label: s === 'model' ? 'Model' : s === 'app' ? 'App' : 'Slot', title: SCOPE_HELP[s] }))}
				/>
				<div class="flex flex-wrap items-center gap-1" role="group" aria-label="Filter by origin">
					{#each origins as origin (origin)}
						<button
							type="button"
							class={cn(
								'cursor-pointer rounded-full border px-2 py-0.5 text-[11px] transition-colors',
								filter.origins.includes(origin)
									? 'border-primary bg-primary/10 text-primary'
									: 'border-border text-muted-foreground hover:text-foreground',
							)}
							aria-pressed={filter.origins.includes(origin)}
							title={ORIGIN_HELP[origin] ?? origin}
							onclick={() => toggleOrigin(origin)}
						>
							{origin} <span class="tabular-nums">{counts[origin]}</span>
						</button>
					{/each}
				</div>
			</div>
			<div class="flex flex-wrap items-center gap-3 text-xs">
				{#if hasParent}
					<label class="flex items-center gap-1.5" title={ORIGIN_HELP.parent}>
						<Checkbox
							checked={filter.hideParent}
							onCheckedChange={(v) => (filter = { ...filter, hideParent: v === true })}
						/>
						Hide parent files
					</label>
				{/if}
				<label class="flex items-center gap-1.5">
					<Checkbox checked={filter.showTests} onCheckedChange={(v) => (filter = { ...filter, showTests: v === true })} />
					Tests
				</label>
				<label class="flex items-center gap-1.5">
					<Checkbox
						checked={filter.showExcluded}
						onCheckedChange={(v) => (filter = { ...filter, showExcluded: v === true })}
					/>
					Excluded
				</label>
				<Input
					class="h-8 w-44"
					placeholder="Filter paths"
					aria-label="Filter paths"
					value={filter.query}
					oninput={(e) => (filter = { ...filter, query: (e.currentTarget as HTMLInputElement).value })}
				/>
			</div>
		</div>

		{#if loading && files.length === 0}
			<LoadingState label="loading files" />
		{:else if error}
			<div class="p-4"><ErrorAlert message={error} /></div>
		{:else if files.length === 0}
			<EmptyState
				compact
				icon={FolderTree}
				title="No files in this scope"
				description={run.status === 'completed'
					? 'The collector recorded no file here (an empty workspace is an output_invalid outcome, never a clean result).'
					: 'Files are collected when the harness finishes.'}
			/>
		{:else}
			<div class="grid min-h-[24rem] gap-0 md:grid-cols-[minmax(14rem,20rem)_minmax(0,1fr)]">
				<div class="max-h-[70vh] overflow-auto border-b border-border p-2 md:border-r md:border-b-0">
					<p class="px-1.5 pb-1 text-[11px] text-muted-foreground">
						{visible.length} of {files.length} files · {fmtInt(totalLoc)} source LOC shown
					</p>
					{#if visible.length === 0}
						<p class="px-1.5 text-xs text-muted-foreground">No file matches the filter.</p>
					{:else}
						<FileTree nodes={tree} {selected} onselect={(p) => (selected = p)} />
					{/if}
				</div>
				<div class="min-w-0 space-y-2 p-3">
					{#if current}
						<div class="flex flex-wrap items-center gap-2 text-xs">
							<span class="font-mono font-medium">{current.path}</span>
							<span class="rounded border border-border px-1 text-[11px] text-muted-foreground" title={ORIGIN_HELP[current.origin] ?? ''}>{current.origin}</span>
							<span class="text-muted-foreground">{current.language || 'text'} · {fmtInt(current.loc)} LOC · {fmtInt(current.size)} B</span>
							{#if current.excluded}<span class="text-muted-foreground">excluded: {current.excluded}</span>{/if}
							{#if content !== null}
								<Button
									variant="ghost"
									size="sm"
									class="ml-auto h-7"
									onclick={() => copyText(content ?? '', 'File copied')}
								>
									<Copy class="mr-1 h-3.5 w-3.5" />Copy
								</Button>
							{/if}
						</div>
						{#if contentError}
							<p class="text-sm text-muted-foreground">{contentError}</p>
						{:else if contentLoading && content === null}
							<LoadingState label="loading file" class="py-8" />
						{:else if content !== null}
							<CodeViewer code={content} language={current.language} path={current.path} class="max-h-[65vh]" />
						{/if}
					{:else}
						<p class="text-sm text-muted-foreground">Pick a file.</p>
					{/if}
				</div>
			</div>
		{/if}
	</Card.Root>

	<SectionHeading size="sm" title="Resolved lock" lead="The platform resolves and records a lock for every sample; builds and re-evaluations install under it." />
	{#if !lock}
		<p class="text-sm text-muted-foreground">
			No lock recorded{run.lock_source ? ` (lock source: ${run.lock_source})` : ''}. It is written by the evaluation's lock stage.
		</p>
	{:else}
		<Card.Root>
			<Card.Content class="space-y-3 text-xs">
				<p class="text-muted-foreground">
					Resolved {lock.resolved_at} · source <span class="font-mono">{run.lock_source || '—'}</span>
				</p>
				{#if Object.keys(lock.files ?? {}).length}
					<div>
						<p class="section-label">Lock files</p>
						<ul class="space-y-0.5">
							{#each Object.entries(lock.files) as [path, sha] (path)}
								<li><span class="font-mono">{path}</span> <span class="font-mono text-muted-foreground" title={sha}>{shortSha(sha)}</span></li>
							{/each}
						</ul>
					</div>
				{/if}
				{#each lockEcosystems as [ecosystem, packages] (ecosystem)}
					<details>
						<summary class="cursor-pointer"><span class="font-mono">{ecosystem}</span> · {packages.length} packages</summary>
						<div class="mt-1 grid grid-cols-1 gap-x-4 sm:grid-cols-2 lg:grid-cols-3">
							{#each packages as pkg (pkg.name)}
								<span class="font-mono">{pkg.name}=={pkg.version}</span>
							{/each}
						</div>
					</details>
				{/each}
			</Card.Content>
		</Card.Root>
	{/if}
</div>
