<script lang="ts">
	import { onMount, onDestroy } from 'svelte';
	import { Button } from '$lib/components/ui/button';
	import StatusBadge from '$lib/components/StatusBadge.svelte';
	import ConfirmDialog from '$lib/components/ConfirmDialog.svelte';
	import { formatApiError } from '$lib/api/core';
	import { containerTones } from '$lib/constants/colors';
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
			toast.error(`${name} failed: ${formatApiError(e, 'unknown error')}`);
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
	// Remove and rebuild both discard the current container: confirm first.
	let confirmAction = $state<'remove' | 'rebuild' | null>(null);
	let confirmOpen = $state(false);
	const onRemove = () => {
		if (!container) return;
		confirmAction = 'remove';
		confirmOpen = true;
	};
	const onRebuild = () => {
		if (!container) return;
		confirmAction = 'rebuild';
		confirmOpen = true;
	};
	function runConfirmed() {
		const action = confirmAction;
		confirmOpen = false;
		if (action === 'remove' && container) {
			doAction('remove', () => removeContainer(container!.id), 'Container removed');
		} else if (action === 'rebuild') {
			rebuild();
		}
	}
	async function rebuild() {
		if (!container) return;
		busy = 'rebuild';
		try {
			await removeContainer(container.id);
			await buildContainerForJob(jobId);
			toast.success('Rebuild started');
			await refresh(true);
		} catch (e) {
			toast.error(`Rebuild failed: ${formatApiError(e, 'unknown error')}`);
		} finally {
			busy = null;
		}
	}

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
		<StatusBadge status={container.status} tones={containerTones} />

		{#if container.status === 'failed' && container.last_error}
			<span class="text-xs text-destructive truncate max-w-[260px]" title={container.last_error}>
				{container.last_error}
			</span>
		{/if}

		{#if showPorts && previewUrl && container.status === 'running'}
			<Button
				variant="outline"
				size={btnSize}
				href={previewUrl}
				target="_blank"
				rel="noopener noreferrer"
				class="text-success-fg"
				title="Open app preview"
			>
				<ExternalLink class="{iconCls} {showLabels ? 'sm:mr-1.5' : ''}" />
				{#if showLabels}<span class="hidden sm:inline">Open</span>{/if}
			</Button>
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
				class="border-destructive/30 text-destructive hover:bg-destructive/10"
				title="Remove container"
			>
				{#if busy === 'remove'}<LoaderCircle class="{iconCls} animate-spin {showLabels ? 'sm:mr-1.5' : ''}" />
				{:else}<Trash2 class="{iconCls} {showLabels ? 'sm:mr-1.5' : ''}" />{/if}
				{#if showLabels}<span class="hidden sm:inline">Remove</span>{/if}
			</Button>
		{/if}
	{/if}
</div>

<ConfirmDialog
	bind:open={confirmOpen}
	title={confirmAction === 'rebuild' ? 'Rebuild the container?' : 'Remove this container?'}
	description={confirmAction === 'rebuild'
		? 'Removes the failed container and starts a fresh build.'
		: 'Stops and removes the container. The generated code is kept.'}
	confirmLabel={confirmAction === 'rebuild' ? 'Rebuild' : 'Remove container'}
	destructive
	onConfirm={runConfirmed}
/>
