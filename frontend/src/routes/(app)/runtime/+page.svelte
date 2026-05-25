<script lang="ts">
	import { onMount } from 'svelte';
	import { goto } from '$app/navigation';
	import * as Card from '$lib/components/ui/card';
	import { Badge } from '$lib/components/ui/badge';
	import { Button } from '$lib/components/ui/button';
	import RefreshCw from '@lucide/svelte/icons/refresh-cw';
	import Eye from '@lucide/svelte/icons/eye';
	import Play from '@lucide/svelte/icons/play';
	import Square from '@lucide/svelte/icons/square';
	import RotateCcw from '@lucide/svelte/icons/rotate-ccw';
	import Trash2 from '@lucide/svelte/icons/trash-2';
	import ExternalLink from '@lucide/svelte/icons/external-link';
	import LoaderCircle from '@lucide/svelte/icons/loader-circle';
	import Layers from '@lucide/svelte/icons/layers';
	import CircleCheck from '@lucide/svelte/icons/circle-check';
	import CircleX from '@lucide/svelte/icons/circle-x';
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

	// Module-level cache — survives SPA navigation so revisiting shows data instantly.
	let _cachedContainers: ContainerInstance[] = [];
	let _cachedTotal = 0;
	let _cachedDockerInfo: DockerInfo | null = null;

	let containers = $state<ContainerInstance[]>(_cachedContainers);
	let total = $state(_cachedTotal);
	let loading = $state(_cachedContainers.length === 0);
	let refreshing = $state(false);
	let error = $state('');
	let dockerInfo = $state<DockerInfo | null>(_cachedDockerInfo);
	let statusFilter = $state<'all' | ContainerStatus>('all');
	let actionLoading = $state<Record<string, boolean>>({});

	const statusColors: Record<ContainerStatus, string> = {
		pending: 'bg-slate-500/15 text-slate-400 border-slate-500/30',
		building: 'bg-amber-500/15 text-amber-500 border-amber-500/30',
		running: 'bg-emerald-500/15 text-emerald-500 border-emerald-500/30',
		stopped: 'bg-blue-500/15 text-blue-400 border-blue-500/30',
		failed: 'bg-red-500/15 text-red-400 border-red-500/30',
		removed: 'bg-neutral-500/15 text-neutral-400 border-neutral-500/30'
	};

	async function load(showSpinner = false) {
		if (showSpinner) loading = true;
		else if (_cachedContainers.length > 0) refreshing = true;
		else loading = true;
		error = '';
		try {
			const [res, info] = await Promise.all([
				getContainers({ per_page: 50, ...(statusFilter !== 'all' ? { status: statusFilter } : {}) }),
				getDockerInfo()
			]);
			containers = res.containers;
			total = res.pagination.total;
			dockerInfo = info;
			_cachedContainers = res.containers;
			_cachedTotal = res.pagination.total;
			_cachedDockerInfo = info;
		} catch (e) {
			error = (e as Error)?.message || 'Failed to load';
		} finally {
			loading = false;
			refreshing = false;
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

	function formatDate(s: string): string {
		return new Date(s).toLocaleString();
	}

	let firstEffect = true;
	$effect(() => {
		void statusFilter;
		if (firstEffect) { firstEffect = false; return; }
		load();
	});

	onMount(() => load());
</script>

<svelte:head>
	<title>Runtime — LLM Eval Lab</title>
</svelte:head>

<div class="space-y-6">
	<div class="flex flex-col gap-3 sm:flex-row sm:items-center sm:justify-between">
		<div class="page-header min-w-0">
			<h1>Runtime</h1>
			<p>Docker container management</p>
		</div>
		<Button variant="outline" size="sm" onclick={() => load(true)} class="self-start" disabled={loading}>
			<RefreshCw class="mr-2 h-4 w-4 {refreshing ? 'animate-spin' : ''}" />Refresh
		</Button>
	</div>

	{#if dockerInfo}
		<Card.Root>
			<Card.Content class="p-4 flex flex-wrap items-center gap-x-4 gap-y-2 text-sm">
				{#if dockerInfo.daemon_available}
					<CircleCheck class="h-4 w-4 text-emerald-500 shrink-0" />
					<span class="text-emerald-500 font-medium">Docker daemon online</span>
					{#if dockerInfo.version}
						<span class="text-muted-foreground">v{dockerInfo.version}</span>
					{/if}
					{#if dockerInfo.containers_running !== null}
						<span class="text-muted-foreground sm:ml-auto">
							{dockerInfo.containers_running} running · {dockerInfo.containers_stopped} stopped · {dockerInfo.images} images
						</span>
					{/if}
				{:else}
					<CircleX class="h-4 w-4 text-red-400 shrink-0" />
					<span class="text-red-400 font-medium">Docker daemon unavailable</span>
				{/if}
			</Card.Content>
		</Card.Root>
	{/if}

	<Card.Root>
		<Card.Content class="p-4 flex flex-wrap gap-3">
			<select bind:value={statusFilter} class="rounded-md border bg-background px-3 py-1.5 text-sm">
				<option value="all">All statuses</option>
				<option value="pending">Pending</option>
				<option value="building">Building</option>
				<option value="running">Running</option>
				<option value="stopped">Stopped</option>
				<option value="failed">Failed</option>
				<option value="removed">Removed</option>
			</select>
			<span class="ml-auto text-xs text-muted-foreground self-center">
				{total} container{total === 1 ? '' : 's'}
			</span>
		</Card.Content>
	</Card.Root>

	{#if loading}
		<Card.Root>
			<Card.Content class="flex items-center justify-center py-20">
				<LoaderCircle class="h-8 w-8 animate-spin text-muted-foreground" />
			</Card.Content>
		</Card.Root>
	{:else if error}
		<Card.Root><Card.Content class="p-6 text-red-400 text-sm">{error}</Card.Content></Card.Root>
	{:else if containers.length === 0}
		<Card.Root>
			<Card.Content class="py-16 text-center">
				<Layers class="mx-auto h-12 w-12 text-muted-foreground/50 mb-4" />
				<h3 class="text-lg font-medium mb-1">No containers found</h3>
				<p class="text-sm text-muted-foreground mb-4">Containers are created automatically when generation jobs run.</p>
			</Card.Content>
		</Card.Root>
	{:else}
		<div class="grid gap-3 sm:grid-cols-2 lg:grid-cols-3">
			{#each containers as c (c.id)}
				<Card.Root class="flex flex-col">
					<Card.Header class="pb-3">
						<div class="flex items-start justify-between gap-2">
							<Badge class={statusColors[c.status]}>{c.status}</Badge>
							{#if c.app_port}
								<span class="font-mono text-xs text-muted-foreground">:{c.app_port}</span>
							{/if}
						</div>
						<Card.Title class="text-base mt-2 font-mono text-sm truncate">{c.name}</Card.Title>
						{#if c.image}
							<Card.Description class="font-mono text-xs truncate">{c.image}</Card.Description>
						{/if}
					</Card.Header>
					<Card.Content class="flex-1 text-xs text-muted-foreground space-y-1">
						<div>Created: {formatDate(c.created_at)}</div>
						{#if c.last_error}
							<div class="text-red-400 line-clamp-2">{c.last_error}</div>
						{/if}
					</Card.Content>
					<Card.Footer class="gap-1 pt-3 flex-wrap">
						<Button size="sm" variant="outline" onclick={() => goto(`/runtime/${c.id}`)}>
							<Eye class="h-3 w-3" />
						</Button>
						{#if c.status === 'running' && c.app_url}
							<a
								href={c.app_url}
								target="_blank"
								rel="noopener noreferrer"
								class="inline-flex h-8 items-center gap-1.5 rounded-md border border-input bg-background px-2.5 text-xs font-medium text-blue-400 hover:bg-accent hover:text-blue-300 transition-colors"
							>
								<ExternalLink class="h-3 w-3" />
								Open App
							</a>
						{/if}
						{#if c.status === 'stopped' || c.status === 'failed'}
							<Button size="sm" variant="outline" onclick={() => act(c.id, 'start')} disabled={actionLoading[c.id]}>
								<Play class="h-3 w-3" />
							</Button>
						{/if}
						{#if c.status === 'running'}
							<Button size="sm" variant="outline" onclick={() => act(c.id, 'stop')} disabled={actionLoading[c.id]}>
								<Square class="h-3 w-3" />
							</Button>
							<Button size="sm" variant="outline" onclick={() => act(c.id, 'restart')} disabled={actionLoading[c.id]}>
								<RotateCcw class="h-3 w-3" />
							</Button>
						{/if}
						{#if c.status !== 'removed'}
							<Button size="sm" variant="outline" onclick={() => act(c.id, 'remove')} disabled={actionLoading[c.id]}>
								<Trash2 class="h-3 w-3" />
							</Button>
						{/if}
					</Card.Footer>
				</Card.Root>
			{/each}
		</div>
	{/if}
</div>
