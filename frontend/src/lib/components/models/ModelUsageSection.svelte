<script lang="ts">
	import { onMount } from 'svelte';
	import * as Card from '$lib/components/ui/card';
	import StatusBadge from '$lib/components/StatusBadge.svelte';
	import LoadingState from '$lib/components/LoadingState.svelte';
	import ErrorAlert from '$lib/components/ErrorAlert.svelte';
	import { containerTones } from '$lib/constants/colors';
	import Activity from '@lucide/svelte/icons/activity';
	import PlayCircle from '@lucide/svelte/icons/play-circle';
	import StopCircle from '@lucide/svelte/icons/stop-circle';
	import CircleDashed from '@lucide/svelte/icons/circle-dashed';
	import { getGenerationJobs } from '$lib/api/generation';
	import { getContainers, type ContainerInstance } from '$lib/api/runtime';

	interface Props {
		modelId: string;
	}

	let { modelId }: Props = $props();

	let loading = $state(true);
	let error = $state('');
	let totalApps = $state(0);
	let containers = $state<ContainerInstance[]>([]);

	const running = $derived(containers.filter((c) => c.status === 'running').length);
	const stopped = $derived(containers.filter((c) => c.status === 'stopped').length);
	const other = $derived(containers.length - running - stopped);
	const coverage = $derived(totalApps > 0 ? Math.round((containers.length / totalApps) * 100) : 0);

	async function load() {
		loading = true;
		error = '';
		try {
			const [jobsRes, containersRes] = await Promise.all([
				getGenerationJobs({ model_id: modelId, per_page: 1 }),
				getContainers({ model_id: modelId, per_page: 100 }),
			]);
			totalApps = jobsRes.total;
			containers = containersRes.containers;
		} catch (e) {
			error = `Failed to load usage: ${(e as Error).message}`;
		} finally {
			loading = false;
		}
	}

	onMount(load);
</script>

<section id="usage" class="space-y-4">
	<Card.Root>
		<Card.Header>
			<div class="flex items-center gap-2">
				<Activity class="h-4 w-4 text-muted-foreground" />
				<Card.Title class="text-base">Usage</Card.Title>
			</div>
		</Card.Header>
		<Card.Content>
			{#if loading}
				<LoadingState label="loading usage" class="py-10" />
			{:else if error}
				<ErrorAlert message={error} onretry={load} />
			{:else}
				{#if totalApps === 0}
					<p class="text-sm text-muted-foreground">No containers: no applications have been generated with this model.</p>
				{:else}
					<p class="flex flex-wrap items-center gap-x-4 gap-y-1 font-mono text-xs tabular-nums text-muted-foreground">
						<span class="flex items-center gap-1.5"><PlayCircle class="h-3.5 w-3.5 text-success-fg" /><span class="text-foreground">{running}</span> running</span>
						<span class="flex items-center gap-1.5"><StopCircle class="h-3.5 w-3.5" /><span class="text-foreground">{stopped}</span> stopped</span>
						<span class="flex items-center gap-1.5"><CircleDashed class="h-3.5 w-3.5 text-warning-fg" /><span class="text-foreground">{other}</span> other</span>
						<span>coverage <span class="text-foreground">{coverage}%</span> ({containers.length}/{totalApps} apps)</span>
					</p>
				{/if}

				{#if containers.length > 0}
					<div class="mt-4">
						<div class="section-label mb-2">Recent containers</div>
						<div class="space-y-1.5">
							{#each containers.slice(0, 8) as c (c.id)}
								<div class="flex items-center justify-between rounded border bg-muted/20 px-2 py-1.5 text-xs">
									<div class="flex items-center gap-2 min-w-0">
										<StatusBadge status={c.status} tones={containerTones} />
										<span class="font-mono truncate">{c.name || c.id.substring(0, 8)}</span>
									</div>
									<div class="text-muted-foreground font-mono whitespace-nowrap">
										{c.app_port ? `:${c.app_port}` : ''}
									</div>
								</div>
							{/each}
						</div>
					</div>
				{/if}
			{/if}
		</Card.Content>
	</Card.Root>
</section>
