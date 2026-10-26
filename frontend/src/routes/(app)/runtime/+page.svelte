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
	import { rowLink, tableFrame } from '$lib/components/table';
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
	let statusFilter = $state<'all' | ContainerStatus>('all');
	let actionLoading = $state<Record<string, boolean>>({});

	async function load() {
		loading = true;
		error = '';
		try {
			const [res, info] = await Promise.all([
				getContainers({ per_page: 50, ...(statusFilter !== 'all' ? { status: statusFilter } : {}) }),
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

	// Runs on mount and again whenever the filter changes.
	$effect(() => {
		void statusFilter;
		load();
	});
</script>

<div class="space-y-4">
	<PageHeader title="Runtime" description="Docker containers created by generation jobs.">
		{#snippet actions()}
			<Button variant="outline" size="sm" onclick={load}>
				<RefreshCw />Refresh
			</Button>
		{/snippet}
	</PageHeader>

	<!-- Daemon status and the filter share one toolbar line. -->
	<div class="flex flex-wrap items-center gap-x-4 gap-y-2 text-sm">
		<label for="runtime-status-filter" class="sr-only">Filter by status</label>
		<select id="runtime-status-filter" bind:value={statusFilter} class="std-select">
			<option value="all">All statuses</option>
			<option value="pending">Pending</option>
			<option value="building">Building</option>
			<option value="running">Running</option>
			<option value="stopped">Stopped</option>
			<option value="failed">Failed</option>
			<option value="removed">Removed</option>
		</select>
		{#if dockerInfo}
			{#if dockerInfo.daemon_available}
				<StatusBadge status="healthy" label="Docker daemon online" />
				{#if dockerInfo.version}
					<span class="font-mono text-xs text-muted-foreground">v{dockerInfo.version}</span>
				{/if}
			{:else}
				<StatusBadge status="unhealthy" label="Docker daemon unavailable" />
			{/if}
		{/if}
		<span class="ml-auto font-mono text-xs text-muted-foreground tabular-nums">
			{total} container{total === 1 ? '' : 's'}
			{#if dockerInfo?.containers_running != null}
				· daemon: {dockerInfo.containers_running} running · {dockerInfo.containers_stopped ?? 0} stopped · {dockerInfo.images ?? 0} images
			{/if}
		</span>
	</div>

	{#if loading}
		<LoadingState label="loading containers" />
	{:else if error}
		<ErrorAlert message={error} onretry={load} />
	{:else if containers.length === 0}
		<EmptyState
			icon={Layers}
			title="No containers found"
			description="Containers are created automatically when generation jobs run."
		/>
	{:else}
		<Card.Root class="gap-0 overflow-clip py-0">
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
						{#each containers as c (c.id)}
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
								<td class="max-w-[22rem] truncate font-mono text-xs text-muted-foreground" title={c.image}>{c.image || '—'}</td>
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
		</Card.Root>
	{/if}
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
