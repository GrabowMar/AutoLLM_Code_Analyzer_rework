<script lang="ts">
	import { onMount } from 'svelte';
	import { goto } from '$app/navigation';
	import * as Card from '$lib/components/ui/card';
	import { Badge } from '$lib/components/ui/badge';
	import { Button } from '$lib/components/ui/button';
	import { Input } from '$lib/components/ui/input';
	import { Label } from '$lib/components/ui/label';
	import Layers from '@lucide/svelte/icons/layers';
	import Plus from '@lucide/svelte/icons/plus';
	import XCircle from '@lucide/svelte/icons/x-circle';
	import LoaderCircle from '@lucide/svelte/icons/loader-circle';
	import {
		listBatches,
		createBatch,
		cancelBatch,
		type BatchSummary
	} from '$lib/api/client';

	interface Props {
		pipelineId: string;
	}
	let { pipelineId }: Props = $props();

	const statusColors: Record<string, string> = {
		pending: 'bg-slate-500/15 text-slate-400 border-slate-500/30',
		running: 'bg-blue-500/15 text-blue-400 border-blue-500/30',
		succeeded: 'bg-emerald-500/15 text-emerald-500 border-emerald-500/30',
		completed: 'bg-emerald-500/15 text-emerald-500 border-emerald-500/30',
		failed: 'bg-red-500/15 text-red-400 border-red-500/30',
		cancelled: 'bg-orange-500/15 text-orange-400 border-orange-500/30'
	};

	let batches = $state<BatchSummary[]>([]);
	let loading = $state(true);
	let showForm = $state(false);
	let newName = $state('');
	let newDescription = $state('');
	let matrixText = $state('{\n  "model_id": ["gpt-4", "claude-3"]\n}');
	let saving = $state(false);
	let formError = $state('');

	async function load() {
		loading = true;
		try {
			const res = await listBatches();
			batches = res.items.filter((b) => {
				const cfg = (b.config ?? {}) as Record<string, unknown>;
				return cfg.pipeline_id === pipelineId;
			});
		} catch {
			batches = [];
		} finally {
			loading = false;
		}
	}

	async function create() {
		formError = '';
		if (!newName.trim()) {
			formError = 'Name is required';
			return;
		}
		let matrix: Record<string, unknown> | undefined;
		try {
			matrix = matrixText.trim() ? JSON.parse(matrixText) : undefined;
		} catch (e) {
			formError = 'Invalid matrix JSON: ' + (e as Error).message;
			return;
		}
		saving = true;
		try {
			await createBatch({
				pipeline_id: pipelineId,
				name: newName.trim(),
				description: newDescription || undefined,
				matrix
			});
			showForm = false;
			newName = '';
			newDescription = '';
			await load();
		} catch (e: unknown) {
			formError = (e as { detail?: string })?.detail ?? 'Failed to create batch';
		} finally {
			saving = false;
		}
	}

	async function cancel(id: string) {
		if (!confirm('Cancel this batch?')) return;
		try {
			await cancelBatch(id);
			await load();
		} catch {
			alert('Failed to cancel batch');
		}
	}

	function fmt(s: string) {
		return new Date(s).toLocaleString();
	}

	onMount(load);
</script>

<Card.Root>
	<Card.Header class="flex flex-row items-center justify-between space-y-0">
		<Card.Title class="flex items-center gap-1.5">
			<Layers class="h-4 w-4" />Batches ({batches.length})
		</Card.Title>
		<Button variant="outline" size="sm" onclick={() => (showForm = !showForm)} class="gap-1.5">
			<Plus class="h-3.5 w-3.5" />{showForm ? 'Cancel' : 'New Batch'}
		</Button>
	</Card.Header>
	<Card.Content class="space-y-3">
		{#if showForm}
			<div class="rounded-md border bg-muted/30 p-3 space-y-2">
				<div class="space-y-1">
					<Label class="text-xs" for="batch-name">Name *</Label>
					<Input id="batch-name" bind:value={newName} class="h-8 text-xs" placeholder="Nightly evaluation" />
				</div>
				<div class="space-y-1">
					<Label class="text-xs" for="batch-desc">Description</Label>
					<Input id="batch-desc" bind:value={newDescription} class="h-8 text-xs" placeholder="optional" />
				</div>
				<div class="space-y-1">
					<Label class="text-xs" for="batch-matrix">Parameter Matrix (JSON)</Label>
					<textarea
						id="batch-matrix"
						bind:value={matrixText}
						rows={5}
						class="w-full rounded-md border bg-background p-2 text-xs font-mono resize-y focus:outline-none focus:ring-1 focus:ring-ring"
					></textarea>
					<p class="text-[10px] text-muted-foreground">One run per combination of values. Empty for a single run.</p>
				</div>
				{#if formError}<p class="text-xs text-destructive">{formError}</p>{/if}
				<Button size="sm" onclick={create} disabled={saving} class="w-full">
					{saving ? 'Starting…' : 'Start Batch'}
				</Button>
			</div>
		{/if}

		{#if loading}
			<div class="flex justify-center py-6"><LoaderCircle class="h-5 w-5 animate-spin text-muted-foreground" /></div>
		{:else if batches.length === 0}
			<p class="text-xs text-muted-foreground italic">No batches. Click <strong>New Batch</strong> to start one with parameter matrix.</p>
		{:else}
			<div class="space-y-1.5">
				{#each batches as b (b.id)}
					<div class="flex items-center gap-2 rounded-md border bg-card px-3 py-2 text-xs">
						<div class="flex-1 min-w-0">
							<p class="font-medium truncate">{b.name}</p>
							<p class="text-[10px] text-muted-foreground">Started {fmt(b.created_at)}</p>
						</div>
						<Badge variant="outline" class="shrink-0 text-[10px] {statusColors[b.status] ?? ''}">
							{b.status}
						</Badge>
						{#if b.status === 'pending' || b.status === 'running'}
							<Button variant="ghost" size="icon" class="h-7 w-7 text-destructive hover:text-destructive" onclick={() => cancel(b.id)} title="Cancel">
								<XCircle class="h-3.5 w-3.5" />
							</Button>
						{/if}
					</div>
				{/each}
			</div>
		{/if}
	</Card.Content>
</Card.Root>
