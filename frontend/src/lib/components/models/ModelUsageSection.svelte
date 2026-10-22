<script lang="ts">
	import { onMount } from 'svelte';
	import * as Card from '$lib/components/ui/card';
	import { Badge } from '$lib/components/ui/badge';
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
			const jobsRes = await getGenerationJobs({ model_id: modelId, per_page: 100 });
			totalApps = jobsRes.total;
			const results = await Promise.all(
				jobsRes.items.map((j) =>
					getContainers({ job_id: j.id, per_page: 5 }).then((r) => r.containers).catch(() => []),
				),
			);
			containers = results.flat();
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
				<div class="grid grid-cols-2 sm:grid-cols-4 gap-3">
					<div class="rounded-md border bg-card p-3">
						<div class="flex items-center gap-2 text-xs text-muted-foreground">
							<PlayCircle class="h-3.5 w-3.5 text-success-fg" />Running
						</div>
						<div class="mt-1 text-2xl font-semibold text-success-fg">{running}</div>
					</div>
					<div class="rounded-md border bg-card p-3">
						<div class="flex items-center gap-2 text-xs text-muted-foreground">
							<StopCircle class="h-3.5 w-3.5 text-muted-foreground" />Stopped
						</div>
						<div class="mt-1 text-2xl font-semibold">{stopped}</div>
					</div>
					<div class="rounded-md border bg-card p-3">
						<div class="flex items-center gap-2 text-xs text-muted-foreground">
							<CircleDashed class="h-3.5 w-3.5 text-warning-fg" />Other
						</div>
						<div class="mt-1 text-2xl font-semibold">{other}</div>
					</div>
					<div class="rounded-md border bg-card p-3">
						<div class="text-xs text-muted-foreground">Container Coverage</div>
						<div class="mt-1 text-2xl font-semibold">{coverage}%</div>
						<div class="text-[11px] text-muted-foreground">{containers.length} / {totalApps} apps</div>
					</div>
				</div>

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
