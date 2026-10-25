<script lang="ts">
	import { replaceState } from '$app/navigation';
	import { page } from '$app/state';
	import { Segmented } from '$lib/components/ui/segmented';
	import ExperimentComposer from '$lib/components/automation/ExperimentComposer.svelte';
	import WorkflowComposer from '$lib/components/automation/WorkflowComposer.svelte';
	import GitCompareArrows from '@lucide/svelte/icons/git-compare-arrows';
	import Workflow from '@lucide/svelte/icons/workflow';

	const type = $derived(page.url.searchParams.get('type') === 'workflow' ? 'workflow' : 'experiment');
	const documentTitle = $derived(type === 'workflow' ? 'New workflow' : 'New experiment');
	const visited = $state({ experiment: false, workflow: false });

	$effect(() => {
		visited[type] = true;
	});

	function selectType(value: string) {
		const url = new URL(page.url);
		url.searchParams.set('type', value);
		replaceState(url, page.state);
	}
</script>

<div class="space-y-6">
	<div class="mx-auto w-full space-y-2 rounded-md border bg-card p-4" class:max-w-3xl={type === 'experiment'}>
		<p id="automation-type-label" class="text-sm font-medium">Automation type</p>
		<Segmented
			aria-labelledby="automation-type-label"
			value={type}
			onValueChange={selectType}
			items={[
				{ value: 'experiment', label: 'Experiment', icon: GitCompareArrows },
				{ value: 'workflow', label: 'Workflow', icon: Workflow },
			]}
		/>
		<p class="text-xs text-muted-foreground">
			{#if type === 'experiment'}
				Compare models and configurations across the same apps with repeated trials.
			{:else}
				Connect generation, analysis and reporting steps, then run them on demand or on a schedule.
			{/if}
		</p>
	</div>

	<!-- Keep visited composers mounted so switching types preserves both drafts. -->
	{#if type === 'experiment' || visited.experiment}
		<div hidden={type !== 'experiment'}>
			<ExperimentComposer {documentTitle} />
		</div>
	{/if}
	{#if type === 'workflow' || visited.workflow}
		<div hidden={type !== 'workflow'}>
			<WorkflowComposer {documentTitle} active={type === 'workflow'} />
		</div>
	{/if}
</div>
