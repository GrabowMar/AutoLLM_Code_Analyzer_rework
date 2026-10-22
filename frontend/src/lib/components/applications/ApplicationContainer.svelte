<script lang="ts">
import { onMount, onDestroy } from 'svelte';
import * as Card from '$lib/components/ui/card';
import StatusBadge from '$lib/components/StatusBadge.svelte';
import LoadingState from '$lib/components/LoadingState.svelte';
import EmptyState from '$lib/components/EmptyState.svelte';
import { containerTones } from '$lib/constants/colors';
import Server from '@lucide/svelte/icons/server';
import ExternalLink from '@lucide/svelte/icons/external-link';
import RefreshCw from '@lucide/svelte/icons/refresh-cw';
import { Button } from '$lib/components/ui/button';
import { getContainers, inspectContainer, type ContainerInstance, type ContainerInspect } from '$lib/api/runtime';
import AppRuntimeControls from './AppRuntimeControls.svelte';

interface Props { jobId: string; jobStatus?: string }
let { jobId, jobStatus = '' }: Props = $props();

let container = $state<ContainerInstance | null>(null);
let inspect = $state<ContainerInspect | null>(null);
let loading = $state(true);
let poll: ReturnType<typeof setInterval> | null = null;

async function refresh() {
	try {
		const res = await getContainers({ job_id: jobId, per_page: 1 });
		container = res.containers[0] ?? null;
		if (container) {
			try { inspect = await inspectContainer(container.id); } catch { inspect = null; }
		} else {
			inspect = null;
		}
	} catch {
		// Keep the last known state; the next poll retries.
	} finally {
		loading = false;
	}
}

onMount(() => {
	refresh();
	poll = setInterval(refresh, 5000);
});
onDestroy(() => { if (poll) clearInterval(poll); });
</script>

<section id="container" class="space-y-4">
	<Card.Root>
		<Card.Header class="flex flex-row flex-wrap items-start justify-between gap-3">
			<div class="space-y-1.5">
				<Card.Title>Runtime container</Card.Title>
				<Card.Description>Build, start and inspect the container serving this app.</Card.Description>
			</div>
			<div class="flex flex-wrap items-center gap-2">
				<AppRuntimeControls {jobId} {jobStatus} onChange={() => refresh()} />
				<Button variant="ghost" size="sm" onclick={refresh} title="Refresh" aria-label="Refresh container">
					<RefreshCw class="h-3.5 w-3.5" />
				</Button>
			</div>
		</Card.Header>
		<Card.Content class="space-y-4">

			{#if loading}
				<LoadingState label="loading container" class="py-8" />
			{:else if !container}
				<EmptyState compact icon={Server} title="No container yet" description="Use Build above to provision a runtime for this app." />
			{:else}
				<div class="grid grid-cols-1 md:grid-cols-2 gap-3 text-sm">
					<div>
						<span class="section-label">Status</span>
						<div><StatusBadge status={container.status} tones={containerTones} /></div>
					</div>
					<div>
						<span class="section-label">Updated</span>
						<div class="font-mono text-xs">{new Date(container.updated_at).toLocaleString()}</div>
					</div>
					<div>
						<span class="section-label">Container ID</span>
						<div class="font-mono text-xs break-all">{container.id}</div>
					</div>
					<div>
						<span class="section-label">Name</span>
						<div class="font-mono text-xs break-all">{container.name}</div>
					</div>
					<div class="md:col-span-2">
						<span class="section-label">Image</span>
						<div class="font-mono text-xs break-all">{inspect?.image || container.image || '—'}</div>
					</div>
				</div>

				<div>
					<h3 class="section-label mb-2">Ports</h3>
					<div class="overflow-x-auto">
						<table class="w-full text-sm">
							<thead>
								<tr>
									<th>Service</th>
									<th>Host Port</th>
									<th>Container Port</th>
									<th>Actions</th>
								</tr>
							</thead>
							<tbody>
								{#if container.app_port}
									<tr>
										<td>App</td>
										<td class="font-mono text-xs">{container.app_port}</td>
										<td class="font-mono text-xs">8000</td>
										<td>
											<a href={container.app_url ?? '#'} target="_blank" rel="noopener noreferrer" class="text-success-fg hover:underline inline-flex items-center gap-1 text-xs">Open <ExternalLink class="h-3 w-3" /></a>
										</td>
									</tr>
								{:else}
									<tr><td colspan="4" class="px-3 py-3 text-xs text-muted-foreground text-center">No ports mapped</td></tr>
								{/if}
							</tbody>
						</table>
					</div>
				</div>

				{#if container.last_error}
					<div class="rounded border border-destructive/30 bg-destructive/10 p-2 font-mono text-xs text-danger-fg">
						{container.last_error}
					</div>
				{/if}
			{/if}
		</Card.Content>
	</Card.Root>
</section>
