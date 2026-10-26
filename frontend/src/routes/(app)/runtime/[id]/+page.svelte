<script lang="ts">
	import { formatDateTime as formatDate } from '$lib/utils/formatters';
	import { containerTones } from '$lib/constants/colors';
	import { onMount } from 'svelte';
	import { page } from '$app/state';
	import { goto } from '$app/navigation';
	import * as Card from '$lib/components/ui/card';
	import * as Tabs from '$lib/components/ui/tabs';
	import PageHeader from '$lib/components/PageHeader.svelte';
	import StatusBadge from '$lib/components/StatusBadge.svelte';
	import ErrorAlert from '$lib/components/ErrorAlert.svelte';
	import LoadingState from '$lib/components/LoadingState.svelte';
	import EmptyState from '$lib/components/EmptyState.svelte';
	import { Button } from '$lib/components/ui/button';
	import RefreshCw from '@lucide/svelte/icons/refresh-cw';
	import Play from '@lucide/svelte/icons/play';
	import Square from '@lucide/svelte/icons/square';
	import RotateCcw from '@lucide/svelte/icons/rotate-ccw';
	import Trash2 from '@lucide/svelte/icons/trash-2';
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
	} from '$lib/api/client';
	import { subscribe } from '$lib/api/sse';
	import { toast } from 'svelte-sonner';

	const containerId = page.params.id ?? '';

	let container = $state<ContainerInstance | null>(null);
	let logs = $state('');
	let actions = $state<ContainerAction[]>([]);
	let health = $state('');
	let loading = $state(true);
	let error = $state('');
	let activeTab = $state('logs');
	let actionLoading = $state(false);
	// The header's `actions` snippet shadows `actions` inside its sibling snippets.
	const actionCount = $derived(actions.length);

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

	$effect(() => {
		if (activeTab === 'logs') loadLogs();
	});

	onMount(() => {
		load();
		loadHealth();

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

<Tabs.Root bind:value={activeTab} class="space-y-4">
	<PageHeader
		title={container?.name ?? 'Container'}
		description="Container lifecycle, logs and action history."
		back={{ href: '/runtime', label: 'Runtime' }}
	>
		{#snippet meta()}
			{#if container}<StatusBadge status={container.status} tones={containerTones} />{/if}
		{/snippet}
		{#snippet actions()}
			<Button variant="outline" size="sm" onclick={load}>
				<RefreshCw />Refresh
			</Button>
			{#if container?.status === 'stopped'}
				<Button size="sm" variant="outline" onclick={() => act('start')} disabled={actionLoading}>
					<Play />Start
				</Button>
			{/if}
			{#if container?.status === 'running'}
				<Button size="sm" variant="outline" onclick={() => act('stop')} disabled={actionLoading}>
					<Square />Stop
				</Button>
				<Button size="sm" variant="outline" onclick={() => act('restart')} disabled={actionLoading}>
					<RotateCcw />Restart
				</Button>
			{/if}
			{#if container && container.status !== 'removed'}
				<Button size="sm" variant="destructive" onclick={() => act('remove')} disabled={actionLoading}>
					<Trash2 />Remove
				</Button>
			{/if}
		{/snippet}
		{#if container && !loading && !error}
			<div class="header-stats">
				<span class="header-stat" title={container.id}>ID <b>{container.id.slice(0, 8)}…</b></span>
				<span class="header-stat min-w-0 max-w-full">image <b class="min-w-0 truncate">{container.image || '—'}</b></span>
				<span class="header-stat">
					port
					{#if container.app_port}
						<a href={container.app_url ?? '#'} target="_blank" rel="noopener noreferrer" class="font-mono text-sm text-primary hover:underline">
							{container.app_port}
						</a>
					{:else}
						<b>—</b>
					{/if}
				</span>
				<span class="header-stat">health <b class="font-sans capitalize">{health || '—'}</b></span>
				<span class="header-stat">created {formatDate(container.created_at)}</span>
			</div>
		{/if}
		{#snippet nav()}
			{#if container && !loading && !error}
				<Tabs.List aria-label="Container sections">
					<Tabs.Trigger value="logs">Logs</Tabs.Trigger>
					<Tabs.Trigger value="actions">
						Actions
						{#if actionCount > 0}
							<span class="text-xs text-muted-foreground">({actionCount})</span>
						{/if}
					</Tabs.Trigger>
				</Tabs.List>
			{/if}
		{/snippet}
	</PageHeader>

	{#if loading}
		<LoadingState label="loading container" />
	{:else if error}
		<ErrorAlert message={error} onretry={load} />
	{:else if container}
		{#if container.last_error}
			<ErrorAlert
				title={container.status === 'failed' ? 'Build failed' : 'Last error'}
				message={container.last_error}
			/>
		{/if}

		<Tabs.Content value="logs" class="pt-0">
			<Card.Root>
				<Card.Header class="flex flex-row items-center justify-between">
					<Card.Title class="text-sm">Container logs</Card.Title>
					<Button size="sm" variant="outline" onclick={loadLogs} aria-label="Refresh logs">
						<RefreshCw class="h-3 w-3" />
					</Button>
				</Card.Header>
				<Card.Content>
					<pre class="bg-background rounded p-3 text-xs font-mono overflow-auto max-h-[500px] whitespace-pre-wrap break-all">{logs || 'No logs available'}</pre>
				</Card.Content>
			</Card.Root>
		</Tabs.Content>

		<Tabs.Content value="actions" class="pt-0">
			{#if actions.length === 0}
				<EmptyState title="No actions recorded" description="Container lifecycle actions will appear here." />
			{:else}
				<div class="space-y-3">
					{#each actions as a (a.id)}
						<Card.Root>
							<Card.Content class="p-4">
								<div class="flex items-center justify-between gap-2 mb-2">
									<div class="flex items-center gap-2">
										<span class="font-medium text-sm capitalize">{a.action_type}</span>
										<StatusBadge status={a.status} />
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
									<p class="text-xs text-danger-fg mt-2">{a.error_message}</p>
								{/if}
								{#if a.log_output}
									<pre class="mt-2 text-xs font-mono bg-muted/40 rounded p-2 overflow-auto max-h-32 whitespace-pre-wrap">{a.log_output}</pre>
								{/if}
							</Card.Content>
						</Card.Root>
					{/each}
				</div>
			{/if}
		</Tabs.Content>
	{/if}
</Tabs.Root>
