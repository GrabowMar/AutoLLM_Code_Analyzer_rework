<script lang="ts">
import { onMount, onDestroy } from 'svelte';
import * as Card from '$lib/components/ui/card';
import { Badge } from '$lib/components/ui/badge';
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
	} finally {
		loading = false;
	}
}

onMount(() => {
	refresh();
	poll = setInterval(refresh, 5000);
});
onDestroy(() => { if (poll) clearInterval(poll); });

const statusColor: Record<string, string> = {
	pending: 'bg-zinc-500/15 text-zinc-400 border-zinc-500/30',
	building: 'bg-amber-500/15 text-amber-400 border-amber-500/30',
	running: 'bg-emerald-500/15 text-emerald-400 border-emerald-500/30',
	stopped: 'bg-zinc-500/15 text-zinc-400 border-zinc-500/30',
	failed: 'bg-red-500/15 text-red-400 border-red-500/30',
	removed: 'bg-zinc-500/15 text-zinc-500 border-zinc-500/30',
};
</script>

<section id="container" class="space-y-4">
	<h2 class="text-lg font-semibold flex items-center gap-2"><Server class="h-5 w-5" /> Container</h2>
	<Card.Root>
		<Card.Content class="p-4 space-y-4">
			<div class="flex items-center gap-3 flex-wrap">
				<AppRuntimeControls {jobId} {jobStatus} onChange={() => refresh()} />
				<Button variant="ghost" size="sm" onclick={refresh} title="Refresh">
					<RefreshCw class="h-3.5 w-3.5" />
				</Button>
			</div>

			{#if loading}
				<p class="text-sm text-muted-foreground">Loading…</p>
			{:else if !container}
				<p class="text-sm text-muted-foreground">No container yet. Click <strong>Build</strong> to provision.</p>
			{:else}
				<div class="grid grid-cols-1 md:grid-cols-2 gap-3 text-sm">
					<div>
						<span class="text-xs text-muted-foreground">Status</span>
						<div><Badge variant="outline" class="text-xs {statusColor[container.status] ?? ''}">{container.status}</Badge></div>
					</div>
					<div>
						<span class="text-xs text-muted-foreground">Updated</span>
						<div class="font-mono text-xs">{new Date(container.updated_at).toLocaleString()}</div>
					</div>
					<div>
						<span class="text-xs text-muted-foreground">Container ID</span>
						<div class="font-mono text-xs break-all">{container.id}</div>
					</div>
					<div>
						<span class="text-xs text-muted-foreground">Name</span>
						<div class="font-mono text-xs break-all">{container.name}</div>
					</div>
					<div class="md:col-span-2">
						<span class="text-xs text-muted-foreground">Image</span>
						<div class="font-mono text-xs break-all">{inspect?.image || container.image || '—'}</div>
					</div>
				</div>

				<div>
					<h3 class="text-sm font-medium mb-2">Ports</h3>
					<div class="overflow-x-auto">
						<table class="w-full text-sm">
							<thead>
								<tr class="border-b bg-muted/30">
									<th class="px-3 py-2 text-left text-xs font-medium text-muted-foreground">Service</th>
									<th class="px-3 py-2 text-left text-xs font-medium text-muted-foreground">Host Port</th>
									<th class="px-3 py-2 text-left text-xs font-medium text-muted-foreground">Container Port</th>
									<th class="px-3 py-2 text-left text-xs font-medium text-muted-foreground">Actions</th>
								</tr>
							</thead>
							<tbody class="divide-y">
								{#if container.app_port}
									<tr class="hover:bg-muted/30">
										<td class="px-3 py-2">App</td>
										<td class="px-3 py-2 font-mono text-xs">{container.app_port}</td>
										<td class="px-3 py-2 font-mono text-xs">8000</td>
										<td class="px-3 py-2">
											<a href={container.app_url ?? '#'} target="_blank" rel="noopener noreferrer" class="text-primary hover:underline inline-flex items-center gap-1 text-xs">Open <ExternalLink class="h-3 w-3" /></a>
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
					<div class="text-xs text-red-400 font-mono bg-red-500/5 border border-red-500/20 rounded p-2">
						{container.last_error}
					</div>
				{/if}
			{/if}
		</Card.Content>
	</Card.Root>
</section>
