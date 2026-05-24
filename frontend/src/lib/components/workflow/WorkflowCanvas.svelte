<script lang="ts">
	import { browser } from '$app/environment';
	import type { Edge } from '@xyflow/svelte';
	import LoaderCircle from '@lucide/svelte/icons/loader-circle';
	import type { WorkflowNode } from '$lib/utils/pipeline-flow';

	interface Props {
		nodes: WorkflowNode[];
		edges: Edge[];
		selectedNodeId?: string | null;
		readOnly?: boolean;
		runStatusMap?: Record<string, string>;
	}

	let {
		nodes = $bindable(),
		edges = $bindable(),
		selectedNodeId = $bindable<string | null>(null),
		readOnly = false,
		runStatusMap = {}
	}: Props = $props();
</script>

{#if browser}
	{#await import('./WorkflowCanvasClient.svelte') then mod}
		{@const Client = mod.default}
		<Client
			bind:nodes
			bind:edges
			bind:selectedNodeId
			{readOnly}
			{runStatusMap}
		/>
	{:catch err}
		<div class="h-full w-full flex items-center justify-center text-destructive text-sm p-4">
			Failed to load workflow editor: {err.message}
		</div>
	{/await}
{:else}
	<div class="h-full w-full flex items-center justify-center bg-background">
		<LoaderCircle class="h-6 w-6 animate-spin text-muted-foreground" />
	</div>
{/if}
