<script lang="ts">
import { page } from '$app/state';
import { goto } from '$app/navigation';
import { onMount } from 'svelte';
import * as Card from '$lib/components/ui/card';
import { Badge } from '$lib/components/ui/badge';
import { Button } from '$lib/components/ui/button';
import ArrowLeft from '@lucide/svelte/icons/arrow-left';
import XCircle from '@lucide/svelte/icons/x-circle';
import LoaderCircle from '@lucide/svelte/icons/loader-circle';
import { getBatch, cancelBatch, type BatchDetail } from '$lib/api/client';

const id = page.params.id;
let batch = $state<BatchDetail | null>(null);
let loading = $state(true);
let error = $state('');
let cancelling = $state(false);

const statusColors: Record<string, string> = {
	pending: 'bg-slate-500/15 text-slate-400 border-slate-500/30',
	running: 'bg-blue-500/15 text-blue-400 border-blue-500/30',
	succeeded: 'bg-emerald-500/15 text-emerald-500 border-emerald-500/30',
	failed: 'bg-red-500/15 text-red-400 border-red-500/30',
	cancelled: 'bg-orange-500/15 text-orange-400 border-orange-500/30'
};

function fmt(s: string) { return new Date(s).toLocaleString(); }

async function cancel() {
	if (!confirm('Cancel this batch?')) return;
	cancelling = true;
	try {
		batch = await cancelBatch(id);
	} catch (e: unknown) {
		const body = e as { detail?: string };
		if ((e as { status?: number })?.status === 404) {
			alert('Cancel endpoint not yet available');
		} else {
			alert(body?.detail ?? 'Failed to cancel batch');
		}
	} finally {
		cancelling = false;
	}
}

const succeeded = $derived(batch?.items.filter((i) => i.status === 'succeeded').length ?? 0);
const total = $derived(batch?.items.length ?? 0);

onMount(async () => {
	try {
		batch = await getBatch(id);
	} catch (e) {
		error = 'Failed to load batch';
	} finally {
		loading = false;
	}
});
</script>

<svelte:head><title>Batch — LLM Eval Lab</title></svelte:head>

<div class="space-y-6">
	<nav aria-label="Breadcrumb" class="flex items-center gap-2 text-sm text-muted-foreground">
		<a href="/automation" class="hover:text-foreground transition-colors flex items-center gap-1">
			<ArrowLeft class="h-3.5 w-3.5" />
			<span class="font-medium text-foreground">Automation</span>
		</a>
		<span>/</span>
		<a href="/automation/batches" class="hover:text-foreground transition-colors">Batches</a>
		<span>/</span>
		<span class="truncate max-w-[300px]">{batch?.name ?? 'Batch'}</span>
	</nav>
	<div class="flex flex-col gap-3 sm:flex-row sm:items-start sm:justify-between">
		<div class="page-header">
			<h1>
				{batch?.name ?? 'Batch'}
				{#if batch}<Badge class={statusColors[batch.status] ?? ''} variant="outline">{batch.status}</Badge>{/if}
			</h1>
			<p class="font-mono text-xs">{id}</p>
		</div>
		{#if batch && (batch.status === 'pending' || batch.status === 'running')}
			<div class="flex items-center gap-2 flex-wrap">
				<Button variant="destructive" size="sm" onclick={cancel} disabled={cancelling}>
					<XCircle class="mr-2 h-4 w-4" />{cancelling ? 'Cancelling...' : 'Cancel Batch'}
				</Button>
			</div>
		{/if}
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
	{:else if batch}
		<Card.Root>
			<Card.Header><Card.Title>Details</Card.Title></Card.Header>
			<Card.Content class="space-y-3 text-sm">
				<div class="flex justify-between"><span class="text-muted-foreground">Created</span><span>{fmt(batch.created_at)}</span></div>
				{#if batch.description}<p class="text-muted-foreground">{batch.description}</p>{/if}
				<!-- Progress bar -->
				{#if total > 0}
					<div class="space-y-1">
						<div class="flex justify-between text-xs text-muted-foreground">
							<span>Progress</span><span>{succeeded}/{total} succeeded</span>
						</div>
						<div class="h-2 w-full rounded-full bg-muted overflow-hidden">
							<div class="h-full rounded-full bg-emerald-500 transition-all" style="width: {(succeeded / total * 100).toFixed(0)}%"></div>
						</div>
					</div>
				{/if}
				<pre class="text-xs font-mono bg-muted/50 rounded p-2 overflow-auto max-h-40">{JSON.stringify(batch.config, null, 2)}</pre>
			</Card.Content>
		</Card.Root>
		<Card.Root>
			<Card.Header><Card.Title>Items ({batch.items.length})</Card.Title></Card.Header>
			<Card.Content class={batch.items.length === 0 ? '' : 'p-0'}>
				{#if batch.items.length === 0}
					<p class="text-sm text-muted-foreground">No items.</p>
				{:else}
					<!-- Table (desktop) -->
					<div class="hidden md:block">
						<div class="overflow-x-auto">
							<table class="w-full">
								<thead>
									<tr class="border-b bg-muted/40 sticky top-0 z-10">
										<th class="px-3 py-2.5 text-left text-xs font-medium text-muted-foreground whitespace-nowrap">Item ID</th>
										<th class="px-3 py-2.5 text-left text-xs font-medium text-muted-foreground whitespace-nowrap">Status</th>
										<th class="px-3 py-2.5 text-left text-xs font-medium text-muted-foreground whitespace-nowrap">Run</th>
										<th class="px-3 py-2.5 text-left text-xs font-medium text-muted-foreground whitespace-nowrap">Created</th>
									</tr>
								</thead>
								<tbody>
									{#each batch.items as item, i (item.id)}
										<tr class="border-b transition-colors hover:bg-muted/50 group {i % 2 === 0 ? '' : 'bg-muted/15'} {item.status === 'failed' ? 'bg-destructive/[0.03]' : ''}">
											<td class="px-3 py-2 align-top font-mono text-xs">{item.id.slice(0, 8)}…</td>
											<td class="px-3 py-2 align-top"><Badge variant="outline" class="text-[10px] {statusColors[item.status] ?? ''}">{item.status}</Badge></td>
											<td class="px-3 py-2 align-top">
												{#if item.pipeline_run_id}
													<button class="hover:underline text-xs font-mono text-primary" onclick={() => goto(`/automation/runs/${item.pipeline_run_id}`)}>{item.pipeline_run_id.slice(0, 8)}…</button>
												{:else}—{/if}
											</td>
											<td class="px-3 py-2 align-top text-sm text-muted-foreground">{fmt(item.created_at)}</td>
										</tr>
									{/each}
								</tbody>
							</table>
						</div>
					</div>

					<!-- Cards (mobile) -->
					<div class="md:hidden space-y-3 p-3">
						{#each batch.items as item (item.id)}
							<div class="border rounded-lg p-3 bg-card">
								<div class="flex items-start justify-between gap-2 mb-2">
									<span class="font-mono text-xs">{item.id.slice(0, 8)}…</span>
									<Badge variant="outline" class="shrink-0 text-[10px] {statusColors[item.status] ?? ''}">{item.status}</Badge>
								</div>
								<div class="text-xs text-muted-foreground space-y-0.5">
									<div>Created: {fmt(item.created_at)}</div>
									{#if item.pipeline_run_id}
										<div>Run: <button class="hover:underline font-mono text-primary" onclick={() => goto(`/automation/runs/${item.pipeline_run_id}`)}>{item.pipeline_run_id.slice(0, 8)}…</button></div>
									{/if}
								</div>
							</div>
						{/each}
					</div>
				{/if}
			</Card.Content>
		</Card.Root>
	{/if}
</div>
