<script lang="ts">
	import { formatDateTime as formatDate } from '$lib/utils/formatters';
	import { containerTones } from '$lib/constants/colors';
	import * as Card from '$lib/components/ui/card';
	import PageHeader from '$lib/components/PageHeader.svelte';
	import StatusBadge from '$lib/components/StatusBadge.svelte';
	import ErrorAlert from '$lib/components/ErrorAlert.svelte';
	import LoadingState from '$lib/components/LoadingState.svelte';
	import EmptyState from '$lib/components/EmptyState.svelte';
	import { Button } from '$lib/components/ui/button';
	import ConfirmDialog from '$lib/components/ConfirmDialog.svelte';
	import { formatApiError } from '$lib/api/core';
	import { toast } from 'svelte-sonner';
	import RefreshCw from '@lucide/svelte/icons/refresh-cw';
	import Play from '@lucide/svelte/icons/play';
	import Square from '@lucide/svelte/icons/square';
	import RotateCcw from '@lucide/svelte/icons/rotate-ccw';
	import Trash2 from '@lucide/svelte/icons/trash-2';
	import Layers from '@lucide/svelte/icons/layers';
	import { rowLink, tableFrame, TableToolbar } from '$lib/components/table';
	import FilterMenu, { type FilterMenuOption } from '$lib/components/FilterMenu.svelte';
	import Search from '@lucide/svelte/icons/search';
	import X from '@lucide/svelte/icons/x';
	import Flag from '@lucide/svelte/icons/flag';
	import Archive from '@lucide/svelte/icons/archive';
	import {
		getContainers,
		startContainer,
		stopContainer,
		restartContainer,
		removeContainer,
		getDockerInfo,
		type ContainerInstance,
		type ContainerStatus,
		type DockerInfo
	} from '$lib/api/client';

	let containers = $state<ContainerInstance[]>([]);
	let total = $state(0);
	let loading = $state(true);
	let error = $state('');
	let dockerInfo = $state<DockerInfo | null>(null);
	// One fetch of every container (the list is small); filters, counts and search are client-side.
	// Default view: live containers; removed ones (the bulk of the history) behind a toggle.
	const LIMIT = 500;
	let statusFilter = $state<'' | ContainerStatus>('');
	let showRemoved = $state(false);
	let query = $state('');
	let actionLoading = $state<Record<string, boolean>>({});

	async function load() {
		loading = true;
		error = '';
		try {
			const [res, info] = await Promise.all([
				getContainers({ per_page: LIMIT }),
				getDockerInfo()
			]);
			containers = res.containers;
			total = res.pagination.total;
			dockerInfo = info;
		} catch (e) {
			error = (e as Error)?.message || 'Failed to load';
		} finally {
			loading = false;
		}
	}

	async function act(id: string, action: 'start' | 'stop' | 'restart' | 'remove') {
		actionLoading = { ...actionLoading, [id]: true };
		try {
			const fn = { start: startContainer, stop: stopContainer, restart: restartContainer, remove: removeContainer }[action];
			await fn(id);
			await load();
		} catch (e) {
			toast.error(`${action[0].toUpperCase()}${action.slice(1)} failed: ${formatApiError(e, 'unknown error')}`);
		} finally {
			actionLoading = { ...actionLoading, [id]: false };
		}
	}

	// Removing discards the container: confirm first.
	let removeId = $state<string | null>(null);
	let removeOpen = $state(false);

	$effect(() => {
		load();
	});

	const STATUSES: ContainerStatus[] = ['running', 'building', 'pending', 'stopped', 'failed', 'removed'];
	const counts = $derived.by(() => {
		const m = new Map<string, number>();
		for (const c of containers) m.set(c.status, (m.get(c.status) ?? 0) + 1);
		return m;
	});
	const statusOptions = $derived<FilterMenuOption[]>(
		STATUSES.filter((st) => counts.has(st) || st === statusFilter).map((st) => ({
			value: st,
			label: st.charAt(0).toUpperCase() + st.slice(1),
			hint: String(counts.get(st) ?? 0),
		})),
	);
	const removedCount = $derived(counts.get('removed') ?? 0);
	const shown = $derived.by(() => {
		const q = query.trim().toLowerCase();
		return containers.filter(
			(c) =>
				(statusFilter ? c.status === statusFilter : showRemoved || c.status !== 'removed') &&
				(!q || `${c.name} ${c.image}`.toLowerCase().includes(q)),
		);
	});
	/** `llmlab/sample:<sha>-<uuid>` → `llmlab/sample:8f829df8…` (full in the title). */
	function shortImage(image: string): string {
		const [repo, tag] = image.split(':');
		return tag && tag.length > 12 ? `${repo}:${tag.slice(0, 8)}…` : image;
	}
</script>

<div class="space-y-4">
	<PageHeader title="Runtime" description="Docker containers started for generated samples.">
		{#snippet meta()}
			{#if dockerInfo}
				{#if dockerInfo.daemon_available}
					<StatusBadge status="healthy" label="Docker daemon online" />
				{:else}
					<StatusBadge status="unhealthy" label="Docker daemon unavailable" />
				{/if}
			{/if}
		{/snippet}
		{#snippet actions()}
			<Button variant="outline" size="sm" onclick={load}>
				<RefreshCw />Refresh
			</Button>
		{/snippet}
		<div class="header-stats">
			<span class="header-stat"><b>{counts.get('running') ?? 0}</b> running</span>
			<span class="header-stat"><b>{(counts.get('stopped') ?? 0) + (counts.get('failed') ?? 0)}</b> stopped / failed</span>
			<span class="header-stat"><b>{total.toLocaleString()}</b> containers recorded</span>
			{#if dockerInfo?.images != null}<span class="header-stat" title="Images on the apps daemon"><b>{dockerInfo.images}</b> images</span>{/if}
			{#if dockerInfo?.version}<span class="header-stat">Docker <b>{dockerInfo.version}</b></span>{/if}
			{#if total > LIMIT}<span class="header-stat text-muted-foreground">(newest {LIMIT} listed)</span>{/if}
		</div>
	</PageHeader>

	{#if error}<ErrorAlert message={error} onretry={load} />{/if}
	<Card.Root class="gap-0 overflow-clip py-0">
		<div class="tb-bar" role="search" aria-label="Search and filter containers">
			<div class="tb-search">
				<Search class="tb-search-icon" aria-hidden="true" />
				<input type="search" placeholder="Search container or image…" aria-label="Search containers" bind:value={query} autocomplete="off" />
			</div>
			<div class="tb-menus">
				<FilterMenu label="Status" icon={Flag} value={statusFilter} options={statusOptions} onChange={(v) => (statusFilter = v as ContainerStatus | '')} anyLabel="Live (not removed)" />
				<button
					type="button"
					class="fm-trigger {showRemoved ? 'fm-trigger-on' : ''}"
					aria-pressed={showRemoved}
					disabled={!!statusFilter}
					onclick={() => (showRemoved = !showRemoved)}
					title="Removed containers are kept as history"
				>
					<Archive class="size-3.5" aria-hidden="true" />Include removed <span class="font-mono text-[11px] opacity-70">{removedCount}</span>
				</button>
				{#if statusFilter || query}
					<button type="button" class="fm-trigger" onclick={() => { statusFilter = ''; query = ''; }}><X class="size-3" />Clear</button>
				{/if}
			</div>
		</div>
	{#if loading && containers.length === 0}
		<LoadingState label="loading containers" />
	{:else if shown.length === 0}
		<EmptyState
			compact
			icon={Layers}
			title={containers.length ? 'No container matches' : 'No containers found'}
			description={containers.length
				? removedCount && !showRemoved && !statusFilter
					? `No live container. ${removedCount} removed ones are hidden.`
					: 'Change or clear the filters.'
				: 'Containers are created automatically when samples are built and tested.'}
		/>
	{:else}
		<TableToolbar>
			{#snippet summary()}{shown.length} of {containers.length}{/snippet}
		</TableToolbar>
			<div class="table-frame" {@attach tableFrame}>
				<table>
					<thead>
						<tr>
							<th>Container</th>
							<th>Status</th>
							<th>Image</th>
							<th class="num">Port</th>
							<th>Created</th>
							<th class="text-right">Actions</th>
						</tr>
					</thead>
					<tbody>
						{#each shown as c (c.id)}
							{@const href = `/runtime/${c.id}`}
							<!-- svelte-ignore a11y_click_events_have_key_events, a11y_no_noninteractive_element_interactions (the name link is the keyboard path) -->
							<tr class="row-link" onclick={rowLink(href)}>
								<td>
									<a {href} class="font-mono text-xs font-medium hover:text-primary">{c.name}</a>
									{#if c.last_error}
										<p class="cell-sub line-clamp-1 max-w-md text-[11px] text-danger-fg" title={c.last_error}>{c.last_error}</p>
									{/if}
								</td>
								<td class="whitespace-nowrap"><StatusBadge status={c.status} tones={containerTones} /></td>
								<td class="max-w-[22rem] truncate font-mono text-xs text-muted-foreground" title={c.image}>{c.image ? shortImage(c.image) : '—'}</td>
								<td class="num text-xs">
									{#if c.app_url}
										<a href={c.app_url} target="_blank" rel="noopener noreferrer" class="text-primary hover:underline">:{c.app_port}</a>
									{:else}
										<span class="text-muted-foreground">—</span>
									{/if}
								</td>
								<td class="whitespace-nowrap text-xs text-muted-foreground">{formatDate(c.created_at)}</td>
								<td>
									<div class="flex justify-end gap-0.5">
										{#if c.status === 'stopped' || c.status === 'failed'}
											<Button size="icon" variant="ghost" class="size-7" aria-label="Start container" title="Start container" onclick={() => act(c.id, 'start')} disabled={actionLoading[c.id]}>
												<Play />
											</Button>
										{/if}
										{#if c.status === 'running'}
											<Button size="icon" variant="ghost" class="size-7" aria-label="Stop container" title="Stop container" onclick={() => act(c.id, 'stop')} disabled={actionLoading[c.id]}>
												<Square />
											</Button>
											<Button size="icon" variant="ghost" class="size-7" aria-label="Restart container" title="Restart container" onclick={() => act(c.id, 'restart')} disabled={actionLoading[c.id]}>
												<RotateCcw />
											</Button>
										{/if}
										{#if c.status !== 'removed'}
											<Button size="icon" variant="ghost" class="size-7 hover:bg-destructive/10 hover:text-danger-fg" aria-label="Remove container" title="Remove container" onclick={() => { removeId = c.id; removeOpen = true; }} disabled={actionLoading[c.id]}>
												<Trash2 />
											</Button>
										{/if}
									</div>
								</td>
							</tr>
						{/each}
					</tbody>
				</table>
			</div>
	{/if}
	</Card.Root>
</div>

<ConfirmDialog
	bind:open={removeOpen}
	title="Remove this container?"
	description="Stops and removes the container. The generated code is kept."
	confirmLabel="Remove container"
	destructive
	onConfirm={() => {
		removeOpen = false;
		if (removeId) act(removeId, 'remove');
	}}
/>
