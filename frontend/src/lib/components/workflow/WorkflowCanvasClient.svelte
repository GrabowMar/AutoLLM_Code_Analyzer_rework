<script lang="ts">
	import { SvelteFlowProvider, type Edge } from '@xyflow/svelte';
	import '@xyflow/svelte/dist/style.css';

	import WorkflowCanvasInner from './WorkflowCanvasInner.svelte';
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

<SvelteFlowProvider>
	<WorkflowCanvasInner
		bind:nodes
		bind:edges
		bind:selectedNodeId
		{readOnly}
		{runStatusMap}
	/>
</SvelteFlowProvider>
