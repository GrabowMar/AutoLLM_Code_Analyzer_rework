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

	const accentClasses: Record<string, string> = {
		violet: 'border-violet-500/40 bg-violet-500/5',
		blue: 'border-blue-500/40 bg-blue-500/5',
		amber: 'border-amber-500/40 bg-amber-500/5',
		slate: 'border-slate-500/40 bg-slate-500/5',
		emerald: 'border-emerald-500/40 bg-emerald-500/5',
		gray: 'border-gray-500/40 bg-gray-500/5'
	};

	const headerClasses: Record<string, string> = {
		violet: 'bg-violet-500/15 text-violet-300',
		blue: 'bg-blue-500/15 text-blue-300',
		amber: 'bg-amber-500/15 text-amber-300',
		slate: 'bg-slate-500/15 text-slate-300',
		emerald: 'bg-emerald-500/15 text-emerald-300',
		gray: 'bg-gray-500/15 text-gray-300'
	};

	const statusRings: Record<string, string> = {
		pending: 'ring-1 ring-slate-500/30',
		running: 'ring-2 ring-blue-400 animate-pulse motion-reduce:animate-none',
		succeeded: 'ring-2 ring-emerald-500',
		failed: 'ring-2 ring-red-500',
		cancelled: 'ring-2 ring-orange-400'
	};

	const ring = $derived(runStatus ? (statusRings[runStatus] ?? '') : '');
	const selRing = $derived(selected ? 'ring-2 ring-primary' : '');
</script>

<div
	class="rounded-lg border-2 {accentClasses[accent]} {ring} {selRing} shadow-md backdrop-blur-sm w-[260px] text-foreground"
>
	<Handle type="target" position={Position.Left} class="!w-3 !h-3 !bg-foreground/60 !border-2 !border-background" />

	<div class="flex items-center gap-2 px-3 py-2 rounded-t-md {headerClasses[accent]} border-b border-current/20">
		<Icon class="h-4 w-4 shrink-0" />
		<div class="flex-1 min-w-0">
			<p class="text-xs uppercase tracking-wider opacity-70 leading-tight">{kindLabel}</p>
			<p class="text-sm font-medium truncate leading-tight">{name || 'unnamed'}</p>
		</div>
		{#if runStatus === 'running'}
			<LoaderCircle class="h-4 w-4 animate-spin text-blue-400 motion-reduce:animate-none" />
		{:else if runStatus === 'succeeded'}
			<CheckCircle class="h-4 w-4 text-emerald-400" />
		{:else if runStatus === 'failed'}
			<AlertCircle class="h-4 w-4 text-destructive" />
		{:else if runStatus === 'cancelled'}
			<XCircle class="h-4 w-4 text-orange-400" />
		{:else if runStatus === 'pending'}
			<Clock class="h-4 w-4 text-slate-400" />
		{/if}
	</div>

	<div class="px-3 py-2.5 space-y-1.5 text-xs">
		{@render children()}
	</div>

	<Handle type="source" position={Position.Right} class="!w-3 !h-3 !bg-foreground/60 !border-2 !border-background" />
</div>
