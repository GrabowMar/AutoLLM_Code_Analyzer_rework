<script lang="ts">
	import { goto } from '$app/navigation';
	import { onMount } from 'svelte';
	import * as Card from '$lib/components/ui/card';
	import { Button } from '$lib/components/ui/button';
	import { Badge } from '$lib/components/ui/badge';
	import { listExperiments, type Experiment } from '$lib/api/experiments';
	import { experimentLifecycleColors as statusColors } from '$lib/constants/colors';
	import Plus from '@lucide/svelte/icons/plus';
	import GitCompareArrows from '@lucide/svelte/icons/git-compare-arrows';

	let experiments = $state<Experiment[]>([]);
	let loading = $state(true);
	let error = $state('');

	onMount(load);

	async function load() {
		loading = true;
		error = '';
		try {
			experiments = await listExperiments();
		} catch (err: any) {
			error = err?.detail ?? err?.message ?? 'Failed to load experiments';
		} finally {
			loading = false;
		}
	}
</script>

<svelte:head>
	<title>Experiments - LLM Lab</title>
</svelte:head>

<div class="space-y-6">
	<div class="flex flex-col gap-3 sm:flex-row sm:items-center sm:justify-between">
		<div class="page-header min-w-0">
			<h1>Experiments</h1>
			<p>Designed runs: apps × models × prompt bundles × repeats, with a reproducibility seed.</p>
		</div>
		<Button href="/experiments/create" size="sm">
			<Plus class="h-4 w-4" />
			New experiment
		</Button>
	</div>

	{#if error}
		<div
			class="rounded-md border border-destructive/30 bg-destructive/10 px-4 py-3 text-sm text-destructive"
		>
			{error}
		</div>
	{/if}

	{#if loading}
		<div class="flex items-center justify-center py-16">
			<span class="loading-mono">loading experiments</span>
		</div>
	{:else if experiments.length === 0}
		<Card.Root>
			<Card.Content class="flex flex-col items-center gap-3 py-16 text-center">
				<GitCompareArrows class="h-8 w-8 text-muted-foreground" />
				<p class="text-sm text-muted-foreground">
					No experiments yet. Create one to compare models and prompt variants across a set of apps.
				</p>
				<Button onclick={() => goto('/experiments/create')}>
					<Plus class="h-4 w-4" />
					New experiment
				</Button>
			</Card.Content>
		</Card.Root>
	{:else}
		<div class="grid gap-3 sm:grid-cols-2 lg:grid-cols-3">
			{#each experiments as exp (exp.id)}
				<button type="button" class="text-left" onclick={() => goto(`/experiments/${exp.id}`)}>
					<Card.Root class="h-full transition-colors hover:border-primary/40">
						<Card.Header class="pb-2">
							<div class="flex items-start justify-between gap-2">
								<Card.Title class="text-base">{exp.name}</Card.Title>
								<Badge variant="outline" class="shrink-0 text-[10px] {statusColors[exp.status] ?? ''}">
									{exp.status}
								</Badge>
							</div>
							{#if exp.description}
								<Card.Description class="line-clamp-2">{exp.description}</Card.Description>
							{/if}
						</Card.Header>
						<Card.Content>
							<div class="flex flex-wrap gap-x-4 gap-y-1 text-xs text-muted-foreground">
								<span>{exp.app_requirement_ids.length} apps</span>
								<span>{exp.condition_count} conditions</span>
								<span>{exp.repeats}× repeats</span>
								{#if exp.base_seed !== null}
									<span>seed {exp.base_seed}</span>
								{/if}
							</div>
						</Card.Content>
					</Card.Root>
				</button>
			{/each}
		</div>
	{/if}
</div>
