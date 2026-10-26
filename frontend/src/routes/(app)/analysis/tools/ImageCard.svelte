<script lang="ts">
	/**
	 * ImageCard — the pinned analyzer image: tag, build state, manifest tool
	 * versions and baked config hashes, with a build action.
	 *
	 * Props: `image` (`ImageStatus` or null while loading), `error` (load
	 * failure), `building` (a build request is in flight), `onbuild()`, `onrefresh()`.
	 */
	import type { ImageStatus } from '$lib/api/analysis';
	import { formatDate } from '$lib/utils/formatters';
	import { cn } from '$lib/utils';
	import { Button } from '$lib/components/ui/button';
	import * as Card from '$lib/components/ui/card';
	import StatusBadge from '$lib/components/StatusBadge.svelte';
	import ErrorAlert from '$lib/components/ErrorAlert.svelte';
	import LoadingState from '$lib/components/LoadingState.svelte';
	import { shortHash } from '$lib/components/analysis/profile/profile-utils';
	import ChevronRight from '@lucide/svelte/icons/chevron-right';
	import Hammer from '@lucide/svelte/icons/hammer';
	import HardDrive from '@lucide/svelte/icons/hard-drive';
	import LoaderCircle from '@lucide/svelte/icons/loader-circle';
	import RefreshCw from '@lucide/svelte/icons/refresh-cw';

	interface Props {
		image: ImageStatus | null;
		error?: string;
		requesting?: boolean;
		onbuild?: () => void;
		onrefresh?: () => void;
	}

	let { image, error = '', requesting = false, onbuild, onrefresh }: Props = $props();

	let configsOpen = $state(false);

	const imageState = $derived.by(() => {
		if (!image) return { status: 'pending', label: 'checking' };
		if (!image.daemon_ok) return { status: 'unhealthy', label: 'daemon unreachable' };
		if (image.building) return { status: 'building', label: 'building' };
		if (image.built) return { status: 'ready', label: 'built' };
		return { status: 'image_missing', label: 'not built' };
	});

	const manifest = $derived(image?.manifest ?? null);
	const manifestTools = $derived(
		manifest ? Object.entries(manifest.tools).sort(([a], [b]) => a.localeCompare(b)) : []
	);
	const configs = $derived(manifest ? Object.entries(manifest.configs ?? {}) : []);
	const canBuild = $derived(
		!!image && image.daemon_ok && !image.built && !image.building && !requesting
	);

	function formatSize(bytes: number | null | undefined): string {
		if (bytes == null) return '—';
		return `${(bytes / 1024 ** 3).toLocaleString(undefined, { maximumFractionDigits: 2 })} GB`;
	}
</script>

<Card.Root class="flex flex-col">
	<Card.Header class="pb-3">
		<div class="flex flex-wrap items-start justify-between gap-2">
			<div class="min-w-0 space-y-1">
				<Card.Title class="flex items-center gap-2 text-base">
					<HardDrive class="size-4 text-muted-foreground" aria-hidden="true" />Analyzer image
				</Card.Title>
				<Card.Description>
					Every tool runs from one pinned image. Its tag hashes the image definition, so any tool or
					config change yields a new tag — and new plan hashes.
				</Card.Description>
			</div>
			<StatusBadge status={imageState.status} label={imageState.label} />
		</div>
	</Card.Header>
	<Card.Content class="flex-1 space-y-4 text-sm">
		{#if error && !image}
			<ErrorAlert message={error} onretry={onrefresh} />
		{:else if !image}
			<LoadingState label="checking image" class="py-8" />
		{:else}
			{#if image.error}
				<p
					class="rounded-md border border-destructive/30 bg-destructive/10 px-3 py-2 text-xs break-words text-danger-fg"
					role="alert"
				>
					{image.error}
				</p>
			{/if}
			<dl class="grid grid-cols-[auto_minmax(0,1fr)] gap-x-4 gap-y-1.5 text-xs">
				<dt class="text-muted-foreground">Tag</dt>
				<dd class="break-all font-mono">{image.tag || '—'}</dd>
				<dt class="text-muted-foreground">Image id</dt>
				<dd class="font-mono" title={image.image_id ?? ''}>
					{image.image_id ? shortHash(image.image_id.replace(/^sha256:/, ''), 16) : '—'}
				</dd>
				<dt class="text-muted-foreground">Built</dt>
				<dd>{image.created ? formatDate(image.created) : '—'}</dd>
				<dt class="text-muted-foreground">Size</dt>
				<dd class="tabular-nums">{formatSize(image.size_bytes)}</dd>
				{#if manifest}
					<dt class="text-muted-foreground">Release cutoff</dt>
					<dd title="Every tool version was released on or before this date">
						{manifest.release_cutoff}
					</dd>
					<dt class="text-muted-foreground">Runtime</dt>
					<dd class="font-mono">
						python {manifest.runtime.python} · node {manifest.runtime.node} · debian {manifest
							.runtime.os} · uid {manifest.runtime.uid}
					</dd>
				{/if}
			</dl>

			{#if image.building}
				<p class="flex items-center gap-2 text-xs text-muted-foreground" role="status">
					<LoaderCircle
						class="size-3.5 animate-spin motion-reduce:animate-none"
						aria-hidden="true"
					/>
					Building in the background; this page refreshes when it is done (several minutes on a cold
					cache).
				</p>
			{:else if !image.built && image.daemon_ok}
				<p class="text-xs text-muted-foreground">
					Not built yet: tool versions below are unverified and runs build the image before their
					first tool.
				</p>
			{/if}

			{#if manifestTools.length > 0}
				<div class="table-scroll-wrapper rounded-md border border-border">
					<table class="text-xs">
						<caption class="sr-only">Tool versions in the image manifest</caption>
						<thead>
							<tr>
								<th scope="col">Tool</th>
								<th scope="col">Version</th>
								<th scope="col" class="hide-mobile">Runtime</th>
								<th scope="col" class="hide-mobile">Lock sha256</th>
							</tr>
						</thead>
						<tbody>
							{#each manifestTools as [slug, entry] (slug)}
								<tr>
									<td class="font-medium">{slug}</td>
									<td class="font-mono">{entry.version}</td>
									<td class="hide-mobile text-muted-foreground">{entry.runtime ?? '—'}</td>
									<td
										class="hide-mobile font-mono text-muted-foreground"
										title={String(entry.lock_sha256 ?? entry.sha256 ?? '')}
									>
										{shortHash(String(entry.lock_sha256 ?? entry.sha256 ?? ''), 12)}
									</td>
								</tr>
							{/each}
						</tbody>
					</table>
				</div>
			{/if}

			{#if configs.length > 0}
				<div class="rounded-md border border-border">
					<button
						type="button"
						class="flex w-full cursor-pointer items-center gap-2 px-3 py-2 text-left text-xs font-medium transition-colors hover:bg-muted/40 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-inset focus-visible:ring-ring/40"
						aria-expanded={configsOpen}
						aria-controls="image-configs"
						onclick={() => (configsOpen = !configsOpen)}
					>
						<ChevronRight
							class={cn(
								'size-3.5 text-muted-foreground transition-transform duration-150',
								configsOpen && 'rotate-90'
							)}
							aria-hidden="true"
						/>
						Baked configs
						<span class="font-normal text-muted-foreground">({configs.length}, sha256)</span>
					</button>
					{#if configsOpen}
						<ul id="image-configs" class="divide-y divide-border/60 border-t border-border text-xs">
							{#each configs as [file, sha] (file)}
								<li class="flex flex-wrap items-baseline justify-between gap-x-3 px-3 py-1.5">
									<span class="font-mono">{file}</span>
									<span class="font-mono text-muted-foreground" title={sha}
										>{shortHash(sha, 16)}</span
									>
								</li>
							{/each}
						</ul>
					{/if}
				</div>
			{/if}
		{/if}
	</Card.Content>
	<Card.Footer class="flex flex-wrap justify-end gap-2 border-t border-border pt-4">
		<Button variant="ghost" size="sm" onclick={onrefresh} disabled={!image && !error}>
			<RefreshCw />Refresh
		</Button>
		{#if image && !image.built}
			<Button
				size="sm"
				onclick={onbuild}
				disabled={!canBuild}
				title={!image.daemon_ok ? 'The analysis Docker daemon is unreachable' : undefined}
			>
				{#if requesting || image.building}<LoaderCircle
						class="animate-spin motion-reduce:animate-none"
					/>{:else}<Hammer />{/if}
				{image.building ? 'Building…' : 'Build image'}
			</Button>
		{/if}
	</Card.Footer>
</Card.Root>
