<script lang="ts">
import { goto } from '$app/navigation';
import { onMount } from 'svelte';
import * as Card from '$lib/components/ui/card';
import { Button } from '$lib/components/ui/button';
import { Input } from '$lib/components/ui/input';
import { Textarea } from '$lib/components/ui/textarea';
import { Label } from '$lib/components/ui/label';
import { Badge } from '$lib/components/ui/badge';
import ArrowLeft from '@lucide/svelte/icons/arrow-left';
import Plus from '@lucide/svelte/icons/plus';
import X from '@lucide/svelte/icons/x';
import Code from '@lucide/svelte/icons/code-2';
import Wand from '@lucide/svelte/icons/wand-sparkles';
import { createBatch, listPipelines, getModels, getScaffoldingTemplates, getAppTemplates, type PipelineListItem, type LLMModelSummary, type ScaffoldingTemplate, type AppRequirementTemplate } from '$lib/api/client';

let pipelines = $state<PipelineListItem[]>([]);
let models = $state<LLMModelSummary[]>([]);
let scaffoldingTemplates = $state<ScaffoldingTemplate[]>([]);
let appTemplates = $state<AppRequirementTemplate[]>([]);

let pipelineId = $state('');
let name = $state('');
let description = $state('');
let saving = $state(false);
let errors = $state<string[]>([]);

// Matrix builder
let showJson = $state(false);
let matrixText = $state('{\n  "models": [],\n  "templates": []\n}');
let selectedModels = $state<string[]>([]);
let selectedTemplates = $state<string[]>([]);
let newModelInput = $state('');
let newTemplateInput = $state('');

const expandedCount = $derived(selectedModels.length * selectedTemplates.length);

function toggleModel(slug: string) {
	selectedModels = selectedModels.includes(slug)
		? selectedModels.filter((m) => m !== slug)
		: [...selectedModels, slug];
}

function toggleTemplate(slug: string) {
	selectedTemplates = selectedTemplates.includes(slug)
		? selectedTemplates.filter((t) => t !== slug)
		: [...selectedTemplates, slug];
}

function addModelManual() {
	const val = newModelInput.trim();
	if (val && !selectedModels.includes(val)) selectedModels = [...selectedModels, val];
	newModelInput = '';
}

function addTemplateManual() {
	const val = newTemplateInput.trim();
	if (val && !selectedTemplates.includes(val)) selectedTemplates = [...selectedTemplates, val];
	newTemplateInput = '';
}

function buildMatrix(): Record<string, unknown> {
	return {
		model_ids: selectedModels,
		template_slugs: selectedTemplates,
	};
}

async function save() {
	errors = [];
	if (!pipelineId) { errors = ['Select a pipeline']; return; }
	if (!name.trim()) { errors = ['Name is required']; return; }

	let matrix: Record<string, unknown>;
	if (showJson) {
		try { matrix = JSON.parse(matrixText); } catch (e) { errors = ['Invalid JSON: ' + (e as Error).message]; return; }
	} else {
		matrix = buildMatrix();
	}

	saving = true;
	try {
		const b = await createBatch({ pipeline_id: pipelineId, name, description, matrix });
		goto(`/automation/batches/${b.id}`);
	} catch (e: unknown) {
		const body = e as { detail?: string };
		errors = [body?.detail ?? 'Failed to create batch'];
	} finally {
		saving = false;
	}
}

onMount(async () => {
	const [pipelinesRes, modelsRes, scaffRes, appRes] = await Promise.allSettled([
		listPipelines({ per_page: 100 }),
		getModels({ per_page: 100 }),
		getScaffoldingTemplates(),
		getAppTemplates(),
	]);
	if (pipelinesRes.status === 'fulfilled') { pipelines = pipelinesRes.value.items; if (pipelines.length > 0) pipelineId = pipelines[0].id; }
	if (modelsRes.status === 'fulfilled') models = modelsRes.value.items;
	if (scaffRes.status === 'fulfilled') scaffoldingTemplates = scaffRes.value;
	if (appRes.status === 'fulfilled') appTemplates = appRes.value;
});
</script>

<svelte:head><title>New Batch — LLM Eval Lab</title></svelte:head>

<div class="space-y-6 max-w-2xl">
	<nav aria-label="Breadcrumb" class="flex items-center gap-2 text-sm text-muted-foreground">
		<a href="/automation" class="hover:text-foreground transition-colors flex items-center gap-1">
			<ArrowLeft class="h-3.5 w-3.5" />
			<span class="font-medium text-foreground">Automation</span>
		</a>
		<span>/</span>
		<a href="/automation/batches" class="hover:text-foreground transition-colors">Batches</a>
		<span>/</span>
		<span>New Batch</span>
	</nav>
	<div class="flex flex-col gap-3 sm:flex-row sm:items-center sm:justify-between">
		<div class="page-header">
			<h1>New Batch</h1>
			<p>Run a pipeline across a matrix of models and templates.</p>
		</div>
	</div>
	<Card.Root>
		<Card.Content class="pt-6 space-y-4">
			<div class="space-y-2">
				<Label for="pipeline">Pipeline *</Label>
				<select id="pipeline" bind:value={pipelineId} class="w-full rounded-md border bg-background px-3 py-2 text-sm">
					{#each pipelines as p}<option value={p.id}>{p.name}</option>{/each}
				</select>
				<p class="text-xs text-muted-foreground">Pipeline to expand into per-cell runs.</p>
			</div>
			<div class="space-y-2">
				<Label for="name">Batch Name *</Label>
				<Input id="name" bind:value={name} placeholder="My Batch Run" />
			</div>
			<div class="space-y-2">
				<Label for="desc">Description</Label>
				<Textarea id="desc" bind:value={description} rows={2} />
			</div>

			<!-- Matrix builder -->
			<div class="space-y-2">
				<div class="flex items-center justify-between">
					<Label>Matrix Configuration</Label>
					<Button variant="ghost" size="sm" class="text-xs gap-1.5" onclick={() => { showJson = !showJson; if (showJson) matrixText = JSON.stringify(buildMatrix(), null, 2); }}>
						{#if showJson}<Wand class="h-3.5 w-3.5" />Visual Builder{:else}<Code class="h-3.5 w-3.5" />Show JSON{/if}
					</Button>
				</div>

				{#if showJson}
					<textarea bind:value={matrixText} rows={8} class="w-full rounded-md border bg-background p-3 font-mono text-sm resize-y focus:outline-none focus:ring-2 focus:ring-ring"></textarea>
				{:else}
					<!-- Models selection -->
					<div class="rounded-md border p-3 space-y-2">
						<Label class="text-xs font-medium">Models ({selectedModels.length} selected)</Label>
						<div class="flex flex-wrap gap-1.5">
							{#each models.slice(0, 20) as m}
								<button
									type="button"
									onclick={() => toggleModel(m.slug)}
									class="rounded-full border px-2.5 py-0.5 text-xs transition-colors {selectedModels.includes(m.slug) ? 'bg-primary text-primary-foreground border-primary' : 'bg-background text-muted-foreground hover:border-primary/50'}"
								>{m.name}</button>
							{/each}
						</div>
						<div class="flex gap-1">
							<input type="text" bind:value={newModelInput} placeholder="Add model slug..." class="flex-1 rounded-md border bg-background px-2 py-1 text-xs" onkeydown={(e) => e.key === 'Enter' && addModelManual()} />
							<Button size="icon" variant="outline" class="h-7 w-7" onclick={addModelManual}><Plus class="h-3 w-3" /></Button>
						</div>
						{#if selectedModels.length > 0}
							<div class="flex flex-wrap gap-1">
								{#each selectedModels as m}
									<Badge variant="secondary" class="text-xs gap-1">
										{m}
										<button type="button" onclick={() => toggleModel(m)} class="hover:text-destructive"><X class="h-2.5 w-2.5" /></button>
									</Badge>
								{/each}
							</div>
						{/if}
					</div>

					<!-- Templates selection -->
					<div class="rounded-md border p-3 space-y-2">
						<Label class="text-xs font-medium">Templates ({selectedTemplates.length} selected)</Label>
						<div class="flex flex-wrap gap-1.5">
							{#each [...scaffoldingTemplates, ...appTemplates].slice(0, 20) as t}
								<button
									type="button"
									onclick={() => toggleTemplate(t.slug)}
									class="rounded-full border px-2.5 py-0.5 text-xs transition-colors {selectedTemplates.includes(t.slug) ? 'bg-primary text-primary-foreground border-primary' : 'bg-background text-muted-foreground hover:border-primary/50'}"
								>{t.name}</button>
							{/each}
						</div>
						<div class="flex gap-1">
							<input type="text" bind:value={newTemplateInput} placeholder="Add template slug..." class="flex-1 rounded-md border bg-background px-2 py-1 text-xs" onkeydown={(e) => e.key === 'Enter' && addTemplateManual()} />
							<Button size="icon" variant="outline" class="h-7 w-7" onclick={addTemplateManual}><Plus class="h-3 w-3" /></Button>
						</div>
						{#if selectedTemplates.length > 0}
							<div class="flex flex-wrap gap-1">
								{#each selectedTemplates as t}
									<Badge variant="secondary" class="text-xs gap-1">
										{t}
										<button type="button" onclick={() => toggleTemplate(t)} class="hover:text-destructive"><X class="h-2.5 w-2.5" /></button>
									</Badge>
								{/each}
							</div>
						{/if}
					</div>

					<!-- Expansion preview -->
					{#if selectedModels.length > 0 || selectedTemplates.length > 0}
						<div class="rounded-md bg-muted/50 px-3 py-2 text-sm text-center">
							{#if expandedCount > 0}
								<span class="font-medium text-foreground">{selectedModels.length} model{selectedModels.length !== 1 ? 's' : ''} × {selectedTemplates.length} template{selectedTemplates.length !== 1 ? 's' : ''} = <span class="text-primary font-bold">{expandedCount} run{expandedCount !== 1 ? 's' : ''}</span></span>
							{:else}
								<span class="text-muted-foreground">Select models and templates to see expansion</span>
							{/if}
						</div>
					{/if}
				{/if}
			</div>

			{#if errors.length > 0}
				<div class="rounded-md border border-destructive/50 bg-destructive/10 p-3 text-sm text-destructive space-y-1">
					{#each errors as e}<p>{e}</p>{/each}
				</div>
			{/if}
			<div class="flex justify-end gap-2 pt-2">
				<Button variant="outline" onclick={() => goto('/automation/batches')}>Cancel</Button>
				<Button onclick={save} disabled={saving}>{saving ? 'Creating...' : 'Create Batch'}</Button>
			</div>
		</Card.Content>
	</Card.Root>
</div>
