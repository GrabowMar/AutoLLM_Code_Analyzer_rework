<script lang="ts">
	import { Handle, Position } from '@xyflow/svelte';
	import LoaderCircle from '@lucide/svelte/icons/loader-circle';
	import CheckCircle from '@lucide/svelte/icons/check-circle';
	import AlertCircle from '@lucide/svelte/icons/alert-circle';
	import XCircle from '@lucide/svelte/icons/x-circle';
	import Clock from '@lucide/svelte/icons/clock';
	import type { Snippet, Component } from 'svelte';

	interface Props {
		name: string;
		runStatus?: string;
		accent: 'violet' | 'blue' | 'amber' | 'slate' | 'emerald' | 'gray';
		icon: Component;
		kindLabel: string;
		selected?: boolean;
		readOnly?: boolean;
		children: Snippet;
	}

	let { name, runStatus, accent, icon: Icon, kindLabel, selected, readOnly, children }: Props = $props();

	// Kind identity is the icon tint (categorical chart slots); the shell itself stays neutral.
	const accentClasses: Record<string, string> = {
		violet: 'text-chart-3',
		blue: 'text-chart-2',
		amber: 'text-chart-4',
		emerald: 'text-chart-1',
		slate: 'text-muted-foreground',
		gray: 'text-muted-foreground'
	};

	const statusRings: Record<string, string> = {
		pending: 'ring-1 ring-border',
		running: 'ring-2 ring-info animate-pulse motion-reduce:animate-none',
		succeeded: 'ring-2 ring-success',
		failed: 'ring-2 ring-destructive',
		cancelled: 'ring-2 ring-warning'
	};

	const ring = $derived(runStatus ? (statusRings[runStatus] ?? '') : '');
	const selRing = $derived(selected ? 'ring-2 ring-primary' : '');
</script>

<div
	class="w-[260px] rounded-md border border-border bg-card text-card-foreground shadow-sm {ring} {selRing}"
>
	<Handle type="target" position={Position.Left} class="!w-3 !h-3 !bg-foreground/60 !border-2 !border-background" />

	<div class="flex items-center gap-2 rounded-t-md border-b border-border bg-muted/40 px-3 py-2">
		<Icon class="h-4 w-4 shrink-0 {accentClasses[accent]}" aria-hidden="true" />
		<div class="flex-1 min-w-0">
			<p class="section-label leading-tight">{kindLabel}</p>
			<p class="text-sm font-medium truncate leading-tight">{name || 'unnamed'}</p>
		</div>
		{#if runStatus === 'running'}
			<LoaderCircle class="h-4 w-4 animate-spin text-info-fg motion-reduce:animate-none" aria-label="running" />
		{:else if runStatus === 'succeeded'}
			<CheckCircle class="h-4 w-4 text-success-fg" aria-label="succeeded" />
		{:else if runStatus === 'failed'}
			<AlertCircle class="h-4 w-4 text-danger-fg" aria-label="failed" />
		{:else if runStatus === 'cancelled'}
			<XCircle class="h-4 w-4 text-warning-fg" aria-label="cancelled" />
		{:else if runStatus === 'pending'}
			<Clock class="h-4 w-4 text-muted-foreground" aria-label="pending" />
		{/if}
	</div>

	<div class="px-3 py-2.5 space-y-1.5 text-xs">
		{@render children()}
	</div>

	<Handle type="source" position={Position.Right} class="!w-3 !h-3 !bg-foreground/60 !border-2 !border-background" />
</div>
