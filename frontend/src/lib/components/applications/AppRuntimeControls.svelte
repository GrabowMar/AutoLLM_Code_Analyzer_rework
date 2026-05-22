<script lang="ts">
	import { onMount, onDestroy } from 'svelte';
	import { Button } from '$lib/components/ui/button';
	import { Badge } from '$lib/components/ui/badge';
	import { toast } from 'svelte-sonner';
	import Play from '@lucide/svelte/icons/play';
	import Square from '@lucide/svelte/icons/square';
	import RotateCw from '@lucide/svelte/icons/rotate-cw';
	import Trash2 from '@lucide/svelte/icons/trash-2';
	import Hammer from '@lucide/svelte/icons/hammer';
	import LoaderCircle from '@lucide/svelte/icons/loader-circle';
	import ExternalLink from '@lucide/svelte/icons/external-link';
	import {
		getContainers,
		buildContainerForJob,
		startContainer,
		stopContainer,
		restartContainer,
		removeContainer,
		type ContainerInstance,
		type ContainerStatus,
	} from '$lib/api/runtime';

	interface Props {
		jobId: string;
		jobStatus?: string;
		compact?: boolean;
		showPorts?: boolean;
		onChange?: (container: ContainerInstance | null) => void;
	}

	let { jobId, jobStatus = '', compact = false, showPorts = true, onChange }: Props = $props();

	let container = $state<ContainerInstance | null>(null);
	let loading = $state(true);
	let busy = $state<string | null>(null);
	let pollTimer: ReturnType<typeof setInterval> | null = null;

	const canBuild = $derived(jobStatus === '' || jobStatus === 'completed');

	const statusColor: Record<ContainerStatus, string> = {
		pending: 'bg-zinc-500/15 text-zinc-400 border-zinc-500/30',
		building: 'bg-amber-500/15 text-amber-400 border-amber-500/30',
		running: 'bg-emerald-500/15 text-emerald-400 border-emerald-500/30',
		stopped: 'bg-zinc-500/15 text-zinc-400 border-zinc-500/30',
		failed: 'bg-red-500/15 text-red-400 border-red-500/30',
		removed: 'bg-zinc-500/15 text-zinc-500 border-zinc-500/30',
	};

	async function refresh(silent = false) {
		if (!silent) loading = true;
		try {
			const res = await getContainers({ job_id: jobId, per_page: 1 });
			const next = res.containers[0] ?? null;
			container = next;
			onChange?.(next);
		} catch {
			container = null;
		} finally {
			loading = false;
		}
	}

	function startPolling() {
		stopPolling();
		pollTimer = setInterval(() => {
			if (container && (container.status === 'building' || container.status === 'pending')) {
				refresh(true);
			} else if (busy) {
				refresh(true);
			}
		}, 2000);
	}

	function stopPolling() {
		if (pollTimer) {
			clearInterval(pollTimer);
			pollTimer = null;
		}
	}

	async function doAction(name: string, fn: () => Promise<unknown>, successMsg: string) {
		busy = name;
		try {
			await fn();
			toast.success(successMsg);
			await refresh(true);
		} catch (e) {
			const msg =
				(e && typeof e === 'object' && 'message' in e && typeof (e as any).message === 'string')
					? (e as any).message
					: (e instanceof Error ? e.message : 'unknown error');
			toast.error(`${name} failed: ${msg}`);
		} finally {
			busy = null;
		}
	}

	const onBuild = () => doAction('build', () => buildContainerForJob(jobId), 'Build started');
	const onStart = () =>
		container && doAction('start', () => startContainer(container!.id), 'Container starting');
	const onStop = () =>
		container && doAction('stop', () => stopContainer(container!.id), 'Container stopping');
	const onRestart = () =>
		container && doAction('restart', () => restartContainer(container!.id), 'Container restarting');
	const onRemove = () => {
		if (!container) return;
		if (!confirm('Remove this container? Generated code is preserved.')) return;
		doAction('remove', () => removeContainer(container!.id), 'Container removed');
	};
	const onRebuild = async () => {
		if (!container) return;
		if (!confirm('Rebuild? This removes the failed container and starts a fresh build.')) return;
		busy = 'rebuild';
		try {
			await removeContainer(container.id);
			await buildContainerForJob(jobId);
			toast.success('Rebuild started');
			await refresh(true);
		} catch (e) {
			const msg =
				(e && typeof e === 'object' && 'message' in e && typeof (e as any).message === 'string')
					? (e as any).message
					: (e instanceof Error ? e.message : 'unknown error');
			toast.error(`Rebuild failed: ${msg}`);
		} finally {
			busy = null;
		}
	};

	onMount(() => {
		refresh();
		startPolling();
	});
	onDestroy(stopPolling);

	const btnSize = $derived(compact ? 'sm' : 'sm');
	const iconCls = $derived(compact ? 'h-3.5 w-3.5' : 'h-3.5 w-3.5');
	const showLabels = $derived(!compact);
	const previewUrl = $derived(container?.app_url ?? null);
</script>

<div class="flex flex-wrap items-center gap-2">
	{#if loading}
		<LoaderCircle class="{iconCls} animate-spin text-muted-foreground" />
	{:else if !container}
		<Button
			size={btnSize}
			onclick={onBuild}
			disabled={!canBuild || busy === 'build'}
			title={canBuild ? 'Build a runnable container from this generation' : 'Job must be completed before build'}
		>
			{#if busy === 'build'}
				<LoaderCircle class="{iconCls} animate-spin {showLabels ? 'sm:mr-1.5' : ''}" />
			{:else}
				<Hammer class="{iconCls} {showLabels ? 'sm:mr-1.5' : ''}" />
			{/if}
			{#if showLabels}<span class="hidden sm:inline">Build</span>{/if}
		</Button>
	{:else}
		<Badge variant="outline" class="text-xs {statusColor[container.status]}">
			{#if container.status === 'building' || container.status === 'pending'}
				<LoaderCircle class="h-3 w-3 mr-1 animate-spin" />
			{:else if container.status === 'running'}
				<span class="mr-1 h-1.5 w-1.5 rounded-full bg-emerald-500 animate-pulse inline-block"></span>
			{/if}
			{container.status}
		</Badge>

		{#if container.status === 'failed' && container.last_error}
			<span class="text-xs text-red-400 truncate max-w-[260px]" title={container.last_error}>
				⚠ {container.last_error}
			</span>
		{/if}

		{#if showPorts && previewUrl && container.status === 'running'}
			<a
				href={previewUrl}
				target="_blank"
				rel="noopener noreferrer"
				class="text-xs font-mono text-primary hover:underline inline-flex items-center gap-1"
				title="Open app preview"
			>
				:{container.app_port}
				<ExternalLink class="h-3 w-3" />
			</a>
		{/if}

		{#if container.status === 'stopped'}
			<Button size={btnSize} onclick={onStart} disabled={!!busy} title="Start container">
				{#if busy === 'start'}<LoaderCircle class="{iconCls} animate-spin {showLabels ? 'sm:mr-1.5' : ''}" />
				{:else}<Play class="{iconCls} {showLabels ? 'sm:mr-1.5' : ''}" />{/if}
				{#if showLabels}<span class="hidden sm:inline">Start</span>{/if}
			</Button>
		{/if}
		{#if container.status === 'failed'}
			<Button size={btnSize} onclick={onRebuild} disabled={!!busy} title="Remove failed container and start a fresh build">
				{#if busy === 'rebuild'}<LoaderCircle class="{iconCls} animate-spin {showLabels ? 'sm:mr-1.5' : ''}" />
				{:else}<Hammer class="{iconCls} {showLabels ? 'sm:mr-1.5' : ''}" />{/if}
				{#if showLabels}<span class="hidden sm:inline">Rebuild</span>{/if}
			</Button>
		{/if}
		{#if container.status === 'running'}
			<Button variant="outline" size={btnSize} onclick={onStop} disabled={!!busy} title="Stop container">
				{#if busy === 'stop'}<LoaderCircle class="{iconCls} animate-spin {showLabels ? 'sm:mr-1.5' : ''}" />
				{:else}<Square class="{iconCls} {showLabels ? 'sm:mr-1.5' : ''}" />{/if}
				{#if showLabels}<span class="hidden sm:inline">Stop</span>{/if}
			</Button>
			<Button variant="outline" size={btnSize} onclick={onRestart} disabled={!!busy} title="Restart container">
				{#if busy === 'restart'}<LoaderCircle class="{iconCls} animate-spin {showLabels ? 'sm:mr-1.5' : ''}" />
				{:else}<RotateCw class="{iconCls} {showLabels ? 'sm:mr-1.5' : ''}" />{/if}
				{#if showLabels}<span class="hidden sm:inline">Restart</span>{/if}
			</Button>
		{/if}
		{#if container.status !== 'building' && container.status !== 'pending' && container.status !== 'removed'}
			<Button
				variant="outline"
				size={btnSize}
				onclick={onRemove}
				disabled={!!busy}
				class="border-red-500/30 text-red-400 hover:bg-red-500/10"
				title="Remove container"
			>
				{#if busy === 'remove'}<LoaderCircle class="{iconCls} animate-spin {showLabels ? 'sm:mr-1.5' : ''}" />
				{:else}<Trash2 class="{iconCls} {showLabels ? 'sm:mr-1.5' : ''}" />{/if}
				{#if showLabels}<span class="hidden sm:inline">Remove</span>{/if}
			</Button>
		{/if}
	{/if}
</div>
