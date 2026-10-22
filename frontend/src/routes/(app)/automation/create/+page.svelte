<script lang="ts">
	import { goto } from '$app/navigation';
	import { Button } from '$lib/components/ui/button';
	import { Input } from '$lib/components/ui/input';
	import * as Sheet from '$lib/components/ui/sheet';
	import * as Card from '$lib/components/ui/card';
	import { Label } from '$lib/components/ui/label';
	import PageHeader from '$lib/components/PageHeader.svelte';
	import ErrorAlert from '$lib/components/ErrorAlert.svelte';
	import Save from '@lucide/svelte/icons/save';
	import Code from '@lucide/svelte/icons/code-2';
	import LayoutGrid from '@lucide/svelte/icons/layout-grid';
	import Settings2 from '@lucide/svelte/icons/settings-2';
	import Plus from '@lucide/svelte/icons/plus';
	import SlidersHorizontal from '@lucide/svelte/icons/sliders-horizontal';
	import { createPipeline } from '$lib/api/client';
	import type { Edge } from '@xyflow/svelte';
	import WorkflowCanvas from '$lib/components/workflow/WorkflowCanvas.svelte';
	import NodePalette from '$lib/components/workflow/NodePalette.svelte';
	import PropertiesPanel from '$lib/components/workflow/PropertiesPanel.svelte';
	import { flowToPipeline, makeNode, validateFlow, type StepKind, type WorkflowNode } from '$lib/utils/pipeline-flow';

	let name = $state('Untitled Workflow');
	let description = $state('');
	let status = $state('draft');
	let tags = $state('');
	let saving = $state(false);
	let errors = $state<string[]>([]);

	let nodes = $state<WorkflowNode[]>([]);
	let edges = $state<Edge[]>([]);
	let selectedNodeId = $state<string | null>(null);

	let showMeta = $state(false);
	let showJson = $state(false);
	let jsonDraft = $state('');
	let jsonError = $state('');

	// Mobile sheets
	let showMobilePalette = $state(false);
	let showMobileProperties = $state(false);

	$effect(() => {
		if (showJson) jsonDraft = JSON.stringify(flowToPipeline(nodes, edges), null, 2);
	});

	$effect(() => {
		if (selectedNodeId) showMobileProperties = true;
	});

	function addNodeAtCenter(kind: StepKind) {
		const idx = nodes.length;
		const x = 80 + (idx % 4) * 280;
		const y = 80 + Math.floor(idx / 4) * 180;
		const node = makeNode(kind, nodes.map((n) => n.id), { x, y });
		nodes = [...nodes, node];
		selectedNodeId = node.id;
		showMobilePalette = false;
	}

	async function save() {
		errors = [];
		jsonError = '';
		let configToSave: Record<string, unknown>;
		if (showJson) {
			try {
				configToSave = JSON.parse(jsonDraft);
			} catch (e) {
				jsonError = 'Invalid JSON: ' + (e as Error).message;
				return;
			}
		} else {
			const validationErrors = validateFlow(nodes);
			if (validationErrors.length > 0) {
				errors = validationErrors;
				return;
			}
			configToSave = flowToPipeline(nodes, edges) as unknown as Record<string, unknown>;
		}
		if (!name.trim()) {
			errors = ['Name is required'];
			return;
		}
		saving = true;
		try {
			const p = await createPipeline({
				name: name.trim(),
				description,
				status,
				config: configToSave,
				tags: tags.split(',').map((t) => t.trim()).filter(Boolean)
			});
			goto(`/automation/${p.id}`);
		} catch (e: unknown) {
			const body = e as { errors?: string[]; detail?: string };
			errors = body?.errors ?? [body?.detail ?? 'Failed to create pipeline'];
		} finally {
			saving = false;
		}
	}
</script>

<div class="flex h-[calc(100dvh-7rem)] flex-col gap-4">
	<PageHeader
		title="New workflow"
		description="Drag steps from the palette onto the canvas and connect them, or edit the JSON directly."
		back={{ href: '/automation', label: 'Workflows' }}
	>
		{#snippet actions()}
			<Button variant="outline" size="sm" onclick={() => (showJson = !showJson)} aria-pressed={showJson}>
				{#if showJson}<LayoutGrid class="h-3.5 w-3.5" />Canvas{:else}<Code class="h-3.5 w-3.5" />JSON{/if}
			</Button>
			<Button variant="outline" size="sm" onclick={() => (showMeta = !showMeta)} aria-expanded={showMeta}>
				<Settings2 class="h-3.5 w-3.5" />Metadata
			</Button>
			<Button onclick={save} disabled={saving} size="sm">
				<Save class="h-3.5 w-3.5" />{saving ? 'Saving…' : 'Create workflow'}
			</Button>
		{/snippet}
		<div class="flex max-w-sm items-center gap-2 pt-1">
			<Label for="wf-name" class="shrink-0 text-xs">Name</Label>
			<Input id="wf-name" bind:value={name} class="h-8 font-medium" placeholder="Workflow name" />
		</div>
	</PageHeader>

	{#if showMeta}
		<Card.Root class="shrink-0">
			<Card.Content class="grid grid-cols-1 gap-3 md:grid-cols-3">
				<div class="space-y-1">
					<Label for="meta-description" class="text-xs">Description</Label>
					<Input id="meta-description" bind:value={description} placeholder="What does this workflow do?" class="h-8 text-xs" />
				</div>
				<div class="space-y-1">
					<Label for="meta-status" class="text-xs">Status</Label>
					<select id="meta-status" bind:value={status} class="std-select h-8 w-full text-xs">
						<option value="draft">Draft</option>
						<option value="active">Active</option>
						<option value="archived">Archived</option>
					</select>
				</div>
				<div class="space-y-1">
					<Label for="meta-tags" class="text-xs">Tags (comma-separated)</Label>
					<Input id="meta-tags" bind:value={tags} placeholder="ci, nightly, prod" class="h-8 text-xs" />
				</div>
			</Card.Content>
		</Card.Root>
	{/if}

	{#if errors.length > 0}
		<ErrorAlert class="shrink-0" title="Can't save workflow" message={errors.join(' · ')} />
	{/if}

	<!-- Main area -->
	{#if showJson}
		<div class="flex min-h-0 flex-1 flex-col gap-1">
			<textarea
				bind:value={jsonDraft}
				aria-label="Workflow JSON"
				aria-invalid={jsonError ? 'true' : undefined}
				class="min-h-0 w-full flex-1 rounded-md border bg-background p-3 font-mono text-sm resize-none focus:outline-none focus:ring-2 focus:ring-ring"
			></textarea>
			{#if jsonError}<p class="text-xs text-destructive" role="alert">{jsonError}</p>{/if}
		</div>
	{:else}
		<div class="relative flex min-h-0 flex-1 overflow-hidden rounded-md border">
			<div class="hidden md:flex">
				<NodePalette />
			</div>
			<div class="flex-1 min-w-0 relative bg-background">
				<WorkflowCanvas bind:nodes bind:edges bind:selectedNodeId />
			</div>
			<div class="hidden md:flex">
				<PropertiesPanel bind:nodes bind:edges bind:selectedNodeId />
			</div>

			<!-- Mobile FABs -->
			<div class="md:hidden absolute bottom-4 inset-x-0 z-20 flex justify-between px-4 pointer-events-none">
				<Button
					size="lg"
					class="rounded-full h-12 w-12 shadow-lg pointer-events-auto"
					onclick={() => (showMobilePalette = true)}
					title="Add node"
				>
					<Plus class="h-5 w-5" />
					<span class="sr-only">Add node</span>
				</Button>
				{#if selectedNodeId}
					<Button
						size="lg"
						variant="secondary"
						class="rounded-full h-12 w-12 shadow-lg pointer-events-auto"
						onclick={() => (showMobileProperties = true)}
						title="Edit selected node"
					>
						<SlidersHorizontal class="h-5 w-5" />
						<span class="sr-only">Edit selected node</span>
					</Button>
				{/if}
			</div>
		</div>

		<!-- Mobile palette sheet -->
		<Sheet.Root bind:open={showMobilePalette}>
			<Sheet.Content side="bottom" class="max-h-[75vh] overflow-y-auto p-0 md:hidden">
				<Sheet.Header class="px-4 pt-4 pb-2">
					<Sheet.Title>Add Node</Sheet.Title>
					<Sheet.Description>Tap a node type to add it to the canvas.</Sheet.Description>
				</Sheet.Header>
				<NodePalette compact onAdd={addNodeAtCenter} />
			</Sheet.Content>
		</Sheet.Root>

		<!-- Mobile properties sheet -->
		<Sheet.Root bind:open={showMobileProperties}>
			<Sheet.Content side="bottom" class="max-h-[80vh] overflow-y-auto p-0 md:hidden">
				<Sheet.Header class="px-4 pt-4 pb-2">
					<Sheet.Title>Node Properties</Sheet.Title>
					<Sheet.Description>Edit the selected node's configuration.</Sheet.Description>
				</Sheet.Header>
				<PropertiesPanel bind:nodes bind:edges bind:selectedNodeId />
			</Sheet.Content>
		</Sheet.Root>
	{/if}
</div>
