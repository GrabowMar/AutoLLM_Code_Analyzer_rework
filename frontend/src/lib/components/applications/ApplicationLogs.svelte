<script lang="ts">
import { onMount, onDestroy } from 'svelte';
import * as Card from '$lib/components/ui/card';
import { Button } from '$lib/components/ui/button';
import { Badge } from '$lib/components/ui/badge';
import Terminal from '@lucide/svelte/icons/terminal';
import RefreshCw from '@lucide/svelte/icons/refresh-cw';
import { getContainers, getContainerLogs, type ContainerInstance } from '$lib/api/runtime';

interface Props { jobId: string }
let { jobId }: Props = $props();

let container = $state<ContainerInstance | null>(null);
let logs = $state('');
let tail = $state(200);
let loading = $state(true);
let autoRefresh = $state(true);
let poll: ReturnType<typeof setInterval> | null = null;

async function refresh() {
	try {
		const cres = await getContainers({ job_id: jobId, per_page: 1 });
		container = cres.containers[0] ?? null;
		if (!container) { logs = ''; return; }
		const r = await getContainerLogs(container.id, tail);
		logs = r.logs ?? '';
	} catch (e) {
		logs = `Failed to fetch logs: ${(e as Error).message}`;
	} finally {
		loading = false;
	}
}

function startPoll() {
	stopPoll();
	if (!autoRefresh) return;
	poll = setInterval(() => {
		if (container?.status === 'running') refresh();
	}, 3000);
}
function stopPoll() { if (poll) { clearInterval(poll); poll = null; } }

onMount(() => { refresh(); startPoll(); });
onDestroy(stopPoll);

$effect(() => { autoRefresh; startPoll(); });
</script>

<section id="logs" class="space-y-4">
	<h2 class="text-lg font-semibold flex items-center gap-2"><Terminal class="h-5 w-5" /> Container Logs</h2>
	<Card.Root>
		<Card.Content class="p-4 space-y-3">
			<div class="flex items-center gap-3 flex-wrap">
				<label class="text-xs text-muted-foreground flex items-center gap-1">
					Tail
					<input type="number" min="10" max="2000" step="50" bind:value={tail} class="w-20 rounded border bg-background px-2 py-1 text-xs" />
				</label>
				<Button size="sm" variant="outline" onclick={refresh} disabled={loading}>
					<RefreshCw class="h-3.5 w-3.5 mr-1.5" /> Refresh
				</Button>
				<label class="text-xs flex items-center gap-1.5">
					<input type="checkbox" bind:checked={autoRefresh} /> Auto-refresh (3s)
				</label>
				{#if container}
					<Badge variant="outline" class="text-xs ml-auto">{container.status}</Badge>
				{/if}
			</div>

			{#if !container && !loading}
				<p class="text-sm text-muted-foreground italic">Start the application container to view logs.</p>
			{:else}
				<pre class="bg-zinc-950 text-zinc-100 text-xs font-mono p-3 rounded max-h-[500px] overflow-auto whitespace-pre-wrap break-words">{logs || '(no logs)'}</pre>
			{/if}
		</Card.Content>
	</Card.Root>
</section>
