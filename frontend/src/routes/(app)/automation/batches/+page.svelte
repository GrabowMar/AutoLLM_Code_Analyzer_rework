<script lang="ts">
import { onMount } from 'svelte';
import { goto } from '$app/navigation';
import * as Card from '$lib/components/ui/card';
import { Badge } from '$lib/components/ui/badge';
import { Button } from '$lib/components/ui/button';
import Plus from '@lucide/svelte/icons/plus';
import ArrowLeft from '@lucide/svelte/icons/arrow-left';
import Eye from '@lucide/svelte/icons/eye';
import Layers from '@lucide/svelte/icons/layers';
import RefreshCw from '@lucide/svelte/icons/refresh-cw';
import LoaderCircle from '@lucide/svelte/icons/loader-circle';
import ChevronLeft from '@lucide/svelte/icons/chevron-left';
import ChevronRight from '@lucide/svelte/icons/chevron-right';
import { listBatches, getBatch, type BatchSummary } from '$lib/api/client';

let batches = $state<BatchSummary[]>([]);
let total = $state(0);
let page = $state(1);
let pages = $state(1);
let loading = $state(true);
let error = $state('');

// Progress counts per batch
let progressMap = $state<Record<string, { succeeded: number; total: number }>>({});

const statusColors: Record<string, string> = {
	pending: 'bg-slate-500/15 text-slate-400 border-slate-500/30',
	running: 'bg-blue-500/15 text-blue-400 border-blue-500/30',
	succeeded: 'bg-emerald-500/15 text-emerald-500 border-emerald-500/30',
	failed: 'bg-red-500/15 text-red-400 border-red-500/30',
	cancelled: 'bg-orange-500/15 text-orange-400 border-orange-500/30'
};

async function load() {
	loading = true;
	try {
		const res = await listBatches(page);
		batches = res.items;
		total = res.total;
		pages = res.pages;
		error = '';
		// Load item counts for progress bars
		for (const b of batches) {
			try {
				const detail = await getBatch(b.id);
				const total = detail.items.length;
				const succeeded = detail.items.filter((i) => i.status === 'succeeded').length;
				progressMap[b.id] = { succeeded, total };
			} catch { /* skip */ }
		}
	} catch (e) {
		error = 'Failed to load batches';
	} finally {
		loading = false;
	}
}

function fmt(s: string) { return new Date(s).toLocaleDateString(); }

onMount(load);
</script>

<svelte:head><title>Batches — LLM Eval Lab</title></svelte:head>

<div class="space-y-6">
	<nav aria-label="Breadcrumb" class="flex items-center gap-2 text-sm text-muted-foreground">
		<a href="/automation" class="hover:text-foreground transition-colors flex items-center gap-1">
			<ArrowLeft class="h-3.5 w-3.5" />
			<span class="font-medium text-foreground">Automation</span>
		</a>
		<span>/</span>
		<span>Batches</span>
	</nav>
	<div class="flex flex-col gap-3 sm:flex-row sm:items-center sm:justify-between">
		<div class="page-header min-w-0">
			<h1>Batches</h1>
			<p>{total} batch{total !== 1 ? 'es' : ''}</p>
		</div>
		<div class="flex flex-wrap items-center gap-2">
			<Button variant="outline" size="sm" onclick={load}><RefreshCw class="mr-2 h-4 w-4" />Refresh</Button>
			<Button size="sm" onclick={() => goto('/automation/batches/create')}><Plus class="mr-2 h-4 w-4" />New Batch</Button>
		</div>
	</div>

	{#if loading}
		<Card.Root>
			<Card.Content class="flex items-center justify-center py-20">
				<LoaderCircle class="h-8 w-8 animate-spin text-muted-foreground" />
			</Card.Content>
		</Card.Root>
	{:else if error}
		<Card.Root>
			<Card.Content class="pt-6 text-center text-destructive">{error}</Card.Content>
		</Card.Root>
	{:else if batches.length === 0}
		<Card.Root>
			<Card.Content class="py-16 text-center">
				<Layers class="mx-auto h-12 w-12 text-muted-foreground/50 mb-4" />
				<h3 class="text-lg font-medium mb-1">No batches yet</h3>
				<p class="text-sm text-muted-foreground mb-4">Create a batch to run a pipeline across a matrix of models and templates.</p>
				<Button size="sm" href="/automation/batches/create">New Batch</Button>
			</Card.Content>
		</Card.Root>
	{:else}
		<!-- Table (desktop) -->
		<div class="hidden md:block">
			<Card.Root>
				<Card.Content class="p-0">
					<div class="overflow-x-auto">
						<table class="w-full">
							<thead>
								<tr class="border-b bg-muted/40 sticky top-0 z-10">
									<th class="px-3 py-2.5 text-left text-xs font-medium text-muted-foreground whitespace-nowrap">Name</th>
									<th class="px-3 py-2.5 text-left text-xs font-medium text-muted-foreground whitespace-nowrap">Status</th>
									<th class="px-3 py-2.5 text-left text-xs font-medium text-muted-foreground whitespace-nowrap">Progress</th>
									<th class="px-3 py-2.5 text-left text-xs font-medium text-muted-foreground whitespace-nowrap">Created</th>
									<th class="px-3 py-2.5 text-right text-xs font-medium text-muted-foreground whitespace-nowrap">Actions</th>
								</tr>
							</thead>
							<tbody>
								{#each batches as b, i (b.id)}
									{@const prog = progressMap[b.id]}
									<tr class="border-b transition-colors hover:bg-muted/50 group {i % 2 === 0 ? '' : 'bg-muted/15'} {b.status === 'failed' ? 'bg-destructive/[0.03]' : ''}">
										<td class="px-3 py-2 align-top">
											<button class="text-sm font-medium hover:underline text-left" onclick={() => goto(`/automation/batches/${b.id}`)}>{b.name}</button>
										</td>
										<td class="px-3 py-2 align-top"><Badge variant="outline" class="text-[10px] {statusColors[b.status] ?? ''}">{b.status}</Badge></td>
										<td class="px-3 py-2 align-top min-w-[140px]">
											{#if prog}
												<div class="space-y-0.5">
													<div class="h-1.5 w-full rounded-full bg-muted overflow-hidden">
														<div
															class="h-full rounded-full bg-emerald-500 transition-all"
															style="width: {prog.total > 0 ? (prog.succeeded / prog.total * 100).toFixed(0) : 0}%"
														></div>
													</div>
													<p class="text-xs text-muted-foreground">{prog.succeeded}/{prog.total} succeeded</p>
												</div>
											{:else}
												<span class="text-xs text-muted-foreground">—</span>
											{/if}
										</td>
										<td class="px-3 py-2 align-top text-sm text-muted-foreground">{fmt(b.created_at)}</td>
										<td class="px-3 py-2">
											<div class="flex items-center justify-end gap-1">
												<Button variant="ghost" size="sm" class="h-7 w-7 p-0" title="View" onclick={() => goto(`/automation/batches/${b.id}`)}>
													<Eye class="h-3.5 w-3.5" />
												</Button>
											</div>
										</td>
									</tr>
								{/each}
							</tbody>
						</table>
					</div>
				</Card.Content>
			</Card.Root>
		</div>

		<!-- Cards (mobile) -->
		<div class="md:hidden space-y-3">
			{#each batches as b (b.id)}
				{@const prog = progressMap[b.id]}
				<div class="border rounded-lg p-3 bg-card">
					<div class="flex items-start justify-between gap-2 mb-2">
						<button class="text-sm font-medium hover:underline truncate text-left" onclick={() => goto(`/automation/batches/${b.id}`)}>{b.name}</button>
						<Badge variant="outline" class="shrink-0 text-[10px] {statusColors[b.status] ?? ''}">{b.status}</Badge>
					</div>
					{#if prog}
						<div class="space-y-0.5 mb-2">
							<div class="h-1.5 w-full rounded-full bg-muted overflow-hidden">
								<div
									class="h-full rounded-full bg-emerald-500 transition-all"
									style="width: {prog.total > 0 ? (prog.succeeded / prog.total * 100).toFixed(0) : 0}%"
								></div>
							</div>
							<p class="text-xs text-muted-foreground">{prog.succeeded}/{prog.total} succeeded</p>
						</div>
					{/if}
					<div class="flex items-center justify-between border-t pt-2">
						<span class="text-xs text-muted-foreground">{fmt(b.created_at)}</span>
						<Button variant="ghost" size="sm" class="h-7 w-7 p-0" title="View" onclick={() => goto(`/automation/batches/${b.id}`)}>
							<Eye class="h-3.5 w-3.5" />
						</Button>
					</div>
				</div>
			{/each}
		</div>

		{#if pages > 1}
			<div class="flex justify-center gap-2">
				<Button variant="outline" size="sm" disabled={page <= 1} onclick={() => { page--; load(); }}><ChevronLeft class="h-4 w-4" /></Button>
				<span class="px-3 py-2 text-sm">Page {page} / {pages}</span>
				<Button variant="outline" size="sm" disabled={page >= pages} onclick={() => { page++; load(); }}><ChevronRight class="h-4 w-4" /></Button>
			</div>
		{/if}
	{/if}
</div>
