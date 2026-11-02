<script lang="ts">
	// Behaviour card + trajectory timeline of one agent run. The stored record
	// (result_data.agent.trajectory) renders at once; the timeline (and a
	// record computed on the fly for runs not backfilled yet) comes from
	// GET /generation/jobs/{id}/trajectory/.
	import { onMount } from 'svelte';
	import * as Card from '$lib/components/ui/card';
	import LoadingState from '$lib/components/LoadingState.svelte';
	import ErrorAlert from '$lib/components/ErrorAlert.svelte';
	import EmptyState from '$lib/components/EmptyState.svelte';
	import Activity from '@lucide/svelte/icons/activity';
	import type { GenerationJob } from '$lib/api/client';
	import { getJobTrajectory, type JobTrajectory, type TrajectoryRecord } from '$lib/api/trajectory';
	import { formatApiError } from '$lib/api/core';
	import BehaviourCard from './BehaviourCard.svelte';
	import TrajectoryTimeline from './TrajectoryTimeline.svelte';

	interface Props {
		job: GenerationJob;
	}

	let { job }: Props = $props();

	let data = $state<JobTrajectory | null>(null);
	let loading = $state(true);
	let error = $state('');

	const stored = $derived(
		((job.result_data?.agent as Record<string, unknown> | undefined)?.trajectory ?? null) as TrajectoryRecord | null,
	);
	const record = $derived(data?.record ?? stored);
	const recordSource = $derived(data?.record_source ?? (stored ? 'stored' : null));
	const analyticsError = $derived(
		data?.trajectory_error ?? ((job.result_data?.agent as Record<string, unknown> | undefined)?.trajectory_error as string | undefined) ?? null,
	);

	async function load() {
		loading = true;
		error = '';
		try {
			data = await getJobTrajectory(job.id);
		} catch (e) {
			error = formatApiError(e);
		} finally {
			loading = false;
		}
	}

	onMount(load);
</script>

<div class="space-y-4">
	{#if record && typeof record === 'object'}
		<BehaviourCard {record} rules={data?.rules ?? null} {recordSource} />
	{:else if loading}
		<Card.Root><Card.Content><LoadingState label="loading behaviour" class="py-8" /></Card.Content></Card.Root>
	{:else if !error}
		<Card.Root>
			<Card.Content>
				<EmptyState
					compact
					icon={Activity}
					title="No behaviour record"
					description={analyticsError ? `Trajectory analytics failed for this run: ${analyticsError}` : 'This run has no call log to analyse.'}
				/>
			</Card.Content>
		</Card.Root>
	{/if}

	{#if data?.notes?.length}
		<ul class="space-y-0.5 text-[11px] text-muted-foreground">
			{#each data.notes as note (note)}<li>{note}</li>{/each}
		</ul>
	{/if}

	{#if error}
		<ErrorAlert title="Could not load the trajectory" message={error} onretry={load} />
	{:else if loading}
		<Card.Root><Card.Content><LoadingState label="loading trajectory" class="py-8" /></Card.Content></Card.Root>
	{:else if data}
		<TrajectoryTimeline jobId={job.id} steps={data.steps} truncated={data.truncated} totalSteps={data.total_steps} source={data.source} />
	{/if}
</div>
