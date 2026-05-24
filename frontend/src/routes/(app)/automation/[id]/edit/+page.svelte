<script lang="ts">
	import { page } from '$app/state';
	import { goto } from '$app/navigation';
	import { onMount } from 'svelte';
	import { Button } from '$lib/components/ui/button';
	import { Input } from '$lib/components/ui/input';
	import * as Sheet from '$lib/components/ui/sheet';
	import ArrowLeft from '@lucide/svelte/icons/arrow-left';
	import Save from '@lucide/svelte/icons/save';
	import Code from '@lucide/svelte/icons/code-2';
	import LayoutGrid from '@lucide/svelte/icons/layout-grid';
	import Settings2 from '@lucide/svelte/icons/settings-2';
	import LoaderCircle from '@lucide/svelte/icons/loader-circle';
	import Plus from '@lucide/svelte/icons/plus';
	import SlidersHorizontal from '@lucide/svelte/icons/sliders-horizontal';
	import { getPipeline, updatePipeline } from '$lib/api/client';
	import type { Edge } from '@xyflow/svelte';
	import WorkflowCanvas from '$lib/components/workflow/WorkflowCanvas.svelte';
	import NodePalette from '$lib/components/workflow/NodePalette.svelte';
	import PropertiesPanel from '$lib/components/workflow/PropertiesPanel.svelte';
	import {
		flowToPipeline,
		makeNode,
		pipelineToFlow,
		validateFlow,
		type PipelineConfig,
		type StepKind,
		type WorkflowNode
	} from '$lib/utils/pipeline-flow';

	const id = page.params.id as string;

	let name = $state('');
	let description = $state('');
	let status = $state('draft');
	let tags = $state('');
	let loading = $state(true);
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

	onMount(async () => {
		try {
			const p = await getPipeline(id);
			name = p.name;
			description = p.description;
			status = p.status;
			tags = p.tags.join(', ');
			const cfg = (p.config as unknown as PipelineConfig | undefined) ?? { steps: [] };
			const flow = pipelineToFlow(cfg);
			nodes = flow.nodes;
			edges = flow.edges;
		} catch {
			errors = ['Failed to load pipeline'];
		} finally {
			loading = false;
		}
	});

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
			await updatePipeline(id, {
				name: name.trim(),
				description,
				status,
				config: configToSave,
				tags: tags.split(',').map((t) => t.trim()).filter(Boolean)
			});
			goto(`/automation/${id}`);
		} catch (e: unknown) {
			const body = e as { errors?: string[]; detail?: string };
			errors = body?.errors ?? [body?.detail ?? 'Failed to save'];
		} finally {
			saving = false;
		}
	}
</script>

<svelte:head><title>Edit Workflow — LLM Eval Lab</title></svelte:head>

<div class="flex flex-col h-[calc(100dvh-7rem)] -m-3 sm:-m-4 md:-m-5 mt-0 sm:mt-0 md:mt-0">
	{#if loading}
		<div class="flex-1 flex items-center justify-center">
			<LoaderCircle class="h-8 w-8 animate-spin text-muted-foreground" />
		</div>
	{:else}
		<header class="flex flex-wrap items-center gap-2 border-b bg-card/40 px-3 py-2 shrink-0">
			<Button variant="ghost" size="sm" onclick={() => goto(`/automation/${id}`)} class="gap-1.5">
				<ArrowLeft class="h-4 w-4" />Back
			</Button>
			<div class="h-5 w-px bg-border"></div>
			<Input bind:value={name} class="h-8 max-w-sm font-medium" placeholder="Workflow name" />
			<div class="ml-auto flex items-center gap-2 flex-wrap">
				<Button
					variant={showJson ? 'default' : 'outline'}
					size="sm"
					onclick={() => (showJson = !showJson)}
					class="gap-1.5"
				>
					{#if showJson}<LayoutGrid class="h-3.5 w-3.5" />Canvas{:else}<Code class="h-3.5 w-3.5" />JSON{/if}
				</Button>
				<Button variant="outline" size="sm" onclick={() => (showMeta = !showMeta)} class="gap-1.5">
					<Settings2 class="h-3.5 w-3.5" />Metadata
				</Button>
				<Button onclick={save} disabled={saving} size="sm" class="gap-1.5">
					<Save class="h-3.5 w-3.5" />{saving ? 'Saving…' : 'Save'}
				</Button>
			</div>
		</header>

		{#if showMeta}
			<div class="grid grid-cols-1 md:grid-cols-3 gap-3 border-b bg-muted/30 px-3 py-3 shrink-0">
				<div class="space-y-1">
					<label class="text-xs font-medium" for="meta-description">Description</label>
					<Input id="meta-description" bind:value={description} class="h-8 text-xs" />
				</div>
				<div class="space-y-1">
					<label class="text-xs font-medium" for="meta-status">Status</label>
					<select id="meta-status" bind:value={status} class="w-full h-8 rounded-md border bg-background px-2 text-xs">
						<option value="draft">Draft</option>
						<option value="active">Active</option>
						<option value="archived">Archived</option>
					</select>
				</div>
				<div class="space-y-1">
					<label class="text-xs font-medium" for="meta-tags">Tags</label>
					<Input id="meta-tags" bind:value={tags} placeholder="ci, nightly" class="h-8 text-xs" />
				</div>
			</div>
		{/if}

		{#if errors.length > 0}
			<div class="border-b bg-destructive/10 px-3 py-2 text-xs text-destructive shrink-0 space-y-0.5">
				{#each errors as e}<p>• {e}</p>{/each}
			</div>
		{/if}

		{#if showJson}
			<div class="flex-1 min-h-0 p-3">
				<textarea
					bind:value={jsonDraft}
					class="w-full h-full rounded-md border bg-background p-3 font-mono text-sm resize-none focus:outline-none focus:ring-2 focus:ring-ring"
				></textarea>
				{#if jsonError}<p class="text-xs text-destructive mt-1">{jsonError}</p>{/if}
			</div>
		{:else}
			<div class="flex-1 min-h-0 flex relative">
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
	{/if}
</div>
