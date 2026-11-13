<script lang="ts">
	/**
	 * StarterViewer — read-only starter: stack profile, level, digest-pinned base images, lock files, the
	 * forbidden-content check, the file tree (`GET /library/starters/{slug}/versions/{v}/tree/`) and the
	 * text of any file (blob store). Starters are seeded; users fork them only via YAML package import.
	 *
	 * Props: `slug`, `version`, `value` (StarterContent).
	 */
	import ErrorAlert from '$lib/components/ErrorAlert.svelte';
	import LoadingState from '$lib/components/LoadingState.svelte';
	import FileText from '@lucide/svelte/icons/file-text';
	import { formatApiError, isAbortError } from '$lib/api/core';
	import { getLibraryBlob, getStarterTree } from '$lib/api/v3/library';
	import type { StarterContent, StarterTreeOut } from '$lib/api/v3/types';
	import { cn } from '$lib/utils';
	import PinnedRefBadge from './PinnedRefBadge.svelte';
	import { formatBytes } from './policy';

	interface Props {
		slug: string;
		version: number;
		value: StarterContent;
	}

	let { slug, version, value }: Props = $props();

	let tree = $state<StarterTreeOut | null>(null);
	let error = $state('');
	let selected = $state<string | null>(null);
	let fileText = $state('');
	let fileError = $state('');
	let fileLoading = $state(false);

	$effect(() => {
		const controller = new AbortController();
		tree = null;
		error = '';
		getStarterTree(slug, version, { signal: controller.signal })
			.then((t) => {
				tree = t;
				selected = value.dockerfile;
			})
			.catch((e) => !isAbortError(e) && (error = formatApiError(e, 'Could not load the starter tree.')));
		return () => controller.abort();
	});

	$effect(() => {
		const path = selected;
		if (!path || !tree) return;
		const entry = tree.files.find((f) => f.path === path);
		if (!entry) return;
		if (path === value.dockerfile && tree.dockerfile) {
			fileText = tree.dockerfile;
			return;
		}
		const controller = new AbortController();
		fileLoading = true;
		fileError = '';
		getLibraryBlob(entry.blob, { signal: controller.signal })
			.then((t) => (fileText = t))
			.catch((e) => !isAbortError(e) && (fileError = formatApiError(e, 'Could not load the file.')))
			.finally(() => (fileLoading = false));
		return () => controller.abort();
	});
</script>

<div class="space-y-4">
	<dl class="grid gap-3 sm:grid-cols-2 lg:grid-cols-4">
		<div><dt class="section-label">Stack profile</dt><dd class="pt-1"><PinnedRefBadge ref={value.stack_profile} kind="stack" /></dd></div>
		<div><dt class="section-label">Level</dt><dd class="pt-1 text-sm">{value.level}</dd></div>
		<div><dt class="section-label">Forbidden content</dt><dd class="pt-1 text-sm">{value.forbidden_content_check.passed ? 'passed' : 'FAILED'} <span class="font-mono text-[11px] text-muted-foreground">linter v{value.forbidden_content_check.linter_version}</span></dd></div>
		<div><dt class="section-label">Lock files</dt><dd class="pt-1 font-mono text-xs">{value.lockfiles.join(', ') || 'none'}</dd></div>
	</dl>

	<div class="space-y-1">
		<p class="section-label">Base images</p>
		<ul class="space-y-0.5">
			{#each value.base_images as img (img.ref)}
				<li class="break-all font-mono text-xs">{img.ref} <span class="text-muted-foreground">@{img.digest}</span></li>
			{/each}
		</ul>
	</div>

	{#if value.install.length}
		<div class="space-y-1">
			<p class="section-label">Install (informational)</p>
			<pre class="overflow-x-auto rounded-md border border-border bg-surface-1 p-2 font-mono text-xs">{value.install.join('\n')}</pre>
		</div>
	{/if}

	{#if error}
		<ErrorAlert message={error} />
	{:else if !tree}
		<LoadingState label="loading the tree" />
	{:else}
		<div class="grid gap-3 lg:grid-cols-[18rem_minmax(0,1fr)]">
			<ul class="max-h-[32rem] overflow-y-auto rounded-md border border-border py-1" aria-label="Files">
				{#each tree.files as f (f.path)}
					<li>
						<button
							type="button"
							class={cn('flex w-full cursor-pointer items-center gap-2 px-2 py-1 text-left font-mono text-xs', selected === f.path ? 'bg-primary/10 text-primary' : 'hover:bg-muted')}
							aria-current={selected === f.path || undefined}
							onclick={() => (selected = f.path)}
						>
							<FileText class="size-3.5 shrink-0" aria-hidden="true" />
							<span class="min-w-0 flex-1 truncate">{f.path}</span>
							{#if f.size !== null}<span class="text-[11px] text-muted-foreground">{formatBytes(f.size)}</span>{/if}
						</button>
					</li>
				{/each}
			</ul>
			<div class="min-w-0">
				{#if fileError}
					<ErrorAlert message={fileError} />
				{:else if fileLoading}
					<LoadingState label="loading the file" />
				{:else if selected}
					<p class="pb-1 font-mono text-[11px] text-muted-foreground">{selected}</p>
					<pre class="max-h-[32rem] overflow-auto rounded-md border border-border bg-surface-1 p-3 font-mono text-xs leading-relaxed">{fileText}</pre>
				{/if}
			</div>
		</div>
	{/if}
</div>
