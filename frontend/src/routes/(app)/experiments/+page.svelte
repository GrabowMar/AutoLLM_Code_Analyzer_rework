<script lang="ts">
	import { onMount } from 'svelte';
	import * as Card from '$lib/components/ui/card';
	import { Badge } from '$lib/components/ui/badge';
	import { Button } from '$lib/components/ui/button';
	import { listExperiments, type Experiment } from '$lib/api/experiments';
	import { formatApiError } from '$lib/api/core';
	import PageHeader from '$lib/components/PageHeader.svelte';
	import StatusBadge from '$lib/components/StatusBadge.svelte';
	import EmptyState from '$lib/components/EmptyState.svelte';
	import LoadingState from '$lib/components/LoadingState.svelte';
	import ErrorAlert from '$lib/components/ErrorAlert.svelte';
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
		} catch (e) {
			error = formatApiError(e, 'Failed to load experiments.');
		} finally {
			loading = false;
		}
	}
</script>

<div class="space-y-6">
	<PageHeader
		title="Experiments"
		description="Designed comparisons: apps × conditions (model × harness preset, or model × prompt profile) × repeats, with shared seeds per cell."
	>
		{#snippet actions()}
			<Button href="/experiments/create" size="sm">
				<Plus class="h-4 w-4" />
				New experiment
			</Button>
		{/snippet}
	</PageHeader>

	{#if loading}
		<LoadingState label="loading experiments" />
	{:else if error}
		<ErrorAlert message={error} onretry={load} />
	{:else if experiments.length === 0}
		<EmptyState
			icon={GitCompareArrows}
			title="No experiments yet"
			description="Create one to compare models and harness configurations across a set of apps."
		>
			{#snippet action()}
				<Button href="/experiments/create" size="sm"><Plus class="h-4 w-4" />New experiment</Button>
			{/snippet}
		</EmptyState>
	{:else}
		<div class="grid gap-3 sm:grid-cols-2 lg:grid-cols-3">
			{#each experiments as exp (exp.id)}
				<a href="/experiments/{exp.id}" class="rounded-md focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring">
					<Card.Root class="card-lift h-full">
						<Card.Header class="pb-2">
							<div class="flex items-start justify-between gap-2">
								<Card.Title class="min-w-0 break-words text-base">{exp.name}</Card.Title>
								<StatusBadge status={exp.status} />
							</div>
							{#if exp.description}
								<Card.Description class="line-clamp-2">{exp.description}</Card.Description>
							{/if}
						</Card.Header>
						<Card.Content>
							<div class="flex flex-wrap items-center gap-x-4 gap-y-1 text-xs text-muted-foreground">
								<Badge variant="outline">{exp.mode === 'agent' ? 'Agent' : 'Template scaffolding'}</Badge>
								<span>{exp.app_requirement_ids.length} apps</span>
								<span>{exp.condition_count} conditions</span>
								<span>{exp.repeats}× repeats</span>
								{#if exp.stack_slug}
									<span class="font-mono">{exp.stack_slug}</span>
								{/if}
								{#if exp.base_seed !== null}
									<span>seed {exp.base_seed}</span>
								{/if}
							</div>
						</Card.Content>
					</Card.Root>
				</a>
			{/each}
		</div>
	{/if}
</div>
