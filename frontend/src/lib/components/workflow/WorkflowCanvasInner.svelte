<script lang="ts">
	import {
		SvelteFlow,
		Background,
		Controls,
		MiniMap,
		Panel,
		useSvelteFlow,
		type Edge,
		type Connection,
		type NodeTypes
	} from '@xyflow/svelte';

	import GenerateNode from './nodes/GenerateNode.svelte';
	import AnalyzeNode from './nodes/AnalyzeNode.svelte';
	import ReportNode from './nodes/ReportNode.svelte';
	import WaitNode from './nodes/WaitNode.svelte';
	import NotifyNode from './nodes/NotifyNode.svelte';
	import ScriptNode from './nodes/ScriptNode.svelte';

	import {
		defaultConfigFor,
		uniqueStepName,
		type StepKind,
		type WorkflowNode
	} from '$lib/utils/pipeline-flow';

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

	const nodeTypes: NodeTypes = {
		generate: GenerateNode as unknown as NodeTypes[string],
		analyze: AnalyzeNode as unknown as NodeTypes[string],
		report: ReportNode as unknown as NodeTypes[string],
		wait: WaitNode as unknown as NodeTypes[string],
		notify: NotifyNode as unknown as NodeTypes[string],
		script: ScriptNode as unknown as NodeTypes[string]
	};

	const { screenToFlowPosition } = useSvelteFlow();

	let lastStatusKey = $state('');
	$effect(() => {
		const key = JSON.stringify(runStatusMap);
		if (key === lastStatusKey) return;
		lastStatusKey = key;
		const map = runStatusMap;
		nodes = nodes.map((n) => ({
			...n,
			data: { ...n.data, runStatus: map[n.data.name] ?? map[n.id] }
		}));
	});

	function handleConnect(connection: Connection) {
		if (!connection.source || !connection.target) return;
		if (connection.source === connection.target) return;
		const id = `${connection.source}->${connection.target}`;
		if (edges.some((e) => e.id === id)) return;
		edges = [
			...edges,
			{
				id,
				source: connection.source,
				target: connection.target,
				animated: true,
				type: 'smoothstep'
			}
		];
	}

	function handleDragOver(event: DragEvent) {
		event.preventDefault();
		if (event.dataTransfer) event.dataTransfer.dropEffect = 'move';
	}

	function handleDrop(event: DragEvent) {
		event.preventDefault();
		if (readOnly) return;
		const kind = event.dataTransfer?.getData('application/workflow-node') as StepKind | undefined;
		if (!kind) return;

		const position = screenToFlowPosition({ x: event.clientX, y: event.clientY });
		const existing = new Set(nodes.map((n) => n.id));
		const name = uniqueStepName(existing, kind);

		const newNode: WorkflowNode = {
			id: name,
			type: kind,
			position,
			data: {
				name,
				kind,
				config: defaultConfigFor(kind),
				max_retries: 0
			}
		};
		nodes = [...nodes, newNode];
		selectedNodeId = newNode.id;
	}

	function handleNodeClick({ node }: { node: WorkflowNode }) {
		selectedNodeId = node.id;
	}

	function handlePaneClick() {
		selectedNodeId = null;
	}
</script>

<div
	class="h-full w-full relative svelte-flow-wrapper"
	ondrop={handleDrop}
	ondragover={handleDragOver}
	role="application"
>
	<SvelteFlow
		bind:nodes
		bind:edges
		{nodeTypes}
		onconnect={handleConnect}
		onnodeclick={handleNodeClick}
		onpaneclick={handlePaneClick}
		nodesDraggable={!readOnly}
		nodesConnectable={!readOnly}
		edgesFocusable={!readOnly}
		elementsSelectable={true}
		fitView
		minZoom={0.2}
		maxZoom={2}
		defaultEdgeOptions={{ type: 'smoothstep', animated: true }}
		colorMode="dark"
	>
		<Background />
		<Controls />
		<MiniMap
			class="!bg-background/80 !border !border-border"
			nodeColor={(n) => {
				const kind = (n.type as StepKind) ?? 'script';
				const map: Record<string, string> = {
					generate: '#8b5cf6',
					analyze: '#3b82f6',
					report: '#f59e0b',
					wait: '#64748b',
					notify: '#10b981',
					script: '#6b7280'
				};
				return map[kind] ?? '#6b7280';
			}}
		/>
		{#if nodes.length === 0 && !readOnly}
			<Panel position="top-center">
				<div class="rounded-md border border-dashed border-border bg-background/80 backdrop-blur px-4 py-3 text-center max-w-md">
					<p class="text-sm font-medium">Empty workflow</p>
					<p class="text-xs text-muted-foreground mt-1">Drag a node from the palette on the left to begin.</p>
				</div>
			</Panel>
		{/if}
	</SvelteFlow>
</div>

<style>
	:global(.svelte-flow-wrapper .svelte-flow__node) {
		background: transparent;
		border: none;
		padding: 0;
		border-radius: 0;
	}
	:global(.svelte-flow-wrapper .svelte-flow__node.selected) {
		box-shadow: none;
	}
	:global(.svelte-flow-wrapper .svelte-flow__edge-path) {
		stroke-width: 2;
	}
	:global(.svelte-flow-wrapper .svelte-flow__attribution) {
		opacity: 0.5;
		font-size: 9px;
	}
</style>
