<script lang="ts">
	import { formatDateTime as formatDate } from '$lib/utils/formatters';
	import { containerTones } from '$lib/constants/colors';
	import { onMount } from 'svelte';
	import * as Card from '$lib/components/ui/card';
	import PageHeader from '$lib/components/PageHeader.svelte';
	import StatusBadge from '$lib/components/StatusBadge.svelte';
	import ErrorAlert from '$lib/components/ErrorAlert.svelte';
	import LoadingState from '$lib/components/LoadingState.svelte';
	import EmptyState from '$lib/components/EmptyState.svelte';
	import { Button } from '$lib/components/ui/button';
	import RefreshCw from '@lucide/svelte/icons/refresh-cw';
	import Eye from '@lucide/svelte/icons/eye';
	import Play from '@lucide/svelte/icons/play';
	import Square from '@lucide/svelte/icons/square';
	import RotateCcw from '@lucide/svelte/icons/rotate-ccw';
	import Trash2 from '@lucide/svelte/icons/trash-2';
	import Layers from '@lucide/svelte/icons/layers';
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
		} finally {
			actionLoading = { ...actionLoading, [id]: false };
		}
	}

	$effect(() => {
		void statusFilter;
		load();
	});

	onMount(load);
</script>

<div class="space-y-6">
	<PageHeader title="Runtime" description="Docker containers created by generation jobs.">
		{#snippet actions()}
			<Button variant="outline" size="sm" onclick={load}>
				<RefreshCw />Refresh
			</Button>
		{/snippet}
	</PageHeader>

	{#if dockerInfo}
		<Card.Root>
			<Card.Content class="p-4 flex flex-wrap items-center gap-x-4 gap-y-2 text-sm">
				{#if dockerInfo.daemon_available}
					<StatusBadge status="healthy" label="Docker daemon online" />
					{#if dockerInfo.version}
						<span class="text-muted-foreground">v{dockerInfo.version}</span>
					{/if}
					{#if dockerInfo.containers_running !== null}
						<span class="text-muted-foreground sm:ml-auto">
							{dockerInfo.containers_running} running · {dockerInfo.containers_stopped} stopped · {dockerInfo.images} images
						</span>
					{/if}
				{:else}
					<StatusBadge status="unhealthy" label="Docker daemon unavailable" />
				{/if}
			</Card.Content>
		</Card.Root>
	{/if}

	<div class="flex flex-wrap items-center gap-3">
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
		<span class="ml-auto text-xs text-muted-foreground">
			{total} container{total === 1 ? '' : 's'}
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
		<div class="grid gap-4 sm:grid-cols-2 lg:grid-cols-3">
			{#each containers as c (c.id)}
				<Card.Root class="flex flex-col">
					<Card.Header class="pb-3">
						<div class="flex items-start justify-between gap-2">
							<StatusBadge status={c.status} tones={containerTones} />
							{#if c.app_url}
								<a
									href={c.app_url}
									target="_blank"
									rel="noopener noreferrer"
									class="text-xs text-primary hover:underline"
								>:{c.app_port}</a>
							{/if}
						</div>
						<Card.Title class="mt-2 font-mono text-sm truncate">{c.name}</Card.Title>
						{#if c.image}
							<Card.Description class="font-mono text-xs truncate">{c.image}</Card.Description>
						{/if}
					</Card.Header>
					<Card.Content class="flex-1 text-xs text-muted-foreground space-y-1">
						{#if c.app_port}
							<div>Port: :{c.app_port}</div>
						{/if}
						<div>Created: {formatDate(c.created_at)}</div>
						{#if c.last_error}
							<div class="text-danger-fg line-clamp-2">{c.last_error}</div>
						{/if}
					</Card.Content>
					<Card.Footer class="gap-1 pt-3 flex-wrap">
						<Button size="sm" variant="outline" href={`/runtime/${c.id}`} aria-label="View container">
							<Eye class="h-3 w-3" />
						</Button>
						{#if c.status === 'stopped' || c.status === 'failed'}
							<Button size="sm" variant="outline" aria-label="Start container" onclick={() => act(c.id, 'start')} disabled={actionLoading[c.id]}>
								<Play class="h-3 w-3" />
							</Button>
						{/if}
						{#if c.status === 'running'}
							<Button size="sm" variant="outline" aria-label="Stop container" onclick={() => act(c.id, 'stop')} disabled={actionLoading[c.id]}>
								<Square class="h-3 w-3" />
							</Button>
							<Button size="sm" variant="outline" aria-label="Restart container" onclick={() => act(c.id, 'restart')} disabled={actionLoading[c.id]}>
								<RotateCcw class="h-3 w-3" />
							</Button>
						{/if}
						{#if c.status !== 'removed'}
							<Button size="sm" variant="outline" aria-label="Remove container" onclick={() => act(c.id, 'remove')} disabled={actionLoading[c.id]}>
								<Trash2 class="h-3 w-3" />
							</Button>
						{/if}
					</Card.Footer>
				</Card.Root>
			{/each}
		</div>
	{/if}
</div>
