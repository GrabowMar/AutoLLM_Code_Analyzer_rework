<script lang="ts">
	import { onMount } from 'svelte';
	import { goto } from '$app/navigation';
	import { Badge } from '$lib/components/ui/badge';
	import { Button } from '$lib/components/ui/button';
	import { Input } from '$lib/components/ui/input';
	import Plus from '@lucide/svelte/icons/plus';
	import Eye from '@lucide/svelte/icons/eye';
	import Edit from '@lucide/svelte/icons/pencil';
	import Copy from '@lucide/svelte/icons/copy';
	import Trash2 from '@lucide/svelte/icons/trash-2';
	import Play from '@lucide/svelte/icons/play';
	import Workflow from '@lucide/svelte/icons/workflow';
	import Search from '@lucide/svelte/icons/search';
	import PaginationBar from '$lib/components/PaginationBar.svelte';
	import PageHeader from '$lib/components/PageHeader.svelte';
	import StatusBadge from '$lib/components/StatusBadge.svelte';
	import EmptyState from '$lib/components/EmptyState.svelte';
	import LoadingState from '$lib/components/LoadingState.svelte';
	import ErrorAlert from '$lib/components/ErrorAlert.svelte';
	import {
		listPipelines,
		deletePipeline,
		clonePipeline,
		triggerPipelineRun,
		type PipelineListItem
	} from '$lib/api/client';

	let pipelines = $state<PipelineListItem[]>([]);
	let total = $state(0);
	let page = $state(1);
	let pages = $state(1);
	let loading = $state(true);
	let error = $state('');
	let search = $state('');
	let statusFilter = $state('');

	async function load() {
		loading = true;
		error = '';
		try {
			const res = await listPipelines({
				page,
				per_page: 20,
				search: search || undefined,
				status: statusFilter || undefined
			});
			pipelines = res.items;
			total = res.total;
			pages = res.pages;
		} catch (e) {
			error = (e as Error)?.message || 'Failed to load workflows';
		} finally {
			loading = false;
		}
	}

	async function remove(id: string, evt: Event) {
		evt.stopPropagation();
		if (!confirm('Delete this workflow?')) return;
		try {
			await deletePipeline(id);
			await load();
		} catch {
			alert('Failed to delete workflow');
		}
	}

	async function clone(id: string, name: string, evt: Event) {
		evt.stopPropagation();
		const newName = prompt('New workflow name:', `${name} (copy)`);
		if (!newName) return;
		try {
			const p = await clonePipeline(id, newName);
			goto(`/automation/${p.id}`);
		} catch {
			alert('Failed to clone workflow');
		}
	}

	async function run(id: string, evt: Event) {
		evt.stopPropagation();
		try {
			const r = await triggerPipelineRun(id);
			goto(`/automation/runs/${r.id}`);
		} catch (e: unknown) {
			alert((e as { detail?: string })?.detail ?? 'Failed to trigger run');
		}
	}

	function fmt(s: string) {
		const d = new Date(s);
		const now = Date.now();
		const diff = now - d.getTime();
		if (diff < 60_000) return 'just now';
		if (diff < 3_600_000) return `${Math.floor(diff / 60_000)}m ago`;
		if (diff < 86_400_000) return `${Math.floor(diff / 3_600_000)}h ago`;
		if (diff < 7 * 86_400_000) return `${Math.floor(diff / 86_400_000)}d ago`;
		return d.toLocaleDateString();
	}

	onMount(load);
</script>

<div class="space-y-6">
	<PageHeader
		title="Workflows"
		description="Visual, node-based automation pipelines. Compose generation, analysis and reporting steps as a workflow."
	>
		{#snippet actions()}
			<Button size="sm" href="/automation/create">
				<Plus class="h-4 w-4" />New workflow
			</Button>
		{/snippet}
	</PageHeader>

	<!-- Search + filter -->
	<div class="flex flex-col sm:flex-row gap-2">
		<div class="relative flex-1">
			<Search class="absolute left-3 top-1/2 -translate-y-1/2 h-3.5 w-3.5 text-muted-foreground pointer-events-none" />
			<Input
				bind:value={search}
				placeholder="Search workflows by name…"
				aria-label="Search workflows"
				class="pl-9 h-9"
				onkeydown={(e) => { if (e.key === 'Enter') { page = 1; load(); } }}
			/>
		</div>
		<select bind:value={statusFilter} onchange={() => { page = 1; load(); }} class="std-select" aria-label="Filter by status">
			<option value="">All statuses</option>
			<option value="draft">Draft</option>
			<option value="active">Active</option>
			<option value="archived">Archived</option>
		</select>
	</div>

	{#if loading}
		<LoadingState label="loading workflows" />
	{:else if error}
		<ErrorAlert message={error} onretry={load} />
	{:else if pipelines.length === 0}
		<EmptyState
			icon={Workflow}
			title={search || statusFilter ? 'No matching workflows' : 'No workflows yet'}
			description={search || statusFilter ? 'Try a different search or status filter.' : 'Build your first node-based automation workflow.'}
		>
			{#snippet action()}
				<Button size="sm" href="/automation/create"><Plus class="h-4 w-4" />Create workflow</Button>
			{/snippet}
		</EmptyState>
	{:else}
		<div class="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-3 gap-4">
			{#each pipelines as p (p.id)}
				<div class="card-lift group relative flex flex-col rounded-md border bg-card focus-within:ring-2 focus-within:ring-ring">
					<div class="p-4 flex-1 space-y-2">
						<div class="flex items-start justify-between gap-2">
							<h3 class="font-medium text-sm leading-tight group-hover:text-primary transition-colors line-clamp-2">
								<!-- stretched link: the whole card opens the workflow; action buttons sit above it -->
								<a href="/automation/{p.id}" class="after:absolute after:inset-0 focus-visible:outline-none">{p.name}</a>
							</h3>
							<StatusBadge status={p.status} />
						</div>
						{#if p.description}
							<p class="text-xs text-muted-foreground line-clamp-2">{p.description}</p>
						{:else}
							<p class="text-xs text-muted-foreground italic">No description</p>
						{/if}
						{#if p.tags.length > 0}
							<div class="flex flex-wrap gap-1 pt-1">
								{#each p.tags.slice(0, 4) as tag (tag)}
									<Badge variant="secondary" class="text-[11px] px-1.5 py-0">{tag}</Badge>
								{/each}
								{#if p.tags.length > 4}
									<Badge variant="secondary" class="text-[11px] px-1.5 py-0">+{p.tags.length - 4}</Badge>
								{/if}
							</div>
						{/if}
					</div>
					<div class="px-4 py-2 border-t bg-muted/30 flex items-center justify-between text-[11px] text-muted-foreground">
						<span>Updated {fmt(p.updated_at)}</span>
						<span class="font-mono">v{p.version}</span>
					</div>
					<div class="relative z-10 px-2 py-2 border-t flex items-center gap-0.5">
						<Button variant="ghost" size="sm" class="h-10 w-10 p-0 text-success-fg hover:text-success-fg hover:bg-success/10 touch-manipulation" title="Run" aria-label="Run workflow {p.name}" onclick={(e) => run(p.id, e)}>
							<Play class="h-4 w-4" />
						</Button>
						<Button variant="ghost" size="sm" class="h-10 w-10 p-0 touch-manipulation" title="View" aria-label="View {p.name}" onclick={(e) => { e.stopPropagation(); goto(`/automation/${p.id}`); }}>
							<Eye class="h-4 w-4" />
						</Button>
						<Button variant="ghost" size="sm" class="h-10 w-10 p-0 touch-manipulation" title="Edit" aria-label="Edit {p.name}" onclick={(e) => { e.stopPropagation(); goto(`/automation/${p.id}/edit`); }}>
							<Edit class="h-4 w-4" />
						</Button>
						<Button variant="ghost" size="sm" class="h-10 w-10 p-0 touch-manipulation" title="Clone" aria-label="Clone {p.name}" onclick={(e) => clone(p.id, p.name, e)}>
							<Copy class="h-4 w-4" />
						</Button>
						<div class="ml-auto">
							<Button variant="ghost" size="sm" class="h-10 w-10 p-0 text-destructive hover:text-destructive touch-manipulation" title="Delete" aria-label="Delete {p.name}" onclick={(e) => remove(p.id, e)}>
								<Trash2 class="h-4 w-4" />
							</Button>
						</div>
					</div>
				</div>
			{/each}
		</div>

		{#if pages > 1}
			<PaginationBar
				resultsText="{total} workflow{total === 1 ? '' : 's'}"
				{page}
				{pages}
				onGoToPage={(p) => { page = p; load(); }}
				class="rounded-md border border-border"
			/>
		{/if}
	{/if}
</div>
