<script lang="ts">
	import { onMount } from 'svelte';
	import { page } from '$app/state';
	import { goto } from '$app/navigation';
	import * as Card from '$lib/components/ui/card';
	import { Badge } from '$lib/components/ui/badge';
	import { Button } from '$lib/components/ui/button';
	import RefreshCw from '@lucide/svelte/icons/refresh-cw';
	import ArrowLeft from '@lucide/svelte/icons/arrow-left';
	import Play from '@lucide/svelte/icons/play';
	import Square from '@lucide/svelte/icons/square';
	import RotateCcw from '@lucide/svelte/icons/rotate-ccw';
	import Trash2 from '@lucide/svelte/icons/trash-2';
	import LoaderCircle from '@lucide/svelte/icons/loader-circle';
	import {
		getContainer,
		getContainerLogs,
		getContainerActions,
		getContainerHealth,
		startContainer,
		stopContainer,
		restartContainer,
		removeContainer,
		type ContainerInstance,
		type ContainerAction,
		type ContainerStatus,
		type ActionStatus
	} from '$lib/api/client';
	import { subscribe } from '$lib/api/sse';
	import { toast } from 'svelte-sonner';

	const containerId = page.params.id;

	let container = $state<ContainerInstance | null>(null);
	let logs = $state('');
	let actions = $state<ContainerAction[]>([]);
	let health = $state('');
	let loading = $state(true);
	let error = $state('');
	let activeTab = $state<'overview' | 'logs' | 'actions'>('overview');
	let actionLoading = $state(false);

	const statusColors: Record<ContainerStatus, string> = {
		pending: 'bg-slate-500/15 text-slate-400 border-slate-500/30',
		building: 'bg-amber-500/15 text-amber-500 border-amber-500/30',
		running: 'bg-emerald-500/15 text-emerald-500 border-emerald-500/30',
		stopped: 'bg-blue-500/15 text-blue-400 border-blue-500/30',
		failed: 'bg-red-500/15 text-red-400 border-red-500/30',
		removed: 'bg-neutral-500/15 text-neutral-400 border-neutral-500/30'
	};

	const actionStatusColors: Record<ActionStatus, string> = {
		pending: 'bg-slate-500/15 text-slate-400 border-slate-500/30',
		running: 'bg-amber-500/15 text-amber-500 border-amber-500/30',
		completed: 'bg-emerald-500/15 text-emerald-500 border-emerald-500/30',
		failed: 'bg-red-500/15 text-red-400 border-red-500/30'
	};

	async function load() {
		loading = true;
		error = '';
		try {
			const [c, a] = await Promise.all([
				getContainer(containerId),
				getContainerActions(containerId)
			]);
			container = c;
			actions = a;
		} catch (e) {
			error = (e as Error)?.message || 'Failed to load container';
		} finally {
			loading = false;
		}
	}

	async function loadLogs() {
		try {
			const res = await getContainerLogs(containerId, 300);
			logs = res.logs;
		} catch {
			logs = 'Failed to fetch logs';
		}
	}

	async function loadHealth() {
		try {
			const res = await getContainerHealth(containerId);
			health = res.health;
		} catch {
			health = 'unavailable';
		}
	}

	async function act(action: 'start' | 'stop' | 'restart' | 'remove') {
		actionLoading = true;
		try {
			const fn = { start: startContainer, stop: stopContainer, restart: restartContainer, remove: removeContainer }[action];
			await fn(containerId);
			if (action === 'remove') {
				goto('/runtime');
			} else {
				await load();
			}
		} catch (e) {
			const msg =
				(e && typeof e === 'object' && 'message' in e && typeof (e as any).message === 'string')
					? (e as any).message
					: (e instanceof Error ? e.message : 'unknown error');
			toast.error(`${action} failed: ${msg}`);
		} finally {
			actionLoading = false;
		}
	}

	function formatDate(s: string | null): string {
		if (!s) return '—';
		return new Date(s).toLocaleString();
	}

	$effect(() => {
		if (activeTab === 'logs') loadLogs();
		if (activeTab === 'overview') loadHealth();
	});

	onMount(() => {
		load();

		// SSE: subscribe for live container status updates; fall back if SSE fails
		const cleanupSse = subscribe([`runtime:${containerId}`], async () => {
			try {
				const [c, a] = await Promise.all([
					getContainer(containerId),
					getContainerActions(containerId)
				]);
				container = c;
				actions = a;
			} catch {
				// refetch failed — user can refresh manually
			}
		});

		return cleanupSse;
	});
</script>

<svelte:head>
	<title>Container — LLM Eval Lab</title>
</svelte:head>

<div class="space-y-6">
	<div class="flex items-center gap-3">
		<Button variant="ghost" size="sm" onclick={() => goto('/runtime')}>
			<ArrowLeft class="mr-1 h-4 w-4" />Back
		</Button>
		{#if container}
			<Badge class={statusColors[container.status]}>{container.status}</Badge>
			<h1 class="text-xl font-bold font-mono truncate">{container.name}</h1>
		{/if}
		<div class="ml-auto flex gap-2">
			<Button variant="outline" size="sm" onclick={load}>
				<RefreshCw class="mr-1 h-4 w-4" />Refresh
			</Button>
			{#if container?.status === 'stopped'}
				<Button size="sm" variant="outline" onclick={() => act('start')} disabled={actionLoading}>
					<Play class="mr-1 h-3 w-3" />Start
				</Button>
			{/if}
			{#if container?.status === 'failed'}
				<span class="text-xs text-red-400 self-center" title={container.last_error}>
					⚠ Build failed{container.last_error ? `: ${container.last_error}` : ''}
				</span>
			{/if}
			{#if container?.status === 'running'}
				<Button size="sm" variant="outline" onclick={() => act('stop')} disabled={actionLoading}>
					<Square class="mr-1 h-3 w-3" />Stop
				</Button>
				<Button size="sm" variant="outline" onclick={() => act('restart')} disabled={actionLoading}>
					<RotateCcw class="mr-1 h-3 w-3" />Restart
				</Button>
			{/if}
			{#if container && container.status !== 'removed'}
				<Button size="sm" variant="destructive" onclick={() => act('remove')} disabled={actionLoading}>
					<Trash2 class="mr-1 h-3 w-3" />Remove
				</Button>
			{/if}
		</div>
	</div>

	{#if loading}
		<Card.Root>
			<Card.Content class="flex items-center justify-center py-20">
				<LoaderCircle class="h-8 w-8 animate-spin text-muted-foreground" />
			</Card.Content>
		</Card.Root>
	{:else if error}
		<Card.Root><Card.Content class="p-6 text-red-400 text-sm">{error}</Card.Content></Card.Root>
	{:else if container}
		<!-- Tabs -->
		<div class="flex gap-1 border-b border-border">
			{#each (['overview', 'logs', 'actions'] as const) as tab (tab)}
				<button
					class="px-4 py-2 text-sm font-medium capitalize transition-colors {activeTab === tab ? 'border-b-2 border-primary text-foreground' : 'text-muted-foreground hover:text-foreground'}"
					onclick={() => (activeTab = tab)}
				>
					{tab}
					{#if tab === 'actions' && actions.length > 0}
						<span class="ml-1 text-xs text-muted-foreground">({actions.length})</span>
					{/if}
				</button>
			{/each}
		</div>

		{#if activeTab === 'overview'}
			<div class="grid gap-4 sm:grid-cols-2">
				<Card.Root>
					<Card.Header><Card.Title class="text-sm">Container Info</Card.Title></Card.Header>
					<Card.Content class="text-sm space-y-2">
						<div class="flex justify-between">
							<span class="text-muted-foreground">ID</span>
							<span class="font-mono text-xs">{container.id.slice(0, 8)}…</span>
						</div>
						<div class="flex justify-between">
							<span class="text-muted-foreground">Image</span>
							<span class="font-mono text-xs">{container.image || '—'}</span>
						</div>
						<div class="flex justify-between">
							<span class="text-muted-foreground">App port</span>
							<span>
								{#if container.app_port}
									<a href={container.app_url ?? '#'} target="_blank" rel="noopener noreferrer" class="text-blue-400 hover:underline">
										{container.app_port}
									</a>
								{:else}
									—
								{/if}
							</span>
						</div>
						<div class="flex justify-between">
							<span class="text-muted-foreground">Health</span>
							<span class="capitalize">{health || '—'}</span>
						</div>
						<div class="flex justify-between">
							<span class="text-muted-foreground">Created</span>
							<span class="text-xs">{formatDate(container.created_at)}</span>
						</div>
					</Card.Content>
				</Card.Root>
				{#if container.last_error}
					<Card.Root>
						<Card.Header><Card.Title class="text-sm text-red-400">Error</Card.Title></Card.Header>
						<Card.Content>
							<p class="text-xs text-red-400">{container.last_error}</p>
						</Card.Content>
					</Card.Root>
				{/if}
			</div>

		{:else if activeTab === 'logs'}
			<Card.Root>
				<Card.Header class="flex flex-row items-center justify-between">
					<Card.Title class="text-sm">Container Logs</Card.Title>
					<Button size="sm" variant="outline" onclick={loadLogs}>
						<RefreshCw class="h-3 w-3" />
					</Button>
				</Card.Header>
				<Card.Content>
					<pre class="bg-background rounded p-3 text-xs font-mono overflow-auto max-h-[500px] whitespace-pre-wrap break-all">{logs || 'No logs available'}</pre>
				</Card.Content>
			</Card.Root>

		{:else if activeTab === 'actions'}
			{#if actions.length === 0}
				<Card.Root>
					<Card.Content class="py-16 text-center">
						<h3 class="text-lg font-medium mb-1">No actions recorded</h3>
						<p class="text-sm text-muted-foreground">Container lifecycle actions will appear here.</p>
					</Card.Content>
				</Card.Root>
			{:else}
				<div class="space-y-3">
					{#each actions as a (a.id)}
						<Card.Root>
							<Card.Content class="p-4">
								<div class="flex items-center justify-between gap-2 mb-2">
									<div class="flex items-center gap-2">
										<span class="font-medium text-sm capitalize">{a.action_type}</span>
										<Badge class={actionStatusColors[a.status]}>{a.status}</Badge>
									</div>
									<span class="text-xs text-muted-foreground">{formatDate(a.created_at)}</span>
								</div>
								{#if a.status === 'running'}
									<div class="text-xs text-muted-foreground mb-1">{a.progress_percent}%</div>
									<div class="w-full bg-muted rounded-full h-1.5">
										<div class="bg-primary h-1.5 rounded-full transition-all" style="width: {a.progress_percent}%"></div>
									</div>
								{/if}
								{#if a.error_message}
									<p class="text-xs text-red-400 mt-2">{a.error_message}</p>
								{/if}
								{#if a.log_output}
									<pre class="mt-2 text-xs font-mono bg-muted/40 rounded p-2 overflow-auto max-h-32 whitespace-pre-wrap">{a.log_output}</pre>
								{/if}
							</Card.Content>
						</Card.Root>
					{/each}
				</div>
			{/if}
		{/if}
	{/if}
</div>
