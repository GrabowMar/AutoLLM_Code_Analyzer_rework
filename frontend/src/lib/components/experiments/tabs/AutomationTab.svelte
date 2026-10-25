<script lang="ts">
	/**
	 * AutomationTab — schedules and post-run of one experiment (what the old
	 * Automation hub did, attached to the experiment). Refetches the post-run
	 * list on `post_run` events (`postRunSignal`).
	 *
	 * Props: `experiment`, `postRunSignal`.
	 */
	import { untrack } from 'svelte';
	import ErrorAlert from '$lib/components/ErrorAlert.svelte';
	import LoadingState from '$lib/components/LoadingState.svelte';
	import {
		getPostRun,
		listSchedules,
		type ExperimentDetail,
		type PostRunConfig,
		type ScheduleOut,
	} from '$lib/api/experiments';
	import { formatApiError } from '$lib/api/core';
	import PostRunCard from '../PostRunCard.svelte';
	import SchedulesCard from '../SchedulesCard.svelte';

	interface Props {
		experiment: ExperimentDetail;
		postRunSignal?: number;
	}

	let { experiment, postRunSignal = 0 }: Props = $props();

	let schedules = $state<ScheduleOut[]>(untrack(() => experiment.schedules ?? []));
	let postRun = $state<PostRunConfig | null>(null);
	let error = $state('');

	async function loadSchedules() {
		try {
			schedules = await listSchedules(experiment.id);
		} catch (e) {
			error = formatApiError(e, 'Could not load the schedules.');
		}
	}

	async function loadPostRun() {
		try {
			postRun = await getPostRun(experiment.id);
		} catch (e) {
			error = formatApiError(e, 'Could not load the post-run settings.');
		}
	}

	function reload() {
		error = '';
		void loadSchedules();
		void loadPostRun();
	}

	$effect(() => {
		void experiment.id;
		untrack(reload);
	});

	$effect(() => {
		if (postRunSignal === 0) return;
		untrack(() => void loadPostRun());
	});
</script>

<div class="space-y-6">
	{#if error}
		<ErrorAlert message={error} onretry={reload} />
	{/if}
	<SchedulesCard experimentId={experiment.id} {schedules} onchange={loadSchedules} />
	{#if postRun}
		<PostRunCard experimentId={experiment.id} mode={experiment.mode} data={postRun} onchange={loadPostRun} />
	{:else if !error}
		<LoadingState label="loading post-run" class="py-8" />
	{/if}
</div>
