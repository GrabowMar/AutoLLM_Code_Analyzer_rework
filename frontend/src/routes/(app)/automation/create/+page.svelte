<script lang="ts">
import { goto } from '$app/navigation';
import * as Card from '$lib/components/ui/card';
import { Button } from '$lib/components/ui/button';
import { Input } from '$lib/components/ui/input';
import { Textarea } from '$lib/components/ui/textarea';
import { Label } from '$lib/components/ui/label';
import ArrowLeft from '@lucide/svelte/icons/arrow-left';
import Code from '@lucide/svelte/icons/code-2';
import Wand from '@lucide/svelte/icons/wand-sparkles';
import { createPipeline } from '$lib/api/client';
import PipelineBuilder from '$lib/components/automation/PipelineBuilder.svelte';

let name = $state('');
let description = $state('');
let status = $state('draft');
let tags = $state('');
let saving = $state(false);
let errors = $state<string[]>([]);
let showJson = $state(false);
let config = $state<Record<string, unknown>>({ steps: [] });
let dslText = $state('{\n  "steps": []\n}');
let parseError = $state('');

function onBuilderValueChange(v: Record<string, unknown>) {
	config = v;
	dslText = JSON.stringify(v, null, 2);
}

function onJsonChange(text: string) {
	dslText = text;
	parseError = '';
	try {
		config = JSON.parse(text);
	} catch (e) {
		parseError = 'Invalid JSON: ' + (e as Error).message;
	}
}

async function save() {
	errors = [];
	parseError = '';
	if (showJson) {
		try { config = JSON.parse(dslText); } catch (e) { parseError = 'Invalid JSON: ' + (e as Error).message; return; }
	}
	if (!name.trim()) { errors = ['Name is required']; return; }
	saving = true;
	try {
		const p = await createPipeline({
			name: name.trim(),
			description,
			status,
			config,
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

<svelte:head><title>New Pipeline — LLM Eval Lab</title></svelte:head>

<div class="space-y-6 max-w-3xl">
	<nav aria-label="Breadcrumb" class="flex items-center gap-2 text-sm text-muted-foreground">
		<a href="/automation" class="hover:text-foreground transition-colors flex items-center gap-1">
			<ArrowLeft class="h-3.5 w-3.5" />
			<span class="font-medium text-foreground">Automation</span>
		</a>
		<span>/</span>
		<span class="truncate max-w-[300px]">New Pipeline</span>
	</nav>
	<div class="flex flex-col gap-3 sm:flex-row sm:items-center sm:justify-between">
		<div class="page-header">
			<h1>New Pipeline</h1>
			<p>Define steps and configuration for a new automation pipeline.</p>
		</div>
	</div>

	<Card.Root>
		<Card.Content class="pt-6 space-y-4">
			<div class="grid grid-cols-2 gap-4">
				<div class="space-y-2 col-span-2 md:col-span-1">
					<Label for="name">Name *</Label>
					<Input id="name" bind:value={name} placeholder="My Pipeline" />
					<p class="text-xs text-muted-foreground">A short, human-readable name.</p>
				</div>
				<div class="space-y-2">
					<Label for="status">Status</Label>
					<select id="status" bind:value={status} class="w-full rounded-md border bg-background px-3 py-2 text-sm">
						<option value="draft">Draft</option>
						<option value="active">Active</option>
						<option value="archived">Archived</option>
					</select>
					<p class="text-xs text-muted-foreground">Only active pipelines can be triggered.</p>
				</div>
			</div>
			<div class="space-y-2">
				<Label for="desc">Description</Label>
				<Textarea id="desc" bind:value={description} rows={2} placeholder="What does this pipeline do?" />
				<p class="text-xs text-muted-foreground">Optional summary shown in lists and detail pages.</p>
			</div>
			<div class="space-y-2">
				<Label for="tags">Tags (comma-separated)</Label>
				<Input id="tags" bind:value={tags} placeholder="ci, nightly, prod" />
				<p class="text-xs text-muted-foreground">Used for filtering and grouping pipelines.</p>
			</div>

			<!-- Builder / JSON toggle -->
			<div class="space-y-2">
				<div class="flex items-center justify-between">
					<Label>Steps</Label>
					<Button variant="ghost" size="sm" class="text-xs gap-1.5" onclick={() => { showJson = !showJson; if (!showJson) { try { config = JSON.parse(dslText); } catch { /* keep old */ } } }}>
						{#if showJson}<Wand class="h-3.5 w-3.5" />Visual Builder{:else}<Code class="h-3.5 w-3.5" />Show JSON{/if}
					</Button>
				</div>

				{#if showJson}
					<div class="space-y-1">
						<textarea
							value={dslText}
							oninput={(e) => onJsonChange((e.target as HTMLTextAreaElement).value)}
							rows={14}
							class="w-full rounded-md border bg-background p-3 font-mono text-sm resize-y focus:outline-none focus:ring-2 focus:ring-ring"
						></textarea>
						{#if parseError}<p class="text-sm text-destructive">{parseError}</p>{/if}
					</div>
				{:else}
					<PipelineBuilder bind:value={config} bind:errors />
				{/if}
			</div>

			{#if errors.length > 0}
				<div class="rounded-md border border-destructive/50 bg-destructive/10 p-3 text-sm text-destructive space-y-1">
					{#each errors as e}<p>{e}</p>{/each}
				</div>
			{/if}

			<div class="flex justify-end gap-2 pt-2">
				<Button variant="outline" onclick={() => goto('/automation')}>Cancel</Button>
				<Button onclick={save} disabled={saving || errors.length > 0}>{saving ? 'Saving...' : 'Create Pipeline'}</Button>
			</div>
		</Card.Content>
	</Card.Root>
</div>
